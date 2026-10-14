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
from .agent import run_agent
from .analysis import run_analysis
from .baidu_client import BmapError, client
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
            run_id = await asyncio.to_thread(db.save_run, result, label, req.archetype) \
                if req.persist else None
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
