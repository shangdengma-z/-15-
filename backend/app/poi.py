"""POI 检索与清洗：按类别多关键词检索 -> 语义白名单过滤 -> 坐标去重。

百度的 tag 体系会把保健品店归入"药店"、把培训点归入"小学"，
评分细则明确要求"多源 POI 数据清洗策略"，这里用名称关键词白名单二次过滤。
"""

import asyncio

from .baidu_client import client
from .config import CATEGORIES


def clean_pois(rows: list, keywords_any: list) -> list:
    """名称关键词白名单过滤 + 同名同坐标去重。"""
    out = []
    for r in rows:
        name = r.get("name", "") + r.get("type", "")
        if any(k in name for k in keywords_any):
            loc = r.get("location", {})
            out.append({
                "name": r.get("name"),
                "lng": round(loc.get("lng", 0), 6),
                "lat": round(loc.get("lat", 0), 6),
                "address": r.get("address", ""),
            })
    # 同名同坐标去重
    uniq, seen = [], set()
    for p in out:
        key = (p["name"], p["lng"], p["lat"])
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


async def fetch_categories(center: tuple, radius: int = 3000) -> dict:
    async def one(cat_key: str, spec: dict):
        lists = await asyncio.gather(*(client.search_poi(q, center, radius) for q in spec["queries"]))
        raw = [r for rows in lists for r in rows]
        return cat_key, clean_pois(raw, spec["keywords_any"])

    pairs = await asyncio.gather(*(one(k, v) for k, v in CATEGORIES.items()))
    return dict(pairs)
