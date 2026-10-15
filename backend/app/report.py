"""把体检结果渲染成可交付的 Markdown 报告：结论、口径、证据、盲区清单、行动建议一次带全。

导出件是"体检"这个词的落点——评委和街道办拿到的不只是一张地图，而是一份能存档的文书。
"""


def _bool(v) -> str:
    return "是" if v else "否"


def to_markdown(r: dict) -> str:
    meta = r.get("meta", {})
    c = r.get("center", {})
    lines = [
        f"# 15 分钟生活圈体检报告 · {r.get('label') or '未命名社区'}",
        "",
        f"- 中心坐标（BD09LL）：`{c.get('lat')}, {c.get('lng')}`",
        f"- 健康分：**{r.get('health_score')} / 100**，15 分钟圈面积 **{r.get('area_km2')} km²**",
        (f"- 数据来源：**{meta.get('data_mode')}**（mock=本地合成路网，real=百度真实路网，"
        f"real+degraded=部分点位退回直线估算）"),
        f"- 证据指纹 `evidence_id`：`{meta.get('evidence_id')}` — 同参数同输出可复现，改一个采样点即失效",
        f"- 生成时间：{r.get('saved_at', '')}",
        "",
        "## 一、计量自证",
        "",
        "| 指标 | 数值 | 口径 |",
        "| --- | --- | --- |",
        f"| 真实出网请求 | {meta.get('api_calls')} 次 | 命中缓存不计 |",
        f"| 配额单位 | {meta.get('quota_units')} | 批量矩阵按路线对数折算，与请求数分栏不合并 |",
        f"| 缓存命中 | {meta.get('cache_hits')} 次 | 磁盘缓存，键含数据模式前缀 |",
        f"| 分服务出网 | {meta.get('by_service')} | 按百度各自 QPS 分桶限流 |",
        f"| 限流重试 | {meta.get('retry_count')} 次 | 指数退避，最终成功不计为降级 |",
        f"| 计算耗时 | {meta.get('elapsed_sec')} s | — |",
        "",
        "## 二、设施覆盖",
        "",
        "| 类别 | 圈内数量 | 检索总量 | 检索是否查全 |",
        "| --- | --- | --- | --- |",
    ]
    for v in (r.get("coverage") or {}).values():
        lines.append(f"| {v['name']} | {v['inside']} | {v['total']} | {_bool(v.get('evidence_complete'))} |")

    std = r.get("standards") or {}
    if std.get("items"):
        lines += ["", "### 导则达标对照", "",
                  f"- 口径：{std.get('method')}；{std.get('passed')}/{std.get('total')} 项达标",
                  "",
                  "| 项目 | 圈内数量 | 中心点直线最近 | 参考半径 | 状态 | 参考出处 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for i in std["items"]:
            near = i["nearest_m"] if i["nearest_m"] is not None else "—"
            lines.append(f"| {i['name']} | {i['inside']} | {near} m | {i['ref_m']} m | "
                         f"{i['status']} | {i['basis']} |")
        lines += ["", "参考半径为公开规划导则的常用取值，属参考口径而非本地强制标准。"]

    lines += ["", "## 三、可解释指标", ""]
    for m in r.get("metrics") or []:
        lines.append(f"- **{m['label']}** = {m['value']} {m['unit']}；公式：`{m['formula']}`；{m['note']}")

    d = r.get("detour") or {}
    lines += [
        "", "## 四、路网受阻代理（不进入评分）", "",
        (f"- 绕行系数中位数（本次实测自标定，非预设）：**{d.get('detour_factor_p50')}**，"
        f"P90：{d.get('detour_factor_p90')}"),
        (f"- 隐含步速中位数：**{d.get('implied_speed_mps')} m/s**；"
        f"低于 0.8 m/s 的采样点 {d.get('slow_samples')} 个（{d.get('slow_ratio')}）"),
        f"- {d.get('note')}",
    ]

    lines += ["", f"## 五、服务盲区（{len(r.get('blindspots') or [])} 个网格点）", ""]
    clusters = r.get("blindspot_clusters") or []
    if clusters:
        lines += ["300 m 网格逐格判定，连片的同性质缺口在此并成片区，避免逐格复读：", "",
                  "| 片区 | 缺乏设施 | 格数 | 约面积 | 中心点 | 核验情况 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for idx, c in enumerate(clusters, 1):
            miss = "、".join(c["missing"]) or "待判定"
            lines.append(f"| {idx} | {miss} | {c['cells']} | {c['area_km2']} km² | "
                         f"{c['lat']}, {c['lng']} | 已核验 {c['verified_cells']} / 待核验 "
                         f"{c['suspect_cells']} |")
        lines += [""]
    bs = r.get("blindspots") or []
    if bs:
        lines += ["### 逐格清单（地图上的形状即这些格子）", "",
                  "| 状态 | 纬度 | 经度 | 缺乏设施 | 无法判定项 | 官方路线核对 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for b in bs:
            items = "、".join(b.get("missing") or b.get("missing_preview") or [])
            und = "、".join(b.get("undetermined") or []) or "—"
            link = (f"[步行核对](https://api.map.baidu.com/dir?origin={b['lat']},{b['lng']}"
                    f"&destination={b['lat']},{b['lng']}&type=walk&coord_type=bd09ll&output=api)")
            status = "已核验" if b.get("verified") else ("疑似" if b.get("missing_preview") else "初筛")
            lines.append(f"| {status} | {b['lat']} | {b['lng']} | {items or '—'} | {und} | {link} |")
    else:
        lines.append("未发现服务盲区。")

    sc = r.get("scenarios") or {}
    lines += ["", "## 六、情景与人群对比（同一批实测耗时重算，零新增配额）", "",
              "| 情景 | 阈值(秒) | 面积 km² | 圈内设施 | 外推顶点 | 顶点置信度 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for name, s in (sc.get("by_minutes") or {}).items():
        lines.append(f"| {name} | {s['target_sec']} | {s['area_km2']} | {s['facilities_inside']} |"
                     f" {s['vertex_kind']['extrapolated']} | {s['confidence']} |")
    for name, s in (sc.get("by_crowd") or {}).items():
        label = {"adult": "成人 1.25m/s", "elderly": "老年人 0.8m/s", "child": "儿童 0.9m/s"}.get(name, name)
        lines.append(f"| {label} | {s['target_sec']} | {s['area_km2']} | {s['facilities_inside']} |"
                     f" {s['vertex_kind']['extrapolated']} | {s['confidence']} |")

    lines += ["", "## 七、必核单品口径", ""]
    for name, txt in (meta.get("required_check") or {}).items():
        lines.append(f"- {name}：{txt}")
    lines += ["", "---",
              ("本报告由开源项目《15 分钟生活圈智能体检与规划助手》自动生成。"
              "数据经百度地图开放能力获取，仅保留派生结果，不含平台原始响应。"), ""]
    return "\n".join(lines)
