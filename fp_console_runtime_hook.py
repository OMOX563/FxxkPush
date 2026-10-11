"""Frozen 运行期钩子 —— windowed exe 没有控制台时，让需要看输出的 role 能回显。

为什么需要它：spec 里 console=False（runw.exe，GUI 子系统）是刻意的
（六个服务经 ``[sys.executable, "--role", r]`` 拉起时不带黑窗，与源码态
pythonw.exe 形态一致）。代价是：从资源管理器双击 ``FxxkPush.exe --role check``
既没有控制台也没有父控制台可附加，那份六项自检输出就**无处可去** ——
用户看到的是一个闪一下就消失的窗口和一份空白的检查结果。

本钩子做两件事，都只动「有没有控制台」，不碰任何业务逻辑：

1. 进程启动时若检测到**既没有控制台、stdout 也没有重定向**，就
   ``AllocateConsole()`` 新开一个控制台，并 ``AttachConsole(ATTACH_PARENT_PROCESS)``
   尝试附到父进程的控制台（双击场景下父进程没有控制台，这步会失败，属预期）。
2. 附加/新建完成后，把 Python 的 sys.stdout / sys.stderr **重绑**到真正的
   控制台句柄 —— windowed 进程启动时 Python 的标准流可能是指向 nul 的
   TextIOWrapper（写到那里等于写进黑洞），不重绑 print() 依然看不见。

两个 role 显式绕开：``gui`` 主动 AttachParentConsole 后退出（自己开窗，
不需要黑框；这里只负责把 gui 从「新建控制台」里排除掉）。

取舍说明：控制台的分配是**有副作用**的（会真的弹一个黑框），所以只在
「确实没有任何输出目标」时才做 —— 从 cmd / 管道 / 重定向启动时一律不动，
实测行为与改造前一致。新开的控制台随进程结束消失，不留残留窗口。

零重依赖：只 import os/sys/ctypes，且只用 kernel32 的标准 Win32 API。
**绝不 import 任何业务模块**：本钩子在 PyInstaller bootstrap 之后、
launcher 分发之前运行，此刻 psutil/paramiko 等重依赖可能尚未就位
（用户机器上首次运行 exe 时它们已经在包里，但保持「钩子永远不失败」
比「早一点 import」重要得多）。
"""
from __future__ import annotations

import os
import sys

# 明确不需要控制台的 role：GUI 自己开窗，附带控制台纯属画蛇添足。
_NO_CONSOLE_ROLES = frozenset({"gui"})

_ATTACH_PARENT_PROCESS = -1                 # AttachConsole 的父进程常量
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12
_INVALID_HANDLE_VALUE = -1                  # (ctypes.c_void_p)(-1).value


def _argv_roles() -> set[str]:
    """从 argv 里粗提 role 名（只看紧跟 --role / -r 的值）。

    刻意不做完整解析：此刻 launcher 还没跑，argv 是原始列表，
    这里的判据只需区分「用户是不是双击了 gui」。
    """
    out: set[str] = set()
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a in ("--role", "-r") and i + 1 < len(argv):
            out.add(argv[i + 1].strip().lower())
        elif a.startswith("--role="):
            out.add(a.split("=", 1)[1].strip().lower())
    return out


def _has_usable_std() -> bool:
    """stdout/stderr 是否已经有真实去处（重定向到文件/管道）。

    windowed 进程即使句柄无效，sys.stdout 也可能是个普通的
    TextIOWrapper —— 判据因此看底层句柄而不是对象类型。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            return False
        try:
            fd = stream.fileno()
        except Exception:                    # noqa: BLE001 —— 已关闭/无 fileno
            return False
        if fd == _INVALID_HANDLE_VALUE or fd is None:
            return False
        if os.fstat(fd).st_size < 0:          # 永远不会发生，显式兜底
            return False
    return True


def _rebind_std(k32) -> bool:
    """把 sys.stdout/stderr 重绑到真实控制台句柄，返回是否成功。

    **窗口程序下 sys.stdout 本身可能是 None**（不是指向 nul 的 TextIOWrapper）：
    没有可继承的标准句柄时 CPython 干脆不建这个对象。此时任何
    ``print()`` / ``stream.write()`` 都会炸
    ``AttributeError: 'NoneType' object has no attribute 'write'``
    —— 实测资源管理器双击 exe 时就是这个形态，而子进程调用（cmd/管道）
    因为句柄有效永远走不到这里。所以 None 分支必须**主动造一个流**：
    拿到控制台句柄就绑上去，拿不到就绑 devnull（至少不炸，且不弹黑框）。
    """
    ok = False
    for name, which in (("stdout", _STD_OUTPUT_HANDLE), ("stderr", _STD_ERROR_HANDLE)):
        stream = getattr(sys, name, None)
        target = None
        try:
            handle = k32.GetStdHandle(which)
            if handle and handle != _INVALID_HANDLE_VALUE:
                target = open(handle, "w", encoding="utf-8", errors="replace",
                              closefd=False)
        except Exception:                    # noqa: BLE001
            target = None
        if target is None:
            # 无可用控制台句柄：devnull 兜底，保证 write() 存在。
            # 静默模式下（DEVNULL 启动）这也是正确的去处。
            try:
                target = open(os.devnull, "w", encoding="utf-8", errors="replace")
            except Exception:                # noqa: BLE001
                target = None
        if target is None:
            continue
        if stream is not None:
            try:
                stream.flush()
            except Exception:                # noqa: BLE001
                pass
        setattr(sys, name, target)
        ok = True
    return ok


def _enable_console() -> None:
    """保证「需要输出」的角色有一个可写的控制台。全部失败也不抛异常。

    钩子在启动早期运行，这里抛任何异常都会让整个 exe 起不来 ——
    而它本来只是改善输出体验。宁可什么都不做，也不能因此启动失败。
    """
    if "gui" in _argv_roles():
        return                              # GUI 不弹黑框
    if _has_usable_std():
        return                              # 已有重定向目标：不干扰
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # 已是控制台进程（cmd 里带重定向）时直接返回，不 Attach 也不 Allocate。
        if not k32.GetConsoleWindow():
            if not k32.AttachConsole(_ATTACH_PARENT_PROCESS):
                # 父进程没控制台（资源管理器双击）→ 自己开一个。
                if not k32.AllocateConsole():
                    return
        _rebind_std(k32)
    except Exception:                        # noqa: BLE001
        return


# runtime hook 的执行方式就是「被 import 一次」，所以动作放在模块级，
# 而不是等谁去调用。
_enable_console()