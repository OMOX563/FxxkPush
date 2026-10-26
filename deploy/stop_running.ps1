# FxxkPush 安装前停服务脚本（2026-10-27）
#
# 用途：安装器在覆盖 exe 之前调用它，结束**正在占用** {app}\FxxkPush.exe 的进程。
# 现象：程序在运行时点安装 → Inno 覆盖 FxxkPush.exe 失败 → 安装报错。
#
# 关键设计：**按完整 exe 路径匹配，不是按进程名**。
#   同机可能同时存在：
#     · 安装版  → C:\Users\<user>\AppData\Local\Programs\FxxkPush\FxxkPush.exe
#     · 源码树  → C:\Users\<user>\FxxkPush\fuckpush\.venv\Scripts\pythonw.exe
#   按名字（IMAGENAME eq FxxkPush.exe）杀不到源码树那份（它叫 pythonw.exe），
#   但按名字杀 FxxkPush.exe 仍可能命中**另一台机器/另一份安装**的同名进程。
#   按 ExecutablePath 精确比路径，只命中本次安装目录这一份。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File stop_running.ps1 -ExePath "C:\...\FxxkPush.exe"
#
# 退出码：
#   0 = 已处理（可能本来就没有进程在跑）
#   1 = 出错（安装器会继续，让 Inno 自己在覆盖时报更明确的错）

param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath
)

$ErrorActionPreference = 'SilentlyContinue'

try {
    $target = [System.IO.Path]::GetFullPath($ExePath)
} catch {
    Write-Host "[stop_running] 无效路径: $ExePath"
    exit 1
}

if (-not (Test-Path -LiteralPath $target)) {
    # 首次安装：没有旧程序可停
    Write-Host "[stop_running] 未找到 $target，跳过"
    exit 0
}

$procs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and ([System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $target)
})

if ($procs.Count -eq 0) {
    Write-Host "[stop_running] 无进程占用 $target"
    exit 0
}

Write-Host "[stop_running] 结束 $($procs.Count) 个占用 $target 的进程:"
foreach ($p in $procs) {
    Write-Host "[stop_running]   pid=$($p.ProcessId) $($p.Name)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

# 等进程真正退出 —— 否则 Inno 紧接着覆盖 exe 仍可能撞上文件占用。
$deadline = (Get-Date).AddSeconds(8)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400
    $left = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and ([System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $target)
    })
    if ($left.Count -eq 0) { break }
}

$left = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and ([System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $target)
})
if ($left.Count -gt 0) {
    Write-Host "[stop_running] 警告：仍有 $($left.Count) 个进程未退出"
    foreach ($p in $left) { Write-Host "[stop_running]   pid=$($p.ProcessId)" }
    exit 1
}

Write-Host "[stop_running] 全部已退出"
exit 0
