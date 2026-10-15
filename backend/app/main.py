"""API 服务入口：POST 提交体检任务 -> 轮询进度 -> 获取报告 JSON；结果落 SQLite 供回放与对比。

sqlite 是同步阻塞库，所有读写经 asyncio.to_thread 丢到线程池，避免拖住事件循环。
"""

import asyncio
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import db
from .advisor import BIZ_TYPES, advise
from .agent import run_agent
from .analysis import run_analysis
from .baidu_client import BmapError, client, probe_ak
from .config import PRESET_COMMUNITIES, settings
from .isochrone import dest_on_ray
from .llm import llm
from .report import to_markdown

app = FastAPI(title="15分钟生活圈体检助手", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

JOBS: dict = {}
AGENT_JOBS: dict = {}


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


@app.get("/api/health")
async def health():
    stats = await asyncio.to_thread(db.stats)
    return {"ok": True, "mock": settings.mock, "ak_configured": bool(settings.server_ak),
            "llm_configured": llm.enabled, "llm_model": settings.llm_model if llm.enabled else None,
            "quota_exhausted": client.quota_exhausted, "db_path": settings.db_path,
            "communities_stored": stats["communities"]}


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


@app.get("/api/config")
async def get_config():
    """当前接入配置（密钥一律掩码返回，只告诉你哪一项已配置、来自哪里）。"""
    return _config_view()


@app.post("/api/config")
async def set_config(req: ConfigRequest):
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
async def verify_config(req: ConfigRequest):
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
async def communities():
    """预设典型社区（覆盖城乡结合部/老旧小区等赛题点名形态）+ 本机已体检过的历史社区。"""
    presets = [{**p, "lat": p["lat"], "lng": p["lng"]} for p in PRESET_COMMUNITIES]
    history = await asyncio.to_thread(db.list_runs, 30)
    return {"presets": presets,
            "history": [{k: v for k, v in h.items() if k != "payload"} for h in history]}


@app.get("/api/stats")
async def api_stats():
    return await asyncio.to_thread(db.stats)


@app.get("/api/runs")
async def list_runs(limit: int = 30):
    return await asyncio.to_thread(db.list_runs, limit)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    """历史回放：直接取派生结果，一次 API 都不打。"""
    result = await asyncio.to_thread(db.get_run, run_id)
    if not result:
        raise HTTPException(404, "run not found")
    return result


@app.delete("/api/runs/{run_id}")
async def delete_run(run_id: str):
    ok = await asyncio.to_thread(db.delete_run, run_id)
    return {"deleted": ok}


@app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def run_report(run_id: str):
    result = await asyncio.to_thread(db.get_run, run_id)
    if not result:
        raise HTTPException(404, "run not found")
    return to_markdown(result)


@app.get("/api/compare")
async def compare(ids: str):
    """跨社区 A/B 对比：ids=a,b，返回两份派生指标与逐类别差值。"""
    picked = [x.strip() for x in ids.split(",") if x.strip()][:3]
    if len(picked) < 2:
        raise HTTPException(422, "至少给出两个 run id")
    return await asyncio.to_thread(db.compare, picked)


@app.get("/api/biz-types")
async def biz_types():
    """选址建议支持的业态清单（前端选项由此接口给，避免前后端各写一份）。"""
    return {"items": [{"key": k, "name": v["name"], "spacing_m": v["spacing_m"]}
                      for k, v in BIZ_TYPES.items()]}


@app.post("/api/advise")
async def advise_site(req: AdviseRequest):
    """选址建议：复用该点位已建档的体检结果，只在候选定稿后补步行实测。"""
    if req.biz not in BIZ_TYPES:
        raise HTTPException(400, f"未知业态：{req.biz}")
    payload = await asyncio.to_thread(db.find_scene_run, (req.lat, req.lng))
    if not payload:
        raise HTTPException(409, "该点位还没有体检记录，请先跑一次体检")
    try:
        return await advise(payload, req.biz, req.top)
    except BmapError as e:
        raise HTTPException(502, f"候选点步行实测失败：{e}") from e


@app.get("/api/agent/history")
async def agent_history(limit: int = 20):
    return await asyncio.to_thread(db.list_agent_runs, limit)

@app.get("/api/agent/{agent_id}/trace")
async def agent_trace(agent_id: str):
    return await asyncio.to_thread(db.agent_trace, agent_id)


@app.post("/api/analysis")
async def start_analysis(req: AnalysisRequest):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "started": time.time()}

    async def worker():
        try:
            if not settings.mock and not settings.server_ak:
                raise BmapError(-1, "BMAP_SERVER_AK 未配置")
            result = await run_analysis((req.lat, req.lng))
            # 名称优先用前端带的社区名（预设/手填），否则退回逆地理编码得到的行政区
            label = req.label or result.get("label", "")
            # 演示模式的合成结果不入库，档案里只留真实路网跑出来的记录
            keep = req.persist and not result["meta"]["mock"]
            run_id = await asyncio.to_thread(db.save_run, result, label, req.archetype) \
                if keep else None
            JOBS[job_id].update(status="done", result=result, run_id=run_id)
        except Exception as e:  # noqa: BLE001 - 任务态需要把任意异常回传给前端
            JOBS[job_id].update(status="error", error=str(e), degraded=client.degraded,
                                quota_exhausted=client.quota_exhausted)

    asyncio.create_task(worker())
    return {"job_id": job_id}


@app.get("/api/analysis/{job_id}")
async def get_analysis(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] == "running":
        return {"status": "running", "elapsed": round(time.time() - job["started"], 1),
                "api_calls": client.calls}
    if job["status"] == "error":
        # 配额/异常降级说明：保留错误上下文供前端提示
        return {"status": "error", "error": job.get("error"), "degraded": job.get("degraded", False),
                "quota_exhausted": job.get("quota_exhausted", False)}
    return {"status": "done", "run_id": job.get("run_id"), "result": job["result"]}


@app.get("/api/preview")
async def preview(lat: float, lng: float, radius: int = 1200):
    """中心点合法性的轻量探测（1 次路线调用），前端选点后可先验证再提交整单。"""
    dest = dest_on_ray((lat, lng), 45, radius)
    sec = await client.walking_duration((lat, lng), dest)
    return {"reachable": sec < 3600, "sample_walk_sec": round(sec)}


@app.post("/api/agent")
async def start_agent(req: AgentRequest):
    """规划智能体对话：LLM 自主编排地图工具，返回诊断结论与地图渲染指令。"""
    job_id = uuid.uuid4().hex[:12]
    center = (req.lat, req.lng) if req.lat is not None and req.lng is not None else None
    job = {"status": "running", "started": time.time(), "progress": None}
    AGENT_JOBS[job_id] = job

    async def on_step(payload: dict):
        job["progress"] = payload

    async def worker():
        try:
            if not settings.mock and not settings.server_ak:
                raise BmapError(-1, "BMAP_SERVER_AK 未配置")
            out = await run_agent(req.message, req.history, center, on_step)
            out["api_calls"] = client.calls
            if not settings.mock:
                await asyncio.to_thread(db.save_agent_run, out, req.message, center)
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
