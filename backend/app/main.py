"""API 服务入口：POST 提交体检任务 -> 轮询进度 -> 获取报告 JSON；结果落 SQLite 供回放与对比。

sqlite 是同步阻塞库，所有读写经 asyncio.to_thread 丢到线程池，避免拖住事件循环。
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, authapi, db, scope
from .advisor import BIZ_TYPES, advise
from .agent import run_agent
from .analysis import run_analysis
from .baidu_client import BmapError, client, probe_ak
from .config import PRESET_COMMUNITIES, settings
from .isochrone import dest_on_ray
from .llm import llm
from .report import to_markdown

app = FastAPI(title="15分钟生活圈体检助手", version="0.2.0")
app.include_router(authapi.router)

# 单机开发时前端在 :5173、后端在 :8000，必须放开跨域；
# 上线（AUTH_ENABLED=1）走 nginx 同源，CORS_ORIGINS 填自己的域名即可收窄。
_ALLOWED = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"]
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED, allow_methods=["*"],
                   allow_headers=["*"], allow_credentials="*" not in _ALLOWED)

JOBS: dict = {}
AGENT_JOBS: dict = {}

# 开了多用户就把账号四张表准备好（幂等）。连不上库也不能让进程起不来——
# 那种情况由 /api/health 的 auth 字段如实报出来，比启动即崩好查。
if settings.auth_enabled:
    try:
        auth.init_schema()
    except Exception as e:  # noqa: BLE001 - 启动期任何失败都要继续 serving 并报真话
        print(f"[warn] 账号库初始化未完成（{settings.db_backend}）：{type(e).__name__}: {str(e)[:120]}")

# 全局并发闸：一次体检是 180+ 次上游请求 + 8 秒等待，2C2G 上不设闸会被十几个并发打满
JOB_SLOTS = asyncio.Semaphore(max(1, settings.job_slots))



class AnalysisRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    label: str = Field("", max_length=60)
    archetype: str = Field("", max_length=40)
    persist: bool = True


class AgentRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    history: list = Field(default_factory=list)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)


class AdviseRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    biz: str = Field("pharmacy", max_length=20)
    top: int = Field(3, ge=1, le=5)


@app.middleware("http")
async def metering_headers(request: Request, call_next):
    """把计量写进响应头：`curl -i` 就能自证调了多少次、折了多少配额，不靠嘴说。"""
    t0 = time.monotonic()
    calls0, quota0, hits0 = client.calls, client.quota_units, client.cache_hits
    response = await call_next(request)
    response.headers["X-Elapsed-Ms"] = str(round((time.monotonic() - t0) * 1000))
    response.headers["X-Api-Calls"] = str(client.calls - calls0)
    response.headers["X-Quota-Units"] = str(client.quota_units - quota0)
    response.headers["X-Cache-Hits"] = str(client.cache_hits - hits0)
    return response


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """解析会话 → 设定请求级缓存与凭据 → 必要时拦未登录与跨站请求。

    AUTH_ENABLED=0 时整段直接放行，单机演示与交付路径的行为、缓存命中都保持不变。
    """
    if not settings.auth_enabled:
        return await call_next(request)
    uid = None
    principal = auth.resolve_session(request.cookies.get(authapi.SID_COOKIE, ""))
    if principal:
        uid = principal.id
        scope.set_scope(principal.cache_subdir)
        creds = auth.user_secrets(principal.id) if principal.store_keys else {}
        scope.set_creds(ak=creds.get("bmap_ak", ""), sk=creds.get("bmap_sk", ""),
                        llm_key=creds.get("llm_key", ""), llm_base=creds.get("llm_base_url", ""),
                        llm_model=creds.get("llm_model", ""))
        # 额度只管"会烧配重的写接口"。用一句 budget<=0 拦住全部写操作是错的：
        # 那样当天跑满的人连"保存自己的 AK""取消公开分享"都点不动，只能干等明天。
        heavy = request.method != "GET" and request.url.path.startswith(("/api/analysis", "/api/agent"))
        if heavy and auth.budget_left(principal.id) <= 0:
            return JSONResponse(status_code=429, content={"detail": "今日分析额度已用完，明天再来或缩短演示"})
    # 请求头里的 key 优先于账户里保存的：默认密钥只待在浏览器
    h = request.headers
    if h.get("x-bmap-ak") or h.get("x-llm-key") or h.get("x-demo-mode"):
        prev = scope.snapshot_creds()
        scope.set_creds(ak=h.get("x-bmap-ak", prev.get("ak", "")),
                        sk=h.get("x-bmap-sk", prev.get("sk", "")),
                        llm_key=h.get("x-llm-key", prev.get("llm_key", "")),
                        llm_base=h.get("x-llm-base", prev.get("llm_base", "")),
                        llm_model=h.get("x-llm-model", prev.get("llm_model", "")),
                        mock=(h.get("x-demo-mode") == "1") if h.get("x-demo-mode") else prev.get("mock"))
    request.state.uid = uid
    request.state.principal = principal
    if authapi.is_protected(request.url.path, request.method):
        if principal is None:
            return JSONResponse(status_code=401, content={"detail": "请先登录"})
        same_site = (h.get("sec-fetch-site") or "").lower()
        if not same_site == "same-origin" and not auth.csrf_ok(
                request.cookies.get(authapi.SID_COOKIE, ""),
                request.cookies.get(authapi.CSRF_COOKIE, ""),
                h.get(authapi.CSRF_HEADER, "")):
            return JSONResponse(status_code=403, content={"detail": "CSRF 校验未通过，请刷新页面重试"})
    return await call_next(request)


@app.get("/env.js")
async def env_js():
    """运行时把浏览器端地图 AK 交给前端：换 key 只改 `.env` 重启容器，不必重新构建镜像。

    浏览器类型的 AK 本来就是公开物（任何人查看页面源码都能看到），真正的约束是
    控制台里的 Referer 白名单；所以这里不鉴权，但也不能让它被 CDN 缓存到换 key 之后。
    """
    body = f"window.__CIRCLE15__={json.dumps({'bmapAk': settings.browser_ak})};"
    return PlainTextResponse(body, media_type="application/javascript",
                             headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health():
    stats = await asyncio.to_thread(db.stats)
    out = {"ok": True, "mock": settings.mock, "ak_configured": bool(settings.server_ak),
           "llm_configured": llm.enabled, "llm_model": settings.llm_model if llm.enabled else None,
           "quota_exhausted": client.quota_exhausted, "db_path": settings.db_path,
           "communities_stored": stats["communities"]}
    if settings.auth_enabled:   # 账号库到底连上没有，要说实话（sqlite/mysql 同一字段）
        out["auth"] = await asyncio.to_thread(auth.healthcheck)
        out["auth_backend"] = settings.db_backend
    return out


class ConfigRequest(BaseModel):
    mode: str | None = Field(None, max_length=8)
    server_ak: str | None = Field(None, max_length=120)
    server_sk: str | None = Field(None, max_length=120)
    llm_api_key: str | None = Field(None, max_length=200)
    llm_base_url: str | None = Field(None, max_length=200)
    llm_model: str | None = Field(None, max_length=60)
    qps: float | None = Field(None, gt=0, le=100)
    concurrency: int | None = Field(None, ge=1, le=32)
    verify: bool = True      # 默认校验：填了密钥就真发一次探测请求，不用等体检时才报错
    # 多用户模式下密钥的去向：不传 = 只留在这个浏览器；true = 加密存入本账户；false = 清掉账户里的密文
    store_keys: bool | None = None


# 只记录「这个字段是运行时填的还是来自 .env」，密钥本身不留副本、不写文件、不进日志
_RUNTIME_SOURCE: dict = {}


def _mask(v: str) -> str:
    if not v:
        return ""
    return f"{v[:4]}…{v[-4:]}" if len(v) > 10 else "····"


def _config_view() -> dict:
    def src(attr: str) -> str:
        if _RUNTIME_SOURCE.get(attr):
            return "runtime"
        return "env" if getattr(settings, attr) else "empty"
    return {"mode": "mock" if settings.mock else "real", "mock": settings.mock,
            "qps": settings.qps, "concurrency": settings.concurrency,
            "qps_source": src("qps"), "quota_exhausted": client.quota_exhausted,
            "bmap": {"ak_masked": _mask(settings.server_ak), "ak_source": src("server_ak"),
                     "sk_set": bool(settings.server_sk), "sk_source": src("server_sk")},
            "llm": {"configured": llm.enabled, "model": settings.llm_model,
                    "base_url": settings.llm_base_url, "key_masked": _mask(settings.llm_api_key),
                    "key_source": src("llm_api_key")}}


def _user_config_view(request: Request, stored: bool | None = None,
                      creds: dict | None = None) -> dict:
    """多用户模式下的接入配置：数据来自当前请求的凭据（浏览器带来的或账户里加密存的）。

    不能读 settings —— 那是进程级全局，上线时要么为空、要么属于部署者，
    把它显示给所有用户等于告诉 B "你的 AK 是别人的"。掩码后的值只回给本人，够用。

    stored/creds 显式传入是给"刚保存完"的那次响应用的：本请求解析会话时改动还没写进库，
    不覆盖就会回一句"未配置"，用户看不出自己刚才是保存成功了。
    """
    p = getattr(request.state, "principal", None)
    c = creds or scope.snapshot_creds()
    stored = bool(p and p.store_keys) if stored is None else stored

    def src(v: str, fallback: bool) -> str:
        if v:
            return "store" if stored else "browser"
        # 请求里没带钥匙、但部署级 .env 有：出网会走那份兜底，面板不能说"未配置"。
        # 掩码仍然留空——那是别人的 key 的前后四位，没必要发给每个登录用户。
        return "env" if fallback else "empty"

    ak, llm_key = c.get("ak") or "", c.get("llm_key") or ""
    mode = "mock" if scope.mock_mode(settings.mock) else "real"
    return {"mode": mode, "mock": mode == "mock", "auth": True, "scope": "user",
            "qps": settings.qps, "concurrency": settings.concurrency, "qps_source": "env",
            "quota_exhausted": client.quota_exhausted,
            "account": None if p is None else {"id": p.id, "email": p.email,
                                               "nickname": p.nickname, "store_keys": p.store_keys,
                                               "budget_left": auth.budget_left(p.id)},
            "bmap": {"ak_masked": _mask(ak), "ak_source": src(ak, bool(settings.server_ak)),
                     "sk_set": bool(c.get("sk")) or bool(settings.server_sk),
                     "sk_source": src(c.get("sk") or "", bool(settings.server_sk))},
            "llm": {"configured": bool(llm_key) or bool(settings.llm_api_key),
                    "model": c.get("llm_model") or settings.llm_model,
                    "base_url": c.get("llm_base") or settings.llm_base_url,
                    "key_masked": _mask(llm_key),
                    "key_source": src(llm_key, bool(settings.llm_api_key))}}


@app.get("/api/config")
async def get_config(request: Request):
    """当前接入配置（密钥一律掩码返回，只告诉你哪一项已配置、来自哪里）。"""
    if settings.auth_enabled:
        return _user_config_view(request)
    return _config_view()


@app.post("/api/config")
async def set_config(request: Request, req: ConfigRequest):
    """运行时切换演示/应用模式、替换百度 AK 与大模型 key。

    刻意只改内存：进程重启就回到 .env 的值，密钥不落盘、不写日志、不回显明文。
    填了密钥就默认真发一次探测请求校验（verify=true），校验不过**不落地**，
    避免"随便填一个也能保存、等体检跑 40 秒才说 AK 无效"。
    """
    if req.mode is not None and req.mode not in ("mock", "real"):
        raise HTTPException(400, "mode 只能是 mock 或 real")

    new_ak = req.server_ak.strip() if req.server_ak is not None else None
    new_llm_key = req.llm_api_key.strip() if req.llm_api_key is not None else None
    new_base = req.llm_base_url.strip() if req.llm_base_url is not None else None
    new_model = req.llm_model.strip() if req.llm_model is not None else None
    checks: dict = {}

    if req.verify and new_ak:
        ok, detail = await probe_ak(new_ak, (req.server_sk or settings.server_sk).strip())
        checks["bmap"] = {"ok": ok, "detail": detail}
        if not ok:
            raise HTTPException(422, f"百度 AK 校验未通过：{detail}")
    if req.verify and new_llm_key:
        ok, detail = await llm.probe(new_base or settings.llm_base_url, new_llm_key,
                                     new_model or settings.llm_model)
        checks["llm"] = {"ok": ok, "detail": detail}
        if not ok:
            raise HTTPException(422, f"大模型配置校验未通过：{detail}")

    if settings.auth_enabled:
        # settings 是进程级全局：多用户下写它等于把 A 的 key 借给 B 用。
        # 因此这里只做"校验（上面已完成）+ 按用户意愿决定去向"，
        # 数据模式与限流属部署级配置，只能由 .env 决定。
        uid = getattr(request.state, "uid", None)
        if uid is None:
            raise HTTPException(401, "请先登录")
        if req.mode is not None or req.qps is not None or req.concurrency is not None:
            raise HTTPException(403, "多用户模式下数据模式与限流是部署级配置，只能在 .env 里改")
        if not (new_ak or new_llm_key or new_base or new_model) and req.store_keys is None:
            raise HTTPException(422, "没有要保存的内容")
        # 默认不入库：明文只存在这个浏览器的 localStorage 里，随请求头带上。
        # 用户主动勾"加密保存到本账户"（store_keys=true）才交给服务端用 MASTER_KEY 加密存列；
        # store_keys=false 是明确关掉：把列清空，否则"取消保存"只是句空话。
        store = bool(req.store_keys)
        keep_ak = (new_ak or "") if store else ""
        keep_sk = ((req.server_sk or "").strip()) if store else ""
        keep_llm = (new_llm_key or "") if store else ""

        def _persist():
            auth.save_user_secrets(uid, ak=keep_ak, sk=keep_sk, llm_key=keep_llm,
                                   base_url=new_base or "", model=new_model or "", store=store)
            # 刚写进库的那份才是这次要显示的状态：本请求解析会话时它还不存在
            return auth.user_secrets(uid) if store else None

        row = await asyncio.to_thread(_persist)
        view = _user_config_view(request, stored=store, creds=None if row is None else {
            "ak": row["bmap_ak"], "sk": row["bmap_sk"], "llm_key": row["llm_key"],
            "llm_base": row["llm_base_url"], "llm_model": row["llm_model"]})
        view["stored"] = store
        if req.verify:
            view["verified"] = checks
        return view

    if req.mode is not None:
        ak = new_ak or settings.server_ak
        if req.mode == "real" and not ak:
            raise HTTPException(400, "应用模式需要百度服务端 AK：请先填写 AK 再切换")
        if req.mode == "real" and req.verify and not new_ak and not _RUNTIME_SOURCE.get("server_ak"):
            # 用 .env 里既有 AK 切换：也确认它还能用，否则切过去就是死路
            ok, detail = await probe_ak(ak, settings.server_sk)
            checks["bmap"] = {"ok": ok, "detail": detail}
            if not ok:
                raise HTTPException(422, f"当前 AK 不可用，未切换：{detail}")
        settings.mock = req.mode == "mock"
        if settings.mock:
            client.quota_exhausted = False   # 演示模式不烧真实配额，熔断无意义
    for attr in ("server_ak", "server_sk", "llm_api_key", "llm_base_url", "llm_model"):
        v = getattr(req, attr)
        if v is None:
            continue
        v = v.strip()
        setattr(settings, attr, v)
        _RUNTIME_SOURCE[attr] = bool(v)
    # 配额档位换了不用重启进程：重建令牌桶与并发闸即可
    if req.qps is not None:
        settings.qps = float(req.qps)
        _RUNTIME_SOURCE["qps"] = True
    if req.concurrency is not None:
        settings.concurrency = int(req.concurrency)
        _RUNTIME_SOURCE["concurrency"] = True
    if req.qps is not None or req.concurrency is not None:
        client.reconfigure()
    view = _config_view()
    if checks:
        view["verified"] = checks
    return view


@app.post("/api/config/verify")
async def verify_config(request: Request, req: ConfigRequest):
    """只测不落地：面板上的「先测一下」按钮走这里。"""
    out = {}
    if req.server_ak:
        ok, detail = await probe_ak(req.server_ak.strip(), (req.server_sk or "").strip())
        out["bmap"] = {"ok": ok, "detail": detail}
    if req.llm_api_key:
        ok, detail = await llm.probe(req.llm_base_url or settings.llm_base_url,
                                     req.llm_api_key.strip(), req.llm_model or settings.llm_model)
        out["llm"] = {"ok": ok, "detail": detail}
    if not out:
        raise HTTPException(422, "没有要测试的密钥")
    out["ok"] = all(v["ok"] for v in out.values() if isinstance(v, dict))
    return out


@app.get("/api/communities")
async def communities(request: Request):
    """预设典型社区（覆盖城乡结合部/老旧小区等赛题点名形态）+ 本机已体检过的历史社区。"""
    presets = [{**p, "lat": p["lat"], "lng": p["lng"]} for p in PRESET_COMMUNITIES]
    # enforce=登录开着：只回自己的 + 勾了公开的；单机模式下 uid 为 None 且不过滤
    enforce, uid = settings.auth_enabled, getattr(request.state, "uid", None)
    history = await asyncio.to_thread(lambda: db.list_runs(30, uid, enforce))
    return {"presets": presets,
            "history": [{k: v for k, v in h.items() if k != "payload"} for h in history]}


@app.get("/api/stats")
async def api_stats(request: Request):
    uid = getattr(request.state, "uid", None)
    enforce = settings.auth_enabled and uid is not None
    return await asyncio.to_thread(lambda: db.stats(uid, enforce))


@app.get("/api/runs")
async def list_runs(request: Request, limit: int = 30):
    uid = getattr(request.state, "uid", None)
    return await asyncio.to_thread(lambda: db.list_runs(limit, uid, settings.auth_enabled))


@app.get("/api/runs/{run_id}")
async def get_run(request: Request, run_id: str):
    """历史回放：直接取派生结果，一次 API 都不打。

    登录开着时按归属过滤——但公开过的分享链接仍允许匿名读取，
    不然"发条链接给评委看"就废了。
    """
    uid = getattr(request.state, "uid", None)
    result = await asyncio.to_thread(lambda: db.get_run(run_id, uid, settings.auth_enabled))
    if not result:
        raise HTTPException(404, "run not found")
    return result


@app.delete("/api/runs/{run_id}")
async def delete_run(request: Request, run_id: str):
    uid = getattr(request.state, "uid", None)
    ok = await asyncio.to_thread(lambda: db.delete_run(run_id, uid, settings.auth_enabled))
    return {"deleted": ok}


class PublicRequest(BaseModel):
    public: bool


@app.post("/api/runs/{run_id}/public")
async def toggle_public(request: Request, run_id: str, req: PublicRequest):
    """勾选/取消"公开只读"，只有归属者能改。"""
    uid = getattr(request.state, "uid", None)
    if uid is None and settings.auth_enabled:
        raise HTTPException(401, "请先登录")
    ok = await asyncio.to_thread(lambda: db.set_public(run_id, req.public, uid))
    if not ok:
        raise HTTPException(404, "run not found 或不属于当前账号")
    return {"id": run_id, "public": req.public}


@app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def run_report(request: Request, run_id: str):
    uid = getattr(request.state, "uid", None)
    result = await asyncio.to_thread(lambda: db.get_run(run_id, uid, settings.auth_enabled))
    if not result:
        raise HTTPException(404, "run not found")
    return to_markdown(result)


@app.get("/api/compare")
async def compare(request: Request, ids: str):
    """跨社区 A/B 对比：ids=a,b，返回两份派生指标与逐类别差值。"""
    picked = [x.strip() for x in ids.split(",") if x.strip()][:3]
    if len(picked) < 2:
        raise HTTPException(422, "至少给出两个 run id")
    uid = getattr(request.state, "uid", None)
    return await asyncio.to_thread(lambda: db.compare(picked, uid, settings.auth_enabled))


@app.get("/api/biz-types")
async def biz_types():
    """选址建议支持的业态清单（前端选项由此接口给，避免前后端各写一份）。"""
    return {"items": [{"key": k, "name": v["name"], "spacing_m": v["spacing_m"]}
                      for k, v in BIZ_TYPES.items()]}


@app.post("/api/advise")
async def advise_site(request: Request, req: AdviseRequest):
    """选址建议：复用该点位已建档的体检结果，只在候选定稿后补步行实测。"""
    if req.biz not in BIZ_TYPES:
        raise HTTPException(400, f"未知业态：{req.biz}")
    uid = getattr(request.state, "uid", None)
    payload = await asyncio.to_thread(
        lambda: db.find_scene_run((req.lat, req.lng), uid, settings.auth_enabled))
    if not payload:
        raise HTTPException(409, "该点位还没有体检记录，请先跑一次体检")
    try:
        return await advise(payload, req.biz, req.top)
    except BmapError as e:
        raise HTTPException(502, f"候选点步行实测失败：{e}") from e


@app.get("/api/agent/history")
async def agent_history(request: Request, limit: int = 20):
    uid = getattr(request.state, "uid", None)
    return await asyncio.to_thread(
        lambda: db.list_agent_runs(limit, uid, settings.auth_enabled))

@app.get("/api/agent/{agent_id}/trace")
async def agent_trace(request: Request, agent_id: str):
    uid = getattr(request.state, "uid", None)
    if settings.auth_enabled:
        owner = await asyncio.to_thread(db.agent_owner, agent_id)
        if owner is not None and owner != uid:
            raise HTTPException(404, "trace not found")   # 不区分"不存在"与"不是你的"
    return await asyncio.to_thread(db.agent_trace, agent_id)


@app.post("/api/analysis")
async def start_analysis(request: Request, req: AnalysisRequest):
    job_id = uuid.uuid4().hex[:12]
    uid = getattr(request.state, "uid", None)
    if uid is not None:                     # 预算按提交计：等完成再扣的话，并发几个请求就能绕过
        await asyncio.to_thread(auth.consume_analysis, uid)
    JOBS[job_id] = {"status": "queued", "started": time.time(), "owner": uid}

    async def worker():
        try:
            async with JOB_SLOTS:            # 全局并发闸：超出的排队，不把小机器打满
                JOBS[job_id]["status"] = "running"
                if not scope.mock_mode(settings.mock) and not scope.cred("ak", settings.server_ak):
                    raise BmapError(-1, "未配置百度服务端 AK：多用户模式下请填入你自己的 AK")
                result = await run_analysis((req.lat, req.lng))
                # 名称优先用前端带的社区名（预设/手填），否则退回逆地理编码得到的行政区
                label = req.label or result.get("label", "")
                # 演示模式的合成结果不入库，档案里只留真实路网跑出来的记录
                keep = req.persist and not result["meta"]["mock"]
                run_id = await asyncio.to_thread(
                    lambda: db.save_run(result, label, req.archetype, uid)) if keep else None
                JOBS[job_id].update(status="done", result=result, run_id=run_id)
        except Exception as e:  # noqa: BLE001 - 任务态需要把任意异常回传给前端
            JOBS[job_id].update(status="error", error=str(e), degraded=client.degraded,
                                quota_exhausted=client.quota_exhausted)

    asyncio.create_task(worker())
    return {"job_id": job_id}


@app.get("/api/analysis/{job_id}")
async def get_analysis(request: Request, job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if settings.auth_enabled and job.get("owner") != getattr(request.state, "uid", None):
        raise HTTPException(404, "job not found")     # 不区分"不存在"与"不是你的"
    if job["status"] in ("running", "queued"):
        return {"status": job["status"], "elapsed": round(time.time() - job["started"], 1),
                "api_calls": client.calls}
    if job["status"] == "error":
        # 配额/异常降级说明：保留错误上下文供前端提示
        return {"status": "error", "error": job.get("error"), "degraded": job.get("degraded", False),
                "quota_exhausted": job.get("quota_exhausted", False)}
    return {"status": "done", "run_id": job.get("run_id"), "result": job["result"]}


@app.get("/api/search")
async def search_places(q: str = Query(..., min_length=2, max_length=40),
                        city: str = Query("", max_length=30)):
    """地名搜索：省 / 市 / 区县与县级市 / 乡镇 / 村，以及具体门店都能命中并给出 BD09LL 坐标。

    组合地理编码与城市内检索两条上游（各自独立配额池，都不占路线规划的 1 万次/天），
    结果里用 `kind` 与 `precise` 说清"这是区划中心还是精确点位"——区划中心当体检原点是
    合理的，但报告口径里必须让人知道它不是某个具体门口。
    """
    if not scope.mock_mode(settings.mock) and not scope.cred("ak", settings.server_ak):
        raise HTTPException(400, "未配置百度服务端 AK，无法搜索")
    results = await client.suggest(q, city)
    return {"query": q, "count": len(results), "results": results,
            "meta": {"api_calls": client.calls, "cache_hits": client.cache_hits,
                     "by_service": dict(client.by_service)}}


@app.get("/api/preview")
async def preview(lat: float, lng: float, radius: int = 1200):
    """中心点合法性的轻量探测（1 次路线调用），前端选点后可先验证再提交整单。"""
    dest = dest_on_ray((lat, lng), 45, radius)
    sec = await client.walking_duration((lat, lng), dest)
    return {"reachable": sec < 3600, "sample_walk_sec": round(sec)}


@app.post("/api/agent")
async def start_agent(request: Request, req: AgentRequest):
    """规划智能体对话：LLM 自主编排地图工具，返回诊断结论与地图渲染指令。"""
    job_id = uuid.uuid4().hex[:12]
    center = (req.lat, req.lng) if req.lat is not None and req.lng is not None else None
    uid = getattr(request.state, "uid", None)
    if uid is not None:
        await asyncio.to_thread(auth.consume_agent, uid)
    job = {"status": "running", "started": time.time(), "progress": None, "owner": uid}
    AGENT_JOBS[job_id] = job

    async def on_step(payload: dict):
        job["progress"] = payload

    async def worker():
        try:
            if not scope.mock_mode(settings.mock) and not scope.cred("ak", settings.server_ak):
                raise BmapError(-1, "未配置百度服务端 AK：多用户模式下请填入你自己的 AK")
            out = await run_agent(req.message, req.history, center, on_step)
            out["api_calls"] = client.calls
            if not scope.mock_mode(settings.mock):
                await asyncio.to_thread(
                    lambda: db.save_agent_run(out, req.message, center, uid))
            job.update(status="done", progress=None, **out)
        except Exception as e:  # noqa: BLE001 - 任务态需要把任意异常回传给前端
            job.update(status="error", error=str(e), progress=None)

    asyncio.create_task(worker())
    return {"job_id": job_id}


@app.get("/api/agent/{job_id}")
async def get_agent(job_id: str):
    job = AGENT_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] == "running":
        return {"status": "running", "elapsed": round(time.time() - job["started"], 1),
                "progress": job.get("progress"), "api_calls": client.calls}
    if job["status"] == "error":
        return {"status": "error", "error": job.get("error")}
    return {"status": "done",
            **{k: v for k, v in job.items() if k not in ("status", "started", "progress")}}


# ---------- 单镜像部署：同一个进程兼作前端静态站（Dockerfile.single） ----------
# 目录不存在时下面这些路由根本不注册，本地开发（vite dev）与 backend-only 镜像行为不变。
# 用 STATIC_DIR 环境变量指到别处，主要是为了让这条路径能被自测覆盖到。
STATIC_DIR = Path(os.getenv("STATIC_DIR") or Path(__file__).resolve().parents[1] / "frontend" / "dist")

if (STATIC_DIR / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            # 接口 404 必须还是 JSON，不能回一页 HTML：前端与 curl 都会解析响应体
            raise HTTPException(404, "not found")
        f = (STATIC_DIR / path).resolve()
        if not f.is_relative_to(STATIC_DIR.resolve()):
            raise HTTPException(400, "bad path")
        if f.is_file():
            return FileResponse(f)
        if "." in path.split("/")[-1]:
            # 带扩展名的资源请求找不到就是 404：回一页 HTML 会让浏览器按 CSS/JS 解析出错，
            # 报错信息还指向别的文件，比 404 难查得多
            raise HTTPException(404, "not found")
        return FileResponse(STATIC_DIR / "index.html")
