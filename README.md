# 15 分钟生活圈智能体检与规划助手

基于百度地图开放能力的社区生活圈"体检"工具：输入任意社区中心点，自动计算基于**真实路网**的
15 分钟步行等时圈，统计圈内医疗 / 教育 / 购物 / 养老四类民生设施覆盖情况，
识别并核验"周边 1 公里内缺乏设施"的**服务盲区**，输出可视化体检报告。

> 赛题：【开源 AI 工具赛道】基于地图开放能力的"15 分钟生活圈"智能体检与规划助手

## 30 秒跑通（不需要任何密钥）

```bash
bash scripts/demo.sh          # Windows PowerShell:  .\scripts\demo.ps1
# 或
docker compose up --build
```

脚本会自动装依赖、以 **Mock 模式**（`BMAP_MOCK=1`）启动后端，不消耗任何百度配额、不需要 AK/SK，
打开 http://localhost:5173 即可点选社区、生成等时圈与盲区报告、使用 AI 规划助手（规则模式）。
要接**真实路网**数据：`cp .env.example .env` 填入 `BMAP_SERVER_AK` 后正常启动即可。

## 功能演示

1. 左栏一键选取典型社区预设（城乡结合部 / 老旧小区 / 超大型社区 / 新区 / 商务中心），或在地图上
   点击、拖拽中心点、右键直接「设为中心点并体检」
2. 「开始体检」→ 后端扇形射线采样真实步行耗时，插值生成 5/10/15 分钟三层等时圈
3. 地图叠加：等时圈等值面、按真实耗时着色的热力散点、分类 POI 标点、盲区格标记，
   同性质连片盲区在报告里并成**片区**（11 格 → 1 行「约 0.99 km² 连片缺口」）而不是 11 行重复文字，
   **图层可逐个开关**并配图例；点击采样点/设施/盲区弹出详情与**百度官方步行路线核对链接**
4. 右侧「体检报告」：刻度分仪表健康分、实测 vs 检索总量叠影柱、**导则达标对照**
   （7 个民生项目各自对齐 GB 50180-2018 / 上海 15 分钟导则的参考半径，给出达标/临界/缺口/无点位）、
   盲区清单（可点「定位」与地图联动）、报告末尾折叠的「指标口径与算法」（每条带公式与口径）
5. 「选址建议」：选一个业态（药店 / 生鲜菜店 / 养老驿站 / 托幼 / 诊所），后端复用已建档的等时圈与
   设施池，按 **人气（500m 内住宅小区数）+ 距同类设施 + 是否补已核验盲区** 三因子给候选点位排序，
   只对最终 3 个候选各发 1 次步行路线规划实测复核可达性；口径与本次出网增量随结果返回
6. 「AI 规划助手」：用自然语言提问（如"这里适合开药店吗"），智能体自主决定调用地名解析、
   等时圈体检、真实步行测距、POI 检索、**选址建议**等 6 个工具，基于真实数据输出诊断结论与规划建议，
   并把结论对应的点位回标到地图上；工具调用轨迹实时展示并落库，事后可按 agent_id 回放
7. 历史与对比：每次**真实路网**体检自动写入 SQLite（同址重跑就地更新，演示模式不落库），
   「历史档案」可回放（0 出网）、勾选两条做跨社区 A/B 对比，并给出品类缺口分布
8. 顶栏「复制链接」把当前报告写成 `#run=<社区>` 直达链接，别人打开即从本机回放，不产生任何调用
9. 导轨最下方「接入设置」：**一键切换演示模式 / 应用模式**，并可填入自己的百度服务端 AK（含 SK 签名）、
   大模型 API Key / Base URL / Model 与限流档位。**填了密钥就默认真发一次探测请求校验**
   （百度用与本项目相同的圆形检索，要求 `status=0` 且有明细；大模型发一条 1 token 的 ping），
   校验不过返回 422 并带上百度/上游的原始状态文案，**不会写入也不会切模式**；面板上还有
   「先测一下（不保存）」可单独验证。免登录单机形态下密钥只改当前后端进程的内存，接口仅回掩码
   （前 4 后 4），不落盘、不写日志、不在页面回显明文，重启即回到 `.env`
10. 门禁与个人中心（`AUTH_ENABLED=1`，账号库可选 SQLite 或 MySQL）：登录页先讲清"密钥与数据存在哪里"，
   会话 cookie 是**浏览器会话级**——关掉浏览器再打开就要重新登录；「个人中心」里能看到自己名下的
   建档数/体检次数/平均分/出网与缓存命中、当日剩余次数、密钥去向，可改昵称、可退出登录
   （退出清本机钥匙并回到登录首页）。数据按账号隔离：同社区两人各存一份，越权读一律 404

