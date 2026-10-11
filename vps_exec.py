#!/usr/bin/env python3
"""FuckPush VPS executor: run commands on the VPS over SSH (password auth).

Usage:
    python vps_exec.py "command"              # run one command, stream output
    python vps_exec.py --put local remote     # upload a file
    python vps_exec.py --get remote local     # download a file

Credentials come from fp.config.json (vps block) via pc/core/fpconfig.py ——
the same single config the PC services read (never committed to git).
"""
import sys
import os
import threading
import time
from pathlib import Path

import paramiko

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "pc" / "core"))
import paths
import fpconfig


def load_secret():
    try:
        return list(fpconfig.vps())          # (host, port, user, password)
    except ValueError as e:
        raise SystemExit(str(e))             # 缺配置：给出人能看懂的一句话


def connect():
    host, port, user, password = load_secret()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(host, port=int(port), username=user, password=password,
                timeout=15, banner_timeout=15, auth_timeout=15,
                look_for_keys=False, allow_agent=False)
    return cli


def run(cli, cmd):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=120)
    # 两条流必须**同时**在读：paramiko 的通道窗口只有 2MB
    # （DEFAULT_WINDOW_SIZE = 64*2**15），本地一直不 recv stderr 的话窗口很快
    # 耗尽 → 远端写 stderr 阻塞 → 永远走不到 stdout 的 EOF，而下面的
    # stdout.read() 正在等 EOF —— 两边互相等死。exec_command 的 timeout 只管
    # 「发起命令」，管不到这里的 read，所以一挂就是挂到 Ctrl+C。
    # stderr 交给后台线程排空；打印顺序保持原样（先 stdout 后 stderr）。
    err_chunks: list[bytes] = []
    t = threading.Thread(target=lambda: err_chunks.append(stderr.read()), daemon=True)
    t.start()
    out = stdout.read().decode("utf-8", "replace")
    t.join(30)                      # 通道关闭后 stderr.read() 立即返回，正常恒在毫秒级
    err = b"".join(err_chunks).decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)
    return rc


def put(cli, local, remote, retries=3):
    """Upload via a temp file + atomic rename.

    A dropped connection mid-transfer truncates whatever the SFTP client
    opened, so never write straight to the destination: a flaky link would
    leave an empty (or half-written) file behind and the service would die
    on next restart. Upload to <remote>.tmp, verify the size, then move.
    """
    want = os.path.getsize(local)
    last_err = None
    for attempt in range(1, retries + 1):
        sftp = None
        try:
            sftp = cli.open_sftp()
            tmp = remote + ".tmp"
            sftp.put(local, tmp)
            got = sftp.stat(tmp).st_size
            if got != want:
                raise IOError(f"short upload: {got} of {want} bytes")
            try:
                sftp.remove(remote)
            except IOError:
                pass
            sftp.rename(tmp, remote)
            final = sftp.stat(remote).st_size
            if final != want:
                raise IOError(f"rename mismatch: {final} of {want} bytes")
            print(f"uploaded {local} -> {remote} ({final} bytes)")
            return
        except Exception as e:
            last_err = e
            print(f"upload attempt {attempt}/{retries} failed: {e!r}", file=sys.stderr)
            time.sleep(3)
        finally:
            # 失败重试路径原来**不关 channel**：3 次尝试就是 3 个挂着的
            # SFTP channel，全堆在一个进程里直到退出。close 幂等，成功路径
            # 上的显式 close 一并去掉，只留这一处（顺序：先算 final 再关）。
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
    raise SystemExit(f"upload failed after {retries} attempts: {last_err!r}")


def get(cli, remote, local):
    sftp = cli.open_sftp()
    try:
        sftp.get(remote, local)
        print(f"downloaded {remote} -> {local}")
    finally:
        try:
            sftp.close()
        except Exception:
            pass


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cli = connect()
    try:
        if sys.argv[1] == "--put":
            put(cli, sys.argv[2], sys.argv[3])
            return 0
        if sys.argv[1] == "--get":
            get(cli, sys.argv[2], sys.argv[3])
            return 0
        return run(cli, sys.argv[1])
    finally:
        cli.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
