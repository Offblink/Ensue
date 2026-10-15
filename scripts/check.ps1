# SealMark 门禁：ruff 检查 + 格式 + 测试（在仓库根跑）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "== ruff check" -ForegroundColor Cyan
ruff check .
Write-Host "== ruff format --check" -ForegroundColor Cyan
ruff format --check .
Write-Host "== pytest" -ForegroundColor Cyan
$env:PYTHONIOENCODING = "utf-8"
python -m pytest
Write-Host "全部通过" -ForegroundColor Green
