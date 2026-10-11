@echo off
REM ============================================================
REM  FxxkPush 一键打包脚本（PyInstaller onedir）
REM
REM  产出：dist\FxxkPush\FxxkPush.exe + dist\FxxkPush\_internal\
REM        打 zip 后上传 GitHub Release 即可分发
REM
REM  绝不影响线上正在跑的六个服务：本脚本只重建 dist\ 与 build\，
REM  不启动任何服务、不注册自启、不写任何配置。
REM
REM  文件编码 GBK + CRLF：中文 Windows 控制台（cp936）下双击不乱码。
REM  编辑时请保持编码，不要另存为 UTF-8，否则中文会变成问号。
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [失败] 找不到虚拟环境：%PY%
    echo        先建 venv 并装依赖（见 README），再跑本脚本。
    exit /b 1
)

REM ---- 先清理：只删 dist\ 与 build\，两个都是纯产物目录 ----
if exist "dist\" (
    echo [1/3] 清理旧产物 dist\
    rmdir /s /q "dist\" 2>nul
)
if exist "build\" (
    echo [2/3] 清理旧产物 build\
    rmdir /s /q "build\" 2>nul
)

echo [3/3] 开始构建（--clean --noconfirm），耗时较长，请勿关闭本窗口。
echo       开始时间 %date% %time%
"%PY%" -m PyInstaller FxxkPush.spec --clean --noconfirm
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo [失败] PyInstaller 返回码 %RC%，详见上面的输出。
    echo        失败时看 build\FxxkPush\warn-FxxkPush.txt 里的缺失模块清单。
    exit /b %RC%
)

REM ---- 产物自检：没有 exe 就不算成功（不靠退出码猜）----
if not exist "dist\FxxkPush\FxxkPush.exe" (
    echo [失败] 构建结束但没有 dist\FxxkPush\FxxkPush.exe
    exit /b 2
)

echo.
echo ===================== 构建成功 =====================
echo 结束时间 %date% %time%
call :dirsize "dist\FxxkPush" TOTAL
call :dirsize "dist\FxxkPush\_internal" INTERNAL
for %%F in ("dist\FxxkPush\FxxkPush.exe") do set "EXESIZE=%%~zF"
if not defined EXESIZE set "EXESIZE=0"

REM ---- 体积三档：exe / _internal / 合计，以及 _internal 占比 ----
REM 占比**不能**用 set /a 算：它是 32 位整数，51770633*100 = 5177063300
REM 已超 2^31，回绕后 (mod 2^32)/59161501 = 14.9 —— 打印出来就是 14%，
REM 一眼看着「合理」实际全错（实测：先乘后除 / 加括号 / 100*A/B 三种写法
REM 都错，先除后乘则整数除法归零）。直接让 PowerShell 用浮点算一位小数。
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "if (%TOTAL% -gt 0) { [math]::Round(100*%INTERNAL%/%TOTAL%,1) } else { 0 }" 2^>nul`) do set "PCT=%%L"
if not defined PCT set "PCT=0"
set /a EXE_MB=EXESIZE/1048576
set /a INT_MB=INTERNAL/1048576
set /a TOT_MB=TOTAL/1048576

echo.
echo ---- 体积统计 ----
echo   exe          : !EXE_MB! MB   (!EXESIZE! 字节)
echo   _internal    : !INT_MB! MB
echo   合计         : !TOT_MB! MB
echo   _internal 占比: !PCT!%%
echo.
echo 合计超过 250MB 时的可裁剪项（按实测收益排序）：
echo   1. 最大一块是 winrt-* 七个包（纯 WinRT 元数据，无二进制），
echo      只有「Windows 通知读取」这个 role 需要，不能盲删。
echo   2. cryptography/paramiko/cffi 是 SSH 隧道硬依赖，删不得。
echo   3. Pillow 可只留 Image + ImageGrab，去掉字体/ImageTk 等。
echo   想称任何一个可疑目录的重量，把下面这行的目标换成它：
echo   call :dirsize "dist\FxxkPush\_internal\<目录名>" BYTES
echo.
echo 后续：
echo   1) 冒烟：dist\FxxkPush\FxxkPush.exe --roles
echo             dist\FxxkPush\FxxkPush.exe --role check
echo   2) 打 zip 上传 GitHub Release：
echo      powershell -c "Compress-Archive -Path dist\FxxkPush\* -DestinationPath FxxkPush-win64.zip"
echo.
exit /b 0

REM ============================================================
REM  :dirsize <目录> <变量名> —— 累加目录下所有文件的字节数
REM  用 PowerShell 而不是 for /r：更快，且中文路径不会被切错。
REM ============================================================
:dirsize
set "DIR=%~1"
set "VAR=%~2"
set "BYTES=0"
REM 管道符**不能**写成 ^| ：在 for /f 的反引号里 cmd 会把它连同后面的
REM 空格一起传给 powershell，PowerShell 报语法错且 for /f 收不到输出
REM （实测 A 形式返回空、B 形式（不转义）返回正确值）。
REM 这里改成「先算好再 Write-Output」，命令里干脆不出现管道。
if exist "%DIR%" (
    for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "$s=(Get-ChildItem -LiteralPath '%DIR%' -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum; Write-Output $s" 2^>nul`) do (
        if not "%%L"=="" set "BYTES=%%L"
    )
)
set "%VAR%=%BYTES%"
exit /b 0