界面是**四列工作台栅格**（图标导轨 62 · 选点控制 288 · 地图 · 结论面板 408），全部使用实色纸面卡片 +
发丝边框，**没有毛玻璃、没有高饱和渐变**：底色 `#f4f6f2`、卡片 `#fff`、主色低饱和青绿 `#4e807a`，
等时圈与类别色统一降饱和（`#cfa257 / #7fa6c0 / #7ea87e`，医疗 `#b06a6f`、教育 `#8b87ad`、
购物 `#c08a6a`、养老 `#6f9bb5`）。地图仍是最大的一块。点结论面板右上「隐藏面板 ⟩」把该列收成 0 宽、
地图独占工作区，地图上出现「展开面板 ⟩」，或点左栏任意视图也会自动展开。**左栏同样可隐藏**
（「隐藏选点 ⟩」↔ 地图上「⟨ 选点」），两栏能任意组合收起，全收起时地图独占约 950px 宽。
收起实现是把 grid 列宽变量 `--left` / `--right` 归零，而不是用会被入场动画终帧盖掉的 transform
（那是之前「隐藏面板」点了没反应的原因）。
情景（5/10/15/20 分钟）与人群（成人/老年人/儿童步速）重算、三栏出网计量与证据指纹
仍是后端能力，随 `GET /api/analysis/{job_id}` 的结果与 `report.md` 文书接口一起交付，
只是不再单独占据页面——用户看到的是结论，需要核对时打开报告页末尾的「指标口径与算法」。

## 核心技术设计

### 等时圈算法（扇形采样 + 射线插值）
百度未提供等时圈 API。本方案从中心点向 `ISOCHRONE_RAYS`（默认 18）个方位发射射线，
每条射线在 `ISOCHRONE_RADII`（默认 300/600/1000/1500m）四个半径上调用**步行路径规划 REST API**
获取真实路网耗时（红绿灯、天桥、围挡均计入），再沿射线对"耗时-半径"序列线性插值得到
5/10/15 分钟边界点，多边形顶点邻域平滑后输出。总调用量可预期（72 次），且每个采样点同时服务于
三层等时圈与热力图，配合磁盘缓存使重复演示零配额消耗。精度优先可回退到 24 射线 × 6 半径。

每个边界顶点带来源标签 `measured / interpolated / extrapolated` 与置信度，情景对比表里
「外推顶点」一列就是"这个阈值已经超出实测范围、只能看趋势不能下结论"的显式声明。
另外用本次实测自标定量出**路网受阻代理**（绕行系数 P50/P90、隐含步速、低于 0.8m/s 的采样点数）：
不预设 1.3 的绕行系数，也不把它乘进健康分——它只回答"赛题说的红绿灯/天桥/围挡有没有体现在数据里"。

### 服务盲区识别（两阶段 + 三态判定）
1. **初筛**：在 15 分钟圈内铺设 300m 网格，直线距离粗筛"1km 内缺乏某类设施"的候选点；
2. **核验**：候选点经**批量步行距离矩阵**（`/distancematrix/v1/walking/`）到该类最近
   `BLINDSPOT_VERIFY_NEAREST`（默认 3）个 POI 的真实步行距离复核，剔除直线误报（如河桥绕行），
   保证报告可信；
3. **必核单品**：除医疗/教育/购物/养老四大类外，按赛题字面要求把**菜市场、药店、小学**
   三个单品单独纳入盲区判定（从大类检索结果按名称关键词切分，不额外消耗配额），
   避免"有便利店但无菜市场"这类漏判。
4. **三态而不是二态**："1km 内没有药店"是全称否定，只有该类别检索**查全**才允许成立。
   每次 `place/v2/search` 翻页都登记停止原因（`complete`/`empty`/`page_cap`/`dup_stop`/`api_error`），
   未查全的类别记为 `undetermined`（无法判定）并在报告中单列，**不计入扣分**——
   "没查到"永远不等于"没有"。实测 `place/v2/search` 会把 page_size 静默压到 20 条，
   短页判据必须按实测页容量比，不能按请求值。

