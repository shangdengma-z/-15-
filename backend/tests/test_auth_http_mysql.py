"""多用户 HTTP 集成自测：真 MySQL + TestClient，验证"隔离是不是真的成立"。

不碰百度（BMAP_MOCK=1）、不碰你的正式档案（SQLite 走临时文件），
但用户/会话/配额/限速都落在真实 MySQL 里，跑完自己清理。
    .venv/Scripts/python.exe backend/tests/test_auth_http_mysql.py
"""

import os
import sys
import time
import uuid
from pathlib import Path

TAG = uuid.uuid4().hex[:8]
os.environ.update(
    AUTH_ENABLED="1", BMAP_MOCK="1", BMAP_QPS="500", BMAP_CONCURRENCY="8",
    BMAP_CACHE_DIR=f"data/cache_httptest_{TAG}", BMAP_DB_PATH=f"data/db_httptest_{TAG}/lifestyle.db",
    LLM_API_KEY="", DAILY_ANALYSIS_BUDGET="2", MAX_REG_PER_IP_DAY="50",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

ok = True
emails = []


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


def register(client, email, pw="testing-pass-1"):
    emails.append(email)
    r = client.post("/api/auth/register", json={"email": email, "password": pw, "nickname": email.split("@")[0]},
                    headers={"X-Forwarded-For": "10.0.0.1"})
    assert r.status_code == 201, r.text
    return r


def csrf_of(client):
    return client.cookies.get("csrf")


def raw_column(user_id, col):
    """直接查库取原始列值，用来证明"入库的是密文而不是明文"。col 只由本文件传入。"""
    with auth.connect() as c, c.cursor() as cur:
        cur.execute(f"SELECT {col} AS v FROM users WHERE id=%s", (user_id,))
        return (cur.fetchone() or {}).get("v") or ""


def main() -> int:
    if not (settings.db_password and settings.secret_key and settings.master_key):
        print("SKIP  未配置 MySQL 连接，跳过 HTTP 多用户自测")
        return 0
    auth.init_schema()

    with TestClient(app) as anon:
        # 1) 匿名：业务写接口必须 401，读接口不得回别人的档案
        check("匿名 POST /api/analysis 被拒 401",
              anon.post("/api/analysis", json={"lat": 40.07, "lng": 116.33}).status_code == 401)
        check("匿名 GET /api/runs 只回公开（此处为空）", anon.get("/api/runs").json() == [])
        check("匿名 GET /api/stats 不泄漏全站数字", anon.get("/api/stats").json()["communities"] == 0)
        check("健康检查仍可匿名访问", anon.get("/api/health").status_code == 200)

        # 2) 注册 + 会话 + CSRF
        a = register(anon, f"a.{TAG}@example.test")
        check("注册返回 201 且下发 sid", a.cookies.get("sid") is not None)
        check("会话 cookie 是 HttpOnly", "HttpOnly" in a.headers.get("set-cookie", ""))
        me = anon.get("/api/auth/me").json()
        check("me 认出当前用户", me["user"] and me["user"]["email"] == f"a.{TAG}@example.test")
        # 注册当次就是登录：个人中心里"最近登录"不该是空的
        check("注册即登录，last_login 已记录", bool(me["user"]["last_login_at"]),
              str(me["user"]["created_at"]))
        check("me 报剩余预算", me["budget_left"] == settings.daily_analysis_budget, str(me["budget_left"]))

        with TestClient(app) as b:
            register(b, f"b.{TAG}@example.test")
            uid_a = auth.get_user_by_email(f"a.{TAG}@example.test")["id"]
            uid_b = auth.get_user_by_email(f"b.{TAG}@example.test")["id"]
            check("两个账号 id 不同", uid_a != uid_b)

            # 直接落两条档案（等价于各跑一次体检，但不烧配额）
            fake = {"center": {"lat": 40.0703, "lng": 116.3306}, "health_score": 50,
                    "blindspots": [], "coverage": {}, "meta": {"mock": False, "api_calls": 1,
                                                               "cache_hits": 0, "quota_units": 1,
                                                               "elapsed_sec": 0.1, "degraded": False}}
            id_a = db.save_run(fake, "A的社区", "", uid_a)
            id_b = db.save_run(fake, "B的社区", "", uid_b)
            check("同社区两人各存一份", id_a != id_b, f"{id_a} / {id_b}")

            la = [r["label"] for r in anon.get("/api/runs").json()]
            lb = [r["label"] for r in b.get("/api/runs").json()]
            check("A 只看得到自己的档案", la == ["A的社区"], str(la))
            check("B 只看得到自己的档案", lb == ["B的社区"], str(lb))
            check("B 读 A 的档案 = 404", b.get(f"/api/runs/{id_a}").status_code == 404)
            check("A 能读自己的档案", anon.get(f"/api/runs/{id_a}").status_code == 200)

            # 3) 公开分享：A 勾公开后，B 与匿名都能读，但 B 不能删
            pr = anon.post(f"/api/runs/{id_a}/public", json={"public": True},
                           headers={"X-CSRF-Token": csrf_of(anon),
                                    "Origin": "http://testserver"})
            check("归属者可设公开", pr.status_code == 200, pr.text[:60])
            check("B 现在可读该分享档案", b.get(f"/api/runs/{id_a}").status_code == 200)
            check("B 删不掉 A 的档案", b.delete(f"/api/runs/{id_a}",
                                              headers={"X-CSRF-Token": csrf_of(b)}).status_code == 403
                  or b.delete(f"/api/runs/{id_a}", headers={"X-CSRF-Token": csrf_of(b)}).json()["deleted"] is False)
            check("匿名也能读公开档案", TestClient(app).get(f"/api/runs/{id_a}").status_code == 200)

            # 4) CSRF：有会话但缺配对头 -> 拒
            no_csrf = anon.post("/api/runs/%s/public" % id_a, json={"public": False})
            check("缺 CSRF 头的写操作被拒", no_csrf.status_code == 403, str(no_csrf.status_code))

            # 5) 每日预算
            for _ in range(settings.daily_analysis_budget):
                r = anon.post("/api/analysis", json={"lat": 40.07, "lng": 116.33},
                              headers={"X-CSRF-Token": csrf_of(anon)})
                check("预算内提交成功 200", r.status_code == 200, str(r.status_code))
            over = anon.post("/api/analysis", json={"lat": 40.08, "lng": 116.34},
                             headers={"X-CSRF-Token": csrf_of(anon)})
            check("超出每日预算被拒 429", over.status_code == 429, str(over.status_code))
            check("B 的预算不受 A 影响",
                  b.get("/api/auth/me").json()["budget_left"] == settings.daily_analysis_budget)

            # 6) 密钥去向：默认只在浏览器里，勾选才加密入库（verify=false 免发真实探测）
            g = anon.get("/api/config", headers={"X-Bmap-AK": "AKBROWSERONLY1234"})
            body = g.text
            cfg = g.json()
            check("配置视图是账户作用域且带余额",
                  cfg.get("scope") == "user" and cfg["account"]["budget_left"] is not None, str(cfg.get("scope")))
            check("浏览器带进来的 AK 只标来源、不回明文",
                  cfg["bmap"]["ak_source"] == "browser" and "AKBROWSERONLY1234" not in body,
                  cfg["bmap"]["ak_masked"])
            check("没勾选就什么都没入库", auth.user_secrets(uid_a)["bmap_ak"] == "")
            sv = anon.post("/api/config", json={"server_ak": "AKSTOREDTOME9999", "verify": False,
                                                 "store_keys": True},
                           headers={"X-CSRF-Token": csrf_of(anon)})
            check("勾选后掩码回显且标记已入库",
                  sv.status_code == 200 and sv.json().get("stored") is True
                  and sv.json()["bmap"]["ak_masked"].startswith("AKST"), sv.text[:60])
            check("入库的是密文", sv.json()["bmap"]["ak_source"] == "store"
                  and "AKSTOREDTOME9999" not in str(raw_column(uid_a, "bmap_ak_enc")))
            check("解得回明文且来源显示为账户",
                  auth.user_secrets(uid_a)["bmap_ak"] == "AKSTOREDTOME9999"
                  and anon.get("/api/config").json()["bmap"]["ak_source"] == "store")
            off = anon.post("/api/config", json={"verify": False, "store_keys": False},
                            headers={"X-CSRF-Token": csrf_of(anon)})
            check("取消勾选即把密文删干净",
                  off.status_code == 200 and off.json()["stored"] is False
                  and auth.user_secrets(uid_a)["bmap_ak"] == "")
            check("多用户下模式与限流改不动",
                  anon.post("/api/config", json={"mode": "mock", "verify": False},
                            headers={"X-CSRF-Token": csrf_of(anon)}).status_code == 403)
            # 演示头是请求级的：本自测跑在 BMAP_MOCK=1 上，先把部署值假装成真实路网才好对比
            settings.mock = False
            on = anon.get("/api/config", headers={"X-Demo-Mode": "1"}).json()["mock"]
            off = anon.get("/api/config").json()["mock"]
            check("演示头只改本次请求，不改部署配置",
                  on is True and off is False and settings.mock is False, f"{on}/{off}")
            settings.mock = True    # 还原，后面的单机回归仍按合成路网跑

            # 6.6) 会话 cookie 是"浏览器会话级"：关掉浏览器就得重新登录，所以不写 max-age/expires
            li = anon.post("/api/auth/login", json={"email": f"a.{TAG}@example.test",
                                                    "password": "testing-pass-1"})
            sid = next((h for h in li.headers.get_list("set-cookie") if h.startswith("sid=")), "")
            check("重新登录成功且会话 cookie 不带 max-age/expires",
                  li.status_code == 200 and bool(sid) and "max-age" not in sid.lower()
                  and "expires" not in sid.lower(), sid[:70])
            check("会话 cookie 仍是 HttpOnly + SameSite=Lax",
                  "httponly" in sid.lower() and "samesite=lax" in sid.lower())

            # 6.7) 个人中心：昵称能改、字段齐全，写操作既要求登录也要求 CSRF
            prof = anon.post("/api/auth/profile", json={"nickname": "改名后的我"},
                             headers={"X-CSRF-Token": csrf_of(anon)})
            who = anon.get("/api/auth/me").json()
            check("改名生效并回显", prof.status_code == 200
                  and prof.json()["nickname"] == "改名后的我"
                  and who["user"]["nickname"] == "改名后的我", prof.text[:40])
            check("me 带注册时间与当日额度上限",
                  bool(who["user"]["created_at"])
                  and who["budget_total"] == settings.daily_analysis_budget,
                  f'{who["user"]["created_at"]} / {who["budget_total"]}')
            check("空昵称被拒", anon.post("/api/auth/profile", json={"nickname": "   "},
                                        headers={"X-CSRF-Token": csrf_of(anon)}).status_code == 422)
            check("改资料缺 CSRF 一样被拒",
                  anon.post("/api/auth/profile", json={"nickname": "越权"}).status_code == 403)

            # 7) 登出即吊销
            lo = anon.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_of(anon)})
            check("登出成功", lo.status_code == 200)
            after = anon.get("/api/auth/me").json()
            rows = anon.get("/api/runs").json()
            check("登出后旧 cookie 立即失效",
                  after["user"] is None and [r["label"] for r in rows] == ["A的社区"],
                  f'user={after["user"]} rows={[r["label"] for r in rows]}')
            check("失效后仍能看到的只有那条被设为公开的档案",
                  all(r["public"] == 1 for r in rows))

    # 8) 单机回归：AUTH_ENABLED=0 时一切照旧
    settings.auth_enabled = False
    with TestClient(app) as solo:
        rows = solo.get("/api/runs").json()
        check("单机模式不过滤（两条都在）", len(rows) == 2, str(len(rows)))
        check("单机模式匿名可提交分析",
              solo.post("/api/analysis", json={"lat": 40.07, "lng": 116.33}).status_code == 200)
        check("单机模式 stats 统计全部", solo.get("/api/stats").json()["communities"] == 2)

    # 清理：只删自己造的测试账号与临时文件
    conn = auth.connect()
    with conn.cursor() as cur:
        for e in emails:
            cur.execute("DELETE FROM users WHERE email=%s", (e,))
        cur.execute("DELETE FROM rate_log WHERE bucket IN ('reg', 'login') "
                    "AND window_key LIKE %s", (f"%10.0.0.1%",))
    import shutil
    for d in (f"data/cache_httptest_{TAG}", f"data/db_httptest_{TAG}"):
        shutil.rmtree(Path(d), ignore_errors=True)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM users WHERE email LIKE %s", (f"%{TAG}@",))
        check("测试账号已清理", int(cur.fetchone()["n"]) == 0)

    print("\n== 多用户 HTTP 自测：" + ("全部通过 ==" if ok else "存在失败 =="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
