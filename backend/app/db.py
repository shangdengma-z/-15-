"""体检结果持久化：SQLite 存历史，支撑零配额回放、跨社区对比与 Markdown 报告导出。

设计要点
- 主键是 scene_key（社区坐标 + 数据模式哈希），同一地点重复体检就地更新并累加 run_count，
  历史列表因此是「N 个社区」而不是「N 次点击」，演示中途刷新不会刷屏。
- 只存派生结果（清洗后的设施要点 / 覆盖率 / 盲区 / 计量指标），不存百度原始响应，
  规避原始数据再分发风险；库文件挂在 data/db 下，删库即重置演示环境。
- 每次打开连接都建表，免去启动顺序依赖；写操作显式包在事务里。
"""

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .config import settings

DDL = """
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, run_count INTEGER,
  lat REAL, lng REAL, label TEXT, archetype TEXT, mode TEXT, score INTEGER,
  elapsed REAL, api_calls INTEGER, cache_hits INTEGER, quota_units INTEGER,
  degraded INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS blindspots(
  run_id TEXT, lat REAL, lng REAL, items TEXT, verified INTEGER);
CREATE TABLE IF NOT EXISTS coverage(
  run_id TEXT, cat TEXT, name TEXT, inside INTEGER, total INTEGER);
CREATE TABLE IF NOT EXISTS agent_runs(
  id TEXT PRIMARY KEY, created_at TEXT, question TEXT, model TEXT, mode TEXT,
  steps INTEGER, api_calls INTEGER, answer TEXT, center TEXT);
CREATE TABLE IF NOT EXISTS traces(
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, seq INTEGER, tool TEXT,
  args TEXT, summary TEXT, latency_ms INTEGER, decision TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_bs_run ON blindspots(run_id);
CREATE INDEX IF NOT EXISTS idx_cov_run ON coverage(run_id);
CREATE INDEX IF NOT EXISTS idx_trace_agent ON traces(agent_id);
"""

POI_KEEP = 40   # 每类最多留 40 个设施要点：够地图回放渲染，又不至于把库撑大
ANSWER_KEEP = 4000

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def scene_key(center: tuple, mode: str) -> str:
    """同一坐标（小数点后 4 位内，约 11m）+ 同一数据模式视为同一社区。"""
    return f"{mode}-{round(center[0], 4)}-{round(center[1], 4)}"


def new_id(prefix: str = "") -> str:
    return prefix + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _open() -> sqlite3.Connection:
    Path(str(Path(settings.db_path).parent)).mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.db_path, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript(DDL)
    return c


def save_run(result: dict, label: str = "", archetype: str = "") -> str:
    """写入（或就地更新）一次体检结果，并把盲区/覆盖率展开成可查询列。"""
    meta = result.get("meta", {})
    center = result.get("center", {})
    # 降级是数据质量标记（单独成列），不该把同一个社区裂成两条 scene_key 档案
    mode = "mock" if meta.get("mock") else "real"
    run_id = scene_key((center.get("lat", 0), center.get("lng", 0)), mode)
    now = NOW()
    with closing(_open()) as c, c:
        prev = c.execute("SELECT created_at, run_count FROM runs WHERE id=?", (run_id,)).fetchone()
        c.execute(
            "INSERT OR REPLACE INTO runs(id,created_at,updated_at,run_count,lat,lng,label,archetype,"
            "mode,score,elapsed,api_calls,cache_hits,quota_units,degraded,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, prev["created_at"] if prev else now, now, (prev["run_count"] if prev else 0) + 1,
             center.get("lat"), center.get("lng"),
             label or f"{center.get('lat', 0):.4f},{center.get('lng', 0):.4f}", archetype, mode,
             result.get("health_score"), meta.get("elapsed_sec"), meta.get("api_calls"),
             meta.get("cache_hits"), meta.get("quota_units"), 1 if meta.get("degraded") else 0,
             json.dumps(_slim(result), ensure_ascii=False)))
        c.execute("DELETE FROM blindspots WHERE run_id=?", (run_id,))
        c.execute("DELETE FROM coverage WHERE run_id=?", (run_id,))
        for b in result.get("blindspots", []):
            items = b.get("missing") or b.get("missing_preview") or []
            c.execute("INSERT INTO blindspots(run_id,lat,lng,items,verified) VALUES(?,?,?,?,?)",
                      (run_id, b["lat"], b["lng"], "、".join(items), 1 if b.get("verified") else 0))
        for key, v in (result.get("coverage") or {}).items():
            c.execute("INSERT INTO coverage(run_id,cat,name,inside,total) VALUES(?,?,?,?,?)",
                      (run_id, key, v["name"], v["inside"], v["total"]))
    return run_id