### API 工程优化
- **按服务独立限流**：百度各服务配额分别计（本项目账号 2026-10-14 起「路线规划」30 QPS / 1 万次每天，
  地点检索 30 QPS / 1 万次每天，地理编码 10 QPS；个人 AK 默认档曾为步行 3 QPS / 5000 次），
  故每个服务各持一套令牌桶 + 并发闸，一个服务被打满不拖累其他服务
- **阶段并行**：等时圈计算与 POI 检索互不依赖，`asyncio.gather` 并发，整段省掉 POI 排队时间
- 指数退避重试（code=4/401 限流类错误，0.5s 起退避——百度按秒级窗口计），
  超限后**降级为直线估算并在报告中明确标注**；
  **code=10（日配额耗尽）直接熔断到进程结束**，不再反复出网烧额度，页面转为只读缓存并给出横幅说明
- 全量请求级磁盘缓存（`data/cache/`，演示前 `python scripts/prewarm.py` 预热一次即可秒级复现）
- POI 数据清洗：多关键词并查 + 名称白名单二次过滤（剔除保健品店误入"药店"等）+ uid 去重
- 精度/耗时可权衡：`ISOCHRONE_RAYS`、`ISOCHRONE_RADII`、`BLINDSPOT_MAX_CANDIDATES`、
  `BLINDSPOT_VERIFY_NEAREST` 四个旋钮控制路线规划类调用总量（30 QPS 档冷跑实测 183 次 / 8.4 秒；
  旧 3 QPS 档为 91~103 次 / 40~60 秒）
- **三栏计量永不合并**：真实出网请求数、折算配额单位数（批量矩阵按「路线对数」计）、
  POI 检索翻页数各自成列，并写进每个响应的 `X-Api-Calls` / `X-Quota-Units` / `X-Cache-Hits` /
  `X-Elapsed-Ms` 头，`curl -i` 即可自证，不靠口头描述

### 数据持久化与零配额情景重算
- SQLite（`data/db/lifestyle.db`，标准库 `sqlite3`，零额外依赖与云服务的离线演示）保存
  `runs / blindspots / coverage / agent_runs / traces` 五张表；主键是 `mode + 坐标(4 位小数)`
  派生的 scene_key，**同一社区重复体检就地更新并累加 run_count**，历史列表是"N 个社区"而不是"N 次点击"
- 只存**派生结果**（清洗后的设施要点、覆盖率、盲区、计量指标），百度的原始响应永不落库，
  既避免原始数据再分发的合规风险，又把单条记录控制在几十 KB
- 等时圈的原始「方位角-半径-耗时/距离」序列随结果返回，因此 5/10/15/20 分钟情景与
  成人/老年人/儿童步速换算只是**换一次插值阈值**，`api_calls_for_scenarios = 0`
- 人群换算方向：百度实测秒数隐含约 1.25 m/s，老人走同一段路要多花 `1.25/0.8` 倍时间，
  故"老人 15 分钟可达"等价于"实测秒数 ≤ 900×0.8/1.25 = 576 秒"——阈值收窄，可达面积随之缩小
- 每份结果带 `data_mode`（real / real+degraded / mock）与 `evidence_id`
  （对采样中心、射线数、半径与全部实测耗时取 sha256 前 16 位），改任一采样点即失效

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
   - 用 **Docker 部署**：三把密钥（`BMAP_SERVER_AK`、`VITE_BMAP_AK`、`LLM_API_KEY`）都写在根目录 `.env` 里，
     `docker compose` 会用它做变量插值并把浏览器端 AK 注入前端构建产物。
   - 用 **本地 npm 开发**：前端 AK 另放 `frontend/.env.local`（`cp frontend/.env.example frontend/.env.local`），
     Vite 只读该文件。
3. （可选）规划智能体：在 `.env` 填 `LLM_API_KEY`（OpenAI 兼容接口，默认 DeepSeek）。
   留空则 AI 面板自动运行在规则模式，其余功能不受影响。

### Docker 一键启动
```bash
docker compose up --build
# 前端 http://localhost:5173  后端 http://localhost:8000/docs
```

> 国内网络下若 `--build` 卡在拉取基础镜像（buildkit 不继承 Docker Desktop 的代理设置），
> 先手动把三个基础镜像拉下来再构建即可绕过：
> ```bash
> docker pull python:3.12-slim && docker pull node:20-alpine && docker pull nginx:alpine
> DOCKER_BUILDKIT=0 docker compose build && docker compose up -d
> ```

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

## 部署上线（多用户，可选）

