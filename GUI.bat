@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
REM FxxkPush 图形界面入口（v0.8）：带控制台，可看输出、可跑 --smoke 自检。
REM 正常退出（GUI 窗口关闭 → 进程退出，rc=0）时控制台**自动关闭**；
REM 仅失败时 pause 保留窗口以便阅读报错。依赖在 .venv 里（setup.bat 安装）。
pushd "%~dp0"
set "RC=1"

set "PYVER="
for /f "delims=" %%V in ('python --version 2^>^&1') do if not defined PYVER set "PYVER=%%V"
rem 绝不能 echo(!PYVER! —— Store 占位符消息里带 ">"，展开后会被 cmd 当成重定向
if "!PYVER:~0,9!"=="Python 3" goto :with_python

where uv >nul 2>nul
if not errorlevel 1 goto :with_uv

set "PYVER3="
for /f "delims=" %%V in ('python3 --version 2^>^&1') do if not defined PYVER3 set "PYVER3=%%V"
if "!PYVER3:~0,9!"=="Python 3" goto :with_python3

echo [失败] 未找到可用的 Python3。
echo        请安装 Python 3.12+（https://www.python.org）或 uv（https://uv.sh）
set "RC=2"
goto :end

:with_python
python fxxkpush_gui.py %*
set "RC=!errorlevel!"
goto :end

:with_uv
uv run --python 3.12 python fxxkpush_gui.py %*
set "RC=!errorlevel!"
goto :end

:with_python3
python3 fxxkpush_gui.py %*
set "RC=!errorlevel!"
goto :end

:end
popd
if not "!RC!"=="0" pause
exit /b !RC!
