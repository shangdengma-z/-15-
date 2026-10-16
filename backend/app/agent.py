"""社区规划智能体：把体检能力封装为工具，交由 LLM 做任务规划与工具调用。

对应赛道对「行业智能体」的定义：任务规划（LLM 自主决定调用顺序与参数）、工具调用（百度
地图开放能力）、结果落地（诊断结论 + 地图渲染指令）。LLM 不可用时退化为固定编排 + 规则
文案，保证产品打开即用、演示不翻车。
"""

import asyncio
import json
import logging
import re

from .analysis import run_analysis
from . import db
from .advisor import BIZ_TYPES, advise
from .baidu_client import BmapError, client, straight_line_m
from .config import CATEGORIES, settings
from .llm import LLMError, llm
from .poi import clean_pois

log = logging.getLogger("agent")

TOOL_TIMEOUT_SEC = 240  # 冷启动一次完整体检约 50s（QPS 3），留足余量

SYSTEM_PROMPT = """你是"15 分钟生活圈"社区体检与规划顾问，服务对象是城市规划部门、社区工作者和普通居民。

工作准则：
1. 任何关于设施数量、距离、步行时间、服务盲区的结论都必须来自工具返回的真实数据，严禁凭常识编造或估算。
2. 用户提到地名或社区名时，先用 geocode 取坐标再做 health_check；用户说"这里/该社区"指的是当前地图中心点。
3. 判断某类设施是否可达，用 nearest_facility 拿真实步行时间，不要用直线距离下结论。
4. 数据不足以支撑结论时，明确说明缺什么，不要强行下判断。
5. 用户问"该开在哪 / 选址 / 补哪个点位"时，先确保该点位已体检（必要时调用 health_check），
   再调用 site_advice 拿候选点位；建议必须落在工具返回的候选点上，不许自造坐标。
6. 回答用中文，控制在 350 字以内，按三段组织：诊断结论 / 数据依据 / 规划建议（不超过 3 条，要具体可执行）。
7. 不要输出 Markdown 表格和代码块。
8. 面向的是非技术读者：回答中不要出现英文字段名、内部变量名或 JSON 键（如 blindspot_count），一律改用中文业务表述。"""

def coord_param(name: str, desc: str) -> dict:
    """经纬度入参的模板：说明里带上内部名，模型回填时不容易把 lat/lng 搞反。"""
    return {"type": "number", "description": f"{desc}（{name}）"}

TOOLS = [
    {"type": "function", "function": {
        "name": "geocode",
        "description": "把中文地址、社区名或街道名解析为经纬度坐标。用户给出地名时必须先调用它。",
        "parameters": {"type": "object", "properties": {
            "address": {"type": "string", "description": "地址或社区/街道名，建议带城市，如「北京市昌平区回龙观东大街」"}},
            "required": ["address"]}}},
    {"type": "function", "function": {
        "name": "health_check",
        "description": "对某中心点做完整的 15 分钟生活圈体检：基于真实路网的等时圈、四类民生设施覆盖统计、"
                       "服务盲区识别（1 公里内无菜市场/药店/小学等）、健康分。计算较重但结果会缓存。",
        "parameters": {"type": "object", "properties": {
            "lat": coord_param("纬度", "中心点纬度"), "lng": coord_param("经度", "中心点经度")},
            "required": ["lat", "lng"]}}},
    {"type": "function", "function": {
        "name": "nearest_facility",
        "description": "查询某点周边指定类别最近的民生设施，返回名称、地址与真实路网步行距离和时间，"
                       "并判断是否在 15 分钟步行圈内。",
        "parameters": {"type": "object", "properties": {
            "lat": coord_param("纬度", "查询点纬度"), "lng": coord_param("经度", "查询点经度"),
            "category": {"type": "string", "enum": list(CATEGORIES),
                         "description": "设施类别：medical 基础医疗 / education 教育 / shopping 购物 / elderly 养老"},
            "keyword": {"type": "string", "description": "可选，细化关键词，如「药店」「菜市场」「小学」"}},
            "required": ["lat", "lng", "category"]}}},
    {"type": "function", "function": {
        "name": "walking_time",
        "description": "计算两点之间的真实路网步行距离与耗时（含红绿灯、天桥、围挡等阻力），不是直线距离。",
        "parameters": {"type": "object", "properties": {
            "from_lat": coord_param("纬度", "起点纬度"), "from_lng": coord_param("经度", "起点经度"),
            "to_lat": coord_param("纬度", "终点纬度"), "to_lng": coord_param("经度", "终点经度")},
            "required": ["from_lat", "from_lng", "to_lat", "to_lng"]}}},
    {"type": "function", "function": {
        "name": "search_poi",
        "description": "圆形范围 POI 检索，返回名称、地址与坐标列表，用于核查某类设施的实际分布。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索关键词，如「社区卫生服务中心」"},
            "lat": coord_param("纬度", "圆心纬度"), "lng": coord_param("经度", "圆心经度"),
            "radius": {"type": "integer", "description": "检索半径（米），默认 1500，上限 3000"},
            "limit": {"type": "integer", "description": "返回条数上限，默认 10"}},
            "required": ["query", "lat", "lng"]}}},
    {"type": "function", "function": {
        "name": "site_advice",
        "description": "在已体检过的社区里，为某类业态推荐 15 分钟圈内的候选点位：按人气（住宅小区密度）、"
                       "与同类设施的距离、是否填补已核验盲区打分，并用真实步行路线复核候选点到社区中心的可达性。"
                       "调用前该点位必须已做过体检，否则返回错误提示。",
        "parameters": {"type": "object", "properties": {
            "lat": coord_param("纬度", "社区中心纬度"), "lng": coord_param("经度", "社区中心经度"),
            "biz": {"type": "string", "enum": list(BIZ_TYPES),
                    "description": "业态：pharmacy 药店 / market 生鲜菜店 / elderly 养老驿站 / "
                                   "childcare 托幼 / clinic 诊所或卫生服务"}},
            "required": ["lat", "lng", "biz"]}}},
]