默认 `AUTH_ENABLED=0`：单机、无登录、无跨域限制，上面的所有自测与录屏都走这条路径。
要放到公网给多人自助使用时，把 `AUTH_ENABLED` 置 1，此时：

| 变化 | 说明 |
|---|---|
| 账号与会话 | 账号库 MySQL 8（公网多用户）或 SQLite（单机带门禁，与档案同文件）：`users/sessions/quotas/rate_log` 四张表两边同构；口令 argon2id 加盐哈希，会话 cookie 只存 token 的 SHA-256，签名走 `SECRET_KEY` |
| 数据隔离 | 缓存目录与档案 `scene_key` 都按 `u<用户id>` 分片：同社区两人各存一份，互相读不到（读别人的 id 一律 404，不区分"不存在"与"越权"） |
| 密钥去向 | **默认只存用户自己浏览器的 localStorage**，随请求头 `X-Bmap-AK`/`X-Llm-Key` 带上、服务端不留副本；用户主动勾"加密保存到本账户"才用 `MASTER_KEY` Fernet 加密入库，取消勾选即刻删除 |
| 额度与限速 | 每人每日 `DAILY_ANALYSIS_BUDGET` 次体检（提交即扣，防并发绕过）、每 IP 每日注册数、登录尝试 10 分钟窗口；全局 `JOB_SLOTS` 并发闸 |
| 写接口 | 一律要求登录 + CSRF 双提交（`Sec-Fetch-Site: same-origin` 亦视为同源）；`POST /api/config` 在多用户下**不允许**改数据模式与限流档位，那是部署级配置 |
| 演示模式 | 多用户下用请求级 `X-Demo-Mode: 1` 表达，不会把一个账号的开关传染给全站 |

**三种运行形态**（只靠 `.env` 两个开关切换，代码路径同一套）：

| `AUTH_ENABLED` | `DB_BACKEND` | 形态 | 账号库位置 |
|---|---|---|---|
| `0` | 任意 | 免登录单机（最省事的交付/录屏形态） | 不用 |
| `1` | `sqlite` | **单机带门禁**：重开浏览器要登录、有个人中心、按人隔离档案 | 与档案同一个 `BMAP_DB_PATH` 文件 |
| `1` | `mysql` | 公网多用户 | MySQL 8（`DB_*` 四项 + `DB_ROOT_PASSWORD`） |

启用账号后第一次注册的账号会**认领**没有归属的历史档案（`user_id IS NULL` 的那些），
否则你一登录会发现原来那批社区记录"不见了"；之后注册的账号仍各看各的。
`GET /api/health` 里的 `auth` 与 `auth_backend` 会如实报出账号库连没连上。

```bash
# 1) 服务器准备：swap、目录、证书（脚本会打印它不替你做的防火墙与百度控制台两步）
bash deploy/server-setup.sh your.domain.com

# 2) 根目录 .env：AUTH_ENABLED=1、DB_BACKEND=mysql、DB_*（含 DB_ROOT_PASSWORD）、
#    SECRET_KEY/MASTER_KEY（各生成一个随机 32 字节串）、VITE_BMAP_AK=浏览器端类型 AK
#    注意：上线实例里不要写 BMAP_SERVER_AK / LLM_API_KEY，让每个用户填自己的

# 3) 起服务（只有 nginx 边缘容器对外听 80/443，3306 与 8000 都不发布）
docker compose --project-directory . -f deploy/docker-compose.prod.yml up -d --build
curl -i https://your.domain.com/api/health

# 4) 每日备份（MySQL + SQLite 两份都备，保留 14 份）
crontab -e   # 40 3 * * * cd /srv/bisai && bash deploy/backup.sh >> /var/log/circle15-backup.log 2>&1
```

`deploy/my.cnf` 是按 2C2G 调的（关 `performance_schema` 与 binlog、256M buffer pool、40 连接），
`performance_schema` 一项在默认配置下就能吃掉小机器一半内存。前端镜像里编译进去的只有
**浏览器端类型**的地图 AK（展示底图要用，靠 Referer 白名单约束），服务端 REST 的 AK 绝不进镜像，
所以不要把构建好的前端镜像发给别人。

## 坐标系统说明
全链路统一使用百度 **BD09LL** 坐标系（REST API 与 JSAPI 一致），无需额外坐标转换；
若接入其他来源数据，先调用 `/coord/v3/convert` 转换。

