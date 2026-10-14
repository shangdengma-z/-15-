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
    """在单条射线上插值耗时=target 的半径；全程小于/大于 target 时给端点估计。

    同时返回顶点来源标签，供前端按置信度着色（实测 / 内插 / 外推），
    外推点是把「没测到」和「测得出」区分开的唯一办法。
    """
    pairs = sorted(zip(radii, times))
    prev_r, prev_t = None, None
    for r, t in pairs:
        if prev_t is not None and prev_t <= target_sec <= t:
            if t == prev_t:
                return r, "measured"
            ratio = (target_sec - prev_t) / (t - prev_t)
            return prev_r + (r - prev_r) * ratio, "interpolated"
        prev_r, prev_t = r, t
    if times and max(times) < target_sec:
        return radii[-1] * 1.15, "extrapolated"  # 宽松截断，避免射线冲出远郊
    if times:
        return radii[0] * (target_sec / max(times[0], 1)), "extrapolated"
    return 0, "unknown"


def ring_from_series(series: list, center: tuple, target_sec: float,
                     smooth: bool = False) -> dict:
    """由已采样的「方位角-半径-耗时」序列重新生成某一阈值的等时圈，零额外 API 调用。

    这是 5/10/15/20 分钟情景对比与人群步速换算共用的一条路：原始耗时留在结果里，
    换阈值只是换一次插值，不需要再打路网接口。
    """
    raw, kinds = [], []
    for item in series:
        r, kind = _interp_threshold(item["times"], item["radii"], target_sec)
        raw.append(dest_on_ray(center, item["bearing"], max(r, 50)))
        kinds.append(kind)
    pts = smooth_polygon(raw) if smooth else raw
    return {"ring": [[round(lng, 6), round(lat, 6)] for lat, lng in pts],
            "vertex_kind": kinds,
            "radius_m": [round(math.hypot((lat - center[0]) * 111320,
                                          (lng - center[1]) * 111320 *
                                          math.cos(math.radians(center[0]))))
                         for lat, lng in pts]}


def polygon_area_km2(ring: list) -> float:
    """鞋带公式 + 中位纬度校正，返回平方公里。"""
    if len(ring) < 3:
        return 0.0
    mid_lat = sum(p[1] for p in ring) / len(ring)
    kx = 111.320 * math.cos(math.radians(mid_lat))   # 1 度经度 ≈ km
    ky = 111.320                                     # 1 度纬度 ≈ km
    s = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        s += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return round(abs(s) / 2, 3)


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
    """返回 {rings, heat, series}。series 是每条射线的「半径-实测耗时」原始序列，
    留着它就能在不打路网接口的前提下重算任意时间阈值（情景对比 / 人群步速）。"""
    n_rays = settings.rays
    radii = [int(x) for x in settings.sample_radii.split(",")]
    bearings = [i * 360.0 / n_rays for i in range(n_rays)]

    async def probe(ray_i: int):
        row = []
        for r in radii:
            dest = dest_on_ray(center, bearings[ray_i], r)
            meters, sec = await client.walking_route(center, dest)
            row.append((dest, sec, meters))
        return row

    results = await asyncio.gather(*(probe(i) for i in range(n_rays)))

    heat = [{"lng": round(p[1], 6), "lat": round(p[0], 6), "t": round(t), "d": round(d)}
            for row in results for p, t, d in row]
    series = [{"bearing": round(b, 2), "radii": radii,
               "times": [round(t, 1) for _, t, _ in row],
               "distances": [round(d) for _, _, d in row]}
              for b, row in zip(bearings, results)]

    rings = {}
    for label, target in {"5": 300, "10": 600, "15": settings.walk_target_sec}.items():
        rings[label] = ring_from_series(series, center, target, smooth=label == "15")["ring"]
    return {"rings": rings, "heat": heat, "series": series, "sample_radii": radii}


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
