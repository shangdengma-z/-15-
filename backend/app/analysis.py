"""体检分析编排：等时圈 + POI 覆盖统计 + 服务盲区识别 + 情景对比 + 受阻代理 + 计量自证。

设计纪律（写进技术设计文档的三条硬约束）
1. 一次出网，多次复用：原始「方位角-半径-耗时/距离」序列留在结果里，
   5/10/15/20 分钟情景与人群步速换算都只是重新插值，零额外配额。
2. "没查到"不等于"没有"：盲区是全称否定，只有该类别检索查全（stop=complete/empty）才允许判定，
   否则单列为 undetermined 并从评分分母里剔除。
3. 三栏计量永不合并：HTTP 请求数、配额单位数（矩阵按路线对数折算）、检索翻页数各自成列。
"""

import asyncio
import math
import statistics
import time

from .baidu_client import client, straight_line_m
from .config import CATEGORIES, REQUIRED_CATEGORIES, settings
from .db import fingerprint
from .isochrone import (
    compute_isochrones,
    point_in_polygon,
    polygon_area_km2,
    ring_from_series,
)
from .poi import fetch_categories
from .standards import compliance_check

GRID_STEP_M = 300       # 盲区初筛网格步长
GAP_WEIGHT = 45         # 盲区面积占比满分扣量：完全覆盖时 −45，占比按格数×0.09km² / 圈内面积
REFERENCE_SPEED = 1.25  # 百度步行路径规划的隐含步速（m/s），人群换算以此为基准


def gap_area_share(verified_cells: int, area_km2: float) -> float:
    """已核验盲区格覆盖的面积占 15 分钟圈面积的比例（封顶 1）。

    一格代表 300m×300m 邻域，是网格尺度的近似而非边界精确面积；措辞与 `blindspot_clusters` 一致。
    """
    if area_km2 <= 0:
        return 0.0
    return min(1.0, round(verified_cells * (GRID_STEP_M / 1000.0) ** 2 / area_km2, 4))
CROWD_SPEEDS = {"adult": 1.25, "elderly": 0.8, "child": 0.9}
SCENARIO_MINUTES = (5, 10, 15, 20)


def grid_in_ring(ring: list) -> list:
    """在 15 分钟圈外接盒上铺网格，保留圈内点。ring 为 [lng,lat][]。"""
    lngs = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    lat_step = GRID_STEP_M / 111320.0
    mid_lat = (min(lats) + max(lats)) / 2
    lng_step = GRID_STEP_M / (111320.0 * math.cos(math.radians(mid_lat)))
    pts = []
    lat = min(lats)
    while lat <= max(lats):
        lng = min(lngs)
        while lng <= max(lngs):
            if point_in_polygon((lng, lat), ring):
                pts.append((lng, lat))
            lng += lng_step
        lat += lat_step
    return pts


def _min_straight_m(pt: tuple, pois: list) -> float:
    """网格点 (lng,lat) 到设施池的直线最近距离；straight_line_m 约定是 (lat,lng)，此处显式换算。"""
    return min((straight_line_m((pt[1], pt[0]), (p["lat"], p["lng"])) for p in pois), default=1e9)


def required_pois(pois: dict) -> dict:
    """从大类检索结果中按名称关键词切出赛题必核单品（菜市场/药店/小学），不额外消耗配额。"""
    return {key: [p for p in pois.get(spec["source"], []) if any(k in p["name"] for k in spec["keywords"])]
            for key, spec in REQUIRED_CATEGORIES.items()}


def retrieval_completeness() -> dict:
    """按类别汇总检索停止原因：page_cap/dup_stop/api_error 意味着"这一圈没查全"。"""
    by_query = {}
    for p in client.place_probes:
        by_query.setdefault(p["query"], []).append(p["stop"])
    out = {}
    for key, spec in CATEGORIES.items():
        stops = [s for q in spec["queries"] for s in by_query.get(q, [])]
        out[key] = {"probes": len(stops), "complete": bool(stops) and all(
            s in ("complete", "empty") for s in stops)}
    return out


def _missing_near(pt: tuple, pois_by_cat: dict, cat_keys: list, limit_m: int) -> list:
    return [key for key in cat_keys if _min_straight_m(pt, pois_by_cat.get(key, [])) > limit_m]


async def identify_blindspots(ring15: list, pois: dict) -> list:
    """1) 网格初筛（四大类 + 赛题必核单品）；2) 用批量步行距离矩阵核验，剔除直线误报。"""
    grid = grid_in_ring(ring15)
    req = required_pois(pois)
    complete = retrieval_completeness()
    cat_keys = list(CATEGORIES.keys())
    candidates = []
    for pt in grid:
        missing = [k for k in _missing_near(pt, pois, cat_keys, 1000) if complete.get(k, {}).get("complete")]
        undetermined = [k for k in _missing_near(pt, pois, cat_keys, 1000)
                        if not complete.get(k, {}).get("complete")]
        missing_req = [k for k, spec in REQUIRED_CATEGORIES.items()
                       if _min_straight_m(pt, req[k]) > spec["limit"]]
        if missing or missing_req or undetermined:
            preview = ([CATEGORIES[m]["name"] for m in missing]
                       + [REQUIRED_CATEGORIES[m]["name"] for m in missing_req])
            candidates.append({"lng": round(pt[0], 6), "lat": round(pt[1], 6),
                               "missing_preview": preview,
                               "undetermined": [CATEGORIES[m]["name"] for m in undetermined],
                               "_missing_keys": missing, "_missing_req": missing_req})
    candidates.sort(key=lambda c: len(c["_missing_keys"]) + len(c["_missing_req"]), reverse=True)
    # 核验上限与每类比对半径数走配置：这两个数决定冷跑时长，也决定「疑似」格子的多少
    verify = candidates[:settings.max_candidates]

    # 核验：候选点到该类最近 N 个 POI 的真实步行距离必须也超限才判为盲区
    checked = []
    for cand in verify:
        specs = [(CATEGORIES[k]["name"], 1000, pois.get(k, [])) for k in cand["_missing_keys"]]
        specs += [(REQUIRED_CATEGORIES[k]["name"], REQUIRED_CATEGORIES[k]["limit"], req[k])
                  for k in cand["_missing_req"]]
        tasks = []
        for name, limit, pool in specs:
            nearest = sorted(pool, key=lambda p: straight_line_m((cand["lat"], cand["lng"]),
                                                                 (p["lat"], p["lng"])))[:settings.verify_nearest]
            if nearest:
                tasks.append(((name, limit),
                              client.distance_matrix([(cand["lat"], cand["lng"])],
                                                     [(p["lat"], p["lng"]) for p in nearest])))
        results = await asyncio.gather(*(t[1] for t in tasks)) if tasks else []
        confirmed = []
        for (meta, _), rows in zip(tasks, results):
            name, limit = meta
            if min(rows[0]) > limit:
                confirmed.append(name)
        if confirmed:
            checked.append(_public(cand) | {"missing": confirmed, "verified": True})
    # 超出核验上限的候选只作"疑似"输出
    for cand in candidates[settings.max_candidates:]:
        checked.append(_public(cand) | {"verified": False})
    return checked


def _public(cand: dict) -> dict:
    return {k: v for k, v in cand.items() if not k.startswith("_")}


CLUSTER_LINK_M = 450   # 300m 网格的邻格间距（含对角约 424m），用于把连片网格并成一个片区


def cluster_blindspots(blindspots: list) -> list:
    """把连片的盲区网格并成「缺口片区」。

    逐格清单是给地图表达形状用的；给人读的时候，一片 300m×300m 连续 11 格都缺菜市场
    应该是一行「一个约 1 km² 的连片缺口」，而不是 11 行重复文字。
    """
    pending = list(blindspots)
    clusters = []
    while pending:
        seed = pending.pop(0)
        group, frontier = [seed], [seed]
        while frontier:
            c = frontier.pop()
            for n in pending[:]:
                if straight_line_m((c["lat"], c["lng"]), (n["lat"], n["lng"])) <= CLUSTER_LINK_M:
                    pending.remove(n)
                    group.append(n)
                    frontier.append(n)
        tally: dict = {}
        verified_names: set = set()
        for g in group:
            for name in (g.get("missing") or []):
                tally[name] = tally.get(name, 0) + 1
                verified_names.add(name)
            for name in (g.get("missing_preview") or []):
                tally[name] = tally.get(name, 0) + 1
        ver = len([g for g in group if g.get("verified")])
        und = sorted({u for g in group for u in (g.get("undetermined") or [])})
        clusters.append({
            "cells": len(group),
            "area_km2": round(len(group) * (GRID_STEP_M / 1000.0) ** 2, 2),
            "lat": round(sum(g["lat"] for g in group) / len(group), 5),
            "lng": round(sum(g["lng"] for g in group) / len(group), 5),
            "bbox": [round(min(g["lat"] for g in group), 5), round(min(g["lng"] for g in group), 5),
                     round(max(g["lat"] for g in group), 5), round(max(g["lng"] for g in group), 5)],
            "missing": [n for n, _ in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))],
            "missing_cells": tally,
            "verified_names": sorted(verified_names),
            "verified_cells": ver,
            "suspect_cells": len(group) - ver,
            "undetermined": und,
            "level": "连片已核验" if ver == len(group) else ("已核验" if ver else "待核验"),
        })
    clusters.sort(key=lambda c: (-c["cells"], -c["verified_cells"]))
    return clusters


