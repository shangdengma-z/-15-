# 一键演示（无需任何 API 密钥）
# 用法：在仓库根目录执行   .\scripts\demo.ps1
# 后端以 Mock 模式启动（不消耗百度配额、不需要 AK/SK），前端照常渲染完整功能。
# 要接真实路网数据：把 .env.example 复制为 .env 填入 BMAP_SERVER_AK，然后去掉下面的 Mock 环境变量。

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "[准备] 创建虚拟环境并安装后端依赖"
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
}

if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "[准备] 安装前端依赖"
    Push-Location frontend
    npm install
    Pop-Location
}

$env:BMAP_MOCK = "1"
Write-Host "[1/2] 后端启动中（Mock 模式）-> http://localhost:8000/docs" -ForegroundColor Cyan
$backend = Start-Process -FilePath ".\.venv\Scripts\python.exe" `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--port", "8000" `
    -PassThru -NoNewWindow

Write-Host "[2/2] 前端启动中 -> http://localhost:5173  （Ctrl+C 退出）" -ForegroundColor Cyan
Write-Host "提示：演示时点地图选中心点 -> 「开始体检」，或切到「AI 规划助手」用自然语言提问。" -ForegroundColor Yellow
Write-Host "      预置演示点位：回龙观 40.0703, 116.3306（Mock 下每次随机生成数据）" -ForegroundColor Yellow

try {
    Push-Location frontend
    npm run dev
} finally {
    Write-Host "[清理] 停止后端进程" -ForegroundColor Cyan
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
}
