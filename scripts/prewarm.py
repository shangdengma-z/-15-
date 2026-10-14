#!/usr/bin/env python
"""演示预热：把预设社区逐个用真实路网跑一遍，缓存 + 历史库一起焐热。

录视频时点哪个社区都是 0.5 秒返回、0 次出网；这一步同时把 quota_units / evidence_id
等计量字段生成成真实值，避免演示里出现「指标为 0」的空壳感。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/prewarm.py            # 跑全部预设
    .venv/Scripts/python.exe scripts/prewarm.py huilongguan jiadaokou
"""

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db
from app.analysis import run_analysis
from app.config import PRESET_COMMUNITIES, settings


async def main(ids: list):
    if settings.mock:
        print("！当前 BMAP_MOCK=1，预热只会生成合成数据；录真实演示请把 .env 改成 BMAP_MOCK=0")
    targets = [p for p in PRESET_COMMUNITIES if not ids or p["id"] in ids]
    for p in targets:
        t0 = time.monotonic()
        try:
            res = await run_analysis((p["lat"], p["lng"]))
            run_id = db.save_run(res, p["name"], p["archetype"])
            m = res["meta"]
            bs = res["blindspots"]
            verified = len([b for b in bs if b.get("verified")])
            print(f"[{p['name']}] 分数 {res['health_score']} · 盲区 {len(bs)}（已核验 {verified}）"
                  f" · 出网 {m['api_calls']} 次 / 配额 {m['quota_units']} 单位"
                  f" · 缓存命中 {m['cache_hits']} · 耗时 {time.monotonic() - t0:.1f}s"
                  f" · {m['data_mode']} · {run_id}")
            # 顺手把派生结果导成 JSON，作为可离线复现的样张（不含百度原始响应）
            out = ROOT / "data" / "sample" / f"{p['id']}-report.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            slim = {k: v for k, v in res.items() if k not in ("heat", "series")}
            out.write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 - 预热是批处理，单点失败要继续跑完
            print(f"[{p['name']}] 失败：{e}")
            if "quota" in str(e).lower():
                print("！步行配额已耗尽，剩余社区改日再热")
                break


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