def coverage_stats(pois: dict, ring15: list) -> dict:
    """统计各类设施在 15 分钟圈内的数量 + 圈内设施密度评估。"""
    stats = {}
    complete = retrieval_completeness()
    for key, spec in CATEGORIES.items():
        inside = [p for p in pois.get(key, []) if point_in_polygon((p["lng"], p["lat"]), ring15)]
        stats[key] = {"name": spec["name"], "total": len(pois.get(key, [])), "inside": len(inside),
                      "evidence_complete": complete.get(key, {}).get("complete", False)}
    return stats


def detour_residual(center: tuple, heat: list) -> dict:
    """路网受阻代理：用本次实测自标定绕行系数，再量出「比路网距离该有的耗时还慢」的点。

    绕行系数不预设 1.3，而取本次实测分布中位数；隐含步速 = 路线米数 / 实测秒数。
    这只是相关代理量，不能宣称因果，也不乘进健康分——措辞纪律写死在这里。
    """
    factors, speeds, slow = [], [], 0
    for h in heat:
        s = straight_line_m(center, (h["lat"], h["lng"]))
        if s < 120 or h.get("d", 0) <= 0:      # 近处噪声与降级点都不参与标定
            continue
        factors.append(h["d"] / s)
        v = h["d"] / h["t"] if h["t"] else 0
        speeds.append(v)
        if v and v < CROWD_SPEEDS["elderly"]:
            slow += 1
    if not factors:
        return {"samples": 0, "detour_factor_p50": None, "detour_factor_p90": None,
                "implied_speed_mps": None, "slow_samples": 0, "note": "无有效实测样本，不发布数值（发 None 不发 0）"}
    return {
        "samples": len(factors),
        "detour_factor_p50": round(statistics.median(factors), 3),
        "detour_factor_p90": round(sorted(factors)[int(len(factors) * 0.9) - 1], 3),
        "implied_speed_mps": round(statistics.median(speeds), 3),
        "slow_samples": slow,
        "slow_ratio": round(slow / len(factors), 3),
        "note": "受阻代理：隐含步速低于 0.8 m/s 的采样点，可能来自红绿灯/天桥/地下通道/围挡绕行，未做因果归因",
    }


def build_scenarios(center: tuple, series: list, pois: dict) -> dict:
    """时间阈值轴与人群步速轴上的重算：同一批实测耗时，换一次插值，零新增出网。

    人群换算的方向：百度实测秒数按约 1.25 m/s 给出，老人走同一段路要多花 1.25/0.8 倍时间，
    所以「老人 15 分钟可达」等价于「实测秒数 ≤ 900 × 0.8 / 1.25」——阈值收窄而不是放大。
    """
    by_minutes, by_crowd = {}, {}
    for m in SCENARIO_MINUTES:
        # 与主图三层圈用同一套平滑口径，否则同一情景在两处算出不同面积，会被当成口径错误
        by_minutes[f"{m}min"] = _scenario(ring_from_series(series, center, m * 60, smooth=m == 15),
                                          pois, m * 60, REFERENCE_SPEED, minutes=m)
    for crowd, speed in CROWD_SPEEDS.items():
        target = round(900 * speed / REFERENCE_SPEED)
        by_crowd[crowd] = _scenario(ring_from_series(series, center, target, smooth=True),
                                    pois, target, speed, minutes=15)
    return {"by_minutes": by_minutes, "by_crowd": by_crowd,
            "reference_speed_mps": REFERENCE_SPEED,
            "crowd_speeds_mps": CROWD_SPEEDS,
            "api_calls_for_scenarios": 0}


def _scenario(ring: dict, pois: dict, target_sec: int, speed_mps: float, minutes: float = 0) -> dict:
    poly = ring["ring"]
    kinds = ring["vertex_kind"]
    per = {}
    total = 0
    for key, spec in CATEGORIES.items():
        n = sum(1 for p in pois.get(key, []) if point_in_polygon((p["lng"], p["lat"]), poly))
        per[key] = {"name": spec["name"], "inside": n}
        total += n
    return {
        "target_sec": target_sec,
        "minutes": minutes or round(target_sec / 60, 1),
        "speed_mps": speed_mps,
        "area_km2": polygon_area_km2(poly),
        "ring": poly,
        "facilities_inside": total,
        "per_category": per,
        "vertex_kind": {"measured": kinds.count("measured"),
                        "interpolated": kinds.count("interpolated"),
                        "extrapolated": kinds.count("extrapolated")},
        "confidence": round((kinds.count("measured") + kinds.count("interpolated")) / len(kinds), 3),
    }


