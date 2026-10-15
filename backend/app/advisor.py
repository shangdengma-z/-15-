"""选址建议：把"哪里缺设施"换成"该把店开在哪"。

复用已建档的体检结果（等时圈 + 设施池 + 已核验盲区），只在候选定稿后为每个点位
补一次步行路线实测，因此一次建议的出网量等于候选数（默认 3 次），而不是全网格。
需求强度用圈内住宅小区 POI 数量作**人气代理**，不等于常住人口数据——这条口径随结果一起返回。
"""

import asyncio
import re

from .analysis import grid_in_ring
from .baidu_client import client, straight_line_m

BIZ_TYPES = {
    "pharmacy": {"name": "药店", "pool": "medical", "spacing_m": 500,
                 "keywords": ["药店", "药房"], "blindspot_any": ["药店"]},
    "market": {"name": "生鲜菜店", "pool": "shopping", "spacing_m": 600,
               "keywords": ["菜市场", "菜场", "农贸", "超市"], "blindspot_any": ["菜市场", "生鲜"]},
    "elderly": {"name": "养老驿站", "pool": "elderly", "spacing_m": 800,
                "keywords": ["养老", "照料", "老年"], "blindspot_any": ["养老"]},
    "childcare": {"name": "托幼", "pool": "education", "spacing_m": 500,
                  "keywords": ["幼儿园"], "blindspot_any": ["幼儿园"]},
    "clinic": {"name": "诊所 / 卫生服务", "pool": "medical", "spacing_m": 800,
               "keywords": ["诊所", "卫生服务", "卫生室", "门诊"], "blindspot_any": ["卫生服务", "诊所"]},
}

DEMAND_QUERY = "住宅小区"
# 百度 place/v2/search 的精简结果不带 type 字段，所以按名称词筛 + 剔除非住宅噪声，
# 再把"××苑-10号楼"这类楼号合并到小区本体，避免同一小区被数成好几个人气点。
DEMAND_KEYWORDS = ["小区", "家园", "新村", "公寓", "花园", "苑", "府", "里", "庄", "社区", "城", "湾"]
DEMAND_EXCLUDE = ["大厦", "写字楼", "科技园", "产业园", "工业园", "学校", "大学", "医院", "商场",
                  "公园", "地铁", "车站", "停车", "超市", "酒店", "宾馆", "厕所", "物业"]
BUILDING_SUFFIX = re.compile(r"[-—\s]?\d+\s*(号楼|幢|栋|单元|层|号院|院)$")
DEMAND_RADIUS_M = 500
DEMAND_CAP = 6                  # 圈内 6 个住宅小区即视为需求饱和
BLINDSPOT_RADIUS_M = 400        # 落在已核验盲区附近的加分半径
MIN_SPACING_M = 400             # 推荐点位彼此至少间隔，避免三个点挤在一条街

W_DEMAND, W_SPACING, B_BLINDSPOT = 0.55, 0.33, 0.12


def filter_residential(rows: list) -> list:
    """从检索原始行里挑出住宅小区：名称词命中 -> 剔除非住宅 -> 楼号合并到小区本体。"""
    out, seen = [], set()
    for r in rows:
        name = (r.get("name") or "").strip()
        loc = r.get("location") or {}
        if not name or not loc:
            continue
        if any(k in name for k in DEMAND_EXCLUDE):
            continue
        if not any(k in name for k in DEMAND_KEYWORDS):
            continue
        root = BUILDING_SUFFIX.sub("", name)
        if root in seen:
            continue
        seen.add(root)
        out.append({"name": root, "lat": round(loc["lat"], 6), "lng": round(loc["lng"], 6)})
    return out


async def residential_pool(center: tuple) -> list:
    """人气代理数据：圈内住宅小区（一次检索，走 30 QPS 的地点检索，不占步行配额）。"""
    return filter_residential(await client.search_poi(DEMAND_QUERY, center, radius=2000))


