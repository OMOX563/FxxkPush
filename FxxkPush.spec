# -*- mode: python ; coding: utf-8 -*-
"""FxxkPush 打包配置 —— PyInstaller onedir，产出 dist/FxxkPush/ 可分发 exe。

目标：用户机器**不装 Python** 也能跑 FxxkPush 全部能力（六个服务 + GUI +
setup/check/uninstall/vps-exec）。入口是 launcher.py（单一多路复用分发器），
不是 fxxkpush_gui.py —— 因为打包后只有一个 exe，而 FxxkPush 有十一种运行形态，
全部经 launcher.py 的 --role 表分发。

用法（正常情况交给 build_exe.bat，它先 --clean 再 build）：

    .venv\\Scripts\\python.exe -m PyInstaller FxxkPush.spec --clean --noconfirm

产物布局（onedir）：

    dist/FxxkPush/FxxkPush.exe        主程序（无黑窗，console=False）
    dist/FxxkPush/_internal/          依赖 + 只读资源（gui/ README.md）

可写数据**不在** dist 里：paths.DATA_DIR 指向 exe 同级 data/，首次运行才创建
（paths.py 里 migrate_legacy_data 会把用户手工放在 exe 同级的旧数据复制进去）。

--- 为什么 hiddenimports 要手写这么多 ---

launcher.py 用 ``runpy.run_module`` 动态跑各服务的 ``__main__``。PyInstaller
的模块图只跟着 importlib hook 走，**扫不到 runpy 里的字符串模块名**，这些模块
即使被源码 import 过也不会进包。pc/ 下又全是**命名空间包**（没有 __init__.py），
静态分析更不可能把它们当成 ``pc.services.*`` 自动发现。因此下面逐个显式列出：
漏一个，那个 role 就在用户机器上 ``ModuleNotFoundError``。

已实测（PyInstaller 6.22.3 + Python 3.12.14，本机真实构建 + 真跑）：
``PyiFrozenLoader.get_code()`` 对命名空间包子模块（pc.services.ntfy_tunnel）
正常返回 code 对象，故 runpy.run_module 的分发链在 frozen 下成立。

--- console=False 的取舍（已实测，勿轻易改回 console=True）---

console=False（runw.exe，GUI 子系统）后：

1. **stdout/stderr 依然可用**：实测 ``FxxkPush.exe --roles`` / ``--role check``
   的输出与 rc 完全正常。用 cmd 重定向或管道时 exe 复用宿主控制台句柄；
   没有任何可用控制台时（资源管理器双击）输出无处可去 —— 这是 windowed 的
   固有代价，**不是** PyInstaller 吞掉了 stdout。
2. **子进程天然无黑窗**：六个服务经 procspawn.service_cmd 以
   ``[sys.executable, "--role", r]`` 拉起，windowed exe 没有控制台子系统，
   子进程不再需要 CREATE_NO_WINDOW 才不闪黑窗（start_services 仍传该 flag，
   无害）。这正是本项目要 console=False 的主因：源码态用 pythonw.exe，
   打包后要复刻那个「无控制台」形态。
3. **代价**：任何需要人看输出的 role（check / roles / setup 向导）从双击运行
   没有任何回显。取舍写在下面 fp_console_runtime_hook 里做部分兜底 ——
   对「无控制台且无重定向」的调用，AllocateConsole + 附加，让 check 从双击
   也能看见输出；gui role 主动 AttachParentConsole 绕开它。

runtime hook（fp_console_runtime_hook.py）刻意**零业务依赖**（只 import
ctypes/kernel32），只 import os/sys/ctypes，绝不 import 业务模块 —— 它在
PyInstaller bootstrap 之后、launcher 分发之前运行，此刻 psutil/paramiko 等
还没装可能缺失；而 launcher 刻意不 import 业务模块正是为了在这种时刻
先算出自己的位置。

--- 图标 ---

仓库里没有 .ico（只有 pc/*.png，而 .gitignore 忽略 *.png），故不设 icon，
PyInstaller 用默认图标。发正式版前建议加一个多尺寸 .ico（16/32/48/256）
并放到 resources/fxxkpush.ico，再把下方 ICON 行取消注释。
"""

import os