def metric_block(score: int, coverage: dict, blindspots: list, area_km2: float) -> list:
    """每个指标自带公式与口径说明：可解释性不该靠演示时口头补。"""
    empty_cats = sum(1 for v in coverage.values() if v["inside"] == 0)
    verified = len([b for b in blindspots if b.get("verified")])
    undet = len([b for b in blindspots if b.get("undetermined")])
    share = gap_area_share(verified, area_km2)
    return [
        {"key": "health_score", "label": "生活圈健康分", "value": score, "unit": "分",
         "formula": f"100 − 12×圈内零覆盖类别数 − {GAP_WEIGHT}×已核验盲区面积占比",
         "note": f"零覆盖类别 {empty_cats}/4；已核验 {verified} 格 × 0.09 km² ÷ {area_km2} km²"
                 f" = 占比 {round(share * 100)}%，本项扣 {round(GAP_WEIGHT * share)} 分"},
        {"key": "coverage", "label": "设施覆盖密度", "value": round(sum(
            v["inside"] for v in coverage.values()) / max(area_km2, 0.01), 1),
         "unit": "处/km²", "formula": "圈内四类设施合计 ÷ 15 分钟圈面积(km²)",
         "note": "面积由 15 分钟等时环鞋带公式 + 中位纬度校正求得"},
        {"key": "blindspot", "label": "服务盲区", "value": verified, "unit": "格",
         "formula": "直线初筛命中 且 批量距离矩阵到最近 N 个设施均 > 阈值",
         "note": f"连片后为 {len(cluster_blindspots(blindspots))} 个片区；"
                 f"另有 {undet} 格因检索未查全记为无法判定，不计入扣分"},
    ]


async def run_analysis(center: tuple) -> dict:
    t0 = time.monotonic()
    client.reset_metrics()
    # 等时圈（路线规划）与 POI 检索（地点检索）互不依赖，并发跑可整段省掉 POI 排队时间
    iso, pois, place = await asyncio.gather(compute_isochrones(center), fetch_categories(center),
                                            client.regeo(center))
    ring15 = iso["rings"]["15"]
    coverage = coverage_stats(pois, ring15)
    blindspots = await identify_blindspots(ring15, pois)
    area = polygon_area_km2(ring15)
    # 扣分不数"处数"（格子越密越多，5 格就封顶，五个社区挤在三档没有区分度），
    # 改数"已核验盲区占圈内面积的比例"：尺度无关、可解释、并且连片缺口越大扣得越多
    verified_cells = len([b for b in blindspots if b.get("verified")])
    gap_share = gap_area_share(verified_cells, area)
    score = max(0, min(100, round(
        100 - sum(12 * (1 if coverage[k]["inside"] == 0 else 0) for k in CATEGORIES)
        - GAP_WEIGHT * gap_share)))
    scenarios = build_scenarios(center, iso["series"], pois)
    label = " ".join(x for x in [place.get("district"), place.get("town") or place.get("street")] if x)
    return {
        "center": {"lat": center[0], "lng": center[1]},
        "label": label or place.get("address", ""),
        "rings": iso["rings"],
        "heat": iso["heat"],
        "series": iso["series"],
        "pois": pois,
        "coverage": coverage,
        "standards": compliance_check(center, pois, ring15),
        "blindspots": blindspots,
        "blindspot_clusters": cluster_blindspots(blindspots),
        "health_score": score,
        "area_km2": area,
        "gap_share": gap_share,
        "detour": detour_residual(center, iso["heat"]),
        "scenarios": scenarios,
        "metrics": metric_block(score, coverage, blindspots, area),
        "meta": {
            "elapsed_sec": round(time.monotonic() - t0, 1),
            "api_calls": client.calls,
            "cache_hits": client.cache_hits,
            "quota_units": client.quota_units,
            "by_service": dict(client.by_service),
            "place_probes": len(client.place_probes),
            "degraded": client.degraded,
            "retry_count": client.retry_count,
            "matrix_fallback": not client.matrix_ok,
            "mock": settings.mock,
            "data_mode": "mock" if settings.mock else ("real+degraded" if client.degraded else "real"),
            "sampling": {"rays": settings.rays, "radii": [int(x) for x in settings.sample_radii.split(",")],
                         "grid_step_m": GRID_STEP_M, "max_candidates": settings.max_candidates,
                         "verify_nearest": settings.verify_nearest},
            "evidence_id": fingerprint({"m": "fan-ray-walking", "c": [round(center[0], 5), round(center[1], 5)],
                                        "rays": settings.rays, "radii": settings.sample_radii,
                                        "heat": [(h["lat"], h["lng"], h["t"]) for h in iso["heat"]]}),
            "required_check": {v["name"]: f"步行{v['limit']}m内必达" for v in REQUIRED_CATEGORIES.values()},
        },
    }
