"""账号层 SQLite 自测：不装 MySQL、不配任何密钥，也能把注册/登录/隔离/额度/密文往返跑一遍。

单机启用账号（AUTH_ENABLED=1 + DB_BACKEND=sqlite）走的就是这条路径，所以这份进 CI：
它盯的是"两种库共用一份 SQL"这个约定有没有被破坏——占位符、upsert、级联、时间格式
都在这条路上最容易分叉，而分叉的后果是"MySQL 上正常、单机演示当场报错"。
    .venv/Scripts/python.exe backend/tests/test_auth_sqlite.py
"""

import os
import shutil
import sys
import time
from pathlib import Path

TAG = str(int(time.time() * 1000))[-8:]
os.environ.update(
    AUTH_ENABLED="1", DB_BACKEND="sqlite", BMAP_MOCK="1", BMAP_QPS="1000",
    BMAP_CACHE_DIR=f"data/cache_acctest_{TAG}",
    BMAP_DB_PATH=f"data/db_acctest_{TAG}/lifestyle.db",
    SECRET_KEY="sqlite-selftest-secret-key", MASTER_KEY="sqlite-selftest-master-key",
    MAX_REG_PER_IP_DAY="5",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, db  # noqa: E402
from app.config import settings  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


def main() -> int:
    print(f"== 账号层 SQLite 自测（{settings.db_path}）==")
    check("走的是 SQLite 分支", auth.is_sqlite() and settings.db_backend == "sqlite")

    auth.init_schema()
    auth.init_schema()      # 幂等：重启服务不该报"表已存在"
    with auth.connect() as c, c.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r["name"] for r in cur.fetchall()}
    check("四张表就位", {"users", "sessions", "quotas", "rate_log"} <= tables, str(sorted(tables)))
    check("healthcheck 读得到用户数", auth.healthcheck()["ok"] and auth.user_count() == 0)

    # 1) 注册与口令
    email, pw = f"sa+{TAG}@example.test", "sqlite-pass-1"
    uid = auth.create_user(email, pw, "单机账号", ip="10.1.1.1")
    check("注册返回自增 id", uid > 0, f"user_id={uid}")
    check("第二个账号计数累加", auth.user_count() == 1)
    try:
        auth.create_user(email.upper(), pw, "重复", ip="10.1.1.2")
        check("重复邮箱被拒", False)
    except auth.AuthError as e:
        check("重复邮箱被拒（大小写归一也算重复）", "已注册" in str(e), str(e)[:24])
    row = auth.get_user_by_email(email)
    check("库里只有 argon2 哈希", row["pass_hash"].startswith("$argon2") and pw not in str(row))
    check("错误口令被拒", auth.verify_password(row["pass_hash"], "typo") is False)
    check("正确口令通过", auth.verify_password(row["pass_hash"], pw) is True)

    # 2) 会话：只存哈希、能吊销、时间格式两边一致
    signed = auth.open_session(uid, ip="10.1.1.1")
    p = auth.resolve_session(signed)
    check("会话可解析", p is not None and p.id == uid and p.cache_subdir == f"u{uid}")
    check("篡改 cookie 被拒", auth.resolve_session(signed[:-2] + "zz") is None)
    check("SQLite 侧时间是可读文本", isinstance(p.created_at, str), str(type(p.created_at)))
    prof = p.public()
    check("public() 掩掉敏感字段且时间可读",
          "pass_hash" not in prof and prof["created_at"] and len(prof["created_at"]) == 16,
          str(prof["created_at"]))
    with auth.connect() as c, c.cursor() as cur:
        cur.execute("SELECT token_hash, expires_at FROM sessions WHERE user_id=%s", (uid,))
        s = cur.fetchone()
    check("库里存的是 SHA-256 不是 token",
          len(s["token_hash"]) == 64 and s["token_hash"].split(".")[0] != signed.split(".")[0],
          s["expires_at"])
    auth.close_session(signed)
    check("登出后会话立即失效", auth.resolve_session(signed) is None)

    # 3) 用量台账：只记账、不拦请求（每人每日上限已去掉），天然是 per-user
    def quota(user_id, col):
        with auth.connect() as c, c.cursor() as cur:
            cur.execute(f"SELECT {col} AS v FROM quotas WHERE user_id=%s AND day=%s",
                        (user_id, auth._now().date().isoformat()))
            row = cur.fetchone()
        return int(row["v"]) if row and row["v"] else 0

    auth.consume_analysis(uid)
    check("提交记入台账", quota(uid, "analyses") == 1, str(quota(uid, "analyses")))
    auth.consume_agent(uid)
    check("智能体次数单独记", quota(uid, "agent_turns") == 1 and quota(uid, "analyses") == 1)
    check("每日上限这项配置已彻底去掉", not hasattr(settings, "daily_analysis_budget"))

    # 4) 限速窗口
    check("首次放行", auth._allow("reg", "10.9.9.9", 2, 86400) is True)
    auth._allow("reg", "10.9.9.9", 2, 86400)
    check("超窗口上限被拦", auth._allow("reg", "10.9.9.9", 2, 86400) is False)

    # 5) 密钥：勾选才入库，取消即删，文件里搜不到明文
    secret = "SQLITEAKEXAMPLE1234567890"
    auth.save_user_secrets(uid, ak=secret, llm_key="llm-secret-value-1", store=True)
    check("解得回明文", auth.user_secrets(uid)["bmap_ak"] == secret)
    check("store_keys 生效", auth.user_secrets(uid)["store_keys"] is True)
    raw = Path(settings.db_path).read_bytes()
    check("库文件里搜不到明文密钥", secret.encode() not in raw and b"llm-secret-value-1" not in raw)
    auth.save_user_secrets(uid, store=False)
    check("取消保存即清空密文", auth.user_secrets(uid)["bmap_ak"] == "" and
          auth.user_secrets(uid)["store_keys"] is False)

    # 6) 昵称
    check("改名生效", auth.set_nickname(uid, "  改名后的单机  ") == "改名后的单机"
          and auth.get_user_by_email(email)["nickname"] == "改名后的单机")

    # 7) 老档案认领：单机启用账号不该让历史"消失"
    def legacy(label, lat, lng):
        return {"center": {"lat": lat, "lng": lng}, "health_score": 66,
                "blindspots": [], "coverage": {},
                "meta": {"mock": False, "api_calls": 2, "cache_hits": 0, "quota_units": 2,
                         "elapsed_sec": 0.2, "degraded": False}}, label
    # 两个不同坐标：同一 scene_key 的第二条会就地覆盖第一条（档案层的既有语义），那是另一回事
    for res, label in (legacy("无主旧档案A", 40.0703, 116.3306), legacy("无主旧档案B", 39.9403, 116.4038)):
        db.save_run(res, label, "")
    check("启用前有两条无主档案", db.legacy_run_count() == 2, str(db.legacy_run_count()))
    n = db.claim_legacy_runs(uid)
    mine = [r["label"] for r in db.list_runs(20, uid, True)]
    check("第一个账号认领到本机历史", n == 2 and sorted(mine) == ["无主旧档案A", "无主旧档案B"], str(mine))
    other = auth.create_user(f"sb+{TAG}@example.test", pw, "后来者", ip="10.1.1.3")
    db.claim_legacy_runs(other)
    check("后来者看不到别人的档案", db.list_runs(20, other, True) == [] and
          db.legacy_run_count() == 0)

    # 8) 级联：删账号要带走会话与配额（PRAGMA foreign_keys 必须在连接上开着）
    with auth.connect() as c, c.cursor() as cur:
        cur.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,ip) "
                    "VALUES ('deadbeef',%s,'2026-01-01 00:00:00','2099-01-01 00:00:00','')", (uid,))
        cur.execute("SELECT COUNT(*) n FROM sessions WHERE user_id=%s", (uid,))
        before = cur.fetchone()["n"]
        cur.execute("DELETE FROM users WHERE id=%s", (uid,))
        cur.execute("SELECT COUNT(*) n FROM sessions WHERE user_id=%s", (uid,))
        after = cur.fetchone()["n"]
    check("删账号级联清掉会话", before >= 1 and after == 0, f"{before} -> {after}")

    print("\n== 账号层 SQLite 自测：" + ("全部通过 ==" if ok else "存在失败 =="))
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    for d in (f"data/cache_acctest_{TAG}", f"data/db_acctest_{TAG}"):
        shutil.rmtree(Path(d), ignore_errors=True)
    sys.exit(code)
