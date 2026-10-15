"""认证 HTTP 层：注册/登录/登出/我是谁 + 守卫中间件需要的判定函数。

安全默认值（都不依赖"前端记得做"）：
- 会话 cookie `HttpOnly + SameSite=Lax`，走 HTTPS（或 X-Forwarded-Proto=https）时加 `Secure`；
- 所有非 GET 的业务接口都要过 CSRF 双提交；
- 失败信息一律模糊：不告诉调用方"该邮箱不存在"还是"口令错了"；
- `AUTH_ENABLED=0` 时这里的所有判定直接放行，单机演示与交付路径行为完全不变。
"""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import auth, scope
from .config import settings

SID_COOKIE = "sid"
CSRF_COOKIE = "csrf"
CSRF_HEADER = "x-csrf-token"

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(..., max_length=190)
    password: str = Field(..., min_length=1, max_length=128)
    nickname: str = Field("", max_length=60)


def secure(request: Request) -> bool:
    """本机 http 调试时 False，经 nginx TLS 后 True——决定 cookie 的 Secure 位。"""
    return request.headers.get("x-forwarded-proto", "").lower() == "https" or request.url.scheme == "https"


def client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (xff or (request.client.host if request.client else ""))[:45]


def _issue(request: Request, response: Response, signed: str) -> None:
    # request 显式传参，不用全局变量：异步服务里两个请求交叠时，
    # 全局变量会把 A 的 Secure 位决定给 B。
    csrf = auth.csrf_bound_to(signed)
    opts = {"samesite": "lax", "secure": secure(request),
            "max_age": settings.session_days * 86400, "path": "/"}
    response.set_cookie(SID_COOKIE, signed, httponly=True, **opts)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **opts)


def principal(request: Request) -> auth.Principal | None:
    if not settings.auth_enabled:
        return None
    return auth.resolve_session(request.cookies.get(SID_COOKIE, ""))


def require_principal(request: Request) -> auth.Principal:
    p = principal(request)
    if p is None:
        raise HTTPException(401, "请先登录")
    return p


def apply_scope(p: auth.Principal | None) -> None:
    """把缓存作用域切到该用户目录；未登录时留在全局（演示模式与静态资源）。"""
    scope.set_scope(p.cache_subdir if p else "")


def is_protected(path: str, method: str) -> bool:
    """需要登录的业务接口。放行：健康检查、社区预设、认证本身、只测不写的密钥校验、
    以及匿名可看的分享报告（GET 在上面已经整批放行）。

    POST /api/config **不在**放行名单里：它会改账户下的密钥去向，必须登录且过 CSRF。
    """
    if method == "GET" or not settings.auth_enabled:
        return False
    open_paths = ("/api/auth/", "/api/config/verify")
    if path.startswith(open_paths) or path in ("/api/health", "/api/communities"):
        return False
    return path.startswith("/api/")


@router.post("/register", status_code=201)
def register(body: Credentials, request: Request, response: Response):
    if not settings.auth_enabled:
        raise HTTPException(400, "当前为单机模式，无需注册（AUTH_ENABLED=0）")
    if not auth.allow_login(client_ip(request)):
        raise HTTPException(429, "操作过于频繁，请稍后再试")
    try:
        uid = auth.create_user(body.email, body.password, body.nickname, client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(400, str(e)) from None
    signed = auth.open_session(uid, client_ip(request))
    _issue(request, response, signed)
    return {"id": uid, "email": body.email.strip().lower()}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    if not settings.auth_enabled:
        raise HTTPException(400, "当前为单机模式，无需登录（AUTH_ENABLED=0）")
    if not auth.allow_login(client_ip(request)):
        raise HTTPException(429, "尝试次数过多，请 10 分钟后再试")
    row = auth.get_user_by_email(body.email)
    # 用户不存在与口令错误回同一句话，不给枚举账号留口子
    if not row or not auth.verify_password(row["pass_hash"], body.password):
        raise HTTPException(401, "邮箱或口令不正确")
    auth.touch_login(row["id"])
    signed = auth.open_session(row["id"], client_ip(request))
    _issue(request, response, signed)
    return {"id": row["id"], "email": row["email"], "nickname": row["nickname"]}


@router.post("/logout")
def logout(request: Request, response: Response):
    auth.close_session(request.cookies.get(SID_COOKIE, ""))
    response.delete_cookie(SID_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    if not settings.auth_enabled:
        return {"auth_enabled": False, "user": None, "budget_left": None}
    p = principal(request)
    return {"auth_enabled": True,
            "user": None if p is None else {"id": p.id, "email": p.email, "nickname": p.nickname,
                                            "store_keys": p.store_keys},
            "budget_left": None if p is None else auth.budget_left(p.id)}
