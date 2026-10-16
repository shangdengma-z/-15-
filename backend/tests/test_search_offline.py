"""地点搜索离线自测：Mock 地图 + TestClient，验证"搜到县级市"这条链路成立且诚实。

盯的是四件容易做错的事：
1. 区划命中必须带 `kind=region` 且 `precise=false`——区划中心不是某个门口，报告里不能糊过去；
2. 同一坐标的区划中心与同名 POI 只留一条，别让下拉里出现两个"昆山市"；
3. 短输入（<2 字）不发请求，中文逐字上屏时不能每个字烧一次配额；
4. 同一个词第二次搜索应当命中缓存、`api_calls` 不再增长。
    .venv/Scripts/python.exe backend/tests/test_search_offline.py
"""

import os
import shutil
import sys
import uuid
from pathlib import Path

TAG = uuid.uuid4().hex[:8]
os.environ.update(AUTH_ENABLED="0", BMAP_MOCK="1", BMAP_QPS="1000", BMAP_CONCURRENCY="8",
                  BMAP_CACHE_DIR=f"data/cache_searchtest_{TAG}",
                  BMAP_DB_PATH=f"data/db_searchtest_{TAG}/lifestyle.db",
                  BMAP_SERVER_AK="", LLM_API_KEY="")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.baidu_client import client  # noqa: E402
from app.main import app  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


def main() -> int:
    with TestClient(app) as c:
        # 1) 区划命中：县级市也能定位，并明确标成"区划中心"
        r = c.get("/api/search", params={"q": "昆山市"})
        d = r.json()
        hits = d.get("results") or []
        check("搜县级市有结果", r.status_code == 200 and d["count"] >= 1, str(d["count"]))
        first = hits[0]
        check("区划命中带 kind/level 且标为非精确",
              first["kind"] == "region" and first["level"] and first["precise"] is False,
              f"{first['kind']}/{first['level']}")
        check("坐标像中国境内 BD09LL", 18 <= first["lat"] <= 54 and 73 <= first["lng"] <= 135,
              f"{first['lat']:.4f},{first['lng']:.4f}")

        # 2) 省 / 村 / 门店名都不该 500
        for q in ("浙江省", "扎尕那村", "药店", "昆山市中医院"):
            rr = c.get("/api/search", params={"q": q})
            check(f"搜「{q}」返回 200 且有候选", rr.status_code == 200 and rr.json()["count"] >= 1,
                  str(rr.json().get("count")))

        # 2b) 排序：搜门店名时那家门店要排在区划中心前面
        named = c.get("/api/search", params={"q": "昆山市中医院"}).json()["results"]
        check("门店名命中排在区划之前",
              named and named[0]["kind"] == "poi" and "昆山市中医院" in named[0]["name"],
              str([(h["kind"], h["name"]) for h in named[:2]]))
        check("区划命中仍留在列表里（可退而体检整个区划）",
              any(h["kind"] == "region" for h in named), str(len(named)))

        # 3) 去重：同一坐标不出现两条
        coords = [(round(h["lat"], 3), round(h["lng"], 3)) for h in
                  c.get("/api/search", params={"q": "宜兴市"}).json()["results"]]
        check("同一坐标只留一条", len(coords) == len(set(coords)), str(coords))

        # 4) 短输入与缺参数
        check("单字输入被拒 422", c.get("/api/search", params={"q": "昆"}).status_code == 422)
        check("缺 q 被拒 422", c.get("/api/search").status_code == 422)

        # 5) 缓存复用：同词第二次不再增加出网计数
        calls_before = client.calls
        c.get("/api/search", params={"q": "昆山市"})
        c.get("/api/search", params={"q": "昆山市"})
        check("重复搜索命中缓存（零新增出网）", client.calls == calls_before,
              f"calls {calls_before} -> {client.calls}")

        # 6) 搜索结果能直接当体检原点：坐标可用，label/archetype 由档案层落库
        #    （演示模式的结果刻意不入库，所以这里验"报告出得来、中心点就是搜到的那个点"，
        #     label/archetype 的入库往返在 test_persistence_offline 里覆盖）
        hit = c.get("/api/search", params={"q": "宜川县"}).json()["results"][0]
        job = c.post("/api/analysis", json={"lat": hit["lat"], "lng": hit["lng"],
                                           "label": hit["name"],
                                           "archetype": f"区划搜索（{hit['level']}，取区划中心点）"}).json()
        done = None
        for _ in range(20):
            d = c.get(f"/api/analysis/{job['job_id']}").json()
            if d["status"] != "running":
                done = d
                break
        res = (done or {}).get("result") or {}
        center = res.get("center") or {}
        check("搜索点可作为体检原点并出得了报告",
              done and done.get("status") == "done" and len(res.get("rings", {}).get("15") or []) >= 12,
              str((done or {}).get("status")))
        check("体检中心点就是搜到的那个点",
              abs(center.get("lat", 0) - hit["lat"]) < 1e-6 and abs(center.get("lng", 0) - hit["lng"]) < 1e-6,
              f"{center.get('lat')},{center.get('lng')} vs {hit['lat']},{hit['lng']}")

    print("\n== 地点搜索自测：" + ("全部通过 ==" if ok else "存在失败 =="))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        for d in (f"data/cache_searchtest_{TAG}", f"data/db_searchtest_{TAG}"):
            shutil.rmtree(Path(d), ignore_errors=True)
    sys.exit(code)
