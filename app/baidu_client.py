"""百度地图 REST 客户端：SN 签名、令牌桶限流、指数退避重试、磁盘缓存、Mock 与降级。"""

import asyncio
import hashlib
import json
import math
import random
import time
from pathlib import Path

import httpx

from .config import settings

HOST = "https://api.map.baidu.com"

# 常见错误码：4=限流, 10=配额不足/并发超限, 200/210=AK/SK 问题, 240=来源限制
RETRYABLE = {4, 10}


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
        self.bucket = TokenBucket(settings.qps)
        self.sem = asyncio.Semaphore(settings.concurrency)
        self.cache_dir = Path(settings.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0          # 实际发起的 API 调用数（用于报告）
        self.cache_hits = 0
        self.degraded = False   # 是否触发过降级（配额/连续失败）
        self._http = httpx.AsyncClient(timeout=10)

    def _cache_path(self, path: str, params: dict) -> Path:
        key = hashlib.md5((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
        return self.cache_dir / f"{path.strip('/').replace('/', '_')}_{key}.json"

    def _sign(self, path: str, params: dict) -> dict:
        """服务端 AK+SK 数字签名：sn = md5(path + "?" + 有序参数串 + sk)。"""
        if not settings.server_sk:
            return params
        items = {k: v for k, v in params.items() if v != ""}
        query = "&".join(f"{k}={items[k]}" for k in sorted(items))
        sn = hashlib.md5(f"{path}?{query}{settings.server_sk}".encode()).hexdigest()
        return {**params, "sn": sn}

    async def get(self, path: str, params: dict) -> dict:
        cache_file = self._cache_path(path, params)
        if cache_file.exists():
            self.cache_hits += 1
            return json.loads(cache_file.read_text(encoding="utf-8"))

        if settings.mock:
            data = self._mock_response(path, params)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        full = self._sign({"ak": settings.server_ak, "output": "json", **params})
        last_err = None
        for attempt in range(4):
            async with self.sem:
                await self.bucket.acquire()
                try:
                    resp = await self._http.get(HOST + path, params=full)
                    self.calls += 1
                    data = resp.json()
                except (httpx.HTTPError, ValueError) as e:
                    last_err = BmapError(-1, str(e))
                    await asyncio.sleep(1.5 * (2**attempt) + random.random())
                    continue
            code = data.get("status", data.get("result", {}).get("status", -1) if isinstance(data.get("result"), dict) else -1)
            if path.startswith("/place") or path.startswith("/geocoding"):
                code = data.get("status", -1)
            if code == 0:
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            if code in RETRYABLE:
                last_err = BmapError(code, data.get("message", "qps limited"))
                await asyncio.sleep(2 * (2**attempt) + random.random())  # 指数退避
                continue
            raise BmapError(code, data.get("message", "unknown"))
        self.degraded = True
        raise last_err or BmapError(-1, "retries exhausted")

    # ---- 业务接口 ----

    async def walking_duration(self, origin: tuple, dest: tuple) -> float:
        """返回两点真实路网步行时长（秒）；失败时降级为直线距离估算并打标。"""
        path = "/direction/v1/walking/"
        params = {
            "origin": f"{origin[0]},{origin[1]}",
            "destination": f"{dest[0]},{dest[1]}",
        }
        try:
            data = await self.get(path, params)
            route = data["result"]["routes"][0]
            return float(route["duration"]) * 60  # 文档单位：分钟
        except (BmapError, KeyError, IndexError):
            self.degraded = True
            return straight_line_sec(origin, dest)

    async def distance_matrix(self, origins: list, destinations: list) -> list:
        """批量步行距离矩阵：一次请求 origins x destinations（米）。"""
        path = "/distancematrix/v1/walking/"
        o = ";".join(f"{a},{b}" for a, b in origins)
        d = ";".join(f"{a},{b}" for a, b in destinations)
        try:
            data = await self.get(path, {"origins": o, "destinations": d})
            rows = []
            for element in data["result"]["elements"]:
                rows.append([float(e["distance"]) for e in element["distances"]])
            return rows
        except (BmapError, KeyError):
            self.degraded = True
            return [[straight_line_m(origins[i], destinations[j]) for j in range(len(destinations))]
                    for i in range(len(origins))]

    async def search_poi(self, query: str, center: tuple, radius: int = 3000) -> list:
        """圆形区域 POI 检索，自动翻页，按 uid 去重。"""
        path = "/place/v2/search"
        seen, out, page = set(), [], 0
        while page < 3:  # 每类最多取 60 条，足够生活圈尺度
            data = await self.get(path, {
                "query": query, "region": f"{center[0]},{center[1]}",
                "region_strategy": 1, "radius": radius,
                "page_size": 20, "page_num": page,
            })
            results = data.get("result", [])
            if not results:
                break
            for r in results:
                if r.get("uid") not in seen:
                    seen.add(r.get("uid"))
                    out.append(r)
            if len(results) < 20:
                break
            page += 1
        return out

    async def geocode(self, address: str) -> tuple:
        data = await self.get("/geocoding/v3/", {"address": address, "city": settings.city})
        loc = data["result"]["location"]
        return (loc["lat"], loc["lng"])

    # ---- Mock：无 AK 也能跑通全链路，用于开发与演示回放 ----

    def _mock_response(self, path: str, params: dict) -> dict:
        if path == "/direction/v1/walking/":
            o = tuple(map(float, params["origin"].split(",")))
            d = tuple(map(float, params["destination"].split(",")))
            meters = straight_line_m(o, d) * random.uniform(1.15, 1.45)  # 模拟路网绕行系数
            return {"status": 0, "result": {"routes": [{"duration": meters / 80, "distance": round(meters)}]}}
        if path == "/distancematrix/v1/walking/":
            origins = [tuple(map(float, s.split(","))) for s in params["origins"].split(";")]
            dests = [tuple(map(float, s.split(","))) for s in params["destinations"].split(";")]
            return {"status": 0, "result": {"elements": [
                [{"distance": round(straight_line_m(o, dd) * random.uniform(1.15, 1.4))} for dd in dests]
                for o in origins]}}
        if path == "/place/v2/search":
            lat, lng = map(float, params["region"].split(","))
            n = 8 if page_has(params) else 0
            return {"status": 0, "result": [
                {"uid": f"mock-{params['query']}-{i}", "name": f"{params['query']}{i + 1}号",
                 "location": {"lat": lat + random.uniform(-0.015, 0.015),
                              "lng": lng + random.uniform(-0.017, 0.017)},
                 "address": "模拟地址"} for i in range(n)]}
        if path == "/geocoding/v3/":
            return {"status": 0, "result": {"location": {"lat": 39.908, "lng": 116.397}}}
        return {"status": 0, "result": []}


def page_has(params: dict) -> bool:
    return int(params.get("page_num", 0)) == 0


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