class AgentRun:
    """单次对话的累积状态：工具轨迹、地图渲染指令、最近一次完整体检结果。"""

    def __init__(self, center: tuple | None = None):
        self.center = center
        self.steps: list = []
        self.map_actions: list = []
        self.result: dict | None = None


def _num(value, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{name} 必须是数字，实际收到 {value!r}") from None


async def _geocode(run: AgentRun, address: str) -> dict:
    lat, lng = await client.geocode(str(address).strip()[:80])
    run.center = (lat, lng)
    run.map_actions.append({"kind": "center", "lat": round(lat, 6), "lng": round(lng, 6), "label": str(address)})
    return {"address": str(address), "lat": round(lat, 6), "lng": round(lng, 6)}


async def _health_check(run: AgentRun, lat, lng) -> dict:
    lat, lng = _num(lat, "lat"), _num(lng, "lng")
    res = await run_analysis((lat, lng))
    run.result = res
    run.center = (lat, lng)

    ring = res["rings"]["15"]  # [lng, lat][]
    radii = [straight_line_m((lat, lng), (p[1], p[0])) for p in ring]
    for b in res["blindspots"][:8]:
        run.map_actions.append({
            "kind": "blindspot", "lat": b["lat"], "lng": b["lng"],
            "label": "、".join(b.get("missing") or b.get("missing_preview") or []),
            "verified": bool(b.get("verified"))})

    verified = [b for b in res["blindspots"] if b.get("verified")]
    return {
        "center": {"lat": round(lat, 6), "lng": round(lng, 6)},
        "health_score": res["health_score"],
        "coverage": {v["name"]: {"inside_15min": v["inside"], "searched_total": v["total"]}
                     for v in res["coverage"].values()},
        "isochrone_15min_radius_m": {"min": round(min(radii)), "max": round(max(radii)),
                                     "avg": round(sum(radii) / len(radii))},
        "blindspot_count": len(res["blindspots"]),
        "blindspot_verified_count": len(verified),
        "blindspots": [{"lat": b["lat"], "lng": b["lng"], "verified": bool(b.get("verified")),
                        "missing": b.get("missing") or b.get("missing_preview") or []}
                       for b in res["blindspots"][:6]],
        "run_meta": {"api_calls": res["meta"]["api_calls"], "elapsed_sec": res["meta"]["elapsed_sec"],
                     "degraded": res["meta"]["degraded"]},
    }


async def _nearest_facility(run: AgentRun, lat, lng, category: str, keyword: str | None = None) -> dict:
    lat, lng = _num(lat, "lat"), _num(lng, "lng")
    spec = CATEGORIES.get(category)
    if not spec:
        return {"error": f"未知类别 {category}，可选值：{list(CATEGORIES)}"}
    query = str(keyword).strip() if keyword else spec["queries"][0]
    raw = await client.search_poi(query, (lat, lng), 3000)
    cleaned = clean_pois(raw, spec["keywords_any"])
    if not cleaned:
        return {"found": False, "query": query, "category": spec["name"],
                "note": f"3 公里内未检索到符合「{spec['name']}」语义的{query}，该点位存在此类设施缺失风险"}
    cleaned.sort(key=lambda p: straight_line_m((lat, lng), (p["lat"], p["lng"])))
    best = cleaned[0]
    dist_m, dur_s = await client.walking_route((lat, lng), (best["lat"], best["lng"]))
    run.map_actions.append({"kind": "facility", "lat": best["lat"], "lng": best["lng"],
                            "label": f"{best['name']}（步行 {round(dur_s / 60)} 分钟）"})
    return {"found": True, "query": query, "category": spec["name"], "name": best["name"],
            "address": best.get("address", ""), "lat": best["lat"], "lng": best["lng"],
            "walk_distance_m": round(dist_m), "walk_min": round(dur_s / 60, 1),
            "within_15min": dur_s <= settings.walk_target_sec,
            "candidates_in_3km": len(cleaned)}


async def _walking_time(run: AgentRun, from_lat, from_lng, to_lat, to_lng) -> dict:
    o = (_num(from_lat, "from_lat"), _num(from_lng, "from_lng"))
    d = (_num(to_lat, "to_lat"), _num(to_lng, "to_lng"))
    dist_m, dur_s = await client.walking_route(o, d)
    return {"distance_m": round(dist_m), "duration_min": round(dur_s / 60, 1),
            "within_15min": dur_s <= settings.walk_target_sec}


async def _search_poi(run: AgentRun, query: str, lat, lng, radius=1500, limit=10) -> dict:
    lat, lng = _num(lat, "lat"), _num(lng, "lng")
    radius = max(200, min(int(_num(radius, "radius")), 3000))
    limit = max(1, min(int(_num(limit, "limit")), 20))
    rows = await client.search_poi(str(query), (lat, lng), radius)
    items = [{"name": r.get("name"), "address": r.get("address", ""),
              "lat": round(r["location"]["lat"], 6), "lng": round(r["location"]["lng"], 6)}
             for r in rows[:limit] if r.get("location")]
    for it in items[:5]:
        run.map_actions.append({"kind": "poi", "lat": it["lat"], "lng": it["lng"], "label": it["name"]})
    return {"query": str(query), "radius_m": radius, "count": len(rows), "items": items}


async def _site_advice(run: AgentRun, lat, lng, biz: str) -> dict:
    """选址建议复用已建档的体检结果：没体检过就先让模型自己去体检，而不是这里偷偷重跑一遍。"""
    lat, lng = _num(lat, "lat"), _num(lng, "lng")
    if biz not in BIZ_TYPES:
        return {"error": f"未知业态 {biz}，可用：{list(BIZ_TYPES)}"}
    payload = await asyncio.to_thread(db.find_scene_run, (lat, lng))
    if not payload:
        return {"error": "该点位还没有体检记录，请先调用 health_check"}
    out = await advise(payload, biz, top=3)
    for c in out["candidates"]:
        run.map_actions.append({"kind": "site", "lat": c["lat"], "lng": c["lng"],
                                "label": f"候选 {out['biz_name']} {c['score']} 分"})
    return out


HANDLERS = {"geocode": _geocode, "health_check": _health_check, "nearest_facility": _nearest_facility,
            "walking_time": _walking_time, "search_poi": _search_poi, "site_advice": _site_advice}


async def _dispatch(run: AgentRun, name: str, args: dict) -> dict:
    handler = HANDLERS.get(name)
    if not handler:
        return {"error": f"未知工具 {name}，可用工具：{list(HANDLERS)}"}
    try:
        return await asyncio.wait_for(handler(run, **args), TOOL_TIMEOUT_SEC)
    except TypeError as e:          # 模型给错参数：把原因回填，让它自己纠正
        return {"error": f"参数不合法：{e}"}
    except TimeoutError:
        return {"error": f"工具 {name} 执行超时（>{TOOL_TIMEOUT_SEC}s）"}
    except (BmapError, KeyError, ValueError) as e:
        return {"error": f"工具 {name} 执行失败：{e}"}


def _summarize(name: str, out: dict) -> str:
    if "error" in out:
        return out["error"]
    if name == "geocode":
        return f"「{out['address']}」→ {out['lat']}, {out['lng']}"
    if name == "health_check":
        return (f"健康分 {out['health_score']}，核验盲区 {out['blindspot_verified_count']} 处，"
                f"15 分钟半径 {out['isochrone_15min_radius_m']['min']}~{out['isochrone_15min_radius_m']['max']} 米")
    if name == "nearest_facility":
        if not out.get("found"):
            return f"3 公里内无{out['query']}"
        return f"最近{out['name']}：步行 {out['walk_min']} 分钟 / {out['walk_distance_m']} 米"
    if name == "walking_time":
        return f"真实步行 {out['duration_min']} 分钟 / {out['distance_m']} 米"
    if name == "search_poi":
        return f"「{out['query']}」{out['radius_m']} 米内检索到 {out['count']} 处"
    if name == "site_advice":
        cand = out.get("candidates") or []
        if not cand:
            return f"{out['biz_name']}：圈内没有达到人气门槛的候选点"
        return (f"{out['biz_name']}：{len(cand)} 个候选，最优 {cand[0]['score']} 分"
                f"（500m 内 {cand[0]['housing_within_500m']} 个小区，"
                f"距同类 {cand[0]['nearest_same_type_m'] or '—'} 米）")
    return "完成"


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize_history(history: list) -> list:
    out = []
    for m in (history or [])[-6:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            out.append({"role": m["role"], "content": m["content"][:2000]})
    return out


async def _emit(on_step, payload: dict):
    """把工具调用进度推给轮询方；回调异常不能影响主流程。"""
    if not on_step:
        return
    try:
        await on_step(payload)
    except Exception:
        log.warning("on_step 回调异常", exc_info=True)


def _assistant_msg(msg: dict) -> dict:
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def _pack(run: AgentRun, reply: str, mode: str, note: str | None = None) -> dict:
    out = {"reply": reply, "mode": mode, "steps": run.steps, "map_actions": run.map_actions,
           "center": [run.center[0], run.center[1]] if run.center else None}
    if run.result:
        out["result"] = run.result
    if note:
        out["note"] = note
    return out


async def _llm_loop(run: AgentRun, message: str, history: list, on_step) -> dict:
    ctx = (f"当前地图中心点：纬度 {run.center[0]:.6f}，经度 {run.center[1]:.6f}。"
           if run.center else "当前地图尚未选点。")
    messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{ctx}"},
                *_sanitize_history(history),
                {"role": "user", "content": message[:1000]}]

    for _ in range(settings.agent_max_steps):
        msg = await llm.chat(messages, TOOLS)
        messages.append(_assistant_msg(msg))
        calls = msg.get("tool_calls") or []
        if not calls:
            return _pack(run, (msg.get("content") or "").strip(), "llm")
        for call in calls[:3]:
            fn = call.get("function") or {}
            name, args = fn.get("name", ""), _parse_args(fn.get("arguments"))
            await _emit(on_step, {"tool": name, "args": args, "status": "running"})
            out = await _dispatch(run, name, args)
            step = {"tool": name, "args": args, "ok": "error" not in out, "summary": _summarize(name, out)}
            run.steps.append(step)
            await _emit(on_step, {**step, "status": "done"})
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": json.dumps(out, ensure_ascii=False)[:4000]})

    messages.append({"role": "user", "content": "已达工具调用次数上限，请基于已获得的数据直接给出结论与建议。"})
    msg = await llm.chat(messages, None)
    return _pack(run, (msg.get("content") or "").strip(), "llm", note="已达工具调用上限，结论基于已获取数据")


def _rule_report(s: dict) -> str:
    cov = s["coverage"]
    r = s["isochrone_15min_radius_m"]
    zero = [k for k, v in cov.items() if v["inside_15min"] == 0]
    lines = [
        "【诊断结论】",
        (f"该点 15 分钟生活圈健康分 {s['health_score']}/100。基于真实路网的 15 分钟可达半径为 "
        f"{r['min']}~{r['max']} 米（均值 {r['avg']} 米），明显偏离以直线距离估算的圆形范围，"
        f"说明道路绕行、河流与主干道分割对可达性影响显著。"),
    ]
    if zero:
        lines.append(f"圈内完全缺失的类别：{'、'.join(zero)}。")
    lines.append("【数据依据】")
    lines.append("；".join(f"{k}：圈内 {v['inside_15min']} 处（3 公里内共检索到 {v['searched_total']} 处）"
                          for k, v in cov.items()) + "。")
    if s["blindspot_verified_count"]:
        b = s["blindspots"][0]
        lines.append(f"经真实步行距离核验的服务盲区 {s['blindspot_verified_count']} 处，"
                     f"最严重一处缺 {'、'.join(b['missing'])}（{b['lat']:.4f}, {b['lng']:.4f}）。")
    else:
        lines.append("未发现经步行距离核验的服务盲区。")
    lines.append("【规划建议】")
    tips = []
    for k, v in sorted(cov.items(), key=lambda kv: kv[1]["inside_15min"]):
        if v["inside_15min"] == 0:
            tips.append(f"{k}为零覆盖，优先在盲区坐标周边选址补建，过渡期可用流动服务点或社区代办点兜底")
        elif v["inside_15min"] <= 2:
            tips.append(f"{k}仅 {v['inside_15min']} 处，供给偏紧，建议核查现有设施的步行可达性并考虑增补")
    if s["blindspot_verified_count"]:
        tips.append("盲区已经真实步行距离核验，可直接作为规划图则的落点依据")
    lines.append("；".join(tips[:3] or ["各类设施覆盖均衡，建议维持现状并按季度复检"]) + "。")
    lines.append("（当前为规则模式：未配置大模型 API Key，结论由确定性规则生成。）")
    return "\n".join(lines)


async def _fallback(run: AgentRun, message: str, reason: str, on_step) -> dict:
    """无 LLM 或 LLM 故障时的固定编排：解析地点 → 完整体检 → 规则文案。"""
    center = run.center
    hit = re.search(r"(-?\d{1,3}\.\d{2,})\s*[,，、]\s*(-?\d{1,3}\.\d{2,})", message)
    if hit:
        a, b = float(hit.group(1)), float(hit.group(2))
        center = (a, b) if abs(a) <= 90 else (b, a)

    if center is None:
        addr = re.sub(r"[？?！!。,，]", " ", message).strip()[:60]
        await _emit(on_step, {"tool": "geocode", "args": {"address": addr}, "status": "running"})
        try:
            center = await client.geocode(addr)
        except (BmapError, KeyError) as e:
            return _pack(run, f"无法从「{message}」解析出地点（{e}）。请在地图上点选社区中心点后重试。",
                         "fallback", note=reason)
        run.center = center
        run.map_actions.append({"kind": "center", "lat": round(center[0], 6), "lng": round(center[1], 6),
                                "label": addr})
        step = {"tool": "geocode", "args": {"address": addr}, "ok": True,
                "summary": f"「{addr}」→ {center[0]:.6f}, {center[1]:.6f}"}
        run.steps.append(step)
        await _emit(on_step, {**step, "status": "done"})

    args = {"lat": round(center[0], 6), "lng": round(center[1], 6)}
    await _emit(on_step, {"tool": "health_check", "args": args, "status": "running"})
    try:
        summary = await _health_check(run, center[0], center[1])
    except (BmapError, KeyError, ValueError) as e:
        return _pack(run, f"体检执行失败：{e}", "fallback", note=reason)
    step = {"tool": "health_check", "args": args, "ok": True, "summary": _summarize("health_check", summary)}
    run.steps.append(step)
    await _emit(on_step, {**step, "status": "done"})
    return _pack(run, _rule_report(summary), "fallback", note=f"规则模式（{reason}）")


async def run_agent(message: str, history: list | None = None, center: tuple | None = None,
                    on_step=None) -> dict:
    run = AgentRun(center)
    if not llm.enabled:
        return await _fallback(run, message, "LLM_API_KEY 未配置", on_step)
    try:
        return await _llm_loop(run, message, history or [], on_step)
    except LLMError as e:
        log.warning("agent 降级为规则模式：%s", e)
        return await _fallback(run, message, f"大模型不可用：{e}", on_step)
