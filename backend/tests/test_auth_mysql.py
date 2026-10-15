"""MySQL 集成自测：连真库跑一遍账号生命周期，跑完把自己建的数据清干净。

与 `test_auth_offline.py` 的分工：那份不连库、CI 必跑；这份验证的是"SQL 能落、约束生效、
外键级联可用、限速与预算真的计数"。没配 DB_PASSWORD 时直接跳过（返回 0），免得 CI 无 MySQL 变红。
    .venv/Scripts/python.exe backend/tests/test_auth_mysql.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AUTH_ENABLED", "1")
os.environ.setdefault("DB_BACKEND", "mysql")

from app import auth as AUTH  # noqa: E402
from app.config import settings  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


def main() -> int:
    if not (settings.db_password and settings.secret_key and settings.master_key):
        print("SKIP  未在 .env 填 DB_PASSWORD / SECRET_KEY / MASTER_KEY，跳过连库自测")
        return 0

    conn = AUTH.connect()
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE() AS db, VERSION() AS v")
        row = cur.fetchone()
    check("连上 MySQL", row["db"] == settings.db_name, f"{row['db']} / {row['v']}")

    AUTH.init_schema()
    AUTH.init_schema()          # 幂等：重启服务不该报"表已存在"
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        tables = {list(r.values())[0] for r in cur.fetchall()}
    check("四张表就位", {"users", "sessions", "quotas", "rate_log"} <= tables, str(sorted(tables)))

    email = f"ci+{int(time.time())}@example.test"
    pw = "integration-secret-1"
    # 注册限速是按 IP 按天计数的，自测用 203.0.113.0/24（文档专用网段）里的随机地址，
    # 否则反复跑会把 127.0.0.1 的当日额度吃光，第二次就再也测不到注册路径
    ip = f"203.0.113.{int(time.time()) % 251 + 1}"
    uid = AUTH.create_user(email, pw, "自测账号", ip=ip)
    check("注册成功并返回自增 id", uid > 0, f"user_id={uid}")

    try:
        AUTH.create_user(email, pw, "重复", ip=ip)
        check("重复邮箱被拒", False)
    except AUTH.AuthError as e:
        check("重复邮箱被拒", "已注册" in str(e), str(e)[:30])

    check("口令哈希不落明文", AUTH.get_user_by_email(email)["pass_hash"].startswith("$argon2"))
    check("错误口令被拒", AUTH.verify_password(AUTH.get_user_by_email(email)["pass_hash"], "wrong") is False)
    check("正确口令通过", AUTH.verify_password(AUTH.get_user_by_email(email)["pass_hash"], pw) is True)

    signed = AUTH.open_session(uid, ip=ip)
    p = AUTH.resolve_session(signed)
    check("会话可解析且身份正确", p is not None and p.id == uid and p.cache_subdir == f"u{uid}")
    check("篡改 cookie 被拒", AUTH.resolve_session(signed[:-2] + "zz") is None)

    with conn.cursor() as cur:
        cur.execute("SELECT token_hash FROM sessions WHERE user_id=%s", (uid,))
        stored = cur.fetchone()["token_hash"]
    check("库里只存哈希不存 token", len(stored) == 64 and stored.split(".")[0] != signed.split(".")[0])

    AUTH.close_session(signed)
    check("登出后会话立即失效", AUTH.resolve_session(signed) is None)

    left0 = AUTH.budget_left(uid)
    AUTH.consume_analysis(uid)
    check("每日预算按次扣减", AUTH.budget_left(uid) == left0 - 1, f"{left0} -> {AUTH.budget_left(uid)}")

    before = AUTH.healthcheck()
    check("healthcheck 能数到用户", before["ok"] and before["users"] >= 1, str(before))

    blob = AUTH.encrypt_secret("AKEXAMPLE1234567890abcd")
    AUTH.save_user_secrets(uid, ak="AKEXAMPLE1234567890abcd", store=True)
    with conn.cursor() as cur:
        cur.execute("SELECT bmap_ak_enc FROM users WHERE id=%s", (uid,))
        col = cur.fetchone()["bmap_ak_enc"]
    check("明文不进数据库", "AKEXAMPLE1234567890abcd" not in col)
    back = AUTH.user_secrets(uid)
    check("密文可解回明文", back["bmap_ak"] == "AKEXAMPLE1234567890abcd" and back["store_keys"] is True)
    AUTH.save_user_secrets(uid, store=False)
    check("取消保存即清空密文", AUTH.user_secrets(uid)["bmap_ak"] == "")

    hit = AUTH._allow("ci_bucket", "k", 2, 60)
    AUTH._allow("ci_bucket", "k", 2, 60)
    AUTH._allow("ci_bucket", "k", 2, 60)
    third = AUTH._allow("ci_bucket", "k", 2, 60)
    check("限速窗口会计数并拦下", hit is True and third is False)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM rate_log WHERE bucket='ci_bucket'")
        cur.execute("DELETE FROM rate_log WHERE window_key LIKE %s", (f"%:{ip}",))
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))          # 会话/配额靠外键级联清理
        cur.execute("SELECT COUNT(*) AS n FROM sessions WHERE user_id=%s", (uid,))
        left_sessions = int(cur.fetchone()["n"])
    check("自测数据已清理且级联生效", left_sessions == 0)

    print("\n== MySQL 集成自测：" + ("全部通过 ==" if ok else "存在失败 =="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
