# 贡献指南

感谢你有兴趣参与本项目。请先花两分钟读完本页，能省掉后面很多来回。

## 环境与本地运行

要求：Python ≥ 3.10、Node.js ≥ 18、（可选）Docker ≥ 24。

三种起法，任选其一：

```bash
# 1) 零密钥一键演示（Mock 模式，不消耗任何配额）
bash scripts/demo.sh          # Windows PowerShell: .\scripts\demo.ps1

# 2) Docker 一键部署
docker compose up --build

# 3) 手动（接真实路网数据时需要 .env 配置 BMAP_SERVER_AK）
cp .env.example .env
python -m venv .venv && ./.venv/bin/python -m pip install -r backend/requirements.txt
./.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000 --env-file .env
cd frontend && npm install && npm run dev
```

## 提交前必须通过的检查

CI 会跑同样的命令，本地先过一遍能省一次往返：

```bash
# 代码风格（与 CI 完全一致的参数）
ruff check backend/app --select E,F,W --ignore E501

# 智能体离线自测：mock 地图 + 假 LLM，不需要任何密钥、不联网
python backend/tests/test_agent_offline.py

# 前端构建
cd frontend && npm run build
```

## 项目约定

- **后端不引入新依赖**，除非确实无法用标准库 + 现有 `httpx` 解决。依赖合规是评审项。
- **所有百度 REST 调用必须经过 `baidu_client.client.get()`**，不要在业务代码里直接发 HTTP 请求——限流、缓存、重试、降级都挂在这个入口上。
- **坐标系全链路统一 BD09LL**，不要在任何模块里偷偷转换。
- 注释只写"为什么"，不复述代码在做什么。
- 单个 Python 文件控制在 250 行内，超了说明该拆模块。

## 两个最常见的扩展任务

### 加一类民生设施

1. 在 `backend/app/config.py` 的 `CATEGORIES` 增加一项：`queries` 是发给检索接口的关键词，
   `keywords_any` 是名称白名单（用来剔除百度 tag 体系的误分类，如保健品店被归入"药店"）。
2. 前端 `components/MapView.vue` 的 `CAT_COLORS` 补一个颜色。
3. 跑一次 `python backend/tests/test_agent_offline.py` 确认没破坏智能体工具的输出结构。

### 给规划智能体加一个工具

1. 在 `backend/app/agent.py` 的 `TOOLS` 里补一段 function schema——`description` 是写给模型看的，
   越准确它越会用对。
2. 写一个 `async def _your_tool(run, **args)`，并在 `HANDLERS` 里登记同名条目。
3. 返回值必须是**压缩摘要**（几百字节），不要直接吐完整 API 响应：既烧 token 也让模型抓不住重点。
   需要给前端渲染的完整数据挂到 `run.result` / `run.map_actions` 上。
4. 在 `backend/tests/test_agent_offline.py` 里加一条假 LLM 调用该工具的用例。

## 配额与限流（重要）

个人 AK 的步行路线规划只有 **3 QPS / 5000 次每天**，是全项目的吞吐瓶颈。改采样参数前请注意：

- 一次完整体检约消耗 150 次步行调用（18 射线 × 4 半径 + 盲区核验）。
- 调大 `BMAP_QPS` 不会更快，只会换来一片 `401`（服务端按秒级窗口计）。
- 修改 `ISOCHRONE_*` 会让已预热的点位缓存部分失效，需要重新消耗配额预热。

## 分支与 PR 流程

1. 从 `main` 切分支：`feat/xxx`、`fix/xxx`、`docs/xxx`。
2. 一个 PR 只做一件事；行为变更必须附测试结果或截图。
3. PR 描述里说明动机与影响面，勾选模板中的检查项。
4. 维护者会在 3 个工作日内首次响应；被拒的 PR 会说明原因并保留分支记录。

## 许可

提交即同意你的贡献以 [MIT](../LICENSE) 许可纳入本项目。
