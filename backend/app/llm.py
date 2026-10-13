"""LLM 客户端：OpenAI 兼容 /chat/completions，支持 function calling。

设计要点：
- 只依赖 httpx，不引入 openai SDK（减少依赖面，换厂商只改 BASE_URL/MODEL）
- 以完整请求体为键落磁盘缓存，同一轮对话重放零 token、零延迟（录演示视频用）
- 鉴权/参数类错误不重试，网络与限流类错误指数退避
"""

import asyncio
import hashlib
import json
import logging
import random
from pathlib import Path

import httpx

from .config import settings

log = logging.getLogger("llm")

# 4xx 中这些是配置问题，重试没有意义
FATAL_STATUS = {400, 401, 403, 404, 422}


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self):
        self.cache_dir = Path(settings.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.cache_hits = 0
        self._http = httpx.AsyncClient(timeout=90)

    @property
    def enabled(self) -> bool:
        return bool(settings.llm_api_key)

    def _cache_path(self, payload: dict) -> Path:
        key = hashlib.md5(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return self.cache_dir / f"llm_{settings.llm_model}_{key}.json"

    async def chat(self, messages: list, tools: list | None = None) -> dict:
        """返回 assistant 消息体（可能含 tool_calls）。"""
        payload = {"model": settings.llm_model, "messages": messages, "temperature": 0.3}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        cache_file = self._cache_path(payload)
        if cache_file.exists():
            self.cache_hits += 1
            return json.loads(cache_file.read_text(encoding="utf-8"))
        if not self.enabled:
            raise LLMError("LLM_API_KEY 未配置")

        url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._http.post(url, headers=headers, json=payload)
                self.calls += 1
                data = resp.json()
                if resp.status_code in FATAL_STATUS:
                    raise LLMError(f"HTTP{resp.status_code} {str(data)[:200]}")
                if resp.status_code != 200 or "choices" not in data:
                    raise LLMError(f"HTTP{resp.status_code} {str(data)[:200]}")
                msg = data["choices"][0]["message"]
                cache_file.write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
                return msg
            except LLMError as e:
                if any(f"HTTP{c}" in str(e) for c in FATAL_STATUS):
                    raise
                last_err = e
            except (httpx.HTTPError, ValueError) as e:
                last_err = e
            await asyncio.sleep(1.5 * (2**attempt) + random.random())
        raise LLMError(f"LLM 调用失败：{last_err}")


llm = LLMClient()
