# 15 分钟生活圈智能体检与规划助手

基于百度地图开放能力的社区生活圈"体检"工具：输入任意社区中心点，自动计算基于**真实路网**的
15 分钟步行等时圈，统计圈内医疗 / 教育 / 购物 / 养老四类民生设施覆盖情况，
识别并核验"周边 1 公里内缺乏设施"的**服务盲区**，输出可视化体检报告。

> 赛题：【开源 AI 工具赛道】基于地图开放能力的"15 分钟生活圈"智能体检与规划助手

## 功能演示

1. 点击地图选取社区中心点（或输入坐标）
2. 「开始体检」→ 后端扇形射线采样真实步行耗时，插值生成 5/10/15 分钟三层等时圈
3. 地图叠加：等时圈等值面、按真实耗时着色的热力散点、分类 POI 标点、盲区灰红标记
4. 右侧报告：健康分、设施柱状图、覆盖雷达图、盲区清单（含批量距离矩阵核验结果）
5. 「AI 规划助手」：用自然语言提问（如"这里适合开药店吗"），智能体自主决定调用地名解析、
   等时圈体检、真实步行测距、POI 检索等工具，基于返回的真实数据输出诊断结论与规划建议，
   并把结论对应的点位回标到地图上；面板实时展示工具调用轨迹

## 核心技术设计

### 等时圈算法（扇形采样 + 射线插值）
百度未提供等时圈 API。本方案从中心点向 `ISOCHRONE_RAYS`（默认 24）个方位发射射线，
每条射线在 `300~1800m` 的 6 个半径上调用**步行路径规划 REST API** 获取真实路网耗时
（红绿灯、天桥、围挡均计入），再沿射线对"耗时-半径"序列线性插值得到 5/10/15 分钟边界点，
多边形顶点邻域平滑后输出。总调用量可预期（144 次），且每个采样点同时服务于三层等时圈与
热力图，配合磁盘缓存使重复演示零配额消耗。

### 服务盲区识别（两阶段）
1. **初筛**：在 15 分钟圈内铺设 300m 网格，直线距离粗筛"1km 内缺乏某类设施"的候选点；
2. **核验**：候选点经**批量步行距离矩阵**（`/distancematrix/v1/walking/`）到该类最近 5 个
   POI 的真实步行距离复核，剔除直线误报（如河桥绕行），保证报告可信；
3. **必核单品**：除医疗/教育/购物/养老四大类外，按赛题字面要求把**菜市场、药店、小学**
   三个单品单独纳入盲区判定（从大类检索结果按名称关键词切分，不额外消耗配额），
   避免"有便利店但无菜市场"这类漏判。

### API 工程优化
- **按服务独立限流**：百度个人 AK 各服务配额不同（步行路线规划(轻量) 3 QPS / 5000 次每天，
  地点检索 30 QPS / 1 万次每天），故每个服务各持一套令牌桶 + 并发闸，
  让 30 QPS 的检索不必排在 3 QPS 的步行后面
- **阶段并行**：等时圈计算与 POI 检索互不依赖，`asyncio.gather` 并发，整段省掉 POI 排队时间
- 指数退避重试（code=4/10/401 限流类错误，0.5s 起退避——百度按秒级窗口计），
  超限后**降级为直线估算并在报告中明确标注**
- 全量请求级磁盘缓存（`data/cache/`，演示前预热一次即可秒级复现）
- POI 数据清洗：多关键词并查 + 名称白名单二次过滤（剔除保健品店误入"药店"等）+ uid 去重
- 精度/耗时可权衡：`ISOCHRONE_RAYS`、`ISOCHRONE_RADII`、`BLINDSPOT_MAX_CANDIDATES`、
  `BLINDSPOT_VERIFY_NEAREST` 四个旋钮控制步行调用总量（冷跑实测 148 次 / 42 秒）

### 规划智能体（LLM 工具编排）
把体检能力封装为 5 个 function-calling 工具交给大模型自主编排，对应行业智能体的三要素：
任务规划、工具调用、结果落地。

| 工具 | 能力来源 |
|---|---|
| `geocode` | 地名/社区名 → 坐标（地理编码 v3） |
| `health_check` | 完整体检：等时圈 + 覆盖统计 + 盲区核验 + 健康分 |
| `nearest_facility` | 最近某类设施 + 真实步行距离/时间（步行路径规划 v2） |
| `walking_time` | 两点真实路网步行耗时 |
| `search_poi` | 圆形范围 POI 检索 |

