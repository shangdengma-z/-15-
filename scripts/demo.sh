#!/usr/bin/env bash
# 一键演示（无需任何 API 密钥）：后端 Mock 模式 + 前端 Vite
# 用法：在仓库根目录执行   bash scripts/demo.sh
# 接真实路网数据：cp .env.example .env 填入 BMAP_SERVER_AK，并删掉下面的 BMAP_MOCK 行。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe          # Windows venv 布局（Git Bash 下运行本脚本时可用）
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  echo "[准备] 创建虚拟环境并安装后端依赖"
  python3 -m venv .venv
  if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
  "$PY" -m pip install -r backend/requirements.txt
fi

if [ ! -d frontend/node_modules ]; then
  echo "[准备] 安装前端依赖"
  (cd frontend && npm install)
fi

export BMAP_MOCK=1
echo "[1/2] 后端启动中（Mock 模式）-> http://localhost:8000/docs"
"$PY" -m uvicorn app.main:app --app-dir backend --port 8000 &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null || true' EXIT INT TERM

echo "[2/2] 前端启动中 -> http://localhost:5173   （Ctrl+C 退出）"
echo "      演示：点地图选中心点 -> 「开始体检」，或切到「AI 规划助手」用自然语言提问"
(cd frontend && npm run dev)
