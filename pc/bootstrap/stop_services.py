#!/usr/bin/env python3
"""停止本项目拉起的全部服务进程（**不**动自启、**不**动数据）。

为什么需要它：
  安装器（Inno Setup）在覆盖安装时，如果旧的 FxxkPush.exe 还在跑，
  Inno 覆盖 exe 文件会因文件占用而失败 —— 表现为「安装报���」。
  所以安装流程要在**装之前**先把旧进程停掉。

为什么不用 uninstall.py：
  uninstall.py --yes 是「卸载」语义 —— 它会停服务、**并且摘掉 6 项开机自启**。
  安装时用它是错的：装完还要自启，摘了得再开一次。
  这里只要「停进程」这一件事，其余一律不碰。

判定口径：
  复用 procspawn.our_procs() —— 本项目进程的唯一判定口径。
  frozen（打包 exe）下认「exe 名 + --role <已知 role>」，源码态认
  「pythonw.exe + 命令行含 fuckpush\\pc\\」。绝不按名字模糊匹配，
  否则同机存在源码树与安装版两份时会误停另一份（这个坑 uninstall.py
  已经踩过一次并留下了注释）。

用法：
  FxxkPush.exe --role stop          # 停全部
  FxxkPush.exe --role stop --dry-run # 只列不杀
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parent), str(HERE.parent / "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def say(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="停止 FxxkPush 服务进程（不动自启与数据）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要结束的进程，不真杀")
    args = ap.parse_args()

    try:
        import procspawn
    except Exception as e:  # noqa: BLE001
        say("[失败] 无法导入 procspawn: %r" % (e,))
        return 1

    _enum_ok = False

    # 两种形态都要查：
    #   frozen=True  → 认「exe 名 + --role <已知 role>」（打包版）
    #   frozen=False → 认「pythonw.exe + 命令行含 fuckpush\pc\」（源码版）
    # 为什么要都查：procspawn.our_procs() 的 frozen 判定问的是「**当前**进程
    # 是不是 frozen」。安装器调的是旧 exe（frozen）→ 天然能停打包版；
    # 但源码态手动跑 --role stop 时当前进程是 python → 只认 pythonw，
    # 就看不到同机在跑的打包版。两种都查，任一形态都能停干净。
    # 三种口径都查，任一形态都能停干净：
    #   exe_name="FxxkPush.exe" → 打包版（跨形态的关键，见 our_procs 的说明）
    #   frozen=True              → 当前进程是打包版时，认「exe 名 + --role」
    #   frozen=False             → 源码版（pythonw.exe + fuckpush\pc\）
    me = os.getpid()
    procs: list[tuple[int, str, str | None]] = []
    seen_pids: set[int] = set()
    modes = [("打包版(exe)", {"exe_name": "FxxkPush.exe"}),
             ("打包版(self)", {"frozen": True}),
             ("源码版", {"frozen": False})]
    for label, kw in modes:
        try:
            for item in procspawn.our_procs(**kw):
                if item[0] in seen_pids:
                    continue
                seen_pids.add(item[0])
                procs.append(item)
        except Exception as e:  # noqa: BLE001
            say("[警告] 枚举进程（%s）失败: %r" % (label, e))
        else:
            _enum_ok = True
    # 排除自己 —— 当前这个 --role stop 进程也是「本项目的进程」，
    # 杀了自己就没法回报结果了。
    #
    # 两道排除，缺一不可：
    #   1) pid 相同（同一进程内的 runpy 场景）
    #   2) **命令行含 "--role stop"**（打包态的关键场景）：
    #      装成 exe 后，PrepareToInstall 调旧 exe --role stop，
    #      这个进程的**命令行**就含 "--role stop"，our_procs 的 role_of()
    #      会把它认成 role=stop 的本项目进程 —— 不排除的话它会**把自己杀了**，
    #      进程在 kill 循环中途死掉，后续进程可能没杀完，
    #      Inno 紧接着覆盖 exe 仍撞占用（就是这次要修的原始 bug）。
    procs = [x for x in procs
             if x[0] != me and "--role stop" not in (x[1] or "").lower()]
    if not procs and not _enum_ok:
        return 1

    if not procs:
        say("无运行中的 FxxkPush 服务进程，无需停止。")
        return 0

    say("运行中的 FxxkPush 进程 %d 个:" % len(procs))
    for pid, cmd, role in procs:
        say("  pid=%-7s role=%-14s %s" % (pid, role or "?", (cmd or "")[:80]))

    if args.dry_run:
        say("[dry-run] 未结束任何进程。")
        return 0

    import psutil

    killed, refused = 0, []
    for pid, _cmd, _role in procs:
        try:
            psutil.Process(int(pid)).kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            refused.append("%s(%s)" % (pid, type(e).__name__))

    # 等进程真正退出 —— 否则 Inno 紧接着覆盖 exe 仍可能撞上文件占用。
    # 2 秒不够就再等，最多 8 秒；仍未退净就如实报告（安装器会继续，
    # 让 Inno 自己去撞占用并给出更明确的错误）。
    deadline = time.time() + 8
    left: list[str] = []
    while time.time() < deadline:
        time.sleep(0.5)
        left = []
        try:
            for _label, kw in modes:
                for pid, cl, _r in procspawn.our_procs(**kw):
                    if pid == me or "--role stop" in (cl or "").lower():
                        continue
                    if str(pid) not in left:
                        left.append(str(pid))
        except Exception:  # noqa: BLE001
            left = []
            break
        if not left:
            break

    if left:
        say("[警告] 仍有 %d 个进程未退出: %s" % (len(left), ", ".join(left)))
        say("       若安装时报文件占用，可手动结束这些进程后重试。")
        return 1

    say("已结束 %d 个进程%s。" % (killed, ("；%d 个终止失败(%s)" % (len(refused), ", ".join(refused))) if refused else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