## 项目结构
```
backend/app/        FastAPI 服务
  baidu_client.py     REST 客户端（签名/限流/重试/缓存/降级/Mock）
  isochrone.py        等时圈射线采样与插值
  poi.py              POI 检索与清洗
  analysis.py         体检编排：覆盖统计 + 导则达标 + 盲区三态 + 受阻代理 + 情景重算 + 计量
  standards.py        导则达标对照：7 个民生项目对齐 GB 50180-2018 / 上海 15 分钟导则半径（纯计算）
  advisor.py          选址建议：三因子评分（人气/距同类/补盲）+ 候选点步行实测复核
  report.py           Markdown 报告文书（结论/口径/证据/导则表/盲区清单/情景表）
  db.py               SQLite 持久化：runs/coverage/blindspots/agent_runs/traces + 统计
  llm.py              OpenAI 兼容 LLM 客户端（function calling + 缓存 + 重试）
  agent.py            规划智能体：工具定义、受限编排循环、规则模式降级
  main.py             HTTP API（异步任务 + 轮询 + 历史/对比/导出/统计 + 计量响应头）
  auth.py             多用户底座：MySQL 四表、argon2id 口令、签名会话、Fernet 密钥保管、限速与每日额度
  authapi.py          注册/登录/登出/我是谁 + 守卫中间件用到的判定（失败信息一律模糊）
  scope.py            请求级"当前用户"作用域：缓存子目录与凭据都跟着请求走，不写全局 settings
  baidu_client.py 补充：按服务分桶限流、日配额熔断、POI 检索停止原因登记
backend/tests/      离线自测（mock 地图 + 假 LLM，无需任何密钥）
frontend/src/       Vue3 + Vite（四列工作台栅格 + 实色纸面卡片）
  api.js              统一 fetch 包装：每个请求自动补 CSRF 与密钥头，401 广播回登录视图
  theme.css           设计令牌：低饱和鼠尾草绿/灰蓝体系、发丝边框、压缩体数字、打印态样式
  App.vue             顶栏 + 图标导轨 + 选点控制 + 地图 + 结论面板（grid 列宽实现收起）
  components/AuthPanel.vue    登录/注册（写清楚密钥与数据的去向）
  components/MapView.vue      BMapGL 底图、底部图层坞、图例、详情弹窗、路线核对链接、拖拽中心点
  components/ReportPanel.vue  体检报告（刻度分仪表、覆盖叠影柱、盲区清单、圈内设施、指标口径折叠区）
  components/AgentPanel.vue   AI 规划助手对话面板（工具轨迹实时展示）
  components/AdvisePanel.vue  选址建议（业态选择、候选排序、口径与出网成本披露）
  components/Archive.vue      历史档案与跨社区 A/B 对比
  components/SettingsPanel.vue 接入设置（模式一键切换、自填 AK/Key、密钥存放位置、账户与额度、限流档位）
scripts/            demo.ps1 / demo.sh（免密钥一键演示）、prewarm.py（真实数据预热）
deploy/             上线件：nginx.conf（TLS 边缘）、my.cnf（2C2G 调优）、docker-compose.prod.yml、
                    server-setup.sh（swap/目录/证书）、backup.sh（MySQL+SQLite 每日备份）
data/sample/        五个典型社区的派生结果样张（离线复现用，不含平台原始响应）
docs/               技术设计文档
```

## API 文档
启动后端后访问 `http://localhost:8000/docs`（OpenAPI）。

