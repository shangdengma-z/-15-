"""多用户认证与账户数据层（MySQL 8 或 SQLite，由 DB_BACKEND 决定）。

只在 AUTH_ENABLED=1 时启用；关掉就是原来的免登录单机版，交付与录屏不受影响。
DB_BACKEND=sqlite 时账号四张表落在 `BMAP_DB_PATH` 那个文件里（与档案同库），
没装 MySQL 的机器也能注册、登录、按人隔离——单机演示要的就是这条路径。
DB_BACKEND=mysql 是公网多用户的推荐形态（连接与并发更稳，见 deploy/）。

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
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

try:
    import pymysql
except ModuleNotFoundError:      # 单机 SQLite 形态不装驱动也要能起服务
    pymysql = None
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

# SQLite 版：单机（DB_BACKEND=sqlite）用，表与约束语义与 MySQL 版一致。
# DATETIME 在 SQLite 里统一存 'YYYY-MM-DD HH:MM:SS' 文本——这个格式字典序==时间序，
# 所以 expires_at > ? 这类比较照原样成立，不需要额外的转换。
SCHEMA_SQLITE = [
    """
    CREATE TABLE IF NOT EXISTS users (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      email         TEXT    NOT NULL UNIQUE,
      pass_hash     TEXT    NOT NULL,
      nickname      TEXT    NOT NULL DEFAULT '',
      created_at    TEXT    NOT NULL,
      last_login_at TEXT,
      bmap_ak_enc   TEXT,
      bmap_sk_enc   TEXT,
      llm_key_enc   TEXT,
      llm_base_url  TEXT    NOT NULL DEFAULT '',
      llm_model     TEXT    NOT NULL DEFAULT '',
      store_keys    INTEGER NOT NULL DEFAULT 0,
      status        TEXT    NOT NULL DEFAULT 'active'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT    PRIMARY KEY,
      user_id    INTEGER NOT NULL,
      created_at TEXT    NOT NULL,
      expires_at TEXT    NOT NULL,
      ip         TEXT    NOT NULL DEFAULT '',
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quotas (
      user_id     INTEGER NOT NULL,
      day         TEXT    NOT NULL,
      analyses    INTEGER NOT NULL DEFAULT 0,
      agent_turns INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (user_id, day),
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rate_log (
      bucket     TEXT    NOT NULL,
      window_key TEXT    NOT NULL,
      hits       INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (bucket, window_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
]


class AuthError(Exception):
    """认证层的统一失败原因，HTTP 层只把它翻译成状态码，不透传内部细节。"""


# 唯一约束冲突在两种库里是不同异常，上层只关心"这个邮箱已经用过了"
_DUPLICATE_ERRORS = tuple(
    [sqlite3.IntegrityError] + ([pymysql.err.IntegrityError] if pymysql else []))


class _SqliteCursor:
    """让 SQLite 光标长得像 pymysql 的 DictCursor：`%s` 占位、按列名取值。

    这样上层 SQL 只有一份——两份手写 SQL 迟早会跑出"MySQL 上加了列、SQLite 上忘加"那种差异，
    而单机演示走的正是 SQLite 这条路。
    """

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, args=()):
        return self._cur.execute(sql.replace("%s", "?"),
                                 [a.strftime("%Y-%m-%d %H:%M:%S") if isinstance(a, datetime) else a
                                  for a in (args or ())])

    def _row(self, r):
        return None if r is None else {k: r[k] for k in r.keys()}

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    def close(self):
        self._cur.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _SqliteConn:
    """账号层只用 connect() 的三个动作：cursor()、上下文提交、关闭。"""

    def __init__(self, path: str):
        # 目录可能还不存在（首次启动、或测试里的临时库）：与档案层同一套做法
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(path, timeout=15)
        self._c.row_factory = sqlite3.Row           # 让光标能按列名取，长得像 DictCursor
        self._c.execute("PRAGMA foreign_keys=ON")   # 删号要能级联清掉会话/配额

    def cursor(self):
        return _SqliteCursor(self._c.cursor())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._c.commit()
        else:
            self._c.rollback()
        self._c.close()
        return False


def is_sqlite() -> bool:
    return settings.db_backend != "mysql"


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def connect():
    """每次调用一条短连接：本服务是 IO 密集 + 低频写，不值得上连接池复杂度。"""
    if is_sqlite():
        return _SqliteConn(settings.db_path)
    if pymysql is None:
        raise AuthError("DB_BACKEND=mysql 但没装 pymysql：pip install pymysql（或改用 DB_BACKEND=sqlite）")
    return pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=settings.db_user,
        password=settings.db_password, database=settings.db_name, charset="utf8mb4",
        autocommit=True, cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8, read_timeout=20, write_timeout=20,
    )


def init_schema() -> None:
    with connect() as c, c.cursor() as cur:
        for ddl in (SCHEMA_SQLITE if is_sqlite() else SCHEMA):
            cur.execute(ddl)


def _bump(table: str, cols: tuple, col: str, vals: tuple) -> None:
    """计数 +1 的幂等自增：MySQL 用 ON DUPLICATE KEY，SQLite 用 ON CONFLICT DO UPDATE。

    写成 helper 而不是三份相似 SQL：配额与限速三处逻辑必须完全一致，
    漏一处就会出现"MySQL 上额度生效、SQLite 上能无限刷"。
    调用方给的 cols 顺序即主键列顺序（SQLite 的 ON CONFLICT 需要显式点名）。
    """
    n = len(cols)
    sql = f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join(['%s'] * n)})"
    sql += (f" ON CONFLICT({','.join(cols[:n - 1])}) DO UPDATE SET {col} = {col} + 1"
            if is_sqlite() else f" ON DUPLICATE KEY UPDATE {col} = {col} + 1")
    with connect() as c, c.cursor() as cur:
        cur.execute(sql, tuple(vals[:n]))


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
    # MySQL 给 datetime 对象，SQLite 给 'YYYY-MM-DD HH:MM:SS' 文本（见 SCHEMA_SQLITE 注释）
    created_at: datetime | str | None = None
    last_login_at: datetime | str | None = None

    @property
    def cache_subdir(self) -> str:
        # 缓存按用户分目录：全局共享会把 A 的真实结果直接喂给 B
        return f"u{self.id}"

    @staticmethod
    def _stamp(v: datetime | str | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            return v[:16]
        return v.strftime("%Y-%m-%d %H:%M")

    def public(self) -> dict:
        """给前端看的自身资料；不含口令、也不含密钥明文（只有掩码归 /api/config 管）。"""
        return {"id": self.id, "email": self.email, "nickname": self.nickname,
                "store_keys": bool(self.store_keys),
                "created_at": self._stamp(self.created_at),
                "last_login_at": self._stamp(self.last_login_at)}


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
        except _DUPLICATE_ERRORS:
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
    wk = _window(window_sec) + ":" + key
    _bump("rate_log", ("bucket", "window_key", "hits"), "hits", (bucket, wk, 1))
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT hits FROM rate_log WHERE bucket=%s AND window_key=%s", (bucket, wk))
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
    _bump("quotas", ("user_id", "day", "analyses"), "analyses",
          (user_id, _now().date().isoformat(), 1))


def consume_agent(user_id: int) -> None:
    _bump("quotas", ("user_id", "day", "agent_turns"), "agent_turns",
          (user_id, _now().date().isoformat(), 1))


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


def user_count() -> int:
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users")
        return int(cur.fetchone()["n"])


def healthcheck() -> dict:
    """连不上数据库时让 /api/health 说实话，不要装作能用。"""
    try:
        with connect() as c, c.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM users")
            return {"ok": True, "users": int(cur.fetchone()["n"])}
    except Exception as e:  # noqa: BLE001 - 健康检查要把任意失败原因原样报出来
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


os.environ.setdefault("TZ", "Asia/Shanghai")
