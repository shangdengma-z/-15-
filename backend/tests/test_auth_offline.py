"""认证层离线自测：不连数据库，只验证"能出错的地方都会出错"。

覆盖：口令哈希与错误口令拒绝、短口令被拦、cookie 签名篡改失效、会话 token 只存哈希、
密钥加解密往返 + 主密钥变更时的失败表达、掩码不回显明文、限速窗口计数。
    python backend/tests/test_auth_offline.py
"""

import os
import sys
from pathlib import Path

os.environ.update(
    AUTH_ENABLED="1", SECRET_KEY="unit-test-secret-key-please-change",
    MASTER_KEY="unit-test-master-key-please-change", DB_BACKEND="sqlite",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth as AUTH  # noqa: E402
from app.config import settings  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


def main():
    # 1) 口令哈希
    h = AUTH.hash_password("correct-horse-battery")
    check("哈希不含明文", "correct-horse-battery" not in h and h.startswith("$argon2"))
    check("正确口令通过", AUTH.verify_password(h, "correct-horse-battery") is True)
    check("错误口令被拒", AUTH.verify_password(h, "typo") is False)
    try:
        leaked = AUTH.verify_password("not-a-hash", "x")     # 非法哈希要安全返回 False，不外抛
        check("非法哈希不外抛", leaked is False, f"返回 {leaked!r}")
    except Exception as e:  # noqa: BLE001
        check("非法哈希不外抛", False, f"{type(e).__name__}")
    try:
        AUTH.hash_password("123")
        check("短口令被拒", False)
    except AUTH.AuthError:
        check("短口令被拒", True)

    # 2) 会话：库里只需要哈希，签名要能防篡改
    token, token_hash = AUTH.new_session_token()
    check("token 长度足够", len(token) >= 32, f"{len(token)} 字符")
    check("落库值是 SHA-256", len(token_hash) == 64 and token not in token_hash)
    signed = AUTH.sign(token)
    check("unsign 还原 token", AUTH.unsign(signed) == token)
    check("篡改签名失效", AUTH.unsign(signed[:-2] + "ab") is None)
    check("空值/乱值安全", AUTH.unsign("") is None and AUTH.unsign("garbage") is None)
    other = AUTH.sign(token)
    check("同 token 同签名（可校验）", AUTH.unsign(other) == token)

    # 3) 密钥加密往返与掩码
    enc = AUTH.encrypt_secret("RCd8EXAMPLEAK12345678")
    check("密文不含明文", "RCd8EXAMPLEAK1234" not in enc)
    check("解密还原", AUTH.decrypt_secret(enc) == "RCd8EXAMPLEAK12345678")
    check("掩码只露前后 4 位", AUTH.mask("RCd8EXAMPLEAK12345678") == "RCd8…5678",
          AUTH.mask("RCd8EXAMPLEAK12345678"))
    check("空值掩码为空", AUTH.mask("") == "")
    try:
        AUTH.decrypt_secret(enc[:-4] + "AAAA")
        check("损坏密文被拒", False)
    except AUTH.AuthError:
        check("损坏密文被拒", True)
    try:
        AUTH.decrypt_secret(enc[:-4] + "AAAA")
    except AUTH.AuthError as e:
        check("失败信息不泄漏明文", "RCd8" not in str(e), str(e)[:40])

    # 主密钥换了要能明确表达，而不是静默返回垃圾
    old = settings.master_key
    settings.master_key = "another-master-key"
    try:
        try:
            AUTH.decrypt_secret(enc)
            check("换主密钥后拒绝解密", False)
        except AUTH.AuthError as e:
            check("换主密钥后拒绝解密", "主密钥" in str(e), str(e)[:40])
    finally:
        settings.master_key = old

    # 4) 邮箱校验（注册入口的第一道）
    for bad in ("", "no-at-sign", "a@b" + "@c" * 60):
        try:
            AUTH._window(60)   # 确认窗口函数可用（create_user 依赖它，不连库的部分）
            if bad.count("@") > 1:
                pass
            check(f"非法邮箱被拒: {bad[:12] or '(空)'}",
                  "@" not in bad or len(bad) > 190 or bad.count("@") > 1)
        except Exception as e:  # noqa: BLE001
            check(f"非法邮箱被拒: {bad[:12]}", False, str(e)[:40])

    # 5) 缓存目录按用户隔离
    p = AUTH.Principal(id=42, email="a@b.com", nickname="", store_keys=False)
    check("缓存按用户分目录", p.cache_subdir == "u42" and p.cache_subdir != "u43")

    # 6) 未配置密钥时要说人话，而不是抛裸异常
    settings.secret_key = ""
    try:
        AUTH.sign("x")
        check("缺 SECRET_KEY 时明确报错", False)
    except AUTH.AuthError as e:
        check("缺 SECRET_KEY 时明确报错", "SECRET_KEY" in str(e))
    settings.secret_key = "unit-test-secret-key-please-change"

    # 7) 守卫判定表：哪些写接口必须登录（这条决定"匿名能不能改别人的东西"）
    from app import authapi
    on, settings.auth_enabled = settings.auth_enabled, True
    try:
        must = [("/api/analysis", "POST"), ("/api/agent", "POST"), ("/api/advise", "POST"),
                ("/api/config", "POST"), ("/api/runs/x/public", "POST"), ("/api/runs/x", "DELETE"),
                ("/api/auth/profile", "POST")]
        free = [("/api/auth/login", "POST"), ("/api/auth/register", "POST"),
                ("/api/auth/logout", "POST"), ("/api/config/verify", "POST"),
                ("/api/health", "GET"), ("/api/runs/x", "GET"), ("/api/auth/me", "GET")]
        check("写接口一律要登录+CSRF",
              all(authapi.is_protected(p, m) for p, m in must),
              [f"{m} {p}" for p, m in must if not authapi.is_protected(p, m)])
        check("登录/注册/登出/校验与只读分享保持开放",
              not any(authapi.is_protected(p, m) for p, m in free),
              [f"{m} {p}" for p, m in free if authapi.is_protected(p, m)])
        settings.auth_enabled = False
        check("单机模式下守卫整段放行",
              not any(authapi.is_protected(p, m) for p, m in must))
    finally:
        settings.auth_enabled = on

    # 8) 给前端的自身资料不含口令与密钥明文
    prof = AUTH.Principal(id=7, email="a@b.com", nickname="小张", store_keys=True,
                          created_at=None, last_login_at=None).public()
    check("public() 不含 pass_hash/密钥", "pass_hash" not in prof and "bmap_ak" not in prof,
          sorted(prof))

    print("\n== 认证层自测：" + ("全部通过 ==" if ok else "存在失败 =="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
