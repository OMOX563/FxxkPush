#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VPS 直连兜底守卫（FxxkPush / Hermes 通用）。

## 要解决的问题

本机出口经 sing-box 代理，而 sing-box 的 ``proxy`` 组是 **selector**——
默认锁在单个节点上。实测四节点（对 gstatic 各 10 次真实连接）：

    node-anytls   10/10   588ms
    node-vless    10/10   702ms
    node-naive    10/10   791ms   <- 默认锁死的那个，最慢
    node-hy2       0/10   Timeout <- 死节点

早上线路劣化时，节点可能**全部半死**（TCP 握手能成、数据过不来），
于是到 VPS 的流量全断，表现为：

    ntfy_tunnel : SSHException('Error reading SSH protocol banner')
    ai_triager  : ConnectError('All connection attempts failed')

注意 banner 读不到是 **connect() 阶段**的问题（``client.connect()`` 里），
不是「已建立连接被掐」，所以 SSH keepalive 救不了。

## 思路

给「到 VPS 的流量」单独开一个 selector 组 ``vps-out = [auto, direct]``：

* 平时走 ``auto``（URLTest 自动择优，快）
* ``auto`` 探不通时切 ``direct``（直连，慢 21%、抖动大 11 倍，
  但**不依赖代理节点存活**）

本脚本负责探测与切换：

* 探测请求**必须走系统路由**（= 经 TUN = 经 ``vps-out`` 组），
  这样「探测失败」等价于「auto 探不通」——否则探测自己走直连，
  会分不清「auto 挂了」还是「网络全挂」，切 direct 的判断就反了。
* 连续失败 ``FAIL_THRESHOLD`` 次才切（防抖），连续成功 ``OK_THRESHOLD`` 次才切回。

## 依赖

* sing-box 核心开着 Clash API（默认 ``127.0.0.1:10814``）
* 配置里有 ``vps-out`` 这个 selector 组（见 ``sing-box config.json``
  的 ``route.rules`` 里指向它的那条规则）
* 只用标准库

## 用法

    python vps_guard.py            # 跑一轮（适合 cron）
    python vps_guard.py --status   # 只看当前状态，不切换
    python vps_guard.py --dry-run  # 只探测不切换

退出码：0 = 正常 / 已切换成功；1 = 探测失败且切换也失败；2 = 配置未就位。
"""
import argparse
import json
import os
import sys
from pathlib import Path
import time
import urllib.error
import urllib.request

# ---- 配置 ----
CLASH_API = os.environ.get("FP_CLASH_API", "http://127.0.0.1:10814")
VPS_GROUP = os.environ.get("FP_VPS_GROUP", "vps-out")
# 探测地址**不硬编码** —— 本仓库是公开的，真实服务器地址不能进版本库。
# 优先环境变量；否则从本机 fp.config.json 读（与 pc/services 下的服务同源）。
PROBE_URL = os.environ.get("FP_VPS_PROBE") or None
PROBE_TIMEOUT = float(os.environ.get("FP_VPS_PROBE_TIMEOUT", "8"))
FAIL_THRESHOLD = int(os.environ.get("FP_VPS_FAIL_THRESHOLD", "3"))
OK_THRESHOLD = int(os.environ.get("FP_VPS_OK_THRESHOLD", "3"))
STATE_FILE = os.environ.get(
    "FP_VPS_STATE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vps_guard_state.json"))


def _log(msg):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    print("[%s] vps_guard | %s" % (stamp, msg), flush=True)


def resolve_probe_url():
    """拿到 VPS 健康端点地址。**绝不把服务器地址写进代码**。"""
    if PROBE_URL:
        return PROBE_URL
    # 与 pc/services/ntfy_tunnel.py 同源：读本机 fp.config.json 的 vps 块
    try:
        sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                        str(Path(__file__).resolve().parents[1] / "core")]
        import fpconfig
        host, port, _user, _pw = fpconfig.vps()
        return "http://%s:2586/v1/health" % host
    except Exception as e:
        return None


def _api(path, method="GET", body=None, timeout=8):
    """Clash API 调用。返回 (status, data)。"""
    url = CLASH_API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return None, {"error": str(e)}


def probe():
    """探测 VPS 健康端点。

    走系统路由（不设 proxy、不指定网卡），这样流量会经 TUN 命中
    sing-box 的 route 规则 → 落到 vps-out 组 → 走组里当前选中的那条路。
    组里若只有 auto 能通，探测就成功；auto 半死则探测失败 —— 这正是我们要的信号。
    """
    url = resolve_probe_url()
    if not url:
        return False, "拿不到探测地址（FP_VPS_PROBE 未设，且读不到 fp.config.json）"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            body = r.read(200)
            return True, "HTTP %s %.0fms %s" % (r.status, (time.time() - t0) * 1000,
                                                body[:60])
    except urllib.error.HTTPError as e:
        # 有 HTTP 响应 = 链路是通的（服务返回错误也算活）
        return True, "HTTP %s %.0fms (服务报错但链路通)" % (
            e.code, (time.time() - t0) * 1000)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:80])


def group_now():
    st, data = _api("/proxies/" + VPS_GROUP)
    if st != 200:
        return None, "组 %s 不可用 (HTTP %s)" % (VPS_GROUP, st)
    return data.get("now"), ""


def switch_to(name):
    st, _ = _api("/proxies/" + VPS_GROUP, method="PUT", body={"name": name})
    if st in (200, 204):
        return True, ""
    return False, "切换到 %s 失败 (HTTP %s)" % (name, st)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mode": "auto", "fails": 0, "oks": 0, "last": ""}


def save_state(st):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log("状态写入失败: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只看状态")
    ap.add_argument("--dry-run", action="store_true", help="只探测不切换")
    args = ap.parse_args()

    if not resolve_probe_url():
        _log("配置未就位：拿不到 VPS 探测地址（设 FP_VPS_PROBE 或修好 fp.config.json）")
        return 2

    now, err = group_now()
    if now is None:
        _log("配置未就位：%s" % err)
        _log("需要在 sing-box config.json 里加 selector 组 %s=[auto,direct] "
             "并把到 VPS 的 route 规则指向它" % VPS_GROUP)
        return 2

    st = load_state()
    _log("组 %s 当前 = %s | 记录模式 = %s" % (VPS_GROUP, now, st.get("mode")))

    if args.status:
        print(json.dumps({"group": VPS_GROUP, "now": now, "state": st},
                         ensure_ascii=False, indent=2))
        return 0

    ok, detail = probe()
    _log("探测 %s → %s" % (resolve_probe_url(), detail))

    if ok:
        st["oks"] = st.get("oks", 0) + 1
        st["fails"] = 0
    else:
        st["fails"] = st.get("fails", 0) + 1
        st["oks"] = 0

    if not args.dry_run:
        if not ok and st["fails"] >= FAIL_THRESHOLD and now != "direct":
            good, err = switch_to("direct")
            if good:
                _log("auto 连续失败 %d 次 → 切 direct（慢但绕开代理节点）" % st["fails"])
                st["mode"] = "direct"
            else:
                _log("切 direct 失败: %s" % err)
                return 1
        elif ok and st["oks"] >= OK_THRESHOLD and now != "auto":
            good, err = switch_to("auto")
            if good:
                _log("连续成功 %d 次 → 切回 auto" % st["oks"])
                st["mode"] = "auto"
            else:
                _log("切回 auto 失败: %s" % err)
                return 1

    st["last"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_state(st)
    _log("状态: fails=%d oks=%d mode=%s" % (st["fails"], st["oks"], st["mode"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
