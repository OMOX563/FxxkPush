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
import re
import shutil
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
import fpconfig  # noqa: E402

# pythonw 而非 python.exe：python.exe 是控制台程序 —— 自启注册成它
# 会在登录时弹 6 个黑窗；pythonw 无窗口子系统，双击 GUI 也绝不带控制台。
VENV_PY = ROOT / ".venv" / "Scripts" / "pythonw.exe"
GUI_HTML = ROOT / "gui" / "index.html"
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


def _tail(path: Path, n: int = 14) -> list[str]:
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8",
                                              errors="replace").splitlines()
                 if ln.strip()]
        return lines[-n:]
    except OSError:
        return []


def _read_version() -> str:
    try:
        first = (ROOT / "README.md").read_text(encoding="utf-8",
                                                errors="replace").splitlines()[0]
        m = re.search(r"v(\d+\.\d+\.\d+)", first)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def _health() -> tuple[int | None, str]:
    try:
        req = urllib.request.Request("http://127.0.0.1:2586/v1/health")
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
    cfg = fpconfig.raw(force=True) or {}
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
    log_path = ROOT / "pc" / "logs" / "gui_launcher.log"
    try:
        with open(log_path, "a", encoding="utf-8") as lf:
            p = subprocess.Popen(
                [str(VENV_PY), str(ROOT / "pc" / "bootstrap" / "start_services.py")],
                cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            rc = p.wait(timeout=300)
    except Exception as e:                                      # noqa: BLE001
        _set_job("start", state="done", rc=1, detail=f"{type(e).__name__}: {e}")
        return
    tail = _tail(ROOT / "pc" / "logs" / "start_services.log", 3)
    _set_job("start", state="done", rc=rc,
             detail=tail[-1] if tail else f"exit={rc}")


def _job_stop() -> None:
    _set_job("stop", state="running", detail="结束服务进程")
    rc, out = _run_ps(PS_KILL)
    time.sleep(1.5)
    _, left = _run_ps(PS_SCRIPTS)
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
            errors="replace", cwd=str(ROOT), timeout=120,
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
                           cwd=str(ROOT), timeout=300,
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
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = [ln for ln in ((p.stdout or "")).splitlines() if ln.strip()]
        _set_job("check", state="done", rc=p.returncode,
                 detail="\n".join(out[-16:]) or f"exit={p.returncode}")
    except Exception as e:                                      # noqa: BLE001
        _set_job("check", state="done", rc=1, detail=f"{type(e).__name__}: {e}")


def _spawn(name: str, fn, *a) -> dict:
    with _jobs_lock:
        cur = _jobs.get(name)
        if cur and cur.get("state") == "running":
            return {"ok": False, "msg": "该任务已在运行"}
    _set_job(name, state="queued", detail="排队中")
    threading.Thread(target=fn, args=a, daemon=True).start()
    return {"ok": True, "msg": "已开始"}


# ---------------- 配置写入（合并不覆盖） ----------------
def _save_config_impl(form: dict) -> dict:
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
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    cfg = copy.deepcopy(fpconfig.raw(force=True) or {})
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

    tmp = fpconfig.PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(fpconfig.PATH)
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
        log = ROOT / "pc" / "logs" / "gui_launcher.log"
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
        rc, out = _run_ps(PS_SCRIPTS)
        scripts: dict[str, int] = {}
        if rc == 0:
            for ln in out.splitlines():
                s = ln.strip()
                if s:
                    scripts[s] = scripts.get(s, 0) + 1
        hcode, htext = _health()
        wd_tail = _tail(ROOT / "pc" / "logs" / "watchdog.log", 1)
        cfg = fpconfig.raw(force=True) or {}
        vision_keys = [k for k in ((cfg.get("vision") or {})) if k != "settings"]
        return {
            "running": bool(scripts),
            "scripts": scripts,
            "proc_total": sum(scripts.values()),
            "health": {"code": hcode, "text": htext},
            "autostart": _autostart_state(),
            "watchdog_last": wd_tail[-1] if wd_tail else "",
            "start_log": _tail(ROOT / "pc" / "logs" / "start_services.log", 10),
            "version": _read_version(),
            "config_exists": fpconfig.PATH.exists(),
            "vision_targets": vision_keys,
            "form": _form_from_config(),
            "jobs": {k: _get_job(k) for k in
                     ("start", "stop", "move", "uninstall", "check")},
            "paths": {"root": str(ROOT), "logs": str(ROOT / "pc" / "logs")},
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
                pyw, pc = str(VENV_PY), str(ROOT / "pc")
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
        target = {"logs": ROOT / "pc" / "logs",
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
