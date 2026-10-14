"""百度地图 REST 客户端：SN 签名、令牌桶限流、指数退避重试、磁盘缓存、Mock 与降级。

实测校准（2026-10，个人 AK）：
- 步行路径规划 v1 报 Parameter Invalid，统一使用 v2（duration 单位：秒）
- 圆形检索用 place/v2/search，POI 列表字段是 results（不是 result）
- 批量距离矩阵可能未开通（302 到 error.html）：自动熔断并降级为并发路线规划
"""

import asyncio
import hashlib
import json
import logging
import math
import random
import time
from pathlib import Path

import httpx

from .config import settings

log = logging.getLogger("bmap")

HOST = "https://api.map.baidu.com"

# 4=频率超限, 10=每日配额超限, 401=同一 AK 并发/突发超限（秒级窗口，快速重试即可恢复）
RETRYABLE = {4, 10, 401}


def _service_of(path: str) -> str:
    """百度按服务分别限流（步行 3 QPS / 地点检索 30 QPS / 地理编码 10 QPS），故每服务独立配额。
    注意用子串而非 "/geocoding"：正/逆地理编码共用同一个地理编码配额池。"""
    if "distancematrix" in path:
        return "distancematrix"
    if "place" in path:
        return "place"
    if "geocoding" in path:
        return "geocoding"
    return "direction"


class BmapError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"bmap code={code}: {message}")
        self.code = code


class TokenBucket:
    """按 settings.qps 匀速放行，避免触发平台 QPS 限流。"""

    def __init__(self, qps: float):
        self.qps = max(qps, 0.1)
        self.tokens = 1.0
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.qps, self.tokens + (now - self.updated) * self.qps)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.qps)