# ------------------------------------------------------------------ 路径常量
# spec 文件是被 exec() 执行的，**没有 __file__**（PyInstaller 在 spec 命名空间
# 里给的是 SPECPATH）。优先用 SPECPATH；拿不到时退回当前工作目录
# （build_exe.bat 总会 cd /d 到脚本所在目录，两条路径等价）。
# 刻意不写死 C:\\Users\\... ：spec 要能在仓库任意位置被执行。
APP = os.path.abspath(globals().get("SPECPATH") or os.getcwd())    # 仓库根
VENV = os.path.join(APP, ".venv")
SITE = os.path.join(VENV, "Lib", "site-packages")
PYPATHS = [APP, os.path.join(APP, "pc"), os.path.join(APP, "pc", "core")]

# 图标：仓库无 .ico，暂不设（默认图标）。加了 resources/fxxkpush.ico 后改这两行：
# ICON = os.path.join(APP, "resources", "fxxkpush.ico")
ICON = os.path.join(APP, "resources", "fxxkpush.ico")

HOOKS = [os.path.join(SITE, "_pyinstaller_hooks_contrib")]   # contrib 钩子（含 webview/clr）

RUNTIME_HOOKS = [os.path.join(APP, "fp_console_runtime_hook.py")]

# ------------------------------------------------------------------ 只读资源
# RES_DIR = _MEIPASS/RES（paths.py），所有资源必须落进 RES/ 子目录，
# 这样 res_file("gui", "index.html") 之类调用在 frozen 与源码两种模式下同形。
RES = [
    (os.path.join(APP, "gui"), "RES/gui"),
    # README.md 必须落成**文件**且在 RES 下。datas 的第二项是「目标目录」，
    # 与源**目录名**拼接：写 "RES/README.md" 会被当成目录，包里出现
    # RES/README.md/README.md —— res_file("README.md") 于是指向一个目录，
    # GUI 的 _read_version() 读它抛 IsADirectoryError，而那里只接
    # (OSError, IndexError)，于是 get_status 轮询直接炸、仪表盘空白。
    # 写 "." 又会落到 _internal 根（实测），所以目标目录就写 "RES"。
    (os.path.join(APP, "README.md"), "RES"),
]
# 例：需要时打开（会 +~30KB）：
# RES += [(os.path.join(APP, "deploy"), "RES/deploy")]

# ------------------------------------------------------------------ hiddenimports
# 显式模块（runpy 动态分发 + 命名空间包，静态分析扫不到的那一批）：
EXPLICIT = [
    # launcher.py 的 --role 表逐行对应（顺序与 ROLES 一致，便于核对）
    "pc.services.ntfy_tunnel",
    "pc.services.pc_subscriber",
    "pc.services.notification_listener",
    "pc.services.ai_triager",
    "pc.services.vision_listener",
    "pc.services.watchdog",
    "pc.bootstrap.start_services",
    "fxxkpush_gui",
    "setup",
    "uninstall",
    "vps_exec",
    # 各服务 sys.path.insert(0, ROOT/pc[/core]) 后的**顶层**公共模块名。
    # 注意它们在源码树里是 pc/core/x.py，冻结后要按顶层名 pc/core/x 导入，
    # 故用「无点号顶层名」而不是 pc.core.paths —— 见下方 ALIASES 说明。
    "pclog", "paths", "fpconfig", "procspawn", "fp_feedback", "alert_fallback",
]

# 动态/反射 import 的第三方（依赖包自己的 hook 也会补一部分，这里显式兜底）：
THIRD_PARTY = [
    # WinRT 通知读取（notification_listener 用的是纯 Python winrt 绑定，
    # **不是** pythonnet 的 clr；两者都留，pythonnet 是 pywebview
    # EdgeChromium 后端 webview/platforms/edgechromium.py 的硬依赖）
    "winrt.windows.ui.notifications",
    "winrt.windows.ui.notifications.management",
    "winrt.runtime",
    "clr", "clr_loader",
    # pywin32：winreg / pywintypes / win32api 等（setup/uninstall/GUI 都用 winreg）
    "winreg", "pywintypes", "win32api", "win32con", "win32event", "win32file",
    "win32pipe", "win32gui", "win32ui", "win32security", "win32process",
    # GUI + 通知 + 截图
    "webview", "webview.platforms.edgechromium",
    "winotify", "PIL", "PIL.Image", "PIL.ImageGrab", "PIL.ImageFont",
    # 网络 / SSH / 进程 / 加密
    "paramiko", "psutil", "httpx", "httpcore", "h11", "anyio", "sniffio",
    "cryptography", "bcrypt", "nacl", "cffi", "certifi",
    # winotify / pywebview 的零碎传递依赖
    "comtypes", "pyperclip", "tenacity", "proxy_tools", "bottle",
]

