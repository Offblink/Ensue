@echo off
chcp 65001 >nul
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo 还没有虚拟环境：先按 README 的「第一次运行」建好 .venv 再跑本脚本。
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