def save_agent_run(out: dict, question: str, center: tuple | None = None) -> str:
    """存一次智能体问答，并把工具调用轨迹落进 traces（决策可回放，不是黑箱）。"""
    aid = new_id()
    now = NOW()
    llm_mode = out.get("mode") == "llm"
    with closing(_open()) as c, c:
        c.execute(
            "INSERT INTO agent_runs(id,created_at,question,model,mode,steps,api_calls,answer,center)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (aid, now, question[:300], settings.llm_model if llm_mode else "rule",
             "llm" if llm_mode else "rule", len(out.get("steps") or []),
             out.get("api_calls", 0), (out.get("reply") or "")[:ANSWER_KEEP],
             json.dumps(list(center) if center else [])))
        for i, s in enumerate(out.get("steps") or []):
            c.execute("INSERT INTO traces(agent_id,seq,tool,args,summary,latency_ms,decision,created_at)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (aid, i, s.get("tool", ""), json.dumps(s.get("args", {}), ensure_ascii=False)[:400],
                       str(s.get("summary", ""))[:400], s.get("latency_ms", 0),
                       "ok" if s.get("ok") else "error", now))
    return aid


def agent_trace(agent_id: str) -> list:
    with closing(_open()) as c:
        rows = c.execute("SELECT seq,tool,args,summary,latency_ms,decision FROM traces "
                         "WHERE agent_id=? ORDER BY seq", (agent_id,)).fetchall()
    return [dict(r) for r in rows]