# 命名空间包 shim：让 pc.services.* / pc.core.* 两种写法都能在 frozen 下解析。
# 源码树里 pc/ 没有 __init__.py（是命名空间包），PyInstaller 按 pathex 收集时
# 会把它们打成顶层模块 pclog/paths/...（源码里正是这么 import 的）。但 launcher
# 的 runpy 走的是**带点号的完整名**，因此必须显式提供 pc.* 前缀的别名，
# 否则 runpy.run_module("pc.services.ntfy_tunnel") 会 ModuleNotFoundError。
# 生成方式：build_exe.bat 不用干预，PyInstaller 自身对命名空间包会产出
# pc/services/ntfy_tunnel.py 的 TOC 条目；显式列出以确保一定被打进来。
NAMESPACE = [
    "pc",
    "pc.core",
    "pc.core.paths",
    "pc.core.pclog",
    "pc.core.fpconfig",
    "pc.core.procspawn",
    "pc.core.fp_feedback",
    "pc.core.alert_fallback",
    "pc.services",
    "pc.services.ntfy_tunnel",
    "pc.services.pc_subscriber",
    "pc.services.notification_listener",
    "pc.services.ai_triager",
    "pc.services.vision_listener",
    "pc.services.watchdog",
    "pc.bootstrap",
    "pc.bootstrap.start_services",
]

HIDDEN = EXPLICIT + THIRD_PARTY + NAMESPACE

# ------------------------------------------------------------------ excludes
# 排掉 PC 端**确实不用**的大件。判据是「仓库里 grep 不到 import」，
# 不按「看着像没用」拍脑袋 —— 砍错了是打包成功但用户机器上某个 role 崩。
#
# 三个曾经误列、已按实测删掉的名字（留给后来者踩坑记录）：
#   email / html / http   urllib.request.urlopen 依赖链是
#                        urllib → http.client → email.parser，``--role check``
#                        的健康检查（setup.do_check 的 HEALTH_URL）就靠它，
#                        排掉它们 = 自检 role 在用户机器上必崩。
#   distutils             不能排：PyInstaller 自带的 hook-distutils.py 会
#                        add_alias_module("setuptools._distutils", "distutils")，
#                        与 excludes 里的 ExcludedModule 撞出
#                        ValueError: Target module "distutils" already imported。
#   asyncio/concurrent    httpx 的 anyio 后端要用，六个服务都走 httpx。
EXCLUDES = [
    "tkinter", "tkinter.ttk", "tcl", "tk",          # GUI 用 pywebview，不用 tk
    "unittest", "test", "tests", "_pytest", "pytest",
    "numpy", "matplotlib", "scipy", "pandas",       # Pillow 只做截图/缩放
    "IPython", "pydoc_data", "doctest", "pydoc",
    "idlelib", "ensurepip", "venv", "turtle", "curses",
    "lib2to3", "distutils_hack",
    "PyQt5", "PyQt6", "PySide2", "PySide6",
]

# ------------------------------------------------------------------ Analysis
a = Analysis(
    [os.path.join(APP, "launcher.py")],     # 入口：单一多路复用分发器（不是 GUI）
    pathex=PYPATHS,
    binaries=[],
    datas=RES,
    hiddenimports=HIDDEN,
    hookspath=HOOKS,
    runtime_hooks=RUNTIME_HOOKS,
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,                   # onedir：exe 不塞 _internal
    name="FxxkPush",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                               # 未装 UPX；开了易被杀软误报
    console=False,                           # windowed：GUI 无黑窗（见文件头取舍说明）
    disable_windowed_traceback=False,
    argv_emulation=False,                    # Windows 上必须 False，否则 --role 被吞
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FxxkPush",
)