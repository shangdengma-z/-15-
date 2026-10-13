"""等时圈计算：扇形射线采样真实步行耗时 -> 射线上线性插值边界 -> 平滑成多边形。

为什么不用二分：固定半径网格每个采样点都进缓存且全部复用（5/10/15 分钟三条圈 + 热力图），
总请求数可预期（rays x len(radii)），对 QPS 配额友好。
"""

import asyncio
import math

from .baidu_client import client
from .config import settings


def dest_on_ray(center: tuple, bearing_deg: float, radius_m: float) -> tuple:
    """从中心点沿方位角推算目标点（经纬度）。"""
    lat, lng = center
    d = radius_m / 111320.0
    brg = math.radians(bearing_deg)
    lat2 = lat + d * math.cos(brg)
    lng2 = lng + d * math.sin(brg) / math.cos(math.radians(lat))
    return (lat2, lng2)


def _interp_threshold(times: list, radii: list, target_sec: float):
    """在单条射线上插值耗时=target 的半径；全程小于/大于 target 时给端点估计。"""
    pairs = sorted(zip(radii, times))
    prev_r, prev_t = None, None
    for r, t in pairs:
        if prev_t is not None and prev_t <= target_sec <= t:
            if t == prev_t:
                return r
            ratio = (target_sec - prev_t) / (t - prev_t)
            return prev_r + (r - prev_r) * ratio
        prev_r, prev_t = r, t
    if times and max(times) < target_sec:
        return radii[-1] * 1.15  # 宽松截断，避免射线冲出远郊
    return radii[0] * (target_sec / max(times[0], 1)) if times else 0


def smooth_polygon(points: list, iterations: int = 2) -> list:
    """多边形顶点邻域平均，消除落点异常导致的尖刺。"""
    pts = points[:]
    for _ in range(iterations):
        nxt = []
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            nxt.append(((a[0] + 4 * b[0] + c[0]) / 6, (a[1] + 4 * b[1] + c[1]) / 6))
        pts = nxt
    return pts


async def compute_isochrones(center: tuple) -> dict:
    """返回 {rings: {5/10/15分钟: [lng,lat]...}, heat: [{lng,lat,t_sec}...]}。"""
    n_rays = settings.rays
    radii = [int(x) for x in settings.sample_radii.split(",")]
    bearings = [i * 360.0 / n_rays for i in range(n_rays)]

    async def probe(ray_i: int):
        row = []
        for r in radii:
            dest = dest_on_ray(center, bearings[ray_i], r)
            t = await client.walking_duration(center, dest)
            row.append((dest, t))
        return row

    results = await asyncio.gather(*(probe(i) for i in range(n_rays)))

    heat = [{"lng": round(p[1], 6), "lat": round(p[0], 6), "t": round(t)}
            for row in results for p, t in row]

    rings = {}
    targets = {"5": 300, "10": 600, "15": settings.walk_target_sec}
    for label, target in targets.items():
        raw = []
        for i, row in enumerate(results):
            times = [t for _, t in row]
            r = _interp_threshold(times, radii, target)
            raw.append(dest_on_ray(center, bearings[i], max(r, 50)))
        ring = smooth_polygon(raw) if label == "15" else raw
        rings[label] = [[round(lng, 6), round(lat, 6)] for lat, lng in ring]
    return {"rings": rings, "heat": heat}


def point_in_polygon(pt: tuple, polygon: list) -> bool:
    """射线法；polygon 为 [lng,lat] 列表。"""
    x, y = pt
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside
