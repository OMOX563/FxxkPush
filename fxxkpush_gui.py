#!/usr/bin/env python3
"""FxxkPush 图形化界面（v0.8）—— pywebview(EdgeChromium) + 液态玻璃 UI。

用法：
    GUI.bat                     推荐入口（要求 .venv 已装依赖）
    python fxxkpush_gui.py
    python fxxkpush_gui.py --smoke   自检（状态/配置往返/自启幂等）后真开窗数秒自动关

功能：
    仪表盘：6 服务状态 / 隧道 health / 自启 6 项 / 看门狗心跳 / 实时日志尾
    配置：表单直写 fp.config.json（**合并不覆盖** apps/vision 既有条目；
          密钥字段留空 = 保持原值）
    控制：一键启动/停止（start_services 健康门）、自启开关、窗口归位、
          一键卸载（可选清数据）、打开日志、跑 setup --check

后端与 GUI 同进程（pywebview js_api），只读状态轮询在前端 2.5s 一次；
长任务（启动/停止/归位/卸载）在后台线程，结果进 _jobs 供前端查询。
"""
from __future__ import annotations

import argparse
import copy
import json
import os                     # open_dir 的 os.startfile 用（原来漏 import）
import re
import subprocess
import sys
import threading
import time
import urllib.request
import winreg
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _detect_root() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (Path.cwd().resolve(), here):
        if (cand / "pc" / "services" / "ai_triager.py").exists():
            return cand
    sys.exit("[失败] 不是 FxxkPush 项目根（缺 pc\\services\\ai_triager.py）")


ROOT = _detect_root()
sys.path.insert(0, str(ROOT / "pc" / "core"))
import paths  # noqa: E402   唯一路径真源（PyInstaller 打包前置）
import fpconfig  # noqa: E402
import pclog  # noqa: E402   # 只用 read_tail（不 get_logger，GUI 不该持有任何服务的日志句柄）

# pythonw 而非 python.exe：python.exe 是控制台程序 —— 自启注册成它
# 会在登录时弹 6 个黑窗；pythonw 无窗口子系统，双击 GUI 也绝不带控制台。
VENV_PY = paths.venv_dir() / "Scripts" / "pythonw.exe"
GUI_HTML = paths.res_file("gui", "index.html")   # frozen 下走 _MEIPASS/RES
LOG_DIR = paths.log_dir()                        # pc/logs（frozen 下 data/logs）
# 源码树布局（仅非 frozen 存在）：服务脚本 / bootstrap / 代码根。frozen 下
# 这些 .py 已被打进 exe，外壳自己拉起子进程，此处只保留 ROOT 供 GUI 展示。
PC_DIR = paths.PC_DIR
# 健康端点与 4 个服务同口径（README 的「不启隧道、FP_NTFY_URL 直连」模式）：
# 写死 127.0.0.1 的话，那个模式下仪表盘 health 恒红、start 也过不了健康门。
NTFY_BASE = os.environ.get("FP_NTFY_URL", "http://127.0.0.1:2586").rstrip("/")
HEALTH_URL = f"{NTFY_BASE}/v1/health"
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
PS_SCRIPTS = (
    'Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" | '
    "Where-Object { $_.CommandLine -match 'fuckpush[\\\\/]pc[\\\\/]' } | "
    "ForEach-Object { $m=[regex]::Match($_.CommandLine,'([a-z_]+\\.py)'); "
    "if($m.Success){ $m.Groups[1].Value } }"
)
PS_KILL = (
    'Get-CimInstance Win32_Process -Filter "Name=\'pythonw.exe\'" | '
    "Where-Object { $_.CommandLine -match 'fuckpush[\\\\/]pc[\\\\/]' } | "
    "ForEach-Object { Write-Output $_.ProcessId; "
    "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
# 会动服务进程（杀/起）的任务：彼此互斥，避免 GUI 上两个操作并发交错
_EXCLUSIVE = ("start", "stop", "uninstall")

# 子进程都是本项目自己的 Python 脚本：Windows 上 stdout/err 重定向到管道或文件时
# 它们按 locale(cp936) 编码写中文，而父进程一律按 utf-8 解码（_job_move 等），或
# 把字节直接落进用 utf-8 打开的 gui_launcher.log（_job_start）—— 结果是任务详情里
# 全是乱码、同一个日志里混两种编码。PYTHONIOENCODING 只改子进程 stdio 编码，
# 不动它的任何行为。（PowerShell 走 _run_ps，不读这个变量，输出本来就是 ASCII。）
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


# ---------------- 工具 ----------------
def _set_job(name: str, **kw) -> None:
    with _jobs_lock:
        _jobs[name] = {"ts": time.time(), **kw}


def _get_job(name: str) -> dict | None:
    with _jobs_lock:
        return copy.deepcopy(_jobs.get(name))


def _run_ps(cmd: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            # pythonw（无控制台父进程）起 powershell 这种控制台程序，Windows
            # 会**新建一个控制台窗口** —— GUI 每 2.5s 轮询一次状态，不加此
            # 标志 = 黑窗每 2.5s 弹一次（用户看到的“命令行工具无限重启”）。
            # watchdog 的注释早就写过这个坑（为什么它不用 PowerShell）。
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return p.returncode, (p.stdout or "")
    except Exception as e:                                      # noqa: BLE001
        return 1, repr(e)


def _our_scripts() -> list[str]:
    """本项目 6 个服务的脚本名（一个 pythonw 进程 = 一项，与 PS_SCRIPTS 同口径）。

    psutil 进程内枚举，替代「get_status 每 2.5s 冷启一次 PowerShell」：GUI 开着
    期间状态轮询 2.5s 一次，PowerShell 冷启动 1-5s + 几十 MB 工作集，等于常驻一
    个子进程工厂（watchdog / start_services 的注释里早写过「为什么不用
    PowerShell」的同一条理由，GUI 这条路一直没跟上）。psutil 不可用（没走 .venv
    启动）时原样回退 PowerShell，行为不降级。"""
    try:
        import psutil
    except ImportError:
        rc, out = _run_ps(PS_SCRIPTS)
        if rc != 0:
            return []
        return [ln.strip() for ln in out.splitlines() if ln.strip()]
    out: list[str] = []
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() != "pythonw.exe":
                continue
            cmd = " ".join(proc.cmdline() or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        low = cmd.lower()
        if "fuckpush\\pc\\" not in low and "fuckpush/pc/" not in low:
            continue          # 只认自己（含 .venv\\Scripts\\pythonw.exe 那层 shim），Hermes 等不碰
        m = re.search(r"([a-z_]+\.py)", cmd)
        if m:
            out.append(m.group(1))
    return out


def _tail(path: Path, n: int = 14) -> list[str]:
    # pclog.read_tail 只碰尾部 64KB。本函数被 get_status 每 2.5s 调两次
    # （watchdog.log 是 2MB x3 轮转上限），原来 read_text() 把整个日志读进来再
    # 只取 14 行 —— GUI 开着就是持续的 4MB/2.5s 整读（与 watchdog.check_link、
    # pclog.read_tail 的 docstring 点名的是同一个毛病，这里一直是漏网的一处）。
    return [ln for ln in pclog.read_tail(path, n) if ln.strip()]


def _cap_log(path: Path, limit: int = 512 * 1024, keep: int = 128 * 1024) -> None:
    """gui_launcher.log 没有任何轮转：每次「一键启动」都会把 start_services 的
    全部输出追进去，_fatal 也写它，长期只增不减（pc/logs 下唯一一个无上界的
    文本文件 —— 其余都是 RotatingFileHandler 或 append_rotating）。写之前超过
    limit 就砍到 keep：它是「子进程持着句柄在写」的文件，rename 必然
    PermissionError（跨进程句柄挡 rename 那个坑），只能就地截断，与
    pclog.append_rotating 的兜底同思路。
    失败静默：这只是给下次写腾地方，不值得为它打断启动任务。"""
    try:
        if not path.exists() or path.stat().st_size <= limit:
            return
        data = path.read_bytes()[-keep:]
        nl = data.find(b"\n")
        if nl != -1:                 # 从第一个完整行起保留
            data = data[nl + 1:]
        with path.open("r+b") as f:
            f.seek(0)
            f.write(data)
            f.truncate()
    except OSError:
        pass


_ver_cache: tuple[int, str] | None = None   # (README mtime_ns, 版本号)


def _read_version() -> str:
    # get_status 每 2.5s 轮一次，每次都整读 28KB 的 README 再 splitlines 取
    # 第一行 —— 版本号在一次 GUI 会话里不会变，纯属重复读盘（28KB × 24 次/分
    # ≈ 1GB/天的页缓存读，全为一个常量）。按 mtime 缓存：README 一改（发版）
    # 立刻生效，与 fpconfig.raw() 的「mtime 变了重读」同口径；读失败不缓存
    # （AV 抖动不该把版本号钉死成 "?"）。
    global _ver_cache
    p = paths.res_file("README.md")   # frozen 下随包，源码树里才是 ROOT/README.md
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        return "?"
    if _ver_cache is not None and _ver_cache[0] == mtime:
        return _ver_cache[1]
    try:
        first = p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        # IndexError = 空文件：原实现只接 OSError，README 被截成 0 字节时
        # 这里直接抛到 get_status 的调用方，整个仪表盘轮询失败。
        return "?"
    m = re.search(r"v(\d+\.\d+\.\d+)", first)
    ver = m.group(1) if m else "?"
    _ver_cache = (mtime, ver)
    return ver


def _health() -> tuple[int | None, str]:
    try:
        req = urllib.request.Request(HEALTH_URL)
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status, f"HTTP {r.status}"
    except Exception as e:                                      # noqa: BLE001
        return None, f"{type(e).__name__}"


def _autostart_state() -> dict:
    count = valid = 0
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
                    count += 1
                    parts = value.strip('"').split('" "')
                    if len(parts) >= 2 and all(Path(p).exists() for p in parts[:2]):
                        valid += 1
    except OSError:
        pass
    return {"count": count, "valid": valid, "expected": len(AUTOSTART)}


def _form_from_config() -> dict:
    # 只读路径用 mtime 缓存（raw()），不 force：本函数被 get_status 每 2.5s
    # 调一次，force 会让「读状态」每次都真读盘+解析整个 fp.config.json，
    # 等于把 fpconfig 的 mtime 缓存彻底绕开（get_status 里那次 force 同理）。
    # 配置被改过时 mtime 一变就重读，新鲜度与 force 完全一致。
    cfg = fpconfig.raw() or {}
    vps = cfg.get("vps") or {}
    ai = cfg.get("ai") or {}
    ntfy = cfg.get("ntfy") or {}
    settings = ((cfg.get("vision") or {}).get("settings")) or {}
    return {
        "host": str(vps.get("host", "")),
        "port": str(vps.get("port", "")),
        "user": str(vps.get("user", "")),
        "password": "",                 # 空 = 保持原值（不回传明文）
        "has_password": bool(vps.get("password")),
        "token": "",
        "has_token": bool(str(ntfy.get("token", "")).startswith("tk_")),
        "base_url": str(ai.get("base_url", "")),
        "api_key": "",
        "has_api_key": bool(ai.get("api_key")),
        "model": str(ai.get("model", "")),
        "my_name": str(settings.get("my_name", "")),
    }


# ---------------- 后台任务 ----------------
def _job_start() -> None:
    _set_job("start", state="running", detail="start_services 执行中")
    if not VENV_PY.exists():
        _set_job("start", state="done", rc=2, detail=".venv 不存在，请先跑 setup.bat")
        return
    log_path = LOG_DIR / "gui_launcher.log"
    _cap_log(log_path)
    try:
        with open(log_path, "a", encoding="utf-8") as lf:
            p = subprocess.Popen(
                [str(VENV_PY), str(ROOT / "pc" / "bootstrap" / "start_services.py")],
                cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT,
                env=CHILD_ENV,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            rc = p.wait(timeout=300)
    except Exception as e:                                      # noqa: BLE001
        _set_job("start", state="done", rc=1, detail=f"{type(e).__name__}: {e}")
        return
    tail = _tail(LOG_DIR / "start_services.log", 3)
    _set_job("start", state="done", rc=rc,
             detail=tail[-1] if tail else f"exit={rc}")


def _job_stop() -> None:
    _set_job("stop", state="running", detail="结束服务进程")
    _run_ps(PS_KILL)
    time.sleep(1.5)
    rc2, left = _run_ps(PS_SCRIPTS)
    # r7-12 同型（start_services 那侧早就修过）：**枚举失败 ≠ 没有进程**。
    # PowerShell 被安全软件拦掉/冷启动失败时 rc2 != 0、stdout 为空 —— 原实现
    # 拿它算出 n_left=0，直接报「已全部停止」，而服务其实还活着；前端据此显示
    # 已停，用户接着点「一键启动」= 新旧进程并存（双 toast、双分诊、抢 state）。
    if rc2 != 0:
        _set_job("stop", state="done", rc=1,
                 detail=f"停止后无法枚举进程（powershell rc={rc2}），未确认是否全停： "
                        f"{(left or '').strip()[:80]}")
        return
    n_left = len([x for x in left.splitlines() if x.strip()])
    _set_job("stop", state="done", rc=0 if n_left == 0 else 1,
             detail=f"剩余 {n_left} 个进程" if n_left else "已全部停止")


def _job_move(mode: str) -> None:
    _set_job("move", state="running", detail=f"{mode} 执行中")
    if not VENV_PY.exists():
        _set_job("move", state="done", rc=2, detail=".venv 不存在")
        return
    try:
        p = subprocess.run(
            [str(VENV_PY), str(ROOT / "pc" / "services" / "vision_listener.py"), mode],
            input="\n", capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(ROOT), timeout=120, env=CHILD_ENV,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = [ln for ln in ((p.stdout or "") + (p.stderr or "")).splitlines()
                 if ln.strip() and "按回车" not in ln]
        _set_job("move", state="done", rc=p.returncode,
                 detail="；".join(lines[-3:]) or f"exit={p.returncode}")
    except Exception as e:                                      # noqa: BLE001
        _set_job("move", state="done", rc=1, detail=f"{type(e).__name__}: {e}")


def _job_uninstall(purge: bool) -> None:
    _set_job("uninstall", state="running", detail="卸载执行中")
    cmd = [sys.executable, str(ROOT / "uninstall.py"), "--yes"]
    if purge:
        cmd.append("--purge")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=str(ROOT), timeout=300, env=CHILD_ENV,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tail = [ln for ln in ((p.stdout or "").splitlines()) if ln.strip()][-4:]
        _set_job("uninstall", state="done", rc=p.returncode,
                 detail="；".join(tail) or f"exit={p.returncode}")
    except Exception as e:                                      # noqa: BLE001
        _set_job("uninstall", state="done", rc=1, detail=f"{type(e).__name__}: {e}")


def _job_check() -> None:
    _set_job("check", state="running", detail="--check 执行中")
    try:
        p = subprocess.run([sys.executable, str(ROOT / "setup.py"), "--check"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(ROOT), timeout=120,
                           env=CHILD_ENV,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = [ln for ln in ((p.stdout or "")).splitlines() if ln.strip()]
        _set_job("check", state="done", rc=p.returncode,
                 detail="\n".join(out[-16:]) or f"exit={p.returncode}")
    except Exception as e:                                      # noqa: BLE001
        _set_job("check", state="done", rc=1, detail=f"{type(e).__name__}: {e}")


def _spawn(name: str, fn, *a) -> dict:
    # 检查与置位必须在同一把锁里（TOCTOU）：原实现先在锁内读 state、
    # 出了锁再 _set_job("queued")，两次连点能同时通过「该任务已在运行」
    # 这道闸 —— 起两份 start_services（它自己的 O_EXCL 单例锁只保住了一份，
    # 第二份会打印「已在运行」并把 job 状态改掉，前端看到的结果是假的）。
    # queued 也算占位：置位到线程真正把 state 改成 running 之间只有微秒，
    # 但那正是原实现的漏洞窗口。
    with _jobs_lock:
        # 互斥：_spawn 原来只挡「同名任务已在运行」，start 和 stop 分属两个 key，
        # 连点「一键启动」+「一键停止」会同时进后台 —— start_services 杀旧起新
        # 与 PS_KILL 撞在一起，结果取决于时序：服务被杀一半、job 状态却报「全部
        # 就绪」。会动服务进程的三个任务彼此互斥（check/move 只读或只挪窗口，不拦）。
        if name in _EXCLUSIVE:
            busy = [k for k in _EXCLUSIVE
                    if k != name
                    and (_jobs.get(k) or {}).get("state") in ("queued", "running")]
            if busy:
                return {"ok": False,
                        "msg": f"已有任务在运行（{'、'.join(busy)}），请等它结束"}
        cur = _jobs.get(name)
        if cur and cur.get("state") in ("queued", "running"):
            return {"ok": False, "msg": "该任务已在运行"}
        _jobs[name] = {"ts": time.time(), "state": "queued", "detail": "排队中"}
    threading.Thread(target=fn, args=a, daemon=True).start()
    return {"ok": True, "msg": "已开始"}


# ---------------- 配置写入（合并不覆盖） ----------------
# 读-改-写必须整段串行：前端连点两次保存就是两个线程同时 raw(force=True)
# 读同一份底稿、各改各的字段，后写的那份把先写的整个覆盖掉（丢更新）。
_save_config_lock = threading.Lock()


def _save_config_impl(form: dict) -> dict:
    with _save_config_lock:
        return _save_config_locked(form)


def _save_config_locked(form: dict) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    host = str(form.get("host", "")).strip()
    if not host:
        errors.append("VPS 地址不能为空")
    port_s = str(form.get("port", "")).strip() or "22"
    if not (port_s.isdigit() and 0 < int(port_s) < 65536):
        errors.append("SSH 端口需为 1-65535 的数字")
    base_url = str(form.get("base_url", "")).strip().rstrip("/")
    if base_url and not base_url.startswith(("http://", "https://")):
        errors.append("AI 接口地址需以 http:// 或 https:// 开头")
    token = str(form.get("token", "")).strip()
    if token and not token.startswith("tk_"):
        errors.append("ntfy token 应以 tk_ 开头（VPS 脚本输出）")
    model = str(form.get("model", "")).strip()
    if not base_url or not model:
        warnings.append("AI 地址/模型为空：AI 分诊将 fail-open（判不了就全推）")
    # fp.config.json 允许 // 行注释（fpconfig._strip_comments 剥掉后才 json.loads），
    # 而本函数是 json.dumps 整份重写 —— 用户手写的注释会被静默抹掉。JSON 本身不
    # 支持注释，重新注入要保留原文结构、风险大于收益，这里先如实警告。
    try:
        orig = fpconfig.PATH.read_text(encoding="utf-8")
        if fpconfig._strip_comments(orig) != orig:
            warnings.append("fp.config.json 中的 // 注释会在保存后丢失（JSON 不支持注释）")
    except OSError:
        pass
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    # 覆盖保护：raw(force=True) 在「读不到（AV 独占/半写）」和「JSON 解析失败
    # （半写、手改坏）」两种情况下都返回 {} —— 与「文件不存在」同形。此时若照常
    # 往下写，就是用表单里那几个字段**整份覆盖** fp.config.json：vision 目标、
    # apps 规则、以及「留空=保持原值」的 password/token/api_key（原值已经没了）
    # 全部蒸发，而界面上只显示「已保存」。读不出原文就拒绝保存，让用户重试。
    raw_cfg = fpconfig.raw(force=True)
    if not raw_cfg and fpconfig.PATH.exists():
        try:
            text = fpconfig.PATH.read_text(encoding="utf-8")
        except OSError:
            text = None
        # 文本为空 = 首次配置的空壳，允许从零写入；有内容却解析不出来才是读失败
        if text is None or text.strip():
            return {"ok": False,
                    "errors": ["fp.config.json 当前读不出来（可能被占用 / 内容不"
                               "是合法 JSON），为避免覆盖已有配置本次未保存 —— "
                               "请稍后重试"]}
    cfg = copy.deepcopy(raw_cfg or {})
    if not cfg:
        # 从零写入（文件不存在，或 0 字节空壳）：先把 setup 向导写的那 5 个
        # 必需块立起来，再往里填表单字段。少了会怎样（实测 T6.8）：
        #   ntfy/vision/apps 缺块 → setup --check 判「缺块」FAIL，而产品内两条
        #   修复路径都不会补块 —— 向导默认「保留现有配置」，本函数只写表单字段；
        #   vision 缺块还会让 vision_listener 每轮打「缺少 vision 块，无视觉目标」，
        #   视觉通道空转到有人手编 JSON 为止。
        # 立成 {} 的副作用为零：fpconfig 各读端本来就按「块在但字段缺」给默认值。
        for sec in ("ai", "ntfy", "vps", "vision", "apps"):
            cfg.setdefault(sec, {})
        warnings.append("本次为从零创建配置：vision 目标清单 / apps 规则是空的，"
                        "视觉通道不会扫描任何窗口，规则要另行配置")
    changed: list[str] = []

    def _put(section: str, key: str, value) -> None:
        sec = cfg.setdefault(section, {})
        if sec.get(key) != value:
            sec[key] = value
            changed.append(f"{section}.{key}")

    _put("vps", "host", host)
    _put("vps", "port", int(port_s))
    _put("vps", "user", str(form.get("user", "")).strip() or "root")
    # 密钥字段留空 = 保持原值
    if str(form.get("password", "")).strip():
        _put("vps", "password", form["password"])
    if token:
        _put("ntfy", "token", token)
    _put("ai", "base_url", base_url)
    if str(form.get("api_key", "")).strip():
        _put("ai", "api_key", form["api_key"])
    _put("ai", "model", model)
    my_name = str(form.get("my_name", "")).strip()
    if my_name:
        vs = cfg.setdefault("vision", {}).setdefault("settings", {})
        if vs.get("my_name") != my_name:
            vs["my_name"] = my_name
            changed.append("vision.settings.my_name")

    if not changed:
        return {"ok": True, "changed": [], "warnings": warnings,
                "msg": "内容无变化，未写入"}

    # 临时名必须带 pid —— 与 triage_state / listener_state / vision_state /
    # triage_rules 同一口径（那里都写明了理由）。修这里之前，全仓**只剩**这里
    # 和 setup.step_config 两个写点还在用固定名，而它俩写的是同一个
    # fp.config.json.tmp：固定名 + 两个写者（GUI 双击保存 vs 向导重生成，或
    # GUI 内部两个线程）会把 write_text 交错到同一块区域 —— 两个进程各写各的
    # 字节流，落盘的就是**半截 JSON**，而 fp.config.json 是六个服务的唯一配置
    # 源：fpconfig.raw() 从此恒返回 {}（AI 全 fail-open、隧道读不到 vps 块），
    # 表面上却「已保存」。带 pid 后唯一临时名互不相撞，最坏也只是后写者覆盖
    # 先写者（整份覆盖，仍是一份合法 JSON）。
    tmp = fpconfig.PATH.with_name(f"{fpconfig.PATH.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(fpconfig.PATH)
    except OSError:
        # 与 setup.step_config 同型：写/替换失败（磁盘满、AV 拦截）先把半截
        # tmp 清掉再上抛 —— 仓库根目录的 *.tmp 没有任何清理方（ai_triager 的
        # _clean_tmp 只扫 pc/），留一个就是一个永久孤儿。上层 save_config 会把
        # 这个异常变成给用户看的错误提示，不会静默。
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    fpconfig.reset()
    need_restart = any(c.split(".")[0] in ("ai", "ntfy", "vision")
                       for c in changed)
    return {"ok": True, "changed": changed, "warnings": warnings,
            "need_restart": need_restart,
            "msg": ("已保存；" + ("建议点「一键启动」重启服务生效"
                                if need_restart else "隧道下次重连生效"))}


# ---------------- 暴露给前端 ----------------
def _fatal(msg: str) -> None:
    """无控制台场景（pythonw / 输出重定向）下 stdout 不可见：落日志 + 系统弹窗。"""
    print(msg)
    try:
        log = LOG_DIR / "gui_launcher.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] GUI FATAL: {msg}\n")
    except OSError:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "FxxkPush", 0x10)
    except Exception:                                          # noqa: BLE001
        pass


try:
    import webview
except ImportError as e:                                        # pragma: no cover
    _fatal("缺少 pywebview（跑一次 setup.bat 安装依赖）: "
           f"{type(e).__name__}: {e}")
    sys.exit(2)


class Api:
    def get_status(self) -> dict:
        scripts: dict[str, int] = {}
        for s in _our_scripts():
            scripts[s] = scripts.get(s, 0) + 1
        hcode, htext = _health()
        wd_tail = _tail(LOG_DIR / "watchdog.log", 1)
        cfg = fpconfig.raw() or {}          # 只读 + 2.5s 轮询：走 mtime 缓存
        vision_keys = [k for k in ((cfg.get("vision") or {})) if k != "settings"]
        return {
            "running": bool(scripts),
            "scripts": scripts,
            "proc_total": sum(scripts.values()),
            "health": {"code": hcode, "text": htext},
            "autostart": _autostart_state(),
            "watchdog_last": wd_tail[-1] if wd_tail else "",
            "start_log": _tail(LOG_DIR / "start_services.log", 10),
            "version": _read_version(),
            "config_exists": fpconfig.PATH.exists(),
            "vision_targets": vision_keys,
            "form": _form_from_config(),
            "jobs": {k: _get_job(k) for k in
                     ("start", "stop", "move", "uninstall", "check")},
            "paths": {"root": str(ROOT), "logs": str(LOG_DIR)},
        }

    def reveal_secret(self, kind: str) -> dict:
        """按需回读单个密钥（点「显示」且字段为空时调用）。

        密钥默认不回传前端（防 DevTools/截屏直读明文）；这是用户显式
        点击后的按需取回，「留空=保持原值」的保存语义不变。
        """
        cfg = fpconfig.raw(force=True) or {}
        if kind == "password":
            return {"ok": True,
                    "value": str((cfg.get("vps") or {}).get("password", ""))}
        if kind == "token":
            return {"ok": True,
                    "value": str((cfg.get("ntfy") or {}).get("token", ""))}
        if kind == "api_key":
            return {"ok": True,
                    "value": str((cfg.get("ai") or {}).get("api_key", ""))}
        return {"ok": False, "msg": "非法参数"}

    def save_config(self, form: dict) -> dict:
        try:
            return _save_config_impl(form or {})
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "errors": [f"{type(e).__name__}: {e}"]}

    def start_services(self) -> dict:
        return _spawn("start", _job_start)

    def stop_services(self) -> dict:
        return _spawn("stop", _job_stop)

    def move_windows(self, mode: str) -> dict:
        if mode not in ("park", "unpark"):
            return {"ok": False, "msg": "非法参数"}
        return _spawn("move", _job_move, mode)

    def toggle_autostart(self, on: bool) -> dict:
        try:
            if on:
                pyw, pc = str(VENV_PY), str(PC_DIR)
                if not VENV_PY.exists():
                    return {"ok": False, "msg": ".venv 不存在，先跑 setup.bat"}
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY,
                                        0, winreg.KEY_SET_VALUE) as key:
                    for script, name in AUTOSTART.items():
                        val = f'"{pyw}" "{pc}\\services\\{script}.py"'
                        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
            else:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY,
                                    0, winreg.KEY_SET_VALUE) as key:
                    for name in AUTOSTART.values():
                        try:
                            winreg.DeleteValue(key, name)
                        except FileNotFoundError:
                            pass
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SA_KEY,
                                        0, winreg.KEY_SET_VALUE) as skey:
                        for name in AUTOSTART.values():
                            try:
                                winreg.DeleteValue(skey, name)
                            except OSError:
                                pass
                except OSError:
                    pass
            st = _autostart_state()
            return {"ok": True, "count": st["count"], "autostart": st,
                    "msg": (f"已注册 {st['count']} 项（下次登录生效）" if on
                            else "已移除全部自启项")}
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    def run_uninstall(self, purge: bool = False) -> dict:
        return _spawn("uninstall", _job_uninstall, bool(purge))

    def run_check(self) -> dict:
        return _spawn("check", _job_check)

    def open_dir(self, which: str) -> dict:
        target = {"logs": LOG_DIR,
                  "root": ROOT}.get(which)
        if not target:
            return {"ok": False, "msg": "非法参数"}
        try:
            os.startfile(str(target))                            # noqa: S606
            return {"ok": True, "msg": f"已打开 {target}"}
        except Exception as e:                                  # noqa: BLE001
            return {"ok": False, "msg": f"{type(e).__name__}: {e}"}


def _smoke() -> int:
    """无窗口自检：状态/配置往返/自启幂等（不开真窗）。"""
    api = Api()
    fails = []

    def chk(name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + ("  | " + detail if detail else ""),
              flush=True)
        if not cond:
            fails.append(name)

    st = api.get_status()
    chk("status 结构完整",
        all(k in st for k in ("running", "scripts", "health", "autostart",
                              "form", "version", "jobs")))
    chk("status 实值", st["version"].count(".") == 2
        and st["autostart"]["expected"] == 6, f"v={st['version']}")
    form = st["form"]
    r = api.save_config(form)          # 原样回写 → 应无变化
    chk("save_config 往返无变化", r.get("ok") and r.get("changed") == [], str(r)[:80])
    bad = api.save_config({**form, "host": ""})
    chk("save_config 空 host 拦截", bad.get("ok") is False, str(bad.get("errors")))
    bad2 = api.save_config({**form, "token": "not-a-token"})
    chk("save_config 坏 token 拦截", bad2.get("ok") is False, str(bad2.get("errors")))
    at = api.toggle_autostart(True)
    chk("自启注册幂等", at.get("ok") and at.get("count") == 6, str(at.get("msg")))
    for k in ("password", "token", "api_key"):
        r = api.reveal_secret(k)
        chk(f"reveal {k}", r.get("ok") and isinstance(r.get("value"), str),
            f"len={len(r.get('value') or '')}")
    bad3 = api.reveal_secret("nope")
    chk("reveal 非法参数拦截", bad3.get("ok") is False, str(bad3))
    if fails:
        print(f"\n{len(fails)} 项失败")
        return 1
    print("\n无窗口自检 6/6 PASS（接下来真开窗验证 UI）")
    return 0


def _single_instance() -> bool:
    """False = 已有实例在跑（调用方应聚焦旧窗口后退出）。"""
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "FxxkPushGUI_Singleton_v08")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _focus_existing() -> None:
    """把已开的 GUI 窗口拉到前台（重复双击时的行为）。"""
    try:
        import ctypes
        title = f"FxxkPush v{_read_version()}"
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)      # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:                                      # noqa: BLE001
        pass


def _ensure_visible(title: str) -> None:
    """强制把主窗口置为可见。

    实测（历史排障）：某些隐藏控制台启动路径下，WinForms 的 Show() 执行了、
    窗口也创建了（标题/尺寸都对），但 IsWindowVisible=False —— 进程活着、
    窗口在，用户却“打不开”。轮询找到窗口后 ShowWindow(SW_SHOW) 即可；
    对本来就可见的正常路径这是无害幂等操作。
    """
    import ctypes
    user32 = ctypes.windll.user32
    for _ in range(40):                    # 最多 ~20s
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 5)     # SW_SHOW
            user32.SetForegroundWindow(hwnd)
            return
        time.sleep(0.5)


def main() -> int:
    if not _single_instance():
        _focus_existing()
        print("已有实例在运行，已尝试聚焦其窗口")
        return 0
    ap = argparse.ArgumentParser(description="FxxkPush 图形化界面")
    ap.add_argument("--smoke", action="store_true",
                    help="先跑无窗口自检，再开窗数秒自动关闭")
    args = ap.parse_args()

    if args.smoke:
        rc = _smoke()
        if rc != 0:
            return rc
    elif not GUI_HTML.exists():
        _fatal(f"缺少 {GUI_HTML}")
        return 2

    api = Api()
    try:
        webview.create_window(
            f"FxxkPush v{_read_version()}",
            url=GUI_HTML.as_uri(), js_api=api,
            width=1120, height=740, min_size=(980, 660),
            background_color="#EEF2F9",
        )
        if args.smoke:
            def _auto():
                time.sleep(6)
                try:
                    win = webview.windows[0]
                    probe = win.evaluate_js(
                        "JSON.stringify({app:!!document.querySelector('#app'),"
                        "cards:document.querySelectorAll('.glass').length,"
                        "ready:!!window.__ready, css:CSS.supports("
                        "'backdrop-filter','blur(10px)'),"
                        "theme:getComputedStyle(document.documentElement)"
                        ".getPropertyValue('--bg').trim()})")
                    print("UI probe:", probe, flush=True)
                    win.destroy()
                except Exception as e:                          # noqa: BLE001
                    print("UI probe failed:", repr(e), flush=True)
            threading.Thread(target=_auto, daemon=True).start()
        threading.Thread(target=_ensure_visible,
                         args=(f"FxxkPush v{_read_version()}",),
                         daemon=True).start()
        webview.start(debug=False)
    except Exception as e:                                      # noqa: BLE001
        _fatal("窗口启动失败: "
               f"{type(e).__name__}: {e}\n\n"
               "若为 WebView2 缺失：安装 Microsoft Edge WebView2 Runtime\n"
               "https://developer.microsoft.com/microsoft-edge/webview2/ 后重试。")
        return 3
    print("GUI 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
