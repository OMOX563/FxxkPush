#!/usr/bin/env python3
"""FxxkPush 单一多路复用入口 —— 按 --role 分发到各模块（PyInstaller 打包前置）。

用法：
    python launcher.py --role <role> [参数...]
    launcher.py.exe --role gui            # frozen 下的「图形界面」按钮
    launcher.py.exe --role check          # 等价于 setup.py --check

    python launcher.py --roles            # 列出全部 role（不给 role 时也自动列）

为什么需要它：PyInstaller 打一个 exe 就只有一个可执行入口，而 FxxkPush 有
六个服务 + GUI + 安装/卸载 + VPS 命令行共十一种运行形态。原先靠
``setup.bat`` / ``restart_services.bat`` / ``GUI.bat`` / ``注册自启动.bat``
四个批处理 + HKCU Run 里六条 ``pythonw 脚本路径`` 才拼出「一个产品」。
打包后批处理没有意义、.py 与 .venv 都不存在 —— 六个服务一条也拉不起来。

本入口只做**分发**，不复制任何逻辑：每个 role 走的是目标模块自己的
``__main__`` 分支（runpy.run_module），与直接 ``python pc/services/xx.py``
跑出来的行为逐字一致 —— 含各服务自己的 excepthook、``--test`` /
``park`` / ``unpark`` / ``--grant-access`` 等已有子命令。

role 表（--role 无效时打印这张表并 rc=2）：

    tunnel      pc/services/ntfy_tunnel.py          ntfy 隧道（地基，先起）
    subscriber  pc/services/pc_subscriber.py        PC→ntfy 订阅转发
    listener    pc/services/notification_listener.py Windows 通知读取
    triager     pc/services/ai_triager.py           AI 分诊
    vision      pc/services/vision_listener.py      微信截图视觉监听
    watchdog    pc/services/watchdog.py             管道自检 + 告警
    start       pc/bootstrap/start_services.py      启动编排（健康门）
    gui         fxxkpush_gui.py                     图形界面
    setup       setup.py                            安装向导（--check 等价）
    uninstall   uninstall.py                        卸载（--purge/--dry-run）
    check       setup.py --check                    环境自检（只读）
    vps-exec    vps_exec.py                         VPS 命令行（--put/--get/…）

role 之后的参数原样转发（``--role triager`` 后面的 ``--test``、
``--role vision unpark``、``--role uninstall --purge --dry-run`` …），
子模块读 sys.argv 的既有逻辑一行不用改。

frozen 下 psutil 之类重依赖可能还没装 —— 这里**不 import** 任何业务模块，
只按表定位文件；真正的 import 发生在子模块里，失败时它的日志/traceback
照旧落到 DATA_DIR/logs。
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

# ------------------------------------------------------------------ role 表
# role -> (目标文件（相对 APP_DIR）, 说明)。顺序即 --roles 的打印顺序：
# 六个服务按启动依赖排，工具在后。
ROLES: dict[str, tuple[str, str]] = {
    "tunnel":     ("pc/services/ntfy_tunnel.py",          "ntfy 隧道（链路地基，必须先起）"),
    "subscriber": ("pc/services/pc_subscriber.py",        "PC→ntfy 推送转发"),
    "listener":   ("pc/services/notification_listener.py", "Windows 通知读取（含 --grant-access）"),
    "triager":    ("pc/services/ai_triager.py",           "AI 分诊（含 --test）"),
    "vision":     ("pc/services/vision_listener.py",      "微信截图监听（子命令 park/unpark/list）"),
    "watchdog":   ("pc/services/watchdog.py",             "管道自检 + 三层告警"),
    "start":      ("pc/bootstrap/start_services.py",      "启动编排（健康门 + 进程自检）"),
    "gui":        ("fxxkpush_gui.py",                     "图形界面（含 --smoke）"),
    "setup":      ("setup.py",                            "安装向导"),
    "uninstall":  ("uninstall.py",                        "卸载（--yes/--purge/--dry-run）"),
    "check":      ("setup.py",                            "环境自检（只读，等价 setup.py --check）"),
    "vps-exec":   ("vps_exec.py",                         "VPS 命令行（--put/--get/<命令>）"),
}
# 转发时需要**补**的参数：check 复用 setup.py 的 --check 分支。
# 其余 role 一律原样转发（子模块自己认参数，加在这里只会两处解析同一件事）。
ROLE_EXTRA_ARGV: dict[str, list[str]] = {"check": ["--check"]}


def app_dir() -> Path:
    """程序所在目录。frozen = exe 目录（paths 同口径，但 launcher 刻意不
    import paths：frozen 下首次运行可能连 pclog/psutil 都还没就位，
    入口必须在最少的依赖下先算出自己的位置）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def print_roles(app: Path | None = None, stream=None) -> None:
    """打印可用 role 表。app 给了就顺带标出目标文件是否存在 —— 打包漏了
    某个模块时这一眼就能看出来，不必等分发时报 FileNotFoundError。

    **windowed（console=False）下 sys.stdout/sys.stderr 可能是 None**：
    从资源管理器双击 exe 时进程没有可继承的标准句柄，CPython 干脆不建这两个
    对象，而下面原来的 `out = stream or sys.stdout` + `out.write(...)` 会直接
    炸 ``AttributeError: 'NoneType'' object has no attribute 'write'``（实测：
    双击 exe 无参数 → usage_error → print_roles 崩在第一行）。
    fp_console_runtime_hook 已在启动期兜底重绑，但那是「尽力而为」（AllocateConsole
    也可能失败），入口自身必须再兜一层：拿不到可用流就退回 devnull，
    至少不把「打印用法」变成「抛异常」。
    """
    out = stream or sys.stdout
    if out is None or not hasattr(out, "write"):
        try:
            out = open(os.devnull, "w", encoding="utf-8", errors="replace")
        except Exception:                                       # noqa: BLE001
            return                                             # 真的没地方写：静默返回
    out.write("可用 --role 取值：\n")
    width = max(len(r) for r in ROLES)
    for role, (rel, desc) in ROLES.items():
        mark = ""
        if app is not None:
            mark = "  [缺失!]" if not (app / rel).is_file() else ""
        out.write(f"  {role:<{width}}  {rel:<36} {desc}{mark}\n")
    out.write("\n用法: launcher.py --role <role> [参数...]\n"
              "例:   launcher.py --role check\n"
              "      launcher.py --role uninstall --purge --dry-run\n")


