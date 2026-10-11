"""进程拉起与识别 —— frozen（PyInstaller exe）与源码两种进程模型的**唯一分叉点**。

为什么需要它：PyInstaller 之前，六服务是由 ``start_services.py`` / ``watchdog.py``
用 ``[.venv\\Scripts\\pythonw.exe, pc\\services\\xx.py]`` 拉起的 —— 那个 ``.py``
和那个 venv 在打包后**都不存在**，exe 起来也拉不起任何服务（整个自带服务栈
的卖点直接失效）。改成 ``[sys.executable, "--role", "tunnel"]``（同一个 exe
自我多路，见 launcher.py）之后，「怎么拼命令」「怎么认出自己拉起来的进程」
「一个服务该有几个进程」这三件事同时换了口径，而且 start_services 与 watchdog
必须给**完全一致**的答案 —— 一边按脚本名数、一边按 role 数，两边永远对不上，
结果是 watchdog 恒报「进程缺失」并每 5 分钟补刀拉出双份服务。

两种进程模型（判据来自实测，不是推测）：

    非 frozen   ``.venv\\Scripts\\pythonw.exe`` 是 **shim**：它自己再派生一个
                真身解释器，所以每个服务 2 个进程（cmdline 完全一样）。判据
                ``== 2``，缺了说明只起了 shim，多了说明双份编排。

    frozen      onedir 打包下 exe **不再派生子进程**（只有 onefile 才有的
                bootloader 解包层），进程名 = exe 名，cmdline = ``FxxkPush.exe
                --role triager``，一个 role 恒为 1 个进程 —— pythonw shim
                那层双生压根不存在。判据下界仍是 1（少一个 = 真没起），上界
                放宽到 2：万一下一步有人把 spec 改成 onefile，bootloader 那层
                会让计数变成 2，那不是「双份服务」，判成进程重复会引发 watchdog
                无休止的补刀/误报。两界都写死在这里而不是散在调用方，就是这个
                理由。

本模块只做「拼命令 / 数进程 / 判重复」三件事，**不负责**起停顺序与健康门
（那是 start_services 的活）。导入它只需要 psutil 之外的纯标准库。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import paths  # 唯一路径真源（PyInstaller 打包前置）

# 服务脚本名 -> role。role 名必须与 launcher.py 的 ROLES 表逐字一致：
# frozen 下这是进程命令行里唯一能认出「这是谁」的特征，两边拼错一个字母就是
# 「服务全被判死 + watchdog 疯狂补刀拉双份」。
SCRIPT_ROLE: dict[str, str] = {
    "ntfy_tunnel.py": "tunnel",
    "pc_subscriber.py": "subscriber",
    "notification_listener.py": "listener",
    "ai_triager.py": "triager",
    "vision_listener.py": "vision",
    "watchdog.py": "watchdog",
    "start_services.py": "start",     # 启动编排；frozen 下 GUI/setup 也走它
}
ROLE_SCRIPT: dict[str, str] = {r: s for s, r in SCRIPT_ROLE.items()}

# 六个服务（ORDER/WATCHED 的公共口径；bootstrap 不含自己）
SERVICE_SCRIPTS: tuple[str, ...] = (
    "ntfy_tunnel.py", "pc_subscriber.py", "notification_listener.py",
    "ai_triager.py", "vision_listener.py", "watchdog.py",
)

_ROLE_RE = re.compile(r"--role[=\s]+([A-Za-z0-9_.\-]+)")
_SCRIPT_RE = re.compile(r"([a-z_]+\.py)")


def _frozen(frozen: bool | None = None) -> bool:
    """frozen 判定。参数存在时用它（测试/monkeypatch 用），否则问 paths。"""
    return paths.is_frozen() if frozen is None else bool(frozen)


# ------------------------------------------------------------------ 命令构造
def service_cmd(script: str, frozen: bool | None = None) -> list[str]:
    """拉起一个服务进程的命令行。

    frozen      ``[sys.executable, "--role", <role>]``：同一个 exe 自我多路。
                console=False（windowed）时 exe 没有控制台子系统，子进程天然
                不带黑窗；调用方仍照旧传 DETACHED_PROCESS|CREATE_NO_WINDOW。
    非 frozen   ``[<venv>\\Scripts\\pythonw.exe, <pc>/services/<script>]``：
                **逐字节与改造前相同**（同 cwd=PC_DIR、同 creationflags），
                仓库里跑的任何东西都不受影响。
    """
    if _frozen(frozen):
        role = SCRIPT_ROLE.get(script)
        if role is None:
            raise KeyError(f"未知服务脚本，无对应 role: {script}")
        return [sys.executable, "--role", role]
    return [str(paths.venv_dir() / "Scripts" / "pythonw.exe"),
            str(paths.PC_DIR / "services" / script)]


def role_of(cmd: str) -> str | None:
    """从命令行里取 role（``... --role triager`` -> "triager"）。"""
    m = _ROLE_RE.search(cmd or "")
    return m.group(1).lower() if m else None


def role_cmd(*roles: str, frozen: bool | None = None) -> list[str]:
    """按 role 直接拼一条 launcher 命令（frozen 外壳转发子命令用，
    如 uninstall 的 ``exe --role vision unpark``）。"""
    return [sys.executable, "--role", *roles]


# ------------------------------------------------------------------ 进程识别
# cmdline 读不到的进程数（提权会话 / 安全软件）。读不到 = 活进程可能被当成
# 不存在，原来混在 NoSuchProcess 里 continue，watchdog 会误报「进程缺失」
# 并补刀 —— 单独计数让调用方能声明「计数可能偏低」。
_denied = [0]


def denied_count() -> int:
    return _denied[0]


def reset_denied() -> None:
    _denied[0] = 0


def our_procs(frozen: bool | None = None) -> list[tuple[int, str, str | None]]:
    """[(pid, cmdline, role), ...] 本项目拉起来的进程（含 watchdog 自己）。

    frozen      进程名 == exe 文件名 且 cmdline 带 ``--role <已知 role>``。
    非 frozen   pythonw.exe 且 cmdline 含 ``fuckpush\\pc\\`` / ``fuckpush/pc/``
                （与改造前逐字节同口径：反斜杠正斜杠都认，不碰 Hermes 等其它
                pythonw）。role 由命令行里的脚本名反查 —— 两边最终都归一到
                同一个 role 键，调用方的计数/判定逻辑只有一份。
    """
    import psutil                      # 局部导入：没装 psutil 的场景先炸在这里，
                                      # 与改造前一致（都在函数内 import）
    out: list[tuple[int, str, str | None]] = []
    want_exe = Path(sys.executable).name.lower() if _frozen(frozen) else None
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if _frozen(frozen):
                if want_exe and name != want_exe:
                    continue
            elif name != "pythonw.exe":
                continue
            try:
                cmd = " ".join(proc.cmdline() or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if _frozen(frozen):
                role = role_of(cmd)
                if role not in ROLE_SCRIPT:
                    continue      # 只认本项目的 role，别人的 exe --role 无辜
            else:
                low = cmd.lower()
                if "fuckpush\\pc\\" not in low and "fuckpush/pc/" not in low:
                    continue      # 只动自己，Hermes 等其它 pythonw 不碰
                m = _SCRIPT_RE.search(cmd)
                role = SCRIPT_ROLE.get(m.group(1)) if m else None
            out.append((proc.pid, cmd, role))
        except psutil.AccessDenied:
            _denied[0] += 1
            continue
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return out


def service_counts(frozen: bool | None = None) -> dict[str, int]:
    """{服务脚本名: 进程数}。键是**脚本名**（不是 role），因为 watchdog 的
    WATCHED / start_services 的 ORDER / LOG_STEM 全是脚本名口径。"""
    counts: dict[str, int] = {}
    for _pid, _cmd, role in our_procs(frozen):
        if role is None:
            continue
        script = ROLE_SCRIPT.get(role)
        if script is None:
            continue
        counts[script] = counts.get(script, 0) + 1
    return counts


# ------------------------------------------------------------------ 计数判据
def expected_range(frozen: bool | None = None) -> tuple[int, int]:
    """(下界, 上界) —— 一个服务该有几个进程。见模块 docstring 的两种模型。

    非 frozen 恰好 2：pythonw shim + 真身，缺/多都是故障（并发双击两份编排
    会把每个服务起成 4 个 —— 双 toast、双次分诊、手机双推、抢 state 文件）。
    frozen (1, 2)：onedir 不双生；若将来打包成 onefile，bootloader 那层会把
    计数抬到 2，仍在界内不算故障。
    """
    return (1, 2) if _frozen(frozen) else (2, 2)


def expected_text(frozen: bool | None = None) -> str:
    lo, hi = expected_range(frozen)
    if _frozen(frozen):
        return (f"{lo}" if lo == hi else f"{lo}~{hi}") + "（frozen exe，无 pythonw shim）"
    return f"{lo}（venv pythonw shim + 真身）"


def judge(counts: dict[str, int], watched, frozen: bool | None = None
          ) -> tuple[list[str], list[str]]:
    """(缺失列表, 重复列表)。计数 0 = 没起；上界外 = 双份。"""
    lo, hi = expected_range(frozen)
    missing = [s for s in watched if counts.get(s, 0) < lo]
    extra = [s for s in watched if counts.get(s, 0) > hi]
    return missing, extra
