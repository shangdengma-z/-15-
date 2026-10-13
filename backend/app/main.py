"""API 服务入口：POST 提交体检任务 -> 轮询进度 -> 获取报告 JSON。"""

import asyncio
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .analysis import run_analysis
from .baidu_client import BmapError, client
from .config import settings
from .isochrone import dest_on_ray

app = FastAPI(title="15分钟生活圈体检助手", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

JOBS: dict = {}


class AnalysisRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


@app.get("/api/health")
async def health():
    return {"ok": True, "mock": settings.mock, "ak_configured": bool(settings.server_ak)}


@app.post("/api/analysis")
async def start_analysis(req: AnalysisRequest):
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "started": time.time()}

    async def worker():
        try:
            if not settings.mock and not settings.server_ak:
                raise BmapError(-1, "BMAP_SERVER_AK 未配置")
            result = await run_analysis((req.lat, req.lng))
            JOBS[job_id].update(status="done", result=result)
        except Exception as e:
            JOBS[job_id].update(status="error", error=str(e), degraded=client.degraded)

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
        return {"status": "error", "error": job.get("error"), "degraded": job.get("degraded", False)}
    return {"status": "done", "result": job["result"]}


@app.get("/api/preview")
async def preview(lat: float, lng: float, radius: int = 1200):
    """中心点合法性的轻量探测（1 次路线调用），前端选点后可先验证再提交整单。"""
    dest = dest_on_ray((lat, lng), 45, radius)
    sec = await client.walking_duration((lat, lng), dest)
    return {"reachable": sec < 3600, "sample_walk_sec": round(sec)}