def usage_error(msg: str, app: Path | None = None) -> int:
    """非法 role：打印原因 + 可用列表，rc=2（与 argparse 的用法错误同码）。"""
    print(f"[失败] {msg}", file=sys.stderr)
    print_roles(app, stream=sys.stderr)
    return 2


def dispatch(role: str, argv: list[str], app: Path) -> int:
    """定位目标模块并以 runpy 跑它的 __main__。

    runpy 而不是 import：各服务的 ``if __name__ == "__main__"`` 块里带着
    自己的 excepthook 安装、信号处理与退出码语义（pc_subscriber 的 --test、
    vision 的 park/unpark/list、watchdog 的 _log_crash…），走 runpy 才和
    「直接 python 那个文件」完全同一条代码路径 —— 复用逻辑而非复制逻辑。

    cwd 设为 APP_DIR：六个服务的 _detect_root() 是「当前目录优先、脚本目录
    回退」，frozen 下没有 __file__ 可回退，只能靠 cwd。
    """
    if role not in ROLES:
        return usage_error(f"未知 role: {role!r}", app)
    rel, _desc = ROLES[role]
    target = app / rel
    if not target.is_file():
        # 源码树里 pc/services/*.py 的存在性可以校验；frozen 下它们已被打进
        # exe，磁盘上没有同名文件是**正常**的（runpy 直接命中内置模块），
        # 所以只在非 frozen 时才判定为「文件缺失」。
        if not getattr(sys, "frozen", False):
            return usage_error(f"role {role!r} 的目标文件不存在: {target}", app)
    modname = rel[:-3].replace("/", ".").replace("\\", ".")
    if modname.endswith(".__init__"):
        modname = modname[: -len(".__init__")]
    sys.argv = [rel] + list(argv)
    try:
        runpy.run_module(modname, run_name="__main__", alter_sys=True)
    except SystemExit as e:                       # 子模块自己的退出码，原样透传
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app = app_dir()

    # **无参数 = 打开图形界面**（不是打印用法）。
    # 源码版的入口是 GUI.bat，帮你补上了 `--role gui`，所以「双击 = 开面板」
    # 那个直觉在源码态是成立的；打包后 exe 就是这个入口，没有 .bat 帮补参数，
    # 再让无参数走「打印用法 + rc=2」就变成了：双击 → windowed 下用法输出进
    # 新开的控制台 → 窗口一闪就没 → 用户以为程序坏了（实测真被当成打不开）。
    # 所有 CLI 角色本来都有显式 --role，把默认值给 gui 零损失。
    if not argv:
        argv = ["--role", "gui"]

    if argv[0] in ("-h", "--help", "help"):
        print_roles(app)
        return 0
    if argv[0] == "--roles":
        print_roles(app)
        return 0

    # --role 必须真的在 argv 里出现（`-r` 只是省手的别名，不进 HKCU Run）
    try:
        i = argv.index("--role") if "--role" in argv else argv.index("-r")
    except ValueError:
        return usage_error(f"第一参数不是 --role: {argv[0]!r}（本入口只按 role 分发）", app)
    if i + 1 >= len(argv):
        return usage_error("--role 后面缺 role 名", app)

    role = argv[i + 1].strip().lower()
    rest = argv[i + 2:]
    extra = ROLE_EXTRA_ARGV.get(role)
    if extra:
        rest = extra + list(rest)
    # 非 frozen 下把 pc/ 与 pc/core/ 插进 sys.path：frozen 下这些是内置模块
    # 无需路径，原样保留即可；源码树里各服务自己也会插入，这里只保证
    # launcher 先 import paths 之类的公共件时能找到。
    if not getattr(sys, "frozen", False):
        for extra_path in ("pc", "pc/core"):
            p = str(app / extra_path)
            if p not in sys.path:
                sys.path.insert(0, p)
    return dispatch(role, rest, app)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
