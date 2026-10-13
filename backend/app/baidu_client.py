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

# 4=频率超限, 10=每日配额超限, 401=同 AK 并发超限（实测并发 4 时约 15% 触发）：退避重试可恢复
RETRYABLE = {4, 10, 401}


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
        self.degraded = False   # 仅当真正直线兜底时置位（数据质量降级）
        self.retry_count = 0    # 限流退避重试次数（最终成功则不影响数据质量）
        self.matrix_ok = True   # 距离矩阵服务可用性开关
        self._http = httpx.AsyncClient(timeout=10, follow_redirects=False)

    def reset_metrics(self):
        """每次体检任务开始时清零，使 meta 指标反映本轮真实情况（而非进程累积）。"""
        self.calls = 0
        self.cache_hits = 0
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

    async def get(self, path: str, params: dict) -> dict:
        cache_file = self._cache_path(path, params)
        if cache_file.exists():
            self.cache_hits += 1
            return json.loads(cache_file.read_text(encoding="utf-8"))

        if settings.mock:
            data = self._mock_response(path, params)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        full = self._sign(path, {"ak": settings.server_ak, "output": "json", **params})
        last_err = None
        for attempt in range(4):
            async with self.sem:
                await self.bucket.acquire()
                try:
                    resp = await self._http.get(HOST + path, params=full)
                    self.calls += 1
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
            if code in RETRYABLE:
                # 限流/并发超限：退避重试；成功则不算降级，仅计入重试次数
                last_err = BmapError(code, data.get("message", "qps limited"))
                self.retry_count += 1
                await asyncio.sleep(2 * (2**attempt) + random.random())  # 指数退避
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
                })
                return [[float(e["distance"]) for e in el["distances"]]
                        for el in data["result"]["elements"]]
            except (BmapError, KeyError):
                self.matrix_ok = False  # 熔断：后续矩阵请求直接走路线规划
        rows = await asyncio.gather(*(
            asyncio.gather(*(self.walking_route(o, d) for d in destinations)) for o in origins))
        return [[m for m, _ in row] for row in rows]

    async def search_poi(self, query: str, center: tuple, radius: int = 3000) -> list:
        """圆形区域 POI 检索（place/v2/search），自动翻页，按 uid 去重。"""
        path = "/place/v2/search"
        seen, out, page = set(), [], 0
        while page < 3:  # 每类每关键词最多取 60 条，足够生活圈尺度
            data = await self.get(path, {
                "query": query, "location": f"{center[0]},{center[1]}",
                "radius": radius, "page_size": 20, "page_num": page,
            })
            results = data.get("results", [])
            if not results:
                break
            for r in results:
                if r.get("uid") not in seen:
                    seen.add(r.get("uid"))
                    out.append(r)
            total = data.get("total", len(out))
            if len(out) >= total or len(results) < 20:
                break
            page += 1
        return out

    async def geocode(self, address: str) -> tuple:
        data = await self.get("/geocoding/v3/", {"address": address, "city": settings.city})
        loc = data["result"]["location"]
        return (loc["lat"], loc["lng"])

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
