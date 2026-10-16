"""新增模块离线自测：导则达标对照 + 选址建议，全部在 mock 地图上跑，不需要任何密钥。

覆盖：直线距离口径没写反、四种状态都能出现、需求代理的名称过滤与楼号合并、
候选点位间距与 top 限制、每个候选各 1 次步行实测的出网上限、以及口径说明必须随结果返回。
    python backend/tests/test_modules_offline.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，报告里的 km² 之类字符会直接让 print 崩；统一按 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["BMAP_MOCK"] = "1"
os.environ["BMAP_QPS"] = "1000"
os.environ["BMAP_CONCURRENCY"] = "8"
os.environ["BMAP_CACHE_DIR"] = "data/cache_selftest"
os.environ["BMAP_DB_PATH"] = "data/db_selftest/modules.db"
os.environ["LLM_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.advisor import advise, filter_residential
from app.analysis import cluster_blindspots
from app.baidu_client import client, straight_line_m
from app.config import PRESET_COMMUNITIES
from app.isochrone import compute_isochrones
from app.poi import fetch_categories
from app.standards import GUIDE_ITEMS, compliance_check

RING = [[116.30, 40.07], [116.345, 40.07], [116.345, 40.105], [116.30, 40.105]]
CENTER = (40.0875, 116.3225)
POIS = {
    "medical": [{"name": "康泰药店", "lat": 40.0898, "lng": 116.3248, "address": "A"},
                {"name": "仁和药房", "lat": 40.0830, "lng": 116.3180, "address": "B"}],
    "education": [{"name": "第一小学", "lat": 40.0900, "lng": 116.3200, "address": "C"},
                  {"name": "蓓蕾幼儿园", "lat": 40.0889, "lng": 116.3231, "address": "D"}],
    "shopping": [{"name": "社区菜市场", "lat": 40.0882, "lng": 116.3219, "address": "E"},
                 {"name": "惠民超市", "lat": 40.0869, "lng": 116.3206, "address": "F"}],
    "elderly": [],
}

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


async def main():
    # 1) 距离口径：把中心点正东 0.0045°（约 380m）的点拿来校核，写反会得到 ~500m 以上的离谱值
    east = (CENTER[1] + 0.0045, CENTER[0])
    d = straight_line_m((east[1], east[0]), (CENTER[0], CENTER[1]))
    check("直线距离按 (lat,lng) 口径", 300 < d < 420, f"{d:.0f}m")

    # 2) 导则达标：四类状态都要能出现，且逐项带回参考半径与出处
    std = compliance_check(CENTER, POIS, RING)
    statuses = {i["status"] for i in std["items"]}
    check("导则项目数与配置一致", len(std["items"]) == len(GUIDE_ITEMS), f"{len(std['items'])} 项")
    check("状态取自四种取值", statuses <= {"达标", "临界", "缺口", "无点位"}, str(sorted(statuses)))
    check("无点位能被识别", "无点位" in statuses and any(i["nearest_m"] is None for i in std["items"]))
    check("达标项最近距离不超过参考半径",
          all(i["nearest_m"] <= i["ref_m"] for i in std["items"] if i["status"] == "达标"))
    check("每项都带口径出处", all(i["basis"] and i["ref_m"] > 0 for i in std["items"]))
    check("passed 计数自洽", std["passed"] == sum(1 for i in std["items"] if i["status"] == "达标"))
    check("方法学随结果返回", "直线" in std["method"])

    # 3) 需求代理过滤：楼号合并、非住宅剔除
    raw = [{"name": f"龙腾苑{i}号楼", "location": {"lat": 40.088, "lng": 116.322}, "uid": f"a{i}"}
           for i in range(1, 6)]
    raw += [{"name": "回龙观小区", "location": {"lat": 40.087, "lng": 116.321}, "uid": "b"},
            {"name": "龙域中心大厦", "location": {"lat": 40.086, "lng": 116.320}, "uid": "c"},
            {"name": "昌平公园", "location": {"lat": 40.085, "lng": 116.319}, "uid": "d"},
            {"name": "华联超市", "location": {"lat": 40.084, "lng": 116.318}, "uid": "e"}]
    names = [p["name"] for p in filter_residential(raw)]
    check("同小区多楼号合并", names.count("龙腾苑") == 1 and len(raw) == 9, f"得到 {len(names)} 个")
    check("非住宅被剔除", all(x not in " ".join(names) for x in ("大厦", "公园", "超市")), str(names))
    check("小区本体保留", "回龙观小区" in names, str(names))
    check("楼号后缀已剥离", all(not n.endswith(("号楼", "幢", "栋")) for n in names))

    # 4) 盲区连片聚合：3×2 的同质格子 + 一个孤立格子应并成 2 个片区
    block, step_lat, step_lng = [], 0.0027, 0.0035
    for i in range(3):
        for j in range(2):
            block.append({"lat": 40.070 + i * step_lat, "lng": 116.320 + j * step_lng,
                          "missing": ["菜市场"], "verified": True, "undetermined": []})
    block.append({"lat": 40.090, "lng": 116.360, "missing_preview": ["养老"], "verified": False,
                  "undetermined": ["教育"]})
    cl = cluster_blindspots(block)
    big = max(cl, key=lambda c: c["cells"])
    check("连片网格并成片区", len(cl) == 2, f"{len(cl)} 个片区")
    check("片区格数正确", big["cells"] == 6, f"最大片区 {big['cells']} 格")
    check("片区面积按格累计", abs(big["area_km2"] - 0.54) < 0.01, f"{big['area_km2']} km²")
    check("缺什么只写一次", big["missing"] == ["菜市场"], str(big["missing"]))
    check("核验与待核验分开计数", big["verified_cells"] == 6 and big["suspect_cells"] == 0)
    lone = [c for c in cl if c["cells"] == 1][0]
    check("孤立格子自成一区", lone["verified_cells"] == 0 and lone["suspect_cells"] == 1)
    check("无法判定项随片区汇总", lone["undetermined"] == ["教育"], str(lone["undetermined"]))
    check("片区质心在格子范围里", 40.070 <= big["lat"] <= 40.076 and 116.320 <= big["lng"] <= 116.324,
          f'{big["lat"]}, {big["lng"]}')

    # 5) 真实 mock 地图上的选址建议
    preset = PRESET_COMMUNITIES[0]
    iso = await compute_isochrones((preset["lat"], preset["lng"]))
    pois = await fetch_categories((preset["lat"], preset["lng"]))
    payload = {"center": {"lat": preset["lat"], "lng": preset["lng"]}, "rings": iso["rings"],
               "pois": pois, "blindspots": [{"lat": preset["lat"] + 0.004, "lng": preset["lng"] + 0.004,
                                             "missing": ["养老"], "verified": True}],
               "meta": {"mock": True}}
    calls0, quota0 = client.calls, client.quota_units
    out = await advise(payload, "elderly", top=3)
    used_calls, used_quota = client.calls - calls0, client.quota_units - quota0
    cand = out["candidates"]
    check("候选不超过 top", len(cand) <= 3, f"{len(cand)} 个")
    check("每个候选都在 15 分钟圈内", all(straight_line_m((c["lat"], c["lng"]),
          (preset["lat"], preset["lng"])) < 2000 for c in cand))
    check("候选彼此至少间隔 400m", all(
        straight_line_m((a["lat"], a["lng"]), (b["lat"], b["lng"])) >= 399
        for i, a in enumerate(cand) for b in cand[i + 1:]))
    check("分数按需求与间距递减", all(cand[i]["score"] >= cand[i + 1]["score"] for i in range(len(cand) - 1)))
    check("候选带回步行实测", all("walk_sec_to_center" in c and "walk_m_to_center" in c for c in cand),
          f"实测出网 {used_calls} 次")
    check("补盲信号可用", all(isinstance(c["fills_verified_blindspot"], bool) for c in cand))
    check("出网量等于候选数（需求池已缓存后）", used_calls <= 4, f"{used_calls} 次 / {used_quota} 单位")
    check("口径说明随结果返回", all(out["basis"][k] for k in ("demand_proxy", "spacing_m", "weights", "verify")))
    check("人气代理措辞克制", "代理" in out["basis"]["demand_proxy"])

    # 6) 未知业态必须被拦在外面
    try:
        await advise(payload, "hotpot", top=3)
        check("未知业态被拒绝", False)
    except KeyError:
        check("未知业态被拒绝", True)

    print("\n== 新模块自测：" + ("全部通过 ==" if ok else "存在失败 =="))
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
