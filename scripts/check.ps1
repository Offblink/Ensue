# Ensue 门禁：ruff 检查 + 格式 + 测试（在仓库根跑）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Step([string]$Name, [scriptblock]$Body) {
    Write-Host "== $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "!! $Name 失败（退出码 $LASTEXITCODE）" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Step "ruff check" { ruff check . }
Step "ruff format --check" { ruff format --check . }
# 用 .venv 的解释器跑测试：全局 qfluentwidgets 是 PyQt5 版，会跟 PySide6 界面冲突
$env:PYTHONIOENCODING = "utf-8"
Step "pytest" { & "$PSScriptRoot\..\.venv\Scripts\python.exe" -m pytest }
Write-Host "全部通过" -ForegroundColor Green
