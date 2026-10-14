#!/usr/bin/env python
"""批量 vs 逐条的出网策略基准：零真实出网，产出 docs/bench_route_strategy.json。

要回答评委一定会问的那句"你省的是什么"：批量距离矩阵省的是 **HTTP 往返**，不是配额单位
（配额按路线对数计，两种打法完全相同）。附带一个预算实验：给定固定请求预算时，
逐条打法会先撞预算上限而漏掉候选，批量打法一次请求就覆盖全部。

计数方式：给客户端的两个业务方法套计数器（mock 分支不计真实出网，故不能直接读 client.calls）；
耗时按步行服务 3 QPS 令牌桶**建模**给出下限，mock 实测耗时仅作参考。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/bench_route_strategy.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import os

os.environ.setdefault("BMAP_MOCK", "1")
os.environ["BMAP_CACHE_DIR"] = "data/cache_bench"   # 隔离目录，避免污染演示缓存

from app.baidu_client import client
from app.config import settings

PAIRS = 24           # 8 个盲区候选 × 每类最近 3 个设施，与 BLINDSPOT_* 默认口径一致
REQUEST_BUDGET = 20  # 一个"今天只剩 20 次请求"的假设预算


def instrument():
    """套计数器：mock 分支不计真实出网，基准只能数"本来要发几次请求"。"""
    n = {"walking": 0, "matrix": 0, "pairs": 0}
    orig_wr, orig_dm = client.walking_route, client.distance_matrix

    async def walking_route(o, d):
        n["walking"] += 1
        return await orig_wr(o, d)

    async def distance_matrix(origins, dests):
        n["matrix"] += 1
        n["pairs"] += len(origins) * len(dests)
        return await orig_dm(origins, dests)

    client.walking_route, client.distance_matrix = walking_route, distance_matrix
    return n


async def main():
    origins = [(39.908 + i * 0.001, 116.397 + i * 0.001) for i in range(8)]
    dests = [(39.912 + j * 0.001, 116.401 + j * 0.001) for j in range(3)]
    qps = settings.qps

    n = instrument()

    t0 = time.monotonic()
    for o in origins:                      # 逐条：每对一次请求
        for d in dests:
            await client.walking_route(o, d)
    serial_sec = round(time.monotonic() - t0, 3)
    serial_req, serial_quota = n["walking"], n["walking"]

    n.update(walking=0, matrix=0, pairs=0)
    t0 = time.monotonic()
    rows = await client.distance_matrix(origins, dests)   # 批量：一次请求承载全部路线对
    batch_sec = round(time.monotonic() - t0, 3)
    batch_req, batch_quota = n["matrix"], len(rows) * len(rows[0])

    covered_serial = min(PAIRS, REQUEST_BUDGET)
    covered_batch = PAIRS if batch_req <= REQUEST_BUDGET else 0

    doc = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "mode": "mock 计数 + 3 QPS 建模耗时（不消耗真实配额；请求数与配额单位在真实环境下同构）",
        "pairs": PAIRS, "origins": len(origins), "destinations": len(dests),
        "qps_model": qps,
        "serial": {
            "http_requests": serial_req, "quota_units": serial_quota,
            "mock_wall_sec": serial_sec,
            "modeled_wall_sec_at_qps": round(serial_req / qps, 2),
        },
        "batch": {
            "http_requests": batch_req, "quota_units": batch_quota,
            "mock_wall_sec": batch_sec,
            "modeled_wall_sec_at_qps": round(batch_req / qps, 2),
        },
        "quota_units_identical": serial_quota == batch_quota,
        "round_trip_saving": round(serial_req / max(batch_req, 1), 1),
        "budget_experiment": {
            "request_budget": REQUEST_BUDGET,
            "serial_pairs_covered": covered_serial,
            "batch_pairs_covered": covered_batch,
            "serial_coverage": round(covered_serial / PAIRS, 3),
            "batch_coverage": round(covered_batch / PAIRS, 3),
            "conclusion": "批量省的是往返不是配额：配额单位两种打法完全相同，"
                          "但在固定请求预算下批量能覆盖全部候选，逐条会先撞预算",
        },
        "notes": [
            "distancematrix 的 quota 按 origins×destinations 计，与 HTTP 次数分栏上报，永不合并",
            "真实环境下串行还受 3 QPS 令牌桶约束：24 对逐条至少 8 秒，批量 1 次请求约 0.33 秒",
        ],
    }
    out = ROOT / "docs" / "bench_route_strategy.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main())
