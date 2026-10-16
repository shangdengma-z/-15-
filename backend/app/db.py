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

DDL_TABLES = """
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, run_count INTEGER,
  lat REAL, lng REAL, label TEXT, archetype TEXT, mode TEXT, score INTEGER,
  elapsed REAL, api_calls INTEGER, cache_hits INTEGER, quota_units INTEGER,
  degraded INTEGER, payload TEXT,
  user_id INTEGER, public INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS blindspots(
  run_id TEXT, lat REAL, lng REAL, items TEXT, verified INTEGER);
CREATE TABLE IF NOT EXISTS coverage(
  run_id TEXT, cat TEXT, name TEXT, inside INTEGER, total INTEGER);
CREATE TABLE IF NOT EXISTS agent_runs(
  id TEXT PRIMARY KEY, created_at TEXT, question TEXT, model TEXT, mode TEXT,
  steps INTEGER, api_calls INTEGER, answer TEXT, center TEXT, user_id INTEGER);
CREATE TABLE IF NOT EXISTS traces(
  id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, seq INTEGER, tool TEXT,
  args TEXT, summary TEXT, latency_ms INTEGER, decision TEXT, created_at TEXT);
"""

DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_bs_run ON blindspots(run_id);
CREATE INDEX IF NOT EXISTS idx_cov_run ON coverage(run_id);
CREATE INDEX IF NOT EXISTS idx_trace_agent ON traces(agent_id);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id);
"""

POI_KEEP = 40   # 每类最多留 40 个设施要点：够地图回放渲染，又不至于把库撑大
ANSWER_KEEP = 4000

def _now() -> str:
    """DATETIME 列统一按这个文本格式写，SQLite 与 MySQL 两边同构、可直接比较。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def scene_key(center: tuple, mode: str, user_id: int | None = None) -> str:
    """同一坐标（小数点后 4 位内，约 11m）+ 同一数据模式视为同一社区。

    多用户下前缀 `u<id>-`：同一个社区由两个人各自体检时是两份独立档案，
    否则 A 的档案会被 B 的就地更新覆盖掉。
    """
    base = f"{mode}-{round(center[0], 4)}-{round(center[1], 4)}"
    return f"u{user_id}-{base}" if user_id else base


def new_id(prefix: str = "") -> str:
    return prefix + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _open() -> sqlite3.Connection:
    Path(str(Path(settings.db_path).parent)).mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.db_path, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript(DDL_TABLES)
    _ensure_owner_columns(c)
    # 索引必须在补列之后建：老库的 runs 表当时没有 user_id，
    # 早一步 CREATE INDEX ... ON runs(user_id) 就是 "no such column: user_id"，
    # 而这个函数每次请求都会跑，等于整个服务对老档案直接 500。
    c.executescript(DDL_INDEXES)
    return c


def _ensure_owner_columns(c: sqlite3.Connection) -> None:
    """老库（多用户之前建的）补上归属列；老记录 user_id 为空，视为单机时期数据。"""
    have = {r["name"] for r in c.execute("PRAGMA table_info(runs)")}
    if "user_id" not in have:
        c.execute("ALTER TABLE runs ADD COLUMN user_id INTEGER")
    if "public" not in have:
        c.execute("ALTER TABLE runs ADD COLUMN public INTEGER NOT NULL DEFAULT 0")
    agent_cols = {r["name"] for r in c.execute("PRAGMA table_info(agent_runs)")}
    if "user_id" not in agent_cols:
        c.execute("ALTER TABLE agent_runs ADD COLUMN user_id INTEGER")


def save_run(result: dict, label: str = "", archetype: str = "", user_id: int | None = None) -> str:
    """写入（或就地更新）一次体检结果，并把盲区/覆盖率展开成可查询列。"""
    meta = result.get("meta", {})
    center = result.get("center", {})
    # 降级是数据质量标记（单独成列），不该把同一个社区裂成两条 scene_key 档案
    mode = "mock" if meta.get("mock") else "real"
    run_id = scene_key((center.get("lat", 0), center.get("lng", 0)), mode, user_id)
    now = _now()
    with closing(_open()) as c, c:
        prev = c.execute("SELECT created_at, run_count FROM runs WHERE id=?", (run_id,)).fetchone()
        c.execute(
            "INSERT OR REPLACE INTO runs(id,created_at,updated_at,run_count,lat,lng,label,archetype,"
            "mode,score,elapsed,api_calls,cache_hits,quota_units,degraded,payload,user_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, prev["created_at"] if prev else now, now, (prev["run_count"] if prev else 0) + 1,
             center.get("lat"), center.get("lng"),
             label or f"{center.get('lat', 0):.4f},{center.get('lng', 0):.4f}", archetype, mode,
             result.get("health_score"), meta.get("elapsed_sec"), meta.get("api_calls"),
             meta.get("cache_hits"), meta.get("quota_units"), 1 if meta.get("degraded") else 0,
             json.dumps(_slim(result), ensure_ascii=False), user_id))
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


