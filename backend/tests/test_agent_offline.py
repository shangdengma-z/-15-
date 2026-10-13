"""规划智能体离线自测：mock 地图 + 假 LLM，无需任何真实密钥或配额。

覆盖：无 Key 规则降级、地名解析编排、LLM 工具调用循环、tool 结果回填顺序、
非法参数纠错、LLM 故障回落、盲区到地图指令的映射。CI 与本地均可直接运行：
    python backend/tests/test_agent_offline.py
"""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

os.environ["BMAP_MOCK"] = "1"
os.environ["BMAP_QPS"] = "1000"
os.environ["BMAP_CONCURRENCY"] = "8"
os.environ["BMAP_CACHE_DIR"] = "data/cache_selftest"
os.environ["LLM_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import agent as A  # noqa: E402
from app.agent import run_agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import LLMError, llm  # noqa: E402

PROGRESS = []


async def on_step(p):
    PROGRESS.append((p.get("tool"), p.get("status")))


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    return cond


async def main():
    ok = True

    # 1) 无 key -> 规则模式，给定中心点
    out = await run_agent("这个社区的生活圈体检怎么样", None, (39.908, 116.397), on_step)
    ok &= check("无 key 降级为规则模式", out["mode"] == "fallback", out.get("note"))
    ok &= check("规则模式跑了体检工具", [s["tool"] for s in out["steps"]] == ["health_check"])
    ok &= check("规则文案含三段结构", all(k in out["reply"] for k in ("【诊断结论】", "【数据依据】", "【规划建议】")))
    ok &= check("返回完整体检结果供地图渲染", bool(out.get("result")) and "rings" in out["result"])

    # 1b) 盲区 -> 地图指令的映射（用合成结果单测，不依赖 mock 数据密度）
    fake_res = {"rings": {"15": [[116.40, 39.91], [116.41, 39.91], [116.41, 39.90]]},
                "health_score": 64, "coverage": {}, "blindspots": [
                    {"lat": 39.905, "lng": 116.405, "missing": ["药店", "菜市场"], "verified": True},
                    {"lat": 39.906, "lng": 116.406, "missing_preview": ["小学"], "verified": False}],
                "meta": {"api_calls": 1, "elapsed_sec": 0.1, "degraded": False}}
    orig = A.run_analysis
    A.run_analysis = lambda c: asyncio.sleep(0, fake_res)
    probe = A.AgentRun()
    summary = await A._health_check(probe, 39.908, 116.397)
    A.run_analysis = orig
    bs_actions = [a for a in probe.map_actions if a["kind"] == "blindspot"]
    ok &= check("盲区映射为地图指令", len(bs_actions) == 2 and bs_actions[0]["label"] == "药店、菜市场",
                str(bs_actions))
    ok &= check("盲区摘要计数正确", summary["blindspot_verified_count"] == 1
                and summary["blindspot_count"] == 2)

    # 2) 无 key 且无中心点 -> 从消息里解析地名（mock geocode 固定返回 39.908,116.397）
    out2 = await run_agent("帮我看看北京市西城区什刹海街道", None, None, on_step)
    ok &= check("地名走 geocode", [s["tool"] for s in out2["steps"]][:1] == ["geocode"])
    ok &= check("geocode 后接体检", "health_check" in [s["tool"] for s in out2["steps"]])

    # 3) 假 LLM：先规划工具调用，再给结论
    script = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "geocode", "arguments": json.dumps({"address": "北京市西城区什刹海街道"})}},
            {"id": "c2", "type": "function",
             "function": {"name": "nearest_facility",
                          "arguments": json.dumps({"lat": 39.908, "lng": 116.397, "category": "medical",
                                                   "keyword": "药店"})}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c3", "type": "function",
             "function": {"name": "health_check", "arguments": json.dumps({"lat": 39.908, "lng": 116.397})}}]},
        {"role": "assistant", "content": "【诊断结论】健康分 100，四类设施齐备。\n【数据依据】药店步行 6 分钟可达。\n【规划建议】维持现状。"},
    ]
    calls = {"n": 0, "roles": [], "system": ""}

    async def fake_chat(messages, tools=None):
        calls["roles"].append([m["role"] for m in messages])
        calls["system"] = messages[0]["content"]
        calls["n"] += 1
        return script[min(calls["n"] - 1, len(script) - 1)]

    llm.chat = fake_chat
    settings.llm_api_key = "sk-selftest"
    out3 = await run_agent("这里适合开药店吗", None, (39.908, 116.397), on_step)
    ok &= check("LLM 模式标记正确", out3["mode"] == "llm")
    ok &= check("三个工具都被执行", [s["tool"] for s in out3["steps"]] == ["geocode", "nearest_facility", "health_check"],
                str([s["tool"] for s in out3["steps"]]))
    ok &= check("工具摘要非空", all(s["summary"] for s in out3["steps"]),
                str([s["summary"] for s in out3["steps"]]))
    ok &= check("结论原样返回", out3["reply"].startswith("【诊断结论】"))
    ok &= check("tool 消息按序回填给模型",
                calls["roles"][-1].count("tool") == 3, str(calls["roles"][-1]))
    ok &= check("system 提示含地图中心点", "当前地图中心点" in calls["system"], calls["system"][-60:])

    # 4) 模型给错参数 -> 错误回填而不是崩掉
    async def bad_args_chat(messages, tools=None):
        if len([m for m in messages if m["role"] == "tool"]) == 0:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "b1", "type": "function",
                 "function": {"name": "walking_time", "arguments": json.dumps({"from_lat": "abc"})}},
                {"id": "b2", "type": "function",
                 "function": {"name": "no_such_tool", "arguments": "{}"}}]}
        return {"role": "assistant", "content": "参数有误，已终止。"}

    llm.chat = bad_args_chat
    out4 = await run_agent("算一下步行时间", None, (39.908, 116.397), None)
    ok &= check("非法参数被捕获并回填", all(not s["ok"] for s in out4["steps"]),
                str([s["summary"] for s in out4["steps"]]))

    # 5) LLM 抛错 -> 降级规则模式
    async def boom(messages, tools=None):
        raise LLMError("HTTP401 invalid api key")

    llm.chat = boom
    out5 = await run_agent("体检一下这里", None, (39.908, 116.397), None)
    ok &= check("LLM 故障自动降级", out5["mode"] == "fallback" and "大模型不可用" in (out5.get("note") or ""),
                out5.get("note"))

    print("\n== 总体：", "全部通过" if ok else "存在失败项", "==")
    return 0 if ok else 1


try:
    sys.exit(asyncio.run(main()))
finally:
    shutil.rmtree(Path(settings.cache_dir), ignore_errors=True)
