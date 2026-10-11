"""FxxkPush 路径抽象层 —— 全项目**唯一真源**（PyInstaller 打包的前置地基）。

为什么要有它：六个服务 + GUI + setup/uninstall/vps_exec 原来各自写死
``Path(__file__).resolve().parents[N]``，那套假设只在「仓库根跑」时成立。
PyInstaller 打出来的 exe 里 ``__file__`` 指向解包临时目录（``_MEIPASS``），
数据文件写进去开机就没了（日志、状态、归档全丢），而 exe 同级目录在
Program Files 下**可能没有写权限** —— 于是「打包即不可用」。本模块把
「程序在哪、资源在哪、数据在哪」三件事分开定义一次：

    APP_DIR   程序所在目录。frozen = exe 目录（可写与否由 DATA_DIR 兜底），
              非 frozen = 仓库根（本文件在 pc/core/，parents[2]）
    RES_DIR   **只读**资源目录：gui/ deploy/ README.md 之类。
              frozen = _MEIPASS/RES（只读、随包），非 frozen = 仓库根
    PC_DIR    「pc/ 语义根」：服务脚本与数据同层。frozen = DATA_DIR，
              非 frozen = APP_DIR/pc
    DATA_DIR  **可写**数据目录：日志、状态、归档、配置。
              frozen = APP_DIR/data，建不出来（Program Files 无写权限）时
              降级到 %LOCALAPPDATA%/FxxkPush/data 并记一行日志；
              非 frozen = pc/（**刻意不变**，见下）

非 frozen 下与改造前**逐字节等价**：DATA_DIR/PC_DIR = 仓库根 pc/、log_dir() =
pc/logs、config_path() = 仓库根 fp.config.json、res_file() = 仓库根资源。
仓库里跑的一切照旧，运行中的六个服务读写的还是原来那些文件。

非 frozen 下 DATA_DIR 不去 mkdir 一个 APP_DIR/data —— 那会在仓库根凭空造出
一个 data/（git 未跟踪目录）并把所有数据搬离 pc/，正是「非 frozen 行为不变」
要避免的。非 frozen 的数据本来就在 pc/（可写），无需任何降级逻辑。

导入期零重副作用：mkdir 失败不抛异常（降级 + 一行日志），迁移只**复制**
不删除（正在运行的实例正持有旧文件的句柄，移动会打断它们）。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------- frozen 判定
# 导入期快照：APP_DIR/RES_DIR/DATA_DIR 都由它派生，函数与常量必须同源，
# 中途改 sys.frozen 只会让两者打架（is_frozen() 说 True 而 APP_DIR 是源码根）。
IS_FROZEN: bool = bool(getattr(sys, "frozen", False))


def is_frozen() -> bool:
    """是否跑在 PyInstaller 打包出来的进程里（sys.frozen 已置位）。"""
    return IS_FROZEN


# ---------------------------------------------------------------- 目录常量
def _app_dir() -> Path:
    """程序所在目录。frozen = exe 目录；否则 = 仓库根（parents[2]）。"""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


APP_DIR: Path = _app_dir()
RES_DIR: Path = (Path(getattr(sys, "_MEIPASS", "")) / "RES") if IS_FROZEN else APP_DIR
SRC_DIR: Path = APP_DIR            # 非 frozen 下的源码根（= 仓库根）


def _log_line(msg: str, into: Path | None = None) -> None:
    """一行告警落盘 + stderr。

    刻意**不 import pclog**：pclog 自己要 import 本模块，反向依赖在导入期
    会撞半个模块（sys.modules 里还没有 LOG_DIR）。这里直接追加写，最坏情况
    全 try 掉 —— 记一行日志永远不能反过来变成启动失败。

    导入期（DATA_DIR 还没算出来、PC_DIR 尚未定义）调用时必须传 ``into``：
    那时 log_dir() 引用还没定义的 PC_DIR，写盘会静默丢失 —— 而这恰好是
    「降级到 %LOCALAPPDATA%」这条最该被看见的一行。
    """
    line = f"paths: {msg}"
    for stream in (sys.stderr, sys.stdout):
        try:
            if stream is not None:
                stream.write(line + "\n")
        except Exception:
            pass
    try:
        d = into if into is not None else log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "paths.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _fallback_data_dir() -> Path:
    """APP_DIR/data 建不出来时的落脚点：%LOCALAPPDATA%/FxxkPush/data。"""
    base = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "FxxkPush" / "data"


def _ensure_data_dir() -> Path:
    """frozen 下的 DATA_DIR = APP_DIR/data；建不出来降级 %LOCALAPPDATA%。

    全程不抛异常：这里是导入期，六个服务在 excepthook 挂上**之前**就要
    算路径，一个 mkdir 的 OSError 就是「六个服务启动即死且零痕迹」。

    非 frozen 直接返回 APP_DIR/pc —— 不 mkdir、不降级（数据原地不动）。
    """
    if not IS_FROZEN:
        return APP_DIR / "pc"
    primary = APP_DIR / "data"
    try:
        primary.mkdir(parents=True, exist_ok=True)
        if primary.is_dir():
            return primary
        raise NotADirectoryError(str(primary))
    except OSError as e:
        alt = _fallback_data_dir()
        try:
            alt.mkdir(parents=True, exist_ok=True)
            ok = alt.is_dir()
        except OSError:
            ok = False
        if ok:
            # 写进 data/logs/：与 pclog 的 pc/logs 同一形状，用户找日志的
            # 位置不用记两套（导入期 PC_DIR 还没定义，只能显式传目录）。
            _log_line(f"DATA_DIR 降级：{primary} 建不出来（{type(e).__name__}）"
                      f" → {alt}", into=alt / "logs")
        else:
            _log_line(f"DATA_DIR 建不出来且降级失败：{primary} / {alt}",
                      into=alt / "logs")
        return alt                      # 连降级都失败：仍不抛，返回值由调用方处置


DATA_DIR: Path = _ensure_data_dir()
# 「pc/ 语义根」：服务脚本与数据同层。非 frozen = APP_DIR/pc（与改造前
# HERE = parents[1] 同值）；frozen = DATA_DIR（exe 里服务与数据同层）。
PC_DIR: Path = DATA_DIR


# ---------------------------------------------------------------- 小函数
def ensure_data_dir() -> Path:
    """确保数据目录存在并返回它（幂等；失败不抛，返回当前 DATA_DIR）。"""
    if not DATA_DIR.is_dir():
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            _log_line(f"ensure_data_dir 失败：{DATA_DIR}（{type(e).__name__}）")
    return DATA_DIR


def log_dir() -> Path:
    """日志目录。frozen = DATA_DIR/logs，非 frozen = pc/logs（与改造前一致）。"""
    return PC_DIR / "logs"


def config_path() -> Path:
    """fp.config.json 的位置。

    非 frozen = 仓库根（原样，GUI/wizard 都往这儿写）；
    frozen = DATA_DIR/fp.config.json（旧位置的由 migrate_legacy_data 复制过来）。
    """
    return DATA_DIR / "fp.config.json" if IS_FROZEN else (APP_DIR / "fp.config.json")


def data_file(name: str) -> Path:
    """运行期数据文件（归档 jsonl / 状态 json / 规则 json）。"""
    return DATA_DIR / name


def res_file(*parts: str) -> Path:
    """只读资源（gui/index.html、deploy/…）：frozen 走 _MEIPASS/RES。"""
    return RES_DIR.joinpath(*parts)


def venv_dir() -> Path:
    """.venv 目录（非 frozen 才有意义；frozen 打包后本就无 venv）。"""
    return APP_DIR / ".venv"


# ---------------------------------------------------------------- 迁移兼容
# 旧位置的候选根：exe 同级（用户手工放配置/数据）与 exe 同级的 pc/（旧布局）。
_LEGACY_ROOTS = ("", "pc")
# 需要迁移的数据文件（与 uninstall.PURGE_FILES 的运行数据口径一致）
_LEGACY_FILES = (
    "fp.config.json",
    "notifications.jsonl",
    "listener_state.json",
    "toast_echo.jsonl",
    "triage_log.jsonl",
    "triage_state.json",
    "vision_log.jsonl",
    "vision_state.json",
    "feedback.jsonl",
    "triage_rules.json",
    ".start_services.lock",
)


def _copy(src: Path, dst: Path) -> bool:
    """复制单个文件（已存在 / 类型不对 / 失败都返回 False）。绝不移动或删除。"""
    if dst.exists() or not src.is_file():
        return False
    try:
        shutil.copy2(src, dst)
        return True
    except OSError:
        return False


def migrate_legacy_data() -> list[str]:
    """首次运行时把旧位置的数据**复制**到 DATA_DIR，返回复制清单。

    只在 frozen 下动作（非 frozen 下 DATA_DIR 就是原来的 pc/，无旧位置可迁）。
    不删旧文件：正在运行的实例还开着那些句柄，移动会在 Windows 上直接
    PermissionError，打断线上服务。已存在的目标一律跳过（幂等，第二次空跑）。
    """
    moved: list[str] = []
    if not IS_FROZEN:
        return moved
    target = ensure_data_dir()
    for root_rel in _LEGACY_ROOTS:
        root = APP_DIR / root_rel if root_rel else APP_DIR
        if root == target:
            continue
        for name in _LEGACY_FILES:
            if _copy(root / name, target / name):
                moved.append(f"{root / name} → {target / name}")
        # 日志目录：逐个文件复制（锁着的文件拷不动就跳过，不影响服务）
        src_logs, dst_logs = root / "logs", target / "logs"
        if src_logs.is_dir():
            try:
                dst_logs.mkdir(parents=True, exist_ok=True)
                for f in src_logs.iterdir():
                    if f.is_file() and _copy(f, dst_logs / f.name):
                        moved.append(f"{f} → {dst_logs / f.name}")
            except OSError:
                pass
    if moved:
        _log_line(f"旧位置数据已复制到 {target}（{len(moved)} 项，旧文件保留）: "
                  + "; ".join(moved))
    return moved


# 首次运行即迁移（frozen 下才有旧位置）。失败最多丢一行日志，绝不影响启动。
try:
    migrate_legacy_data()
except Exception:                                   # pragma: no cover - 兜底
    pass