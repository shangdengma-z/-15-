"""体检分析编排：等时圈 + POI 覆盖统计 + 服务盲区识别（直线初筛 -> 批量距离矩阵核验）。"""

import asyncio
import math
import time

from .baidu_client import client, straight_line_m
from .config import CATEGORIES, REQUIRED_CATEGORIES
from .isochrone import compute_isochrones, point_in_polygon
from .poi import fetch_categories

GRID_STEP_M = 300       # 盲区初筛网格步长
MAX_CANDIDATES = 12     # 距离矩阵核验上限（控配额）


def _grid_in_ring(ring: list) -> list:
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
    return min((straight_line_m(pt, (p["lng"], p["lat"])) for p in pois), default=1e9)


def required_pois(pois: dict) -> dict:
    """从大类检索结果中按名称关键词切出赛题必核单品（菜市场/药店/小学），不额外消耗配额。"""
    return {key: [p for p in pois.get(spec["source"], []) if any(k in p["name"] for k in spec["keywords"])]
            for key, spec in REQUIRED_CATEGORIES.items()}


def _missing_near(pt: tuple, pois_by_cat: dict, cat_keys: list, limit_m: int) -> list:
    return [key for key in cat_keys if _min_straight_m(pt, pois_by_cat.get(key, [])) > limit_m]


async def identify_blindspots(ring15: list, pois: dict) -> list:
    """1) 网格初筛（四大类 + 赛题必核单品）；2) 用批量步行距离矩阵核验，剔除直线误报。"""
    grid = _grid_in_ring(ring15)
    req = required_pois(pois)
    cat_keys = list(CATEGORIES.keys())
    candidates = []
    for pt in grid:
        missing = _missing_near(pt, pois, cat_keys, 1000)
        missing_req = [k for k, spec in REQUIRED_CATEGORIES.items()
                       if _min_straight_m(pt, req[k]) > spec["limit"]]
        if missing or missing_req:
            preview = ([CATEGORIES[m]["name"] for m in missing]
                       + [REQUIRED_CATEGORIES[m]["name"] for m in missing_req])
            candidates.append({"lng": round(pt[0], 6), "lat": round(pt[1], 6),
                               "missing_preview": preview,
                               "_missing_keys": missing, "_missing_req": missing_req})
    candidates.sort(key=lambda c: len(c["_missing_keys"]) + len(c["_missing_req"]), reverse=True)
    verify = candidates[:MAX_CANDIDATES]

    # 核验：候选点到该类最近 5 个 POI 的真实步行距离必须也超限才判为盲区
    checked = []
    for cand in verify:
        specs = [(CATEGORIES[k]["name"], 1000, pois.get(k, [])) for k in cand["_missing_keys"]]
        specs += [(REQUIRED_CATEGORIES[k]["name"], REQUIRED_CATEGORIES[k]["limit"], req[k])
                  for k in cand["_missing_req"]]
        tasks = []
        for name, limit, pool in specs:
            nearest = sorted(pool, key=lambda p: straight_line_m((cand["lng"], cand["lat"]),
                                                                 (p["lng"], p["lat"])))[:5]
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
            checked.append({k: v for k, v in cand.items() if not k.startswith("_")}
                            | {"missing": confirmed, "verified": True})
    # 超出核验上限的候选只作"疑似"输出
    for cand in candidates[MAX_CANDIDATES:]:
        checked.append({k: v for k, v in cand.items() if not k.startswith("_")} | {"verified": False})
    return checked


def coverage_stats(pois: dict, ring15: list) -> dict:
    """统计各类设施在 15 分钟圈内的数量 + 圈内设施密度评估。"""
    stats = {}
    for key, spec in CATEGORIES.items():
        inside = [p for p in pois.get(key, []) if point_in_polygon((p["lng"], p["lat"]), ring15)]
        stats[key] = {"name": spec["name"], "total": len(pois.get(key, [])), "inside": len(inside)}
    return stats


async def run_analysis(center: tuple) -> dict:
    t0 = time.monotonic()
    client.reset_metrics()
    iso = await compute_isochrones(center)
    pois = await fetch_categories(center)
    ring15 = iso["rings"]["15"]
    coverage = coverage_stats(pois, ring15)
    blindspots = await identify_blindspots(ring15, pois)
    score = max(0, min(100, round(
        100 - sum(12 * (1 if coverage[k]["inside"] == 0 else 0) for k in CATEGORIES)
        - 8 * min(len([b for b in blindspots if b.get("verified")]), 5))))
    return {
        "center": {"lat": center[0], "lng": center[1]},
        "rings": iso["rings"],
        "heat": iso["heat"],
        "pois": pois,
        "coverage": coverage,
        "blindspots": blindspots,
        "health_score": score,
        "meta": {
            "elapsed_sec": round(time.monotonic() - t0, 1),
            "api_calls": client.calls,
            "cache_hits": client.cache_hits,
            "degraded": client.degraded,
            "retry_count": client.retry_count,
            "matrix_fallback": not client.matrix_ok,
            "required_check": {v["name"]: f"步行{v['limit']}m内必达" for v in REQUIRED_CATEGORIES.values()},
        },
    }