工程约束：
- **受限循环**：单轮对话工具调用轮数上限 `AGENT_MAX_STEPS`（默认 4），超限强制收敛出结论
- **参数校验与错误回填**：模型给错参数时把原因作为 tool 结果返回，由模型自行纠正而非中断
- **结果缓存**：以完整请求体为键落盘，同一问题重放零 token、零延迟（演示视频可离线复现）
- **三级降级**：未配置 `LLM_API_KEY` 或大模型故障时，自动退化为"固定编排 + 确定性规则文案"，
  面板明确标注"规则模式"，保证产品打开即用、演示不翻车
- 接口为 OpenAI 兼容 `/chat/completions`，默认对接 DeepSeek，换厂商只改 `LLM_BASE_URL`/`LLM_MODEL`

## 快速开始

### 环境准备
1. 在[百度地图开放平台](https://lbsyun.baidu.com/)创建两个应用：
   - **浏览器端** AK → 填 `VITE_BMAP_AK`（前端地图渲染；建议配置 IP 白名单）
   - **服务端** AK+SK → 填 `BMAP_SERVER_AK` / `BMAP_SERVER_SK`（REST API，需开通
     步行路径规划、位置搜索、距离矩阵、正逆地理编码）
2. 配置密钥（**已 .gitignore 排除，勿提交**）：
   ```bash
   cp .env.example .env   # 编辑 .env 填入 AK/SK
   ```
3. （可选）规划智能体：在 `.env` 填 `LLM_API_KEY`（OpenAI 兼容接口，默认 DeepSeek）。
   留空则 AI 面板自动运行在规则模式，其余功能不受影响。

### Docker 一键启动
```bash
docker compose up --build
# 前端 http://localhost:5173  后端 http://localhost:8000/docs
```

### 本地开发
```bash
# 后端
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload          # 加环境变量 BMAP_MOCK=1 可免 AK 跑通全链路

# 前端
cd frontend && npm install && cp .env.example .env.local  # 填 VITE_BMAP_AK
npm run dev
```

### Mock 模式（评审快速验证）
`BMAP_MOCK=1` 时不消耗任何配额，返回模拟路网数据，可完整演示全部功能流程；
真实 API 模式将 `BMAP_MOCK=0` 并配置服务端 AK/SK。

## 坐标系统说明
全链路统一使用百度 **BD09LL** 坐标系（REST API 与 JSAPI 一致），无需额外坐标转换；
若接入其他来源数据，先调用 `/coord/v3/convert` 转换。

## 项目结构
```
backend/app/        FastAPI 服务
  baidu_client.py     REST 客户端（签名/限流/重试/缓存/降级/Mock）
  isochrone.py        等时圈射线采样与插值
  poi.py              POI 检索与清洗
  analysis.py         体检编排：覆盖统计 + 盲区两阶段识别
  llm.py              OpenAI 兼容 LLM 客户端（function calling + 缓存 + 重试）
  agent.py            规划智能体：工具定义、受限编排循环、规则模式降级
  main.py             HTTP API（异步任务 + 轮询）
backend/tests/      离线自测（mock 地图 + 假 LLM，无需任何密钥）
frontend/src/       Vue3 + Vite
  components/MapView.vue      BMapGL 地图可视化（含智能体点位回标）
  components/ReportPanel.vue  ECharts 体检报告
  components/AgentPanel.vue   AI 规划助手对话面板（工具轨迹实时展示）
```

## API 文档
启动后端后访问 `http://localhost:8000/docs`（OpenAPI）。

| 端点 | 说明 |
|---|---|
| `POST /api/analysis` | 提交体检任务 `{lat, lng}` → `job_id` |
| `GET /api/analysis/{job_id}` | 轮询进度 / 获取报告 JSON |
| `POST /api/agent` | 规划智能体对话 `{message, history?, lat?, lng?}` → `job_id` |
| `GET /api/agent/{job_id}` | 轮询对话进度（含实时工具轨迹）/ 获取结论与地图指令 |
| `GET /api/preview` | 中心点合法性轻量探测 |
| `GET /api/health` | 服务、Mock 与大模型配置状态 |

## 测试
```bash
# 离线自测：mock 地图 + 假 LLM，覆盖降级路径、工具编排、参数纠错、故障回落
python backend/tests/test_agent_offline.py

# CI（GitHub Actions）每次 push 自动执行：ruff 检查 + Mock 冒烟 + 前端构建 + 上述自测
```

## License
[MIT](LICENSE)
