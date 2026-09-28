@echo off
rem In Verus 编辑器启动脚本（Windows）：优先使用项目虚拟环境 .venv
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_editor.py %*
) else (
    python run_editor.py %*
)
endlocal