| 端点 | 说明 |
|---|---|
| `POST /api/analysis` | 提交体检任务 `{lat, lng, label?, archetype?}` → `job_id` |
| `GET /api/analysis/{job_id}` | 轮询进度 / 获取报告 JSON（含 `run_id`） |
| `POST /api/agent` | 规划智能体对话 `{message, history?, lat?, lng?}` → `job_id` |
| `GET /api/agent/{job_id}` | 轮询对话进度（含实时工具轨迹）/ 获取结论与地图指令 |
| `GET /api/agent/history` | 智能体问答历史（SQLite） |
| `GET /api/agent/{id}/trace` | 某次问答的工具调用轨迹回放 |
| `GET /api/communities` | 预设典型社区 + 本机已体检历史 |
| `GET /api/runs` / `GET /api/runs/{id}` | 历史列表 / 单份派生结果（回放零出网） |
| `DELETE /api/runs/{id}` | 删除一条历史 |
| `GET /api/runs/{id}/report.md` | 导出 Markdown 体检报告 |
| `POST /api/runs/{id}/public` | 把某条报告设为公开（分享链接可匿名读取），再设回 false 即收回 |
| `POST /api/auth/register` / `/login` / `/logout` | 仅 `AUTH_ENABLED=1`：注册/登录下发 `sid`(HttpOnly)+`csrf` 双提交 cookie |
| `GET /api/auth/me` | 是否启用多用户、当前身份、今日剩余体检次数 |
| `GET /api/compare?ids=a,b` | 跨社区 A/B 对比（逐类别差值） |
| `GET /api/biz-types` | 选址建议支持的业态清单（同类间距来自后端单一口径） |
| `POST /api/advise` | 选址建议 `{lat, lng, biz, top?}`：复用已建档结果，只为候选点补步行实测 |
| `GET /api/stats` | 累计观测：社区数、出网、配额单位、缓存命中率、盲区数 |
| `GET /api/preview` | 中心点合法性轻量探测 |
| `GET /api/health` | 服务、Mock、配额熔断与大模型配置状态 |
| `GET /api/config` | 当前接入配置（模式、掩码后的 AK/Key、限流档位与来源） |
| `POST /api/config` | 运行时切换 mock/real、替换密钥与 QPS（仅内存，重启回到 `.env`） |
| `POST /api/config/verify` | 只测不落地：用真实请求校验 AK / 大模型 key 是否可用 |

每个响应都带 `X-Elapsed-Ms` / `X-Api-Calls` / `X-Quota-Units` / `X-Cache-Hits` 头，
`curl -i` 即可自证计量。

## 交付文档与证据件

| 文件 | 内容 |
|---|---|
| `docs/技术设计文档.md` | 赛题第 3 条要求的全部五节：API 调用策略 / 等时圈算法 / POI 清洗 / 盲区识别 / 真实对比测试报告，另附显式偏离与未验证项清单 |
| `docs/bench_route_strategy.json` | 批量 vs 逐条的出网基准：24 对路线 = 24 次请求 vs 1 次请求，配额单位相同；20 次请求预算下覆盖率 83% vs 100%（`scripts/bench_route_strategy.py` 生成） |
| `docs/capability_manifest.json` | 百度开放能力可用/不可用清单与决策依据（含"无公开步行等时圈 API"的核实记录） |
| `data/sample/*.json` | 五个典型社区的派生结果样张，可离线复现报告，不含平台原始响应 |

## 测试
```bash
# 智能体离线自测：mock 地图 + 假 LLM，覆盖降级路径、工具编排、参数纠错、故障回落
python backend/tests/test_agent_offline.py

# 持久化/情景/三态离线自测：临时 SQLite，校验 scene_key 就地更新、人群换算方向、
# 顶点标签守恒、检索查全判据与 Markdown 报告结构
python backend/tests/test_persistence_offline.py

# 新模块离线自测：距离口径校核（防止 (lat,lng) 写反退化）、导则四种状态取值、
# 住宅小区名称过滤与楼号合并、候选间距/top 限制、步行实测出网上限、口径随结果返回
python backend/tests/test_modules_offline.py

# 认证离线自测（不连库、CI 必跑）：argon2id 校验、签名篡改、CSRF 配对、掩码、
# 限速窗口、额度扣减方向、AUTH_ENABLED=0 时整条链路放行
python backend/tests/test_auth_offline.py

# 多用户自测：
#   test_auth_mysql.py     账号生命周期直连真 MySQL（没配 DB_PASSWORD 自动 SKIP）
#   test_auth_sqlite.py    同一套账号层跑在 SQLite 上（不装 MySQL、零密钥，CI 必跑）：
#                          建表幂等→注册→重复邮箱→会话解析/篡改/吊销→额度扣减→
#                          密文往返与"取消即删"→限速→级联清理→无主旧档案认领
#   test_auth_http.py      HTTP 层隔离，账号库跟随 DB_BACKEND：A/B 互不可见、越权 404、
#                          公开分享只读、缺 CSRF 403、额度耗尽 429 且不影响他人、
#                          会话 cookie 不带 max-age、密钥"只在浏览器/入库/取消即删"
python backend/tests/test_auth_mysql.py
python backend/tests/test_auth_sqlite.py
python backend/tests/test_auth_http.py

# 出网策略基准（零真实出网，产出 docs/bench_route_strategy.json）
python scripts/bench_route_strategy.py

# CI（GitHub Actions）每次 push 自动执行：ruff 检查 + 离线自测（含认证离线套件）+ Mock 冒烟 + 前端构建
```

## License
[MIT](LICENSE)
