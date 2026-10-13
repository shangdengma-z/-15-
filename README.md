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
   POI 的真实步行距离复核，剔除直线误报（如河桥绕行），保证报告可信。

### API 工程优化
- 令牌桶限流（`BMAP_QPS`）+ 信号量并发（`BMAP_CONCURRENCY`），贴合个人 AK 配额
- 指数退避重试（code=4/10 限流类错误），超限后**降级为直线估算并在报告中明确标注**
- 全量请求级磁盘缓存（`data/cache/`，演示前预热一次即可秒级复现）
- POI 数据清洗：多关键词并查 + 名称白名单二次过滤（剔除保健品店误入"药店"等）+ uid 去重

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
  main.py             HTTP API（异步任务 + 轮询）
frontend/src/       Vue3 + Vite
  components/MapView.vue      BMapGL 地图可视化
  components/ReportPanel.vue  ECharts 体检报告
```

## API 文档
启动后端后访问 `http://localhost:8000/docs`（OpenAPI）。

| 端点 | 说明 |
|---|---|
| `POST /api/analysis` | 提交体检任务 `{lat, lng}` → `job_id` |
| `GET /api/analysis/{job_id}` | 轮询进度 / 获取报告 JSON |
| `GET /api/preview` | 中心点合法性轻量探测 |
| `GET /api/health` | 服务与 Mock 状态 |

## License
[MIT](LICENSE)