class BmapClient:
    def __init__(self):
        # 每个服务一套「令牌桶 + 并发闸」：步行是硬瓶颈（3 QPS），地点检索可放开到 25 QPS
        limits = {
            "direction": (settings.qps, settings.concurrency),
            "distancematrix": (settings.qps, settings.concurrency),
            "place": (settings.place_qps, settings.place_concurrency),
            "geocoding": (settings.geo_qps, settings.geo_concurrency),
        }
        self.buckets = {svc: TokenBucket(q) for svc, (q, _) in limits.items()}
        self.sems = {svc: asyncio.Semaphore(c) for svc, (_, c) in limits.items()}
        self.cache_dir = Path(settings.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0          # 实际发起的 API 调用数（用于报告）
        self.cache_hits = 0
        self.quota_units = 0    # 折算配额单位：批量矩阵按「路线对数」计费，与 HTTP 请求数分栏不合并
        self.by_service = {}    # 每服务真实出网次数，供 /api/stats 与响应头自证计量
        self.place_probes = []  # 每次检索的停止原因，用于把"没查到"和"没有"区分开
        self.degraded = False   # 仅当真正直线兜底时置位（数据质量降级）
        self.retry_count = 0    # 限流退避重试次数（最终成功则不影响数据质量）
        self.matrix_ok = True   # 距离矩阵服务可用性开关
        self.quota_exhausted = False  # 日配额耗尽熔断：置位后不再出网，只走缓存
        self._http = httpx.AsyncClient(timeout=10, follow_redirects=False)

    def reset_metrics(self):
        """每次体检任务开始时清零，使 meta 指标反映本轮真实情况（而非进程累积）。"""
        self.calls = 0
        self.cache_hits = 0
        self.quota_units = 0
        self.by_service = {}
        self.place_probes = []
        self.degraded = False
        self.retry_count = 0
        self.matrix_ok = True

    def _cache_path(self, path: str, params: dict) -> Path:
        # 缓存键带模式前缀，防止 Mock 数据污染真实结果
        mode = "mock" if settings.mock else "real"
        key = hashlib.md5((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
        return self.cache_dir / f"{mode}_{path.strip('/').replace('/', '_')}_{key}.json"

    def _sign(self, path: str, params: dict) -> dict:
        """服务端 AK+SK 数字签名：sn = md5(path + "?" + 有序参数串 + sk)。"""
        if not settings.server_sk:
            return params
        items = {k: v for k, v in params.items() if v != ""}
        query = "&".join(f"{k}={items[k]}" for k in sorted(items))
        sn = hashlib.md5(f"{path}?{query}{settings.server_sk}".encode()).hexdigest()
        return {**params, "sn": sn}

    async def get(self, path: str, params: dict, quota: int = 1) -> dict:
        cache_file = self._cache_path(path, params)
        if cache_file.exists():
            self.cache_hits += 1
            return json.loads(cache_file.read_text(encoding="utf-8"))

        if settings.mock:
            data = self._mock_response(path, params)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        if self.quota_exhausted:
            # 日配额已耗尽：不再出网烧请求，直接由上层降级处理（演示现场的保护阀）
            raise BmapError(10, "daily quota exhausted, cached responses only")

        full = self._sign(path, {"ak": settings.server_ak, "output": "json", **params})
        svc = _service_of(path)
        last_err = None
        for attempt in range(5):
            async with self.sems[svc]:
                await self.buckets[svc].acquire()
                try:
                    resp = await self._http.get(HOST + path, params=full)
                    self.calls += 1
                    self.quota_units += quota
                    self.by_service[svc] = self.by_service.get(svc, 0) + 1
                    try:
                        data = resp.json()
                    except ValueError:
                        if resp.status_code != 200:
                            # 302/4xx 且无 JSON：接口下线或服务未开通，重试无意义
                            raise BmapError(resp.status_code, f"HTTP{resp.status_code} no JSON body")
                        raise
                except BmapError:
                    raise
                except (httpx.HTTPError, ValueError) as e:
                    last_err = BmapError(-1, str(e))
                    await asyncio.sleep(1.5 * (2**attempt) + random.random())
                    continue
            code = data.get("status", -1)
            if code == 0:
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            if code == 10:
                # 每日配额超限：重试到明天才有意义，熔断到进程重启为止，避免反复报错烧额度
                self.quota_exhausted = True
                raise BmapError(code, data.get("message", "daily quota exhausted"))
            if code in RETRYABLE:
                # 限流/并发超限：百度按秒级窗口计，0.5s 起退避即可恢复；成功则不算降级，仅计入重试次数
                last_err = BmapError(code, data.get("message", "qps limited"))
                self.retry_count += 1
                await asyncio.sleep(0.5 * (2**attempt) + random.random() * 0.3)
                continue
            raise BmapError(code, data.get("message", "unknown"))
        raise last_err or BmapError(-1, "retries exhausted")

    # ---- 业务接口 ----

    async def walking_route(self, origin: tuple, dest: tuple) -> tuple:
        """返回 (米, 秒)；失败时降级为直线估算并打标。"""
        try:
            data = await self.get("/direction/v2/walking/", {
                "origin": f"{origin[0]},{origin[1]}",
                "destination": f"{dest[0]},{dest[1]}",
            })
            route = data["result"]["routes"][0]
            return float(route["distance"]), float(route["duration"])
        except (BmapError, KeyError, IndexError) as e:
            log.warning("walking fallback to straight-line: %s", e)
            self.degraded = True
            m = straight_line_m(origin, dest)
            return m, m / 1.25

    async def walking_duration(self, origin: tuple, dest: tuple) -> float:
        return (await self.walking_route(origin, dest))[1]

    async def distance_matrix(self, origins: list, destinations: list) -> list:
        """批量步行距离矩阵（米）。服务不可用时自动熔断，降级为并发路线规划。"""
        if self.matrix_ok:
            try:
                data = await self.get("/distancematrix/v1/walking/", {
                    "origins": ";".join(f"{a},{b}" for a, b in origins),
                    "destinations": ";".join(f"{a},{b}" for a, b in destinations),
                }, quota=len(origins) * len(destinations))  # 矩阵按「路线对数」折配额，不是按 HTTP 次数
                return [[float(e["distance"]) for e in el["distances"]]
                        for el in data["result"]["elements"]]
            except (BmapError, KeyError):
                self.matrix_ok = False  # 熔断：后续矩阵请求直接走路线规划
        rows = await asyncio.gather(*(
            asyncio.gather(*(self.walking_route(o, d) for d in destinations)) for o in origins))
        return [[m for m, _ in row] for row in rows]

    async def search_poi(self, query: str, center: tuple, radius: int = 3000) -> list:
        """圆形区域 POI 检索（place/v2/search），自动翻页，按 uid 去重。

        同时登记「这一圈为什么停了」：判"1km 内没有药店"是一个全称否定，只有查全了才成立。
        实测 place/v2/search 会把 page_size 静默压到 20（请求 30/50 时 status=0 也只回 20 条），
        所以短页判据必须按实测页容量比，不能按请求值。
        """
        path = "/place/v2/search"
        seen, out, page, stop = set(), [], 0, "page_cap"
        page_capacity = 0
        while page < 3:
            data = await self.get(path, {
                "query": query, "location": f"{center[0]},{center[1]}",
                "radius": radius, "page_size": 20, "page_num": page,
            })
            results = data.get("results", [])
            page_capacity = max(page_capacity, len(results))
            if not results:
                stop = "empty" if page == 0 else "complete"
                break
            before = len(out)
            for r in results:
                if r.get("uid") not in seen:
                    seen.add(r.get("uid"))
                    out.append(r)
            if len(out) == before and page > 0:
                stop = "dup_stop"
                break
            total = data.get("total", len(out))
            if len(out) >= total or len(results) < 20:
                stop = "complete"
                break
            page += 1
        self.place_probes.append({"query": query, "pages": page + 1, "raw": page_capacity and len(out),
                                  "count": len(out), "stop": stop})
        return out

    async def geocode(self, address: str) -> tuple:
        data = await self.get("/geocoding/v3/", {"address": address, "city": settings.city})
        loc = data["result"]["location"]
        return (loc["lat"], loc["lng"])

    async def regeo(self, point: tuple) -> dict:
        """逆地理编码：给体检记录一个人类可读的社区名（1 次调用，进缓存后零成本）。"""
        try:
            data = await self.get("/reverse_geocoding/v3/", {
                "coordtype": "bd09ll", "location": f"{point[0]},{point[1]}"})
            r = data.get("result", {})
            comp = r.get("addressComponent", {})
            return {"address": r.get("formatted_address", ""),
                    "district": comp.get("district", ""), "town": comp.get("town", ""),
                    "street": comp.get("street", "")}
        except (BmapError, KeyError, IndexError, TypeError) as e:
            log.info("regeo unavailable: %s", e)
            return {}

    # ---- Mock：无 AK 也能跑通全链路，用于开发与演示回放 ----

    def _mock_response(self, path: str, params: dict) -> dict:
        if "direction" in path and "walking" in path:
            o = tuple(map(float, params["origin"].split(",")))
            d = tuple(map(float, params["destination"].split(",")))
            meters = straight_line_m(o, d) * random.uniform(1.15, 1.45)  # 模拟路网绕行系数
            return {"status": 0, "result": {"routes": [
                {"distance": round(meters), "duration": round(meters / 1.15)}]}}
        if "distancematrix" in path:
            origins = [tuple(map(float, s.split(","))) for s in params["origins"].split(";")]
            dests = [tuple(map(float, s.split(","))) for s in params["destinations"].split(";")]
            return {"status": 0, "result": {"elements": [
                {"distances": [{"distance": round(straight_line_m(o, dd) * random.uniform(1.15, 1.4))}
                               for dd in dests]} for o in origins]}}
        if "place" in path:
            lat, lng = map(float, params["location"].split(","))
            n = 8 if int(params.get("page_num", 0)) == 0 else 0
            return {"status": 0, "total": n, "results": [
                {"uid": f"mock-{params['query']}-{i}", "name": f"{params['query']}{i + 1}号",
                 "location": {"lat": lat + random.uniform(-0.015, 0.015),
                              "lng": lng + random.uniform(-0.017, 0.017)},
                 "address": "模拟地址"} for i in range(n)]}
        if "reverse_geocoding" in path:
            lat, lng = map(float, params["location"].split(","))
            return {"status": 0, "result": {"formatted_address": f"模拟市示例区 {lat:.4f},{lng:.4f}",
                    "addressComponent": {"district": "示例区", "town": "示例街道", "street": "示例路"}}}
        if "geocoding" in path:
            return {"status": 0, "result": {"location": {"lat": 39.908, "lng": 116.397}}}
        return {"status": 0, "result": []}


def straight_line_m(a: tuple, b: tuple) -> float:
    """Haversine 直线距离（米），仅用于降级估算。"""
    R = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def straight_line_sec(a: tuple, b: tuple) -> float:
    return straight_line_m(a, b) / 1.25  # 经验步速 1.25 m/s


client = BmapClient()
