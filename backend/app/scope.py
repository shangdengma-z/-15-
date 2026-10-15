"""请求级的"当前用户"作用域：缓存与产物按用户分目录。

为什么必须做：磁盘缓存原本是全局共享的，`data/cache/real_..._<md5>.json` 只按请求参数命中。
单机没问题，多用户上线后 A 检索到的真实结果会直接喂给 B——B 一分钱不花拿到 A 的数据，
而两个账号各自的百度配额、可见范围本应互不相干。

AUTH_ENABLED=0（默认，也是录屏与交付用的那条路径）时作用域为空，路径与现在完全一致，
已有的预热缓存和 0.1~0.5s 回放不受影响。
"""

from contextvars import ContextVar
from pathlib import Path

_SCOPE: ContextVar[str] = ContextVar("cache_scope", default="")
_MADE: set = set()
_CREDS: ContextVar[dict] = ContextVar("creds", default={})


def set_scope(subdir: str) -> None:
    _SCOPE.set(subdir or "")


def get_scope() -> str:
    return _SCOPE.get()


def set_creds(ak: str = "", sk: str = "", llm_key: str = "", llm_base: str = "",
              llm_model: str = "", mock: bool | None = None) -> None:
    """当前请求使用的凭据覆盖。来源只有两种：请求头（用户浏览器里存的）或该用户勾选保存的密文。

    绝不写回 settings：那是进程级全局，多用户下等于 A 的 key 给 B 用。
    """
    _CREDS.set({"ak": ak, "sk": sk, "llm_key": llm_key, "llm_base": llm_base,
                "llm_model": llm_model, "mock": mock})


def cred(name: str, fallback: str = "") -> str:
    return (_CREDS.get().get(name) or "").strip() or fallback


def mock_mode(settings_default: bool) -> bool:
    override = _CREDS.get().get("mock")
    return settings_default if override is None else bool(override)


def snapshot_creds() -> dict:
    """当前请求凭据的浅拷贝，供中间件在"请求头覆盖账户配置"时保留原值。"""
    return dict(_CREDS.get() or {})


def has_creds() -> bool:
    d = _CREDS.get()
    return bool(d and any(v for k, v in d.items() if k != "mock" and v))


def under(base: Path) -> Path:
    """把 base 换成"当前用户的那个子目录"，空作用域时原样返回。"""
    sub = _SCOPE.get()
    if not sub:
        return base
    target = base / sub
    if target not in _MADE:
        target.mkdir(parents=True, exist_ok=True)
        _MADE.add(target)
    return target
