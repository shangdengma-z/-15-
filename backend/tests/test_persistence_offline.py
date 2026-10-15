"""持久化 / 情景重算 / 三态判定离线自测：mock 地图 + 临时 SQLite，无需任何真实密钥或配额。

覆盖：scene_key 就地更新、历史回放零出网、跨社区对比差值、累计统计、
人群步速换算方向（老人可达面积必须小于成人）、顶点来源标签、证据指纹、
检索查全判据与 Markdown 报告结构，以及"多用户之前的老库能否自动补列打开"。
CI 与本地均可直接运行：
    python backend/tests/test_persistence_offline.py
"""

import asyncio
import os
import shutil
import sqlite3
import sys
from pathlib import Path

os.environ["BMAP_MOCK"] = "1"
os.environ["BMAP_QPS"] = "1000"
os.environ["BMAP_CONCURRENCY"] = "8"
os.environ["BMAP_CACHE_DIR"] = "data/cache_selftest"
os.environ["BMAP_DB_PATH"] = "data/db_selftest/lifestyle.db"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.analysis import retrieval_completeness, run_analysis
from app.config import settings
from app.report import to_markdown

CACHE = Path(settings.cache_dir)
DBDIR = Path(settings.db_path).parent


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    return bool(cond)


async def main():
    ok = True
    if DBDIR.exists():
        shutil.rmtree(DBDIR)

    res = await run_analysis((40.0703, 116.3306))

    # 1) 溯源字段齐备
    ok &= check("data_mode 标注为 mock", res["meta"]["data_mode"] == "mock")
    ok &= check("evidence_id 为 16 位十六进制", len(res["meta"]["evidence_id"]) == 16)
    ok &= check("采样参数随结果下发", res["meta"]["sampling"]["rays"] == settings.rays)

    # 2) 顶点标签：每环顶点数 == 射线数，且三类计数之和相等
    sc = res["scenarios"]
    kinds = sc["by_minutes"]["15min"]["vertex_kind"]
    ok &= check("顶点标签计数守恒", sum(kinds.values()) == settings.rays, kinds)
    ok &= check("15min 情景面积与主结果一致",
                abs(sc["by_minutes"]["15min"]["area_km2"] - res["area_km2"]) < 0.02)

    # 3) 人群换算方向：步速越慢可达面积越小（曾写反成老人更大）
    areas = {k: v["area_km2"] for k, v in sc["by_crowd"].items()}
    ok &= check("老人可达面积 < 成人", areas["elderly"] < areas["adult"], areas)
    ok &= check("儿童介于老人与成人之间",
                areas["elderly"] <= areas["child"] <= areas["adult"], areas)
    ok &= check("情景重算零出网", sc["api_calls_for_scenarios"] == 0)

    # 4) 检索查全判据：mock 全部探针应落在 complete/empty
    comp = retrieval_completeness()
    ok &= check("四类检索均判定为查全", all(v["complete"] for v in comp.values()), comp)

    # 5) 持久化：同址重跑就地更新而不是新增行
    rid = db.save_run(res, "测试社区", "测试形态")
    res2 = await run_analysis((40.0703, 116.3306))
    rid2 = db.save_run(res2, "测试社区", "测试形态")
    ok &= check("同址重跑命中同一 scene_key", rid == rid2, rid2)
    runs = db.list_runs()
    ok &= check("历史只有一行且 run_count=2", len(runs) == 1 and runs[0]["run_count"] == 2,
                [(r["id"], r["run_count"]) for r in runs])

    # 6) 回放与对比
    back = db.get_run(rid)
    ok &= check("回放结果字段完整", back["health_score"] == res["health_score"]
                and "rings" in back and "pois" in back)
    db.save_run(res2 | {"center": {"lat": 39.9403, "lng": 116.4038}}, "对照社区", "对照")
    cmp_ = db.compare([rid, db.scene_key((39.9403, 116.4038), "mock")])
    ok &= check("对比返回两份与逐类别差值", len(cmp_["runs"]) == 2 and len(cmp_["diff"]) == 4)

    # 7) 统计与报告
    st = db.stats()
    ok &= check("统计含社区数与配额单位", st["communities"] == 2 and "quota_units" in st, st["communities"])
    md = to_markdown(back)
    ok &= check("报告含计量/盲区/情景三节", all(k in md for k in ("计量自证", "服务盲区", "情景与人群对比")))
    ok &= check("报告标注数据来源", "数据来源：**mock**" in md)

    # 8) 删除
    ok &= check("删除后历史为空", db.delete_run(rid) and not db.get_run(rid))

    # 9) 老库自动迁移：多用户之前建的 SQLite 里 runs 表没有 user_id/public，
    #    而 _open() 每次请求都会执行 DDL。曾经索引建在补列之前，
    #    "no such column: user_id" 直接把 /api/health 打成 500——这条就是钉住那个顺序
    legacy = DBDIR / "legacy.db"
    DBDIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(legacy) as lc:
        lc.execute("CREATE TABLE runs(id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, "
                   "run_count INTEGER, lat REAL, lng REAL, label TEXT, archetype TEXT, mode TEXT, "
                   "score INTEGER, elapsed REAL, api_calls INTEGER, cache_hits INTEGER, "
                   "quota_units INTEGER, degraded INTEGER, payload TEXT)")
        lc.execute("CREATE TABLE agent_runs(id TEXT PRIMARY KEY, created_at TEXT, question TEXT, "
                   "model TEXT, mode TEXT, steps INTEGER, api_calls INTEGER, answer TEXT, center TEXT)")
        lc.execute("INSERT INTO runs(id,run_count,label,mode,score,payload) "
                   "VALUES('old-1',1,'老社区','mock',88,'{\"label\": \"老社区\"}')")
    saved_path, settings.db_path = settings.db_path, str(legacy)
    try:
        st = db.stats()
        ok &= check("老库无需手工迁移就能打开（曾 500）", st["communities"] == 1, str(st["communities"]))
        ok &= check("统计与平均分仍读得到", st["avg_score"] == 88, str(st["avg_score"]))
        with db._open() as lc:
            cols = {r["name"] for r in lc.execute("PRAGMA table_info(runs)")}
            idx = {r["name"] for r in lc.execute("PRAGMA index_list(runs)") if r["name"]}
            old = lc.execute("SELECT user_id, public FROM runs WHERE id='old-1'").fetchone()
        ok &= check("归属列已补齐", {"user_id", "public"} <= cols, sorted(cols - {"payload"}))
        ok &= check("user_id 索引确实建上了（补列在建索引之前）", "idx_runs_user" in idx, sorted(idx))
        ok &= check("老记录归属为空，视为单机时期数据", old["user_id"] is None and old["public"] == 0)
        ok &= check("老档案仍可按 id 回放", db.get_run("old-1")["label"] == "老社区")
    finally:
        settings.db_path = saved_path

    print("\n== 结果：", "全部通过 ==" if ok else "存在失败 ==")
    return ok


if __name__ == "__main__":
    try:
        raise SystemExit(0 if asyncio.run(main()) else 1)
    finally:
        shutil.rmtree(DBDIR, ignore_errors=True)