def list_runs(limit: int = 50) -> list:
    with closing(_open()) as c:
        rows = c.execute(
            "SELECT r.id,r.created_at,r.updated_at,r.run_count,r.lat,r.lng,r.label,r.archetype,"
            "r.mode,r.score,r.elapsed,r.api_calls,r.cache_hits,r.quota_units,r.degraded,"
            "(SELECT COUNT(*) FROM blindspots b WHERE b.run_id=r.id AND b.verified=1) AS verified_bs,"
            "(SELECT COUNT(*) FROM coverage v WHERE v.run_id=r.id AND v.inside=0) AS empty_cats "
            "FROM runs r ORDER BY r.updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    with closing(_open()) as c:
        row = c.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def find_scene_run(center: tuple) -> dict | None:
    """按坐标取已建档的体检结果，供选址等下游能力复用——不再重跑一遍烧配额。"""
    for mode in ("real", "real+降级", "mock"):   # 中间一项兼容修键之前留下的旧记录
        payload = get_run(scene_key(center, mode))
        if payload:
            return payload
    return None


def delete_run(run_id: str) -> bool:
    with closing(_open()) as c, c:
        n = c.execute("DELETE FROM runs WHERE id=?", (run_id,)).rowcount
        c.execute("DELETE FROM blindspots WHERE run_id=?", (run_id,))
        c.execute("DELETE FROM coverage WHERE run_id=?", (run_id,))
    return bool(n)


def compare(run_ids: list) -> dict:
    """跨社区对比：只回派生指标与逐类别差值，前端一张表就能看出形态差异。"""
    with closing(_open()) as c:
        items = []
        for rid in run_ids:
            row = c.execute("SELECT id,label,archetype,mode,score,lat,lng,updated_at FROM runs "
                            "WHERE id=?", (rid,)).fetchone()
            if not row:
                continue
            cov = c.execute("SELECT cat,name,inside,total FROM coverage WHERE run_id=?",
                            (rid,)).fetchall()
            bs = c.execute("SELECT COUNT(*) n, SUM(verified) v FROM blindspots WHERE run_id=?",
                           (rid,)).fetchone()
            d = dict(row)
            d["coverage"] = {x["cat"]: {"name": x["name"], "inside": x["inside"], "total": x["total"]}
                             for x in cov}
            d["blindspots"] = bs["n"] or 0
            d["blindspots_verified"] = bs["v"] or 0
            items.append(d)
        diffs = []
        if len(items) >= 2:
            a, b = items[0], items[1]
            for cat in set(a["coverage"]) | set(b["coverage"]):
                va, vb = a["coverage"].get(cat, {}), b["coverage"].get(cat, {})
                diffs.append({"cat": cat, "name": (va or vb).get("name", cat),
                              "a": va.get("inside", 0), "b": vb.get("inside", 0),
                              "delta": (va.get("inside", 0) or 0) - (vb.get("inside", 0) or 0)})
        return {"runs": items, "diff": sorted(diffs, key=lambda x: x["delta"])}


def list_agent_runs(limit: int = 20) -> list:
    with closing(_open()) as c:
        rows = c.execute("SELECT id,created_at,question,model,mode,steps,api_calls,answer "
                         "FROM agent_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    """累计观测：把「缓存复用 + 历史回放省下的配额」变成屏幕上能指给评委看的数字。"""
    with closing(_open()) as c:
        r = c.execute("SELECT COUNT(*) n, AVG(score) avg_s, MIN(score) min_s, MAX(score) max_s, "
                      "SUM(api_calls) calls, SUM(cache_hits) hits, SUM(elapsed) secs, "
                      "SUM(degraded) degraded_runs, SUM(quota_units) quota, "
                      "SUM(run_count) total_runs FROM runs").fetchone()
        bs = c.execute("SELECT COUNT(*) n, SUM(verified) v FROM blindspots").fetchone()
        cats = c.execute("SELECT name, SUM(inside=0) missing, COUNT(*) total FROM coverage "
                         "GROUP BY name ORDER BY missing DESC").fetchall()
        agent = c.execute("SELECT COUNT(*) n, SUM(mode='llm') llm FROM agent_runs").fetchone()
        tr = c.execute("SELECT COUNT(*) n, SUM(latency_ms) ms FROM traces").fetchone()
    hits = r["hits"] or 0
    return {
        "communities": r["n"] or 0,
        "total_runs": r["total_runs"] or 0,
        "avg_score": round(r["avg_s"], 1) if r["avg_s"] is not None else None,
        "min_score": r["min_s"], "max_score": r["max_s"],
        "api_calls": r["calls"] or 0, "cache_hits": hits, "quota_units": r["quota"] or 0,
        "cache_hit_rate": round(hits / (hits + (r["calls"] or 0)), 3) if hits or r["calls"] else None,
        "total_seconds": round(r["secs"] or 0, 1),
        "degraded_runs": r["degraded_runs"] or 0,
        "blindspots": bs["n"] or 0, "blindspots_verified": bs["v"] or 0,
        "agent_turns": agent["n"] or 0, "agent_llm_turns": agent["llm"] or 0,
        "tool_calls": tr["n"] or 0, "tool_latency_ms": tr["ms"] or 0,
        "category_gaps": [{"name": x["name"], "missing_runs": x["missing"], "runs": x["total"]}
                          for x in cats],
    }


def _slim(result: dict) -> dict:
    """保留可回放渲染的派生数据；设施明细截断，原始响应从不入库。"""
    r = dict(result)
    r["pois"] = {k: v[:POI_KEEP] for k, v in (result.get("pois") or {}).items()}
    r["saved_at"] = NOW()
    return r


def fingerprint(payload: dict) -> str:
    """证据指纹：同一组参数与输出得到同一 ID，用于报告里标注「这份结论来自哪一次实测」。"""
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]
