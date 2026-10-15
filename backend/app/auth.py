"""多用户认证与账户数据层（MySQL 8）。

只在 AUTH_ENABLED=1 时启用；关掉就是原来的单机版，交付与录屏不受影响。

三条刻意的取舍：
1. **不用 JWT**：会话落库 + HttpOnly 签名 cookie，可随时吊销（登出/改密/删号即刻失效），
   不引入"token 到期前无法撤销"这个坑。
2. **口令只存 argon2id 哈希**（argon2-cffi 默认参数），错误口令抛 VerifyMismatchError，
   由调用方统一回"邮箱或口令不正确"，不暴露账号是否存在。
3. **用户 AK 默认不落库**：只有用户主动勾选"加密保存到账户"才写，用 MASTER_KEY 做 AES-GCM
   （Fernet）加密；接口任何响应只回掩码，日志与异常信息里永不出现明文密钥。
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pymysql
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_ph = PasswordHasher()

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      email         VARCHAR(190)  NOT NULL,
      pass_hash     VARCHAR(255)  NOT NULL,
      nickname      VARCHAR(60)   NOT NULL DEFAULT '',
      created_at    DATETIME(3)   NOT NULL,
      last_login_at DATETIME(3)   NULL,
      bmap_ak_enc   TEXT          NULL,
      bmap_sk_enc   TEXT          NULL,
      llm_key_enc   TEXT          NULL,
      llm_base_url  VARCHAR(200)  NOT NULL DEFAULT '',
      llm_model     VARCHAR(80)   NOT NULL DEFAULT '',
      store_keys    TINYINT(1)    NOT NULL DEFAULT 0,
      status        VARCHAR(16)   NOT NULL DEFAULT 'active',
      UNIQUE KEY uk_users_email (email)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash CHAR(64)      NOT NULL PRIMARY KEY,
      user_id    BIGINT UNSIGNED NOT NULL,
      created_at DATETIME(3)   NOT NULL,
      expires_at DATETIME(3)   NOT NULL,
      ip         VARCHAR(45)   NOT NULL DEFAULT '',
      KEY idx_sessions_user (user_id),
      CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quotas (
      user_id    BIGINT UNSIGNED NOT NULL,
      day        DATE          NOT NULL,
      analyses   INT UNSIGNED  NOT NULL DEFAULT 0,
      agent_turns INT UNSIGNED NOT NULL DEFAULT 0,
      PRIMARY KEY (user_id, day),
      CONSTRAINT fk_quotas_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS rate_log (
      bucket     VARCHAR(60)     NOT NULL,
      window_key VARCHAR(32)     NOT NULL,
      hits       INT UNSIGNED    NOT NULL DEFAULT 0,
      PRIMARY KEY (bucket, window_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


class AuthError(Exception):
    """认证层的统一失败原因，HTTP 层只把它翻译成状态码，不透传内部细节。"""


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def connect() -> pymysql.connections.Connection:
    """每次调用一条短连接：本服务是 IO 密集 + 低频写，不值得上连接池复杂度。"""
    return pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_password, database=settings.db_name, charset="utf8mb4",
        autocommit=True, cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8, read_timeout=20, write_timeout=20,
    )


def init_schema() -> None:
    with connect() as c, c.cursor() as cur:
        for ddl in SCHEMA:
            cur.execute(ddl)


# ---------- 口令与加密 ----------

def hash_password(raw: str) -> str:
    if len(raw) < 8:
        raise AuthError("口令至少 8 位")
    return _ph.hash(raw)


def verify_password(stored: str, raw: str) -> bool:
    try:
        return _ph.verify(stored, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _fernet() -> Fernet:
    key = settings.master_key.strip()
    if not key:
        raise AuthError("MASTER_KEY 未配置，无法加密保存密钥")
    # 允许把任意口令当主密钥：先 SHA-256 再 base64，得到合法的 32 字节 Fernet key
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(raw: str) -> str:
    return _fernet().encrypt(raw.encode("utf-8")).decode("ascii")


def decrypt_secret(blob: str) -> str:
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise AuthError("主密钥已变更，无法解密已保存的密钥") from e


def mask(value: str) -> str:
    if not value:
        return ""
    return f"{value[:4]}…{value[-4:]}" if len(value) > 10 else "····"


# ---------- 会话 ----------

def new_session_token() -> tuple:
    """返回 (明文 token, 落库用的 SHA-256 哈希)。库里永远拿不到可用的 token。"""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sign(token: str) -> str:
    if not settings.secret_key.strip():
        raise AuthError("SECRET_KEY 未配置，无法签发会话 cookie")
    mac = hmac.new(settings.secret_key.encode("utf-8"), token.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return f"{token}.{mac}"


def unsign(signed: str) -> str | None:
    """校验 cookie 的 HMAC，防的是"随便填个字符串就来试"，真正的授权仍然查库。"""
    if not signed or "." not in signed:
        return None
    token, mac = signed.rsplit(".", 1)
    want = hmac.new(settings.secret_key.encode("utf-8"), token.encode("utf-8"),
                    hashlib.sha256).hexdigest()
    return token if hmac.compare_digest(mac, want) else None


def csrf_bound_to(signed_session: str) -> str:
    """把 CSRF 令牌绑到具体会话：跨站脚本拿不到别人的 sid，也就伪造不出配对值。

    双提交校验：这个值同时写在可读 cookie 与请求头里，两者相等才放行。
    """
    return hmac.new(settings.secret_key.encode("utf-8"),
                    ("csrf:" + (signed_session or "")).encode("utf-8"), hashlib.sha256).hexdigest()


def csrf_random() -> str:
    return secrets.token_urlsafe(24)


def csrf_ok(signed_session: str, cookie_val: str, header_val: str) -> bool:
    if not header_val:
        return False
    expected = csrf_bound_to(signed_session) if signed_session else cookie_val
    # 未登录（注册/登录本身）时没有会话可绑，退化为"cookie 与 header 必须一致"
    if not signed_session and not cookie_val:
        return False
    return hmac.compare_digest(expected or "", header_val) and bool(expected)


# ---------- 账号读写 ----------

@dataclass
class Principal:
    id: int
    email: str
    nickname: str
    store_keys: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None

    @property
    def cache_subdir(self) -> str:
        # 缓存按用户分目录：全局共享会把 A 的真实结果直接喂给 B
        return f"u{self.id}"

    def public(self) -> dict:
        """给前端看的自身资料；不含口令、也不含密钥明文（只有掩码归 /api/config 管）。"""
        return {"id": self.id, "email": self.email, "nickname": self.nickname,
                "store_keys": self.store_keys,
                "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
                "last_login_at": (self.last_login_at.strftime("%Y-%m-%d %H:%M")
                                  if self.last_login_at else None)}


def create_user(email: str, raw_pass: str, nickname: str = "", ip: str = "") -> int:
    email = email.strip().lower()
    if "@" not in email or len(email) > 190:
        raise AuthError("邮箱格式不正确")
    if not _allow("reg", ip or "na", limit=settings.max_reg_per_ip_day, window_sec=86400):
        raise AuthError("该网络今日注册次数已达上限，明天再试或换个网络")
    with connect() as c, c.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO users(email, pass_hash, nickname, created_at) VALUES (%s,%s,%s,%s)",
                (email, hash_password(raw_pass), nickname.strip()[:60], _now()))
        except pymysql.err.IntegrityError:
            raise AuthError("该邮箱已注册，请直接登录（本站不做邮箱验证，也没有找回通道）") from None
        return int(cur.lastrowid)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE email=%s AND status='active'",
                    (email.strip().lower(),))
        return cur.fetchone()


def touch_login(user_id: int) -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute("UPDATE users SET last_login_at=%s WHERE id=%s", (_now(), user_id))


def set_nickname(user_id: int, nickname: str) -> str:
    """只改显示名。邮箱是登录凭据、口令走独立流程，都不在这里动。"""
    clean = (nickname or "").strip()[:60]
    with connect() as c, c.cursor() as cur:
        cur.execute("UPDATE users SET nickname=%s WHERE id=%s", (clean, user_id))
    return clean


def open_session(user_id: int, ip: str = "") -> str:
    token, token_hash = new_session_token()
    expires = _now() + timedelta(days=settings.session_days)
    with connect() as c, c.cursor() as cur:
        cur.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,ip) "
                    "VALUES (%s,%s,%s,%s,%s)", (token_hash, user_id, _now(), expires, ip[:45]))
        cur.execute("DELETE FROM sessions WHERE expires_at < %s", (_now(),))   # 顺手回收
    return sign(token)


def resolve_session(signed: str) -> Principal | None:
    token = unsign(signed or "")
    if not token:
        return None
    with connect() as c, c.cursor() as cur:
        cur.execute(
            "SELECT u.id, u.email, u.nickname, u.store_keys, u.created_at, u.last_login_at "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash=%s AND s.expires_at > %s AND u.status='active'",
            (hashlib.sha256(token.encode("utf-8")).hexdigest(), _now()))
        row = cur.fetchone()
    return Principal(**row) if row else None


def close_session(signed: str) -> None:
    token = unsign(signed or "")
    if not token:
        return
    with connect() as c, c.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE token_hash=%s",
                    (hashlib.sha256(token.encode("utf-8")).hexdigest(),))


# ---------- 护栏：限速与预算 ----------

def _window(window_sec: int) -> str:
    return str(int(time.time() // window_sec))


def _allow(bucket: str, key: str, limit: int, window_sec: int) -> bool:
    """粗粒度令牌桶：一行计数 + 固定窗口。够用、无额外依赖、重启不复活额度。"""
    with connect() as c, c.cursor() as cur:
        cur.execute("INSERT INTO rate_log(bucket,window_key,hits) VALUES (%s,%s,1) "
                    "ON DUPLICATE KEY UPDATE hits = hits + 1", (bucket, _window(window_sec) + ":" + key))
        cur.execute("SELECT hits FROM rate_log WHERE bucket=%s AND window_key=%s",
                    (bucket, _window(window_sec) + ":" + key))
        row = cur.fetchone()
    return not row or int(row["hits"]) <= limit


def allow_register(ip: str) -> bool:
    return _allow("reg", ip or "na", settings.max_reg_per_ip_day, 86400)


def allow_login(ip: str) -> bool:
    # 每 IP 每 10 分钟 20 次尝试，挡字典爆破（比返回"账号锁定"更难被探测）
    return _allow("login", ip or "na", 20, 600)


def budget_left(user_id: int) -> int:
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT analyses FROM quotas WHERE user_id=%s AND day=%s",
                    (user_id, _now().date().isoformat()))
        row = cur.fetchone()
    return settings.daily_analysis_budget - int(row["analyses"] if row else 0)


def consume_analysis(user_id: int) -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute("INSERT INTO quotas(user_id,day,analyses) VALUES (%s,%s,1) "
                    "ON DUPLICATE KEY UPDATE analyses = analyses + 1",
                    (user_id, _now().date().isoformat()))


def consume_agent(user_id: int) -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute("INSERT INTO quotas(user_id,day,agent_turns) VALUES (%s,%s,1) "
                    "ON DUPLICATE KEY UPDATE agent_turns = agent_turns + 1",
                    (user_id, _now().date().isoformat()))


# ---------- 用户密钥（可选落库） ----------

def save_user_secrets(user_id: int, ak: str = "", sk: str = "", llm_key: str = "",
                      base_url: str = "", model: str = "", store: bool = True) -> None:
    """store=False 表示"只留在浏览器"：清掉库里的密文，不写入任何密钥。

    注意取消保存时**不带新值也要落库**——否则用户点了"不再保存账户密钥"，
    密文还留在行里，等于没关掉。
    """
    fields, vals = [], []
    for col, raw in (("bmap_ak_enc", ak), ("bmap_sk_enc", sk), ("llm_key_enc", llm_key)):
        if raw:
            fields.append(f"{col}=%s")
            vals.append(encrypt_secret(raw))
        elif not store:
            fields.append(f"{col}=NULL")
    for col, raw in (("llm_base_url", base_url), ("llm_model", model)):
        if raw:
            fields.append(f"{col}=%s")
            vals.append(raw)
    fields.append("store_keys=%s")
    vals.append(1 if store else 0)
    vals.append(user_id)
    with connect() as c, c.cursor() as cur:
        cur.execute(f"UPDATE users SET {','.join(fields)} WHERE id=%s", vals)


def user_secrets(user_id: int) -> dict:
    """给前端/后端用的明文视图，仅在服务端进程内存里存在；对外只暴露 mask()。"""
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT bmap_ak_enc, bmap_sk_enc, llm_key_enc, llm_base_url, llm_model, store_keys "
                    "FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone() or {}
    out = {"store_keys": bool(row.get("store_keys"))}
    for key, col in (("bmap_ak", "bmap_ak_enc"), ("bmap_sk", "bmap_sk_enc"), ("llm_key", "llm_key_enc")):
        blob = row.get(col)
        out[key] = decrypt_secret(blob) if blob else ""
    out["llm_base_url"] = row.get("llm_base_url") or ""
    out["llm_model"] = row.get("llm_model") or ""
    return out


def client_ip(request) -> str:
    """优先取反代写上的真实来源 IP，退化到 socket。只用于限速，不参与授权判断。"""
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (xff or (request.client.host if request.client else ""))[:45]


def healthcheck() -> dict:
    """连不上数据库时让 /api/health 说实话，不要装作能用。"""
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM users")
            return {"ok": True, "users": int(cur.fetchone()["n"])}
    except Exception as e:  # noqa: BLE001 - 健康检查要把任意失败原因原样报出来
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


os.environ.setdefault("TZ", "Asia/Shanghai")
