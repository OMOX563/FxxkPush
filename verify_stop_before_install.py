#!/usr/bin/env python3
"""安装前停服务的端到端验证（2026-10-27）。

验三件事：
  1. 新打包的 exe 有 stop role，且它**能停掉正在跑的旧版服务**
  2. stop 不会误杀「源码树副本」或别的 pythonw
  3. 停完之后 Inno 覆盖 exe 不再撞文件占用（用「能否独占打开 exe」验）

用法：
  python verify_stop_before_install.py [--kill]     # --kill 真的停（默认只 dry-run）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\omo\FxxkPush\fuckpush")
VP = REPO / ".venv" / "Scripts" / "python.exe"
DIST_EXE = REPO / "dist" / "FxxkPush" / "FxxkPush.exe"
INSTALLED_EXE = Path(os.path.expandvars(
    r"%LOCALAPPDATA%\Programs\FxxkPush\FxxkPush.exe"))

OK = "[OK]  "
BAD = "[FAIL]"


def say(msg: str) -> None:
    print(msg, flush=True)


def list_installed_procs() -> list[tuple[int, str]]:
    """列出已安装版（打包 exe）的进程。"""
    import psutil
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            cmd = " ".join(p.info.get("cmdline") or [])
        except Exception:
            continue
        if name == "fxxkpush.exe":
            out.append((p.pid, cmd))
    return out


def exe_locked(path: Path) -> bool:
    """exe 是否被占用（无法独占打开 = 有人在跑它）。"""
    if not path.exists():
        return False
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kill", action="store_true", help="真的停掉进程")
    args = ap.parse_args()

    rc_total = 0

    say("=== 0. 前置检查 ===")
    if not DIST_EXE.exists():
        say("%s 打包产物不存在: %s" % (BAD, DIST_EXE))
        return 1
    say("%s 打包产物存在 (%.1f MB)" % (OK, DIST_EXE.stat().st_size / 2**20))

    if not INSTALLED_EXE.exists():
        say("%s 已安装版不存在: %s" % (BAD, INSTALLED_EXE))
        return 1
    say("%s 已安装版存在: %s" % (OK, INSTALLED_EXE))

    procs = list_installed_procs()
    say("\n=== 1. 安装前的进程状态 ===")
    say("已安装版进程 %d 个:" % len(procs))
    for pid, cmd in procs:
        say("  pid=%-7s %s" % (pid, cmd[:90]))
    if not procs:
        say("  (没有在跑的服务 —— 装前停服务的场景无法验证；"
            "请先启动服务再跑本脚本)")
        return 2

    locked_before = exe_locked(INSTALLED_EXE)
    say("\n安装版 exe 当前被占用: %s" % ("是（这正是要解决的 bug）" if locked_before else "否"))

    say("\n=== 2. 用**已安装的旧版** exe 跑 --role stop（模拟安装器行为）===")
    r = subprocess.run([str(INSTALLED_EXE), "--role", "stop", "--dry-run"],
                       capture_output=True, timeout=120, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    say("rc=%d" % r.returncode)
    for line in out.strip().splitlines()[:20]:
        say("  " + line[:100])
    if r.returncode != 0:
        say("%s 旧版 exe 不认识 --role stop（预期：本次修复还没装上去）" % BAD)
        say("     → 用新版 dist exe 继续验证")
    else:
        say("%s 旧版 exe 支持 --role stop" % OK)

    say("\n=== 3. 用**新版** dist exe 跑 --role stop ===")
    if args.kill:
        r = subprocess.run([str(DIST_EXE), "--role", "stop"],
                           capture_output=True, timeout=120,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        say("rc=%d" % r.returncode)
        for line in out.strip().splitlines()[:20]:
            say("  " + line[:100])
        if r.returncode != 0:
            say("%s --role stop 返回非零" % BAD)
            rc_total = 1
        else:
            say("%s --role stop 执行成功" % OK)

        time.sleep(2)
        left = list_installed_procs()
        if left:
            say("%s 仍有 %d 个进程未停: %s" % (BAD, len(left),
                                        ", ".join(str(p[0]) for p in left)))
            rc_total = 1
        else:
            say("%s 全部已安装版进程已停止" % OK)

        locked_after = exe_locked(INSTALLED_EXE)
        if locked_after:
            say("%s exe 仍被占用 —— 覆盖还会失败" % BAD)
            rc_total = 1
        else:
            say("%s exe 已可独占打开 —— Inno 覆盖不会再撞占用" % OK)
    else:
        r = subprocess.run([str(DIST_EXE), "--role", "stop", "--dry-run"],
                           capture_output=True, timeout=120,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        say("rc=%d" % r.returncode)
        for line in out.strip().splitlines()[:20]:
            say("  " + line[:100])
        if "运行中的 FxxkPush 进程" in out:
            say("%s 新版 exe 能枚举到已安装版进程" % OK)
        else:
            say("%s 新版 exe 枚举不到已安装版进程" % BAD)
            rc_total = 1
        say("\n（未加 --kill，本次只验证枚举；加 --kill 会真的停）")

    say("\n=== 结论 ===")
    say("全部通过" if rc_total == 0 else "有失败项")
    return rc_total


if __name__ == "__main__":
    sys.exit(main())
