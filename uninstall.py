#!/usr/bin/env python3
"""FxxkPush 一键卸载（纯标准库，与 setup.bat 向导对称）。

用法：
    uninstall.py                 交互确认后卸载（数据默认保留）
    uninstall.py --yes           免确认（脚本化）
    uninstall.py --purge         连 fp.config.json 与运行数据一并删除（不可恢复）
    uninstall.py --dry-run       只打印将执行的动作，不改动任何东西

卸载动作（顺序固定）：
  1. 停止全部 FxxkPush 服务进程（按命令行匹配 fuckpush/pc，不动其它 pythonw）
  2. 删除 HKCU Run 的 6 个 FuckPush_* 自启项（含 StartupApproved 残留）
  3. 把挪到屏外的聊天窗口拉回屏内（vision_listener unpark）
  4.（--purge）删除 fp.config.json、pc/logs 与运行状态数据

不会碰：仓库代码、.venv、其它 .secret（cf/maddy/vps110 等非本项目文件）、
VPS 侧任何东西（停用 VPS 见结尾提示）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import winreg
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _detect_root() -> Path:
    # frozen：exe 目录（与 paths.APP_DIR 同口径），磁盘上没有 pc/services/*.py
    # —— 原来这个 sys.exit 会让「exe --role uninstall」在分发到的第一行就退出，
    # 根本走不到 step_purge。
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve().parent
    for cand in (Path.cwd().resolve(), here):
        if (cand / "pc" / "services" / "ai_triager.py").exists():
            return cand
    sys.exit("[失败] 当前目录和脚本目录都不是 FxxkPush 项目根"
             "（缺 pc\\services\\ai_triager.py）")


ROOT = _detect_root()
sys.path.insert(0, str(ROOT / "pc" / "core"))
import paths  # noqa: E402   唯一路径真源（PyInstaller 打包前置）
# pythonw：unpark 子进程与自启语义都要无窗口（python.exe 会弹控制台）
VENV_PY = paths.venv_dir() / "Scripts" / "pythonw.exe"
# 子进程 stdout 重定向到管道时按 locale(cp936) 写中文，而本进程按 utf-8 解码 ——
# unpark 的归位结果在卸载输出里全是乱码。只改子进程 stdio 编码，不动其它行为。
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SA_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
AUTOSTART = {
    "ntfy_tunnel": "FuckPush_tunnel",
    "pc_subscriber": "FuckPush_subscriber",
    "notification_listener": "FuckPush_listener",
    "ai_triager": "FuckPush_triager",
    "vision_listener": "FuckPush_wechat_vision",
    "watchdog": "FuckPush_watchdog",
}

# 只按命令行特征匹配本项目进程，绝不按镜像名一刀切（会误杀其它 pythonw）
_PS_LIST = (
    'Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" | '
    "Where-Object { $_.CommandLine -match 'fuckpush[\\\\/]pc[\\\\/]' } | "
    "ForEach-Object { Write-Output $_.ProcessId }"
)
_PS_KILL = (
    'Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" | '
    "Where-Object { $_.CommandLine -match 'fuckpush[\\\\/]pc[\\\\/]' } | "
    "ForEach-Object { Write-Output $_.ProcessId; "
    "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)

# 运行数据文件名（相对 paths.DATA_DIR）：归档 jsonl / 状态 json / 规则 json / 锁。
# 锁文件的真实位置就是 DATA_DIR 根（start_services.LOCK = HERE / ".start_services.lock"，
# HERE = paths.PC_DIR = DATA_DIR）—— 原来写成 pc/bootstrap/ 是照着文件所在目录猜的，
# 那个路径**永远不存在**，--purge 会报「已清除 N 项」却把锁文件留在原地。
# frozen 下 DATA_DIR = exe 同级 data/，这批文件与 config/logs 同层，清一处即全清。
PURGE_NAMES = [
    "feedback.jsonl",
    "notifications.jsonl",
    "toast_echo.jsonl",
    "triage_log.jsonl",
    "vision_log.jsonl",
    "listener_state.json",
    "triage_state.json",
    "vision_state.json",
    "triage_rules.json",
    ".start_services.lock",
]


def purge_targets() -> list[Path]:
    """--purge 要删的路径（由 paths 推导，不再手写相对路径）。

    非 frozen（DATA_DIR = 仓库根 pc/，与改造前逐字节等价）：
        <仓库根>/fp.config.json      （= paths.config_path()）
        <仓库根>/pc/logs/            （= paths.log_dir()，目录整体）
        <仓库根>/pc/*.jsonl|*.json|锁   （= paths.data_file()）
    frozen（DATA_DIR = exe 同级 data/，即 paths.PC_DIR）：同一份清单落到
        <exe 目录>/data/…            （= paths.data_file() / paths.log_dir()）
    原来这张表是手写的仓库相对路径，frozen 下算出来的目标是「exe 旁边的
    <exe目录>/fp.config.json」与「<exe目录>/pc/logs」—— 恰恰是**打包后一个都
    不存在**的地方，于是 --purge 打印「无待删除文件」，用户的配置、密钥、
    归档、状态、日志一个都没删，而界面说的是「已清除」。锁文件那一条曾经
    写成 pc/bootstrap/ 也是同类错误（真实位置在 pc/）。

    与 paths._LEGACY_FILES 的运行数据口径保持一致（同一批数据，两处名字
    不同必然漂移）。
    """
    out = [paths.config_path(), paths.log_dir()]
    out += [paths.data_file(n) for n in PURGE_NAMES]
    return out


def say(msg: str = "") -> None:
    print(msg, flush=True)


def ok(msg: str) -> None:
    say(f"  [OK] {msg}")


def warn(msg: str) -> None:
    say(f"  [!!] {msg}")


# 与 setup.py 的同名辅助保持一致。原来漏了这个定义：step_stop / step_autostart
# 的**失败分支**（杀不掉进程、读 Run 键失败、删值失败）一执行就是 NameError，
# 异常冒到 main 之外 —— 卸载打不出那句「仍有 N 个进程未退出」，用户只看到
# traceback，还以为卸载工具本身坏了（错误分支恰好是最需要说明的分支）。
def fail(msg: str) -> None:
    say(f"  [X ] {msg}")


def run_ps(cmd: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            # **只给 CREATE_NO_WINDOW，不给 DETACHED_PROCESS**（实测 2026-10-11，
            # 见下）。pythonw 父进程下不加标志会弹控制台黑窗，那条理由成立；
            # 但 DETACHED_PROCESS 让 Get-CimInstance Win32_Process **静默返回空
            # stdout 且 rc=0**：同一台机器上 12 个本项目进程在跑，无标志 12 个、
            # 只给 CREATE_NO_WINDOW 12 个、给了 DETACHED_PROCESS 0 个。
            # rc=0 让 step_stop 的「rc!=0 算枚举失败」那道闸也拦不住，于是
            # 卸载会打印「无运行中的服务进程」直接跳过停服，用户以为服务停了，
            # 实际 12 个照跑（接着重装就是新旧并存）。正对 r7-12 的教训。
            # CREATE_NO_WINDOW 已足够消除黑窗，不需要 DETACHED。
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:                                  # noqa: BLE001
        return 1, repr(e)


def list_service_pids() -> tuple[int, list[str]]:
    """(0, pid 列表)。走 procspawn.our_procs —— 本项目进程的唯一判定口径。

    原实现用 PowerShell 按 `pythonw.exe` + `fuckpush\\pc\\` 正则硬匹配，frozen
    （打包 exe）下**一个都匹配不到**：exe 进程叫 FxxkPush.exe 不叫 pythonw.exe。
    更糟的是它在源码形态下**会匹配到别的副本** —— 同机同时存在源码树与安装版时，
    卸载安装版会把源码树在跑的 12 个服务一起 Stop-Process（实测真发生了，
    生产环境被误停，紧急重启才恢复）。procspawn.our_procs 按 frozen 语义各自
    精确识别「本份安装拉起的进程」，不误伤另一份。

    psutil 枚举不到时抛异常 → 捕获成 rc≠0，与原 PowerShell 失败口径一致：
    枚举失败 ≠ 没有进程。
    """
    try:
        import procspawn
        return 0, [str(pid) for pid, _cl, _role in procspawn.our_procs()]
    except Exception as e:                                       # noqa: BLE001
        return -1, [f"<枚举失败: {type(e).__name__}: {e}>"]


def step_stop(dry: bool) -> bool:
    say("\n== 1/3 停止服务进程 ==")
    rc, pids = list_service_pids()
    # r7-12 同型（start_services 那侧早就修过）：**枚举失败 ≠ 没有进程**。
    # 原实现 PowerShell 被安全软件拦掉 / 冷启动失败时返回空列表 → 打印
    # 「无运行中的服务进程」继续往下走，用户以为服务停了实际还在跑
    #（接着卸载/重装就是新旧并存）。枚举不出来就如实说。
    if rc != 0:
        fail(f"无法枚举服务进程（{pids[0] if pids else '?'}），本步未确认已停止")
        return False
    if not pids:
        ok("无运行中的服务进程")
        return True
    if dry:
        say(f"  [dry] 将结束 {len(pids)} 个进程: {', '.join(pids)}")
        return True
    # kill 同样按 pid 精确终止（原先的 PowerShell 正则会连另一份副本一起杀）。
    import time

    import psutil
    killed, refused = 0, []
    for pid_s in pids:
        try:
            psutil.Process(int(pid_s)).kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            refused.append(f"{pid_s}({type(e).__name__})")
    time.sleep(1.5)
    rc2, left = list_service_pids()
    if rc2 != 0:
        fail("停止后无法枚举进程，未确认是否全部退出")
        return False
    if left:
        fail(f"仍有 {len(left)} 个进程未退出: {', '.join(left)}"
             + (f"；终止失败 {', '.join(refused)}" if refused else ""))
        return False
    ok(f"已结束 {killed} 个服务进程")
    return True


def step_autostart(dry: bool) -> bool:
    say("\n== 2/3 移除开机自启 ==")
    # **只删指向本份安装的**自启项。
    # 原实现按「值名在 AUTOSTART 里」就删 —— 六项的名字（FuckPush_tunnel 等）
    # 是固定的，源码树注册的和 exe 注册的**同名**，于是卸载安装版会把源码树
    # 挂在开机启动上的那六项一起删掉（实测真发生了：源码版开机自启被摘成 0，
    # 服务本体却还在跑，重启电脑才会暴露）。改为比对 REG_SZ 的**值**：只有指向
    # 自己（APP_DIR 内）的才删，别人的一行都不动。
    mine = str(paths.APP_DIR).lower()
    names, foreign = [], []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if name in AUTOSTART.values():
                    if mine in value.lower():
                        names.append(name)
                    else:
                        foreign.append(name)
    except OSError as e:
        fail(f"读取 Run 键失败: {e!r}")
        return False
    if foreign:
        say(f"  保留 {len(foreign)} 项指向其它安装的自启（非本程序，未动）: "
            f"{', '.join(foreign)}")
    if not names:
        ok("无本程序的自启项（可能已移除）")
        return True
    if dry:
        say(f"  [dry] 将删除 {len(names)} 项: {', '.join(names)}")
        return True
    removed = 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                            0, winreg.KEY_SET_VALUE) as key:
            for name in names:
                try:
                    winreg.DeleteValue(key, name)
                    removed += 1
                except FileNotFoundError:
                    pass
    except OSError as e:
        fail(f"删除 Run 值失败: {e!r}")
        return False
    # StartupApproved 同名残留（任务管理器里勾选状态），尽力清理
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SA_KEY,
                            0, winreg.KEY_SET_VALUE) as key:
            for name in names:
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
    except OSError:
        pass
    ok(f"已删除 {removed}/{len(names)} 项自启（含 StartupApproved 残留清理）")
    return True


def step_unpark(dry: bool) -> bool:
    say("\n== 3/3 聊天窗口拉回屏内 ==")
    vision = ROOT / "pc" / "services" / "vision_listener.py"
    if paths.is_frozen():
        # frozen 下没有 .venv 也没有这个 .py：同一个 exe 自我多路
        # （--role vision unpark），无控制台子系统，不弹黑窗。
        cmd = [sys.executable, "--role", "vision", "unpark"]
        if not getattr(sys, "frozen", False):     # 理论不可达，留作对照
            cmd = [str(VENV_PY), str(vision), "unpark"]
    else:
        if not VENV_PY.exists():
            warn(".venv 不存在，跳过（窗口可能仍在屏外；装回后可运行 "
                 "vision_listener.py unpark）")
            return True
        cmd = [str(VENV_PY), str(vision), "unpark"]
    if dry:
        say(f"  [dry] 将运行: {' '.join(cmd)}")
        return True
    try:
        p = subprocess.run(cmd,
                           input="\n", capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=str(ROOT), timeout=120, env=CHILD_ENV)
        tail = [ln for ln in ((p.stdout or "") + (p.stderr or "")).splitlines()
                if ln.strip()]
        for ln in tail[-8:]:
            say("  " + ln)
        if p.returncode != 0:
            warn(f"unpark 退出码 {p.returncode}（窗口没开的话属正常）")
        else:
            ok("窗口归位完成")
        return True
    except Exception as e:                                  # noqa: BLE001
        warn(f"unpark 执行失败: {e!r}")
        return False


def step_purge(dry: bool) -> bool:
    say("\n== 4/4 清除配置与运行数据（--purge）==")
    targets = purge_targets()
    existing = [p for p in targets if p.exists()]
    if not existing:
        ok("无待删除文件")
        return True
    if dry:
        for p in existing:
            # 绝对路径：frozen 下 DATA_DIR 在 exe 同级（可能是 %LOCALAPPDATA%
            # 兜底），不在 ROOT 之下 —— 原来的 p.relative_to(ROOT) 会在这里
            # 抛 ValueError，把 --dry-run 变成崩溃。
            say(f"  [dry] 将删除 {p}{'  [目录]' if p.is_dir() else ''}")
        return True
    for p in existing:
        try:
            if p.is_dir():
                import shutil
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
        except OSError as e:
            warn(f"删除失败 {p.name}: {e!r}")
    ok(f"已清除 {len(existing)} 项（配置/日志/状态）")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="FxxkPush 一键卸载")
    ap.add_argument("--yes", action="store_true", help="免交互确认")
    ap.add_argument("--purge", action="store_true",
                    help="连 fp.config.json 与运行数据一起删（不可恢复）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不改动")
    args = ap.parse_args()

    say("=" * 52)
    say("  FxxkPush 一键卸载" + ("（dry-run，只看不改）" if args.dry_run else ""))
    say(f"  项目根: {ROOT}")
    say("=" * 52)
    say("将执行：停服务 → 摘自启 → 窗口回屏内" +
        (" → 清除配置与数据" if args.purge else "（数据保留）"))

    purge = args.purge
    if not args.dry_run and not args.yes:
        try:
            ans = input("确认卸载？输入 yes 继续: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            say("\n已取消")
            return 130
        if ans != "yes":
            say("已取消（未做任何改动）")
            return 130
        if purge:
            try:
                ans2 = input("purge 将删除 fp.config.json（含密钥）与全部运行"
                             "数据，不可恢复！输入 purge 确认: ").strip()
            except (EOFError, KeyboardInterrupt):
                say("\n已取消")
                return 130
            if ans2 != "purge":
                purge = False
                warn("取消 purge，仅执行基础卸载（数据保留）")

    all_ok = True
    all_ok &= step_stop(args.dry_run)
    all_ok &= step_autostart(args.dry_run)
    all_ok &= step_unpark(args.dry_run)
    if purge:
        all_ok &= step_purge(args.dry_run)

    say("\n================ 卸载结果 ================")
    say("  " + ("全部完成" if all_ok else "部分步骤失败（见上方 [!!]/[X ]）"))
    if not purge:
        say("  数据保留: fp.config.json / pc/logs / 运行状态（重装直接复用）")
    say("  重装: 双击 setup.bat")
    say("  VPS 侧不受影响；如需停用：")
    say("    systemctl disable --now fuckpush-monitor ntfy")
    say("==========================================")
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\n已中断")
        sys.exit(130)