def _demand(lng_lat: tuple, housing: list) -> int:
    return sum(1 for h in housing
               if straight_line_m((h["lat"], h["lng"]), (lng_lat[1], lng_lat[0])) <= DEMAND_RADIUS_M)


def _nearest_supply(lng_lat: tuple, supply: list) -> float:
    return min((straight_line_m((s["lat"], s["lng"]), (lng_lat[1], lng_lat[0])) for s in supply),
               default=1e9)


def _fills_blindspot(lng_lat: tuple, blindspots: list, names: list) -> bool:
    for b in blindspots:
        if not b.get("verified"):
            continue
        if not any(n in "、".join(b.get("missing", [])) for n in names):
            continue
        if straight_line_m((b["lat"], b["lng"]), (lng_lat[1], lng_lat[0])) <= BLINDSPOT_RADIUS_M:
            return True
    return False


def _rank(ring15: list, housing: list, supply: list, blindspots: list, spec: dict) -> list:
    scored = []
    for pt in grid_in_ring(ring15):        # grid_in_ring 返回 (lng,lat)
        demand = _demand(pt, housing)
        if demand == 0:
            continue
        near_type = _nearest_supply(pt, supply)
        d_score = min(demand / DEMAND_CAP, 1.0)
        s_score = min(near_type / spec["spacing_m"], 1.0)
        blind = _fills_blindspot(pt, blindspots, spec["blindspot_any"])
        score = round(100 * (W_DEMAND * d_score + W_SPACING * s_score + (B_BLINDSPOT if blind else 0)), 1)
        scored.append({"lng": round(pt[0], 6), "lat": round(pt[1], 6), "score": score,
                       "housing_within_500m": demand,
                       "nearest_same_type_m": None if near_type > 9e8 else round(near_type),
                       "fills_verified_blindspot": blind})
    scored.sort(key=lambda c: c["score"], reverse=True)
    picked = []
    for c in scored:
        if all(straight_line_m((c["lat"], c["lng"]), (p["lat"], p["lng"])) >= MIN_SPACING_M for p in picked):
            picked.append(c)
        if len(picked) >= 3:
            break
    return picked


async def advise(payload: dict, biz: str, top: int = 3) -> dict:
    """基于一次已建档的体检结果给出选址建议；top 决定补几次步行实测（每次 1 个配额单位）。"""
    spec = BIZ_TYPES[biz]
    center = (payload["center"]["lat"], payload["center"]["lng"])
    ring15 = payload["rings"]["15"]
    supply = [p for p in (payload.get("pois") or {}).get(spec["pool"], [])
              if any(k in p["name"] for k in spec["keywords"])]

    calls0, quota0 = client.calls, client.quota_units
    housing = await residential_pool(center)
    blindspots = payload.get("blindspots") or []
    cand = _rank(ring15, housing, supply, blindspots, spec)[:top]

    # 候选定稿后才做路网实测：网格上百个点不可能每个都烧一次配额
    routes = await asyncio.gather(*(client.walking_route((c["lat"], c["lng"]), center)
                                    for c in cand)) if cand else []
    for c, (meters, seconds) in zip(cand, routes):
        c["walk_m_to_center"] = round(meters)
        c["walk_sec_to_center"] = round(seconds)
        c["within_15min_walk"] = seconds <= 900

    return {
        "biz": biz, "biz_name": spec["name"], "center": {"lat": center[0], "lng": center[1]},
        "candidates": cand,
        "basis": {
            "demand_proxy": f"500m 内住宅小区 POI 数（{DEMAND_QUERY} 检索，{len(housing)} 个候选池点），"
                            f"人气代理而非人口数据",
            "spacing_m": spec["spacing_m"],
            "weights": {"demand": W_DEMAND, "same_type_spacing": W_SPACING, "blindspot_bonus": B_BLINDSPOT},
            "verify": f"候选点各 1 次步行路线实测（本次 {len(cand)} 次出网）",
        },
        "meta": {"api_calls": client.calls - calls0, "quota_units": client.quota_units - quota0,
                 "mock": bool((payload.get("meta") or {}).get("mock"))},
    }