def save_agent_run(out: dict, question: str, center: tuple | None = None, user_id: int | None = None) -> str:
    """存一次智能体问答，并把工具调用轨迹落进 traces（决策可回放，不是黑箱）。"""
    aid = new_id()
    now = _now()
    llm_mode = out.get("mode") == "llm"
    with closing(_open()) as c, c:
        c.execute(
            "INSERT INTO agent_runs(id,created_at,question,model,mode,steps,api_calls,answer,center,"
            "user_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (aid, now, question[:300], settings.llm_model if llm_mode else "rule",
             "llm" if llm_mode else "rule", len(out.get("steps") or []),
             out.get("api_calls", 0), (out.get("reply") or "")[:ANSWER_KEEP],
             json.dumps(list(center) if center else []), user_id))
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


def list_runs(limit: int = 50, user_id: int | None = None, enforce: bool = False) -> list:
    """给了 user_id 就只回这个人的档案；没给（单机模式）回全部。"""
    where, args = "", [limit]
    cols = ("r.id,r.created_at,r.updated_at,r.run_count,r.lat,r.lng,r.label,r.archetype,"
            "r.mode,r.score,r.elapsed,r.api_calls,r.cache_hits,r.quota_units,r.degraded,r.public,"
            "(SELECT COUNT(*) FROM blindspots b WHERE b.run_id=r.id AND b.verified=1) AS verified_bs,"
            "(SELECT COUNT(*) FROM coverage v WHERE v.run_id=r.id AND v.inside=0) AS empty_cats")
    with closing(_open()) as c:
        if enforce:
            where = "WHERE (r.user_id IS ? OR r.public=1)" if user_id is not None else "WHERE r.public=1"
            args = ([user_id, limit] if user_id is not None else [limit])
        rows = c.execute(
            f"SELECT {cols} FROM runs r {where} ORDER BY r.updated_at DESC LIMIT ?", args).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str, user_id: int | None = None, enforce: bool = False) -> dict | None:
    """user_id 非空时做归属校验：不是自己的、且没勾"公开"的档案一律当作不存在（404 而非 403，
    免得把"这条记录存在"这件事泄漏出去）。"""
    with closing(_open()) as c:
        row = c.execute("SELECT payload, user_id, public FROM runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return None
    if enforce and (user_id is None or row["user_id"] != user_id) and not row["public"]:
        return None
    return json.loads(row["payload"])


def find_scene_run(center: tuple, user_id: int | None = None, enforce: bool = False) -> dict | None:
    """按坐标取已建档的体检结果，供选址等下游能力复用——不再重跑一遍烧配额。"""
    for mode in ("real", "real+降级", "mock"):   # 中间一项兼容修键之前留下的旧记录
        payload = get_run(scene_key(center, mode, user_id), user_id, enforce)
        if payload:
            return payload
    return None


def claim_legacy_runs(user_id: int) -> int:
    """把"没有账号时期"的本机旧档案认领给第一个注册的用户。

    单机启用账号只是为了门禁与个人中心，档案本来就在这台机器上；
    不认领的话第一次登录会发现历史全空了，像是被删掉一样。
    只对第一个账号生效（调用方负责判断），后来者仍各看各的。
    """
    with closing(_open()) as c, c:
        return c.execute("UPDATE runs SET user_id=? WHERE user_id IS NULL", (user_id,)).rowcount


def legacy_run_count() -> int:
    with closing(_open()) as c:
        return int(c.execute("SELECT COUNT(*) n FROM runs WHERE user_id IS NULL").fetchone()["n"])


def delete_run(run_id: str, user_id: int | None = None, enforce: bool = False) -> bool:
    """只能删自己的；公开与否都不允许删别人的档案。"""
    with closing(_open()) as c, c:
        if enforce:
            owner = c.execute("SELECT user_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if not owner or owner["user_id"] != user_id:
                return False
        n = c.execute("DELETE FROM runs WHERE id=?", (run_id,)).rowcount
        c.execute("DELETE FROM blindspots WHERE run_id=?", (run_id,))
        c.execute("DELETE FROM coverage WHERE run_id=?", (run_id,))
    return bool(n)


def compare(run_ids: list, user_id: int | None = None, enforce: bool = False) -> dict:
    """跨社区对比：只回派生指标与逐类别差值，前端一张表就能看出形态差异。"""
    with closing(_open()) as c:
        items = []
        for rid in run_ids:
            row = c.execute("SELECT id,label,archetype,mode,score,lat,lng,updated_at,user_id,public "
                            "FROM runs WHERE id=?", (rid,)).fetchone()
            if row and enforce and not row["public"] and (user_id is None or row["user_id"] != user_id):
                continue
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


def list_agent_runs(limit: int = 20, user_id: int | None = None, enforce: bool = False) -> list:
    sql = "SELECT id,created_at,question,model,mode,steps,api_calls,answer FROM agent_runs"
    args: list = []
    if enforce:
        sql += " WHERE user_id IS ?" if user_id is not None else " WHERE 0"
        if user_id is not None:
            args.append(user_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with closing(_open()) as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def agent_owner(agent_id: str) -> int | None:
    with closing(_open()) as c:
        row = c.execute("SELECT user_id FROM agent_runs WHERE id=?", (agent_id,)).fetchone()
    return None if not row else row["user_id"]


def set_public(run_id: str, flag: bool, user_id: int | None = None) -> bool:
    """勾选/取消"公开只读"；只有归属者能改。"""
    with closing(_open()) as c, c:
        n = c.execute("UPDATE runs SET public=? WHERE id=? AND (user_id IS ? OR ? IS NULL)",
                      (1 if flag else 0, run_id, user_id, user_id)).rowcount
    return bool(n)


def stats(user_id: int | None = None, enforce: bool = False) -> dict:
    """累计观测：把「缓存复用 + 历史回放省下的配额」变成屏幕上能指给评委看的数字。

    多用户下必须按人统计，否则任何人登录后都能看到全站的调用量与平均分。
    """
    own = ("WHERE user_id IS ?" if user_id is not None else "WHERE 0") if enforce else ""
    own_args: list = [user_id] if user_id is not None else []
    run_ids = "SELECT id FROM runs " + own
    j = jv = ""
    with closing(_open()) as c:
        if enforce and user_id is not None:      # enforce 且匿名时不过滤：查询自带 WHERE 0
            c.execute("CREATE TEMP TABLE _own AS " + run_ids, own_args)
            j = "JOIN _own o ON o.id = b.run_id"
            jv = "JOIN _own o ON o.id = v.run_id"
        sel = ("SELECT COUNT(*) n, AVG(score) avg_s, MIN(score) min_s, MAX(score) max_s, "
               "SUM(api_calls) calls, SUM(cache_hits) hits, SUM(elapsed) secs, "
               "SUM(degraded) degraded_runs, SUM(quota_units) quota, SUM(run_count) total_runs FROM runs {}")
        r = c.execute(sel.format(own), own_args).fetchone()
        bs = c.execute("SELECT COUNT(*) n, SUM(b.verified) v FROM blindspots b " + j).fetchone()
        cats = c.execute("SELECT v.name, SUM(v.inside=0) missing, COUNT(*) total FROM coverage v "
                         + jv + " GROUP BY v.name ORDER BY missing DESC").fetchall()
        a_where = ("WHERE a.user_id IS ?" if user_id is not None else "WHERE 0") if enforce else ""
        agent = c.execute("SELECT COUNT(*) n, SUM(mode='llm') llm FROM agent_runs a " + a_where,
                          own_args).fetchone()
        tr = c.execute("SELECT COUNT(*) n, SUM(t.latency_ms) ms FROM traces t "
                       "JOIN agent_runs a ON a.id = t.agent_id " + a_where, own_args).fetchone()
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
    r["saved_at"] = _now()
    return r


def fingerprint(payload: dict) -> str:
    """证据指纹：同一组参数与输出得到同一 ID，用于报告里标注「这份结论来自哪一次实测」。"""
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]
