# FxxkPush v0.9.0

自建 AI 消息分诊推送服务：PC 上的 AI 判断"哪些通知值得打扰手机"，重要的经 ntfy 推到手机/手表，垃圾静默归档。

**核心能力：手机可以放心关闭微信、QQ、企业微信等后台**——所有需要你知道的消息，由 PC 端 AI 分诊后主动推到手机；垃圾消息静默归档，不打扰。同时 VPS 的异常（宕机前兆、入侵、磁盘告急）也会第一时间推到你手上。

## 基于 ntfy 二次开发

本项目基于 [ntfy](https://ntfy.sh/)（[GitHub · binwiederhier/ntfy](https://github.com/binwiederhier/ntfy)）
进行二次开发与深度集成：服务端直接复用 ntfy 作为消息中继与手机推送通道，
PC 端按其发布/订阅协议接入，并在其之上构建了 AI 分诊、误判反馈、链路自愈
与三层降级告警等能力。ntfy 采用 Apache-2.0 许可证。

## 这解决了什么问题

- 手机关掉微信/QQ 后台 → 省电省内存，再也不用频繁清后台
- 但怕错过重要消息？→ PC 替你值班：家人联系、@所有人、@我、日程提醒、账户安全、服务器异常……AI 判重要的才推手机
- 群聊灌水、营销推送、转账回执、学校新闻 → 全部静默，手机一天安静
- VPS 半夜磁盘爆了 / 被入侵 → 手机立刻收到告警，不用早上起来看服务器才发现

## 架构

```
VPS 日志（OOM/磁盘>90%/SSH 登录/服务挂掉）
  ├─ 硬规则 → ntfy(fp-vps) ──────────────┐
  └─ 灰区事件 → ntfy(fp-gray) ───────────┤
                                         ├→ PC: ai_triager (云端/本地OCR模型)
Windows toast 通知（QQ/钉钉/学习通等）    │     ├─ 重要 → ntfy(fp-phone) → 手机/手表
  └─ notification_listener → ntfy(fp-pc)┘     └─ 忽略 → 静默归档 JSONL
                                         │
微信 + 企业微信（自绘UI，无系统通知）      │
  └─ vision_listener ───────────────────┘
     （窗口藏屏外 + 定时截图 → 云端/本地OCR模型视觉分诊）

PC 侧所有服务 → http://127.0.0.1:2586 → (pc/services/ntfy_tunnel.py 走 SSH) → VPS ntfy:2586
                                                        ↑
手机上点 👍/👎 按钮 ── ntfy http action ──→ VPS /fp-feedback ┘
                                       （pc/core/fp_feedback.py 消费 → 规则自动纠正）
```

- **VPS（1C1G）**：传感器 + 中继。ntfy 常驻（推送通道永远在线），日志硬规则判级（grep 级零内存），不跑 AI
- **PC**：大脑 + 采集器。Windows 通知监听、微信/企业微信视觉识别、AI 分诊、结果推送
- **手机**：ntfy 客户端接收 + Wear OS 手表震动，微信/QQ/企业微信后台全杀

### 为什么 PC 侧要绕一条 SSH 隧道

很多网络（校园网、公共 Wi-Fi、移动热点）会封掉非常规端口——本项目实测环境就封了 80 / 2586 / 8080 / 8888，而 SSH 端口放行。于是 `pc/services/ntfy_tunnel.py` 在本地监听 `127.0.0.1:2586`，把每个连接通过 SSH 的 `direct-tcpip` 通道转到 VPS 的 ntfy 上。好处：

- 不管换到哪个网络，只要 SSH 通，推送通道就通
- ntfy 端口不必对外暴露给 PC，暴露面更小
- PC 侧所有服务只需认 `http://127.0.0.1:2586` 一个地址

如果不需要（你的网络不封端口），把各服务里的 `FP_NTFY_URL` 环境变量设为直连地址即可，隧道进程可以不启动。

### PC 侧目录结构（2026-09-23 合并后）

```
pc/
├── services/    6 个常驻服务（= 6 个自启入口 / 独立进程边界，共 12 进程）
│   ├── ntfy_tunnel / pc_subscriber / notification_listener
│   ├── ai_triager / vision_listener（窗口视觉分诊，清单见 fp.config.json vision 块）/ watchdog
├── core/        公共件：pclog（统一日志）/ fp_feedback（反馈闭环）/ alert_fallback（三层降级推送）
│                / paths（路径抽象层）/ procspawn（拉起与进程识别）/ fpconfig（配置读取器）
├── bootstrap/   启动编排与自启注册：start_services.py + install/restart 两个 .bat
├── logs/        统一日志（pclog 唯一出口）
└── *.secret / *.json(l)   运行时配置与数据（不入库）
```

### 仓库根结构（v0.9 起新增打包相关文件）

```
仓库根/
├── launcher.py                 单一多路复用入口：--role 分发到各模块（打包后是唯一 exe 入口）
├── FxxkPush.spec               PyInstaller 打包配置（onedir、console=False、图标与只读资源）
├── build_exe.bat               构建脚本：清 dist/build → 调 PyInstaller → 打印体积统计
├── installer.iss               Inno Setup 7 安装脚本（安装包 + 卸载两档）
├── fp_console_runtime_hook.py  PyInstaller runtime hook：windowed 模式下补回 stderr 兜底
├── resources/
│   └── fxxkpush.ico           应用图标（spec 与 installer.iss 都读它）
├── GUI.bat / setup.bat / uninstall.bat   源码态入口（exe 用户不需要这三个）
├── fxxkpush_gui.py + gui/      图形界面本体与前端资源
├── setup.py / uninstall.py / vps_exec.py  向导 / 卸载 / VPS 命令行助手
└── pc/ deploy/ docs/           服务与采集器 / VPS 侧 / 审查档案
```

## 消息采集方式

| 来源 | 方式 | 实时性 |
|---|---|---|
| QQ / 钉钉 / 学习通等（走 Windows toast 的 App） | UserNotificationListener 抓系统通知 | 秒级实时 |
| 自绘 UI、不走系统通知的应用（微信 / 企微…，清单见 fp.config.json vision 块） | 窗口藏屏外，定时 PrintWindow 截图 → 云端/本地OCR模型视觉识别 | 30 分钟轮询 |
| VPS 日志 | journalctl 采集 + 硬规则 grep 判级 | 60 秒巡检 |

微信/企业微信的视觉方案说明：这两个 App 的通知是自绘的、不走 Windows 通知中心，UIA 控件树也是黑盒。本项目的解法是把主窗口挪到屏幕外（保持可见不最小化），定时用 PrintWindow 离屏截图发给视觉模型识别——**全程无鼠标劫持、不挡屏幕、不影响操作电脑**。

## 重要消息判定规则（按优先级）

1. **必推（硬规则，不过 AI、不去重）**：消息含 `@所有人` / `@我` / `@全体成员`；内容含 `text`（测试通道）
   > 硬规则在**通往手机的那道闸门**（`ai_triager`）上执行。上游采集器（视觉监听）虽然也会标记 @提及，但如果只在采集器里标记，下游 AI 仍可能以"群聊闲聊"为由把它压掉——实测踩过这个坑。
2. **AI 判定**（云端/本地OCR模型）：家人/紧急联系人来信、日程提醒、账户安全、服务异常需要处理
3. **黑名单**（无条件忽略，声明于 fp.config.json 的 `apps` 块）：微信支付、公众号、服务通知、学校新闻、失物招领等系统号
4. AI 调用失败时默认按重要推送（宁滥勿缺）
5. 附带 30 分钟滑动窗口去重（同类事件只报一次+计数）

## 安装包（推荐）

**从这一版起，PC 端用户机器不需要装 Python。** 编译器把解释器和全部依赖打进了
exe，下载 → 双击 → 桌面图标，一条路走完。下面这一章是新手主线；后面「上手教程
（GUI 主线）」是**从源码跑**的等价路径（要改代码、想读日志源码、或在多台机器上
做定制时才走它）。

### 第 0 步：下载

打开本仓库的 **Releases** 页，下载 `FxxkPush-Setup-<版本号>.exe`（约 22 MB）。

想避开安装、也不要注册表痕迹，就下同一个 Release 里的
`FxxkPush-<版本号>-windows-x64.zip`（约 25 MB）——两种产物并存、内容同源，
前者是安装包（自带卸载器），后者是便携版（解压即用）。

### 第 1 步：双击安装 → 启动

1. 双击 `FxxkPush-Setup-<版本号>.exe`，一路「下一步」即可，**不需要管理员权限**
2. 默认装到 `%LOCALAPPDATA%\Programs\FxxkPush`（当前用户目录），安装向导里
   可以改
3. 装完勾选「立即启动 FxxkPush」直接开界面，或之后从快捷方式启动：
   **开始菜单**「FxxkPush」一定有；**桌面**快捷方式是安装时的可选项（默认不勾，
   在「附加任务」里勾上才有）
4. 双击 `FxxkPush.exe`（不带任何参数）= 打开图形界面，行为与源码态的
   `GUI.bat` 一致

装完的目录长这样（`data/` 首次运行才创建，装完当场就建好让你看得见）：

```
%LOCALAPPDATA%\Programs\FxxkPush\
├── FxxkPush.exe        主程序（无黑窗）
├── _internal\          解释器 + 依赖 + 只读资源（gui/、README.md）
├── data\               **可写**运行数据：fp.config.json、logs\、状态与归档
└── unins000.exe        卸载器
```

### 为什么默认不装进 Program Files

Program Files 默认**不可写**，而 FxxkPush 的配置（含 ntfy token、AI key、VPS 凭据）、
日志、状态与消息归档都要落在**可写**目录里。硬装进 Program Files 会让首次运行
建不出 `data/`，只好降级到 `%LOCALAPPDATA%\FxxkPush\data`——配置在 Program
Files 里、日志在 AppData 里，两处分家，用户永远找不到。装到用户目录则：

- 零提权（`PrivilegesRequired=lowest`，不需要管理员、不弹 UAC）
- `data/` 就在 exe 旁边，一眼看得见
- 卸载就是删一个自己完全拥有的目录，不碰别人的东西

**坚持选 Program Files 也不是不能用**：程序会自动把可写数据降级到
`%LOCALAPPDATA%\FxxkPush\data` 并记一行日志，功能不缺，只是文件分两处。

### 卸载

开始菜单「卸载 FxxkPush」（或控制面板里的同名项）。卸载时会先问你一句
**是否同时删除配置与历史数据**：

- 选「否」= 默认档：删程序文件、**保留** `data/`（配置、日志、消息历史都在，
  重装后直接接着用）
- 选「是」 = 清数据档：连 `data/` 下的配置（**含 API key 与服务器凭据**）、
  日志与消息历史一并删除，不可撤销

静默卸载（`/VERYSILENT` 等）**不询问**，一律按「保留数据」处理——无人值守场景
下宁可留数据也不该卡在弹窗上。

卸载器只停/删**指向自己那一份**的进程与开机自启项：同机还跑着源码树（例如你既
装了 exe 又 clone 了仓库）时，源码树那 12 个服务进程和它自己的自启项不会被误伤。

### 系统要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11，x64 |
| Python | **不需要**——解释器与全部依赖已内置 |
| 管理员权限 | **不需要**（用户级安装，无 UAC） |
| Microsoft Edge WebView2 Runtime | **只有图形界面需要**。Win11 自带；Win10 缺了也能用：启动界面时会在开窗前探测到并弹出安装指引（附官方下载地址），装完无需重启直接再打开即可 |

WebView2 缺失**只影响界面，不影响服务**：命令行一切照常可用，比如
`FxxkPush.exe --role check`（环境自检）、`FxxkPush.exe --role start`
（启动全部服务）、`FxxkPush.exe --roles`（列出全部命令）。也就是说这个软依赖
最坏的结果是「暂时只能用命令行」，不会让你收不到任何推送。

### 装完之后的命令行形态

一个 exe 顶全部：双击 = 图形界面，`--role` 切到别的运行形态。

```powershell
FxxkPush.exe                      # 打开图形界面（等价 GUI.bat）
FxxkPush.exe --roles              # 列出全部可用 role（附各 role 指向的文件）
FxxkPush.exe --role check         # 环境自检（只读，等价 setup.py --check）
FxxkPush.exe --role start         # 启动全部服务（杀旧 → 起隧道 → 健康门 → 其余）
FxxkPush.exe --role vision park   # 把清单里的窗口挪到屏幕外
FxxkPush.exe --role listener --grant-access   # 补做 Windows 通知读取授权
FxxkPush.exe --role uninstall --purge         # 卸载并清数据（等价 GUI 危险区）
```

可用 role：`tunnel` / `subscriber` / `listener` / `triager` / `vision` /
`watchdog`（六个常驻服务）、`start`（启动编排）、`gui`、`setup`、`uninstall`、
`check`、`vps-exec`。

两处与源码态不同、值得知道的行为：

- **开机自启的形态**是 `"FxxkPush.exe" --role <role>`（6 项，HKCU Run，无需管理员），
  在 GUI 控制页的「开机自启」开关里写 —— 不是 `pythonw.exe` + `.py` 路径
- **一个服务只占 1 个进程**（源码态是 2 个：venv 的 `pythonw.exe` 是个跳板，会再
  派生一个真身解释器）。exe 模式没有那层跳板，任务管理器里 6 个服务就是 6 个进程

> 窗口标题与顶栏徽标上的版本号读的是**打进包里的那份 README 首行**，也就是构建
> 当下的版本——它标的是「你手上这个 exe 是哪一版」，不会跟着仓库后续更新变。

## 上手教程（GUI 主线）

> 走的是**源码路线**：需要 Python 3.12+ 或 [uv](https://uv.sh)，装依赖、跑向导
> 都发生在你自己的机器上。**只想用、不想折腾环境的话，从上一章「安装包」开始**，
> 那一章才是主线，本章是给要改代码的人准备的等价路径。

### 第 0 步：前提

- 一台有公网 IP 的 Linux VPS（1C1G 够用；VPS 端没有图形界面，命令行省不掉）
- 一台 Windows 10/11 PC：PATH 里有 Python 3.12+ 或 [uv](https://uv.sh)
  （`GUI.bat` 按 `python` → `uv` → `python3` 的顺序找解释器）；Win11 自带
  WebView2 Runtime，Win10 缺了就装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
- 手机能装 ntfy 客户端

> 只想**用**不要改代码的话，上面这两条都不必满足——走上一章的安装包，
> 用户机器不需要 Python。

### 第 1 步：下载

克隆仓库或下载 zip 解压，拿到仓库根（`GUI.bat`、`setup.bat`、`deploy/`、`pc/`、
`gui/` 都在这层）。`fp.config.json` 和 `.venv` 不入库：前者首次由向导生成，
后者 `setup.bat` 现建。

### 第 2 步：VPS —— 一条命令

把仓库传到服务器（root 权限，可重复执行）：

```bash
bash deploy/setup_vps.sh
```

脚本按序做 5 件事：装 ntfy → 探测公网 IP → 写 `/etc/ntfy/server.yml` 并启动 →
建管理员账号签 token → 部署日志监控 systemd。结束时打印**手机订阅所需的全部
信息**（服务器地址、话题、账号密码）和要粘到 PC 的 `tk_` 开头的 token。
可选环境变量：`FP_PUBLIC_IP=1.2.3.4` 覆盖自动探测、`FP_NTFY_USER` /
`FP_NTFY_PASSWORD` 指定管理员账号（不给就随机生成并打印）。

不用脚本的话，同样的事就是下面这几条（脚本替你逐条跑过）：

```bash
apt install ntfy                                # 或用 GitHub release 二进制
cp deploy/ntfy-server.yml /etc/ntfy/server.yml  # 记得改 base-url 成你的地址
NTFY_PASSWORD=你的密码 ntfy user add --role admin 你的管理员用户名
ntfy token add 你的管理员用户名                 # 输出 tk_你的token，PC 侧要用
systemctl enable --now ntfy
curl http://127.0.0.1:2586/v1/health            # 返回 200 即成功
mkdir -p /opt/fuckpush && cp deploy/vps_monitor.py /opt/fuckpush/
```

日志监控的 systemd unit（`deploy/setup_vps.sh` 会替你写好，手动部署照抄，
放到 `/etc/systemd/system/fuckpush-monitor.service`）：

```ini
[Unit]
Description=FxxkPush VPS monitor
After=network.target ntfy.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/fuckpush/vps_monitor.py
Environment=FP_NTFY_TOKEN=tk_你的token
Environment=FP_NTFY_URL=http://127.0.0.1:2586/
# 这些 IP 的 SSH 登录不上报（比如 VPS 自身、你自己的固定出口 IP），逗号分隔
Environment=FP_SELF_IPS=你的VPS_IP
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now fuckpush-monitor
systemctl is-active ntfy fuckpush-monitor   # 两个都应是 active
```

### 第 3 步：PC 首次 —— 双击 `setup.bat`（一次性）

向导按序走 7 步，其中**只有它能做的两件**是：建 `.venv` 装依赖（GUI 要的
`pywebview` / `pythonnet` 就装在这里）和 Windows 通知读取授权（会弹系统窗口，
点「允许」，这是 toast 通道的前提）。顺带问答生成 `fp.config.json`，首次会一并
立好 `vision` 窗口清单与 `apps` 分诊规则的默认值（**这两块 GUI 表单不碰**），
再注册 6 项开机自启、立即启动（含隧道健康门）、窗口挪到屏外、健康验证。
已有配置默认保留，绝不静默覆盖。

装完之后日常**不再需要** `setup.bat`：改配置、启停、自启、窗口归位、卸载
全在 GUI 里做。

### 第 4 步：双击 `GUI.bat` 打开控制台

- 界面三个页签：**仪表盘 / 配置 / 控制**（逐卡说明见下一节「图形化界面（v0.8）」）
- 控制台跟着 GUI：窗口正常关闭 → 控制台自动关闭；只有启动失败才 `pause` 保留，
  好让你读报错
- 重复双击会聚焦已开的窗口（单实例），窗口标题 `FxxkPush v0.9.0`（版本号读
  README 首行 H1）
- 打不开、提示没找到 Python 或缺 pywebview → 见附录「排障」

### 第 5 步：配置页填四项

「配置」页表单分四组，「保存配置」= **合并写入** `fp.config.json`（不动 `apps` /
`vision` 既有条目；密钥字段**留空 = 保持原值**）：

| 表单分组 | 字段 | 写入 | 校验与语义 |
|---|---|---|---|
| VPS SSH | 公网地址\*、端口、用户、密码 | `vps.*` | 地址必填；端口须是 1–65535 的数字（默认 22），用户默认 `root`；密码留空保持，点「显示」按需回读 |
| ntfy 推送通道 | token（`tk_` 开头） | `ntfy.token` | 前缀不对直接拦下；留空保持 |
| AI / OCR 模型（三处分诊共用） | 接口地址 base_url、api_key、模型名 | `ai.*` | base_url 须以 `http://` / `https://` 开头；api_key 留空保持；**地址或模型留空 = AI fail-open（判不了就全推）** |
| 通用 | 你的昵称（「@我」判定）+ 视觉目标（只读 chips） | `vision.settings.my_name` | 视觉目标来自 `fp.config.json` 的 `vision` 块，表单只显示不编辑 |

保存后 banner 列出改了哪些键；`ai` / `ntfy` / `vision` 有改动时会多一个
「一键启动（重启生效）」按钮（服务启动时才取快照，`apps` 段则是 mtime 热加载、
即时生效，见「配置文件」一节）。校验不过就整份不写，错误逐条列在 banner 里；
配置文件当前读不出来时同样拒绝保存，避免把已有配置整份覆盖掉。

> `apps` 规则与 `vision` 窗口清单**不在表单里**：首次由 `setup.bat` 向导生成
> 默认值，之后直接改 `fp.config.json`。另外保存是整份 `json.dumps` 重写，
> 手写的 `//` 注释会被吃掉，保存前会如实警告。

### 第 6 步：一键启动

仪表盘「▶ 一键启动」等价 `pc\bootstrap\start_services.py`，走完整健康门：

1. 杀旧进程 → 起隧道 → 轮询 `/v1/health` 直到 200 → 才起其余 5 个 → 校验 6×2 进程
2. 启动过程输出落 `pc/logs/gui_launcher.log`，「启动日志」卡显示
   `start_services.log` 的尾部；`.venv` 不存在会直接报「.venv 不存在，请先跑
   setup.bat」

起来之后对着「链路自检」卡核三样：**隧道 health = HTTP 200**、**开机自启 x/6**、
**视觉目标**不是「（无）」；「看门狗心跳」有一行在更新 = 管道自检在跑。顶栏三枚
药丸（服务 / 隧道 / 自启）同时变绿就算成了。进程数是服务数的 2 倍（venv 的
`pythonw.exe` 是跳板，派生真正的解释器），正常。要停点「■ 停止」。

启动顺序那道门不能省：隧道慢一点（SSH 抖动、代理切换、开机网络未就绪），后面
五个服务就会对着一个还没就绪的 `2586` 空转刷错，而且没有任何报错提示这是
「启动顺序」问题。

### 第 7 步：手机装 ntfy 订阅

1. 装 ntfy（Play Store / F-Droid / App Store）
2. 按第 2 步脚本打印的信息添加订阅：服务器 `http://你的VPS-IP:2586`，话题 `fp-phone`
3. 订阅设置里填账号密码（第 2 步创建的用户），开**高优先级 + 震动**

### 装完验证一遍

- 仪表盘「链路自检」→ 隧道 `HTTP 200`；点「跑 setup --check」看五项汇总
  （项目标记 / venv 与依赖 / fp.config.json 五块 / 开机自启 x/6 / 服务健康）
- 端到端：任意渠道发含 `text` 的消息 → 手机收到（测试通道，绕过 AI 与去重）
- 微信 / 企业微信：让人发条消息 → 30 分钟内 AI 分诊，重要则手机响
- VPS：停一个被监控的服务 → 手机收到告警
- 日志：点「打开日志目录」→ `pc\logs\*.log` 全部走 `pc/core/pclog.py`（统一格式 +
  统一出口），`trace_id` 贯穿一条消息的全链路，grep 一次就能拉出完整过程：

```
2026-09-22T13:20:01.781+0800 [INFO ] ai_triager | trace=verify1 | TEST_PUSH ok ...
grep "trace=verify1" pc/logs/*.log
```

## 图形化界面（v0.8）

依赖由 `setup.bat` 装进 `.venv`（`pywebview` + `pythonnet`，EdgeChromium 内核），
Win10/11 要有 WebView2 Runtime（Win11 自带）。入口 `GUI.bat`（带控制台，可看输出、
可跑自检），**无需先手动启动任何命令行工具**。逐页：

### 顶栏

版本徽标（读 README 首行 H1）+ 三枚状态药丸，2.5 秒刷新一次：
`服务 N 进程` / `服务未运行`、`隧道 200` / `隧道不通`、`自启 x/6` / `自启未注册`。

### 仪表盘

- **服务进程**：6 个服务标签（跑着显示 `×N`、没跑显示 `✕`），副题
  `N 进程 · x/6 服务`；按钮「▶ 一键启动」「■ 停止」（两者互斥，执行中转圈禁用）
- **链路自检**：隧道 health、开机自启（`x/6 项路径有效` / `未注册`）、视觉目标
  三个只读字段；按钮「跑 setup --check」（结果进启动日志区 + toast 提示通过与否）、
  「打开日志目录」（资源管理器打开 `pc\logs`）
- **看门狗心跳 + 启动日志**：`watchdog.log` 最后一行、`start_services.log` 尾部
  10 行；启动 / 自检这类长任务的进度也显示在这里

### 配置

`fp.config.json` 四组表单字段（第 5 步的表）+「保存配置」「重置表单」。密钥字段
默认打码、不回传前端，点「显示」才按需回读（「留空 = 保持原值」的语义不变）。
保存结果三种 banner：**未保存**（校验错误逐条列出）/ **已保存**（列出改动键，
需要重启时附「一键启动（重启生效）」按钮）/ **内容无变化，未写入**；
`vision` 目标以只读 chips 显示。

### 控制

- **开机自启**：开关写 / 删 HKCU Run 的 6 项（`FuckPush_*`，无需管理员），标签显示
  「已开启（6 项，下次登录生效）」或「已关闭」
- **聊天窗口归位**：「藏到屏外（park）」「拉回屏内（unpark）」——视觉通道需要窗口
  在屏幕外，重启电脑后手动归位就用这两个按钮
- **危险区**：「一键卸载…」弹窗，可勾选「同时清除 fp.config.json 与运行数据
  （含密钥，不可恢复）」，在输入框敲 `uninstall` 才解锁「确认卸载」；执行顺序 =
  停服务 → 摘 6 项自启 → 窗口拉回屏内（等价 `uninstall.py --yes [--purge]`）

### 自检与后台行为

- `GUI.bat --smoke`（即 `fxxkpush_gui.py --smoke`）：先无窗口断言（状态结构与实值、
  配置往返无变化、空 host / 坏 token 拦截、自启注册幂等、密钥回读与非法参数拦截），
  全过再真开窗跑一次 UI 探针，约 6 秒自动关闭。注意其中「自启注册幂等」会把 6 项
  自启按当前路径**重写一遍**（幂等），不是纯只读
- 「一键启动」「停止」「一键卸载」三个会动服务进程的任务彼此互斥，不会并发交错
- 重复双击 `GUI.bat` 聚焦旧窗口；关窗口即退出，控制台随之自动关闭

## 附录：无 GUI 环境与排障

命令行与向导全在这里：没有图形界面的环境（服务器、精简 shell、远程会话）、GUI
起不来时的排障，靠它们。日常用不上，出事才翻。

### A. 安装向导：setup.bat / setup.py

双击 `setup.bat`（或 `python setup.py`）走 7 步交互向导：① 建 `.venv` 装依赖
（uv / `python -m venv` 双通道）② 问答生成 `fp.config.json`（已有配置默认保留，
不静默覆盖）③ Windows 通知读取授权 ④ 注册 6 项开机自启（HKCU Run）⑤ 立即启动
（含隧道健康门）⑥ 窗口挪到屏外 ⑦ 健康验证 + 汇总。首次安装（教程第 3 步）就是它；
GUI 替代不了的只有 ② 的默认 `vision` / `apps` 清单和 ③ 的通知授权。

### B. 只读自检：setup.py --check

```powershell
.venv\Scripts\python.exe setup.py --check      # GUI 里点「跑 setup --check」跑的是同一套检查
```

只检查环境 / 配置 / 自启 / 健康，不改动任何东西，5 项：项目标记文件、venv 与依赖、
`fp.config.json`（`ai` · `ntfy` · `vps` · `vision` · `apps` 五块）、开机自启 x/6
（路径可达）、服务健康 `health=200`。带 `//` 注释的配置不会被误判成坏文件
（读取前先剥注释）。

### C. 命令行启停与开机自启

```powershell
# 一键重启（含顺序与健康门）：双击 pc\bootstrap\restart_services.bat
# 实际执行 pc\bootstrap\start_services.py：
#   杀旧进程 -> 起隧道 -> 轮询 /v1/health 直到 200 -> 才起其余 5 个 -> 校验 6x2 进程
.venv\Scripts\python.exe pc\bootstrap\start_services.py

# 注册登录自启：双击项目根目录的「注册自启动.bat」（从当前目录向上探测项目根；
# 等价实现：pc\bootstrap\install_autostart.bat）。以后挪动目录后重跑一次即可
# （6 项，Windows 登录时并发启动；各服务自带重试，隧道就绪前会自动等待）
```

那道健康门的意义同第 6 步；GUI 的「▶ 一键启动」跑的就是这个 `start_services.py`。

> 每个服务在任务管理器里会显示成 **2 个进程**（venv 的 `pythonw.exe` 是个跳板，
> 会派生真正的解释器），所以 6 个服务 = 12 个进程，这是正常的。

> **venv 或项目目录变动后**（重建 `.venv`、把仓库挪到别的路径）：HKCU Run 的
> 6 个自启项和 `start_services.PYW` 都**硬编码了 `.venv` 绝对路径**，路径一变
> 6 项（含 watchdog）会一起静默失效且没有任何看护者会发现。改完务必重跑
> `pc\bootstrap\install_autostart.bat`（或在 GUI 控制页把开机自启关一次再开一次，
> 同样按当前路径重写这 6 项），再 `reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run"`
> 核对 6 条路径都指向新位置。

手动单起（不推荐，会跳过健康门）：

```powershell
.venv\Scripts\pythonw.exe pc\services\ntfy_tunnel.py            # 1) 隧道（必须先起）
Start-Sleep 5
.venv\Scripts\pythonw.exe pc\services\pc_subscriber.py          # 2) 订阅 VPS 告警 → 弹 toast
.venv\Scripts\pythonw.exe pc\services\notification_listener.py  # 3) 抓 Windows 通知
.venv\Scripts\pythonw.exe pc\services\ai_triager.py             # 4) AI 分诊
.venv\Scripts\pythonw.exe pc\services\vision_listener.py # 5) 窗口视觉识别（清单见 fp.config.json vision 块）
.venv\Scripts\pythonw.exe pc\services\watchdog.py               # 6) 管道自检（故障直接推手机）
```

### D. 命令行验证

```powershell
# 隧道通不通
curl.exe -H "Authorization: Bearer $((Get-Content fp.config.json -Raw -Encoding UTF8 | ConvertFrom-Json).ntfy.token)" http://127.0.0.1:2586/v1/health   # 期望 200（PS5.1 必须 -Encoding UTF8，否则无 BOM 的 UTF-8 被按 ANSI 解成乱码）
```

端到端 / 视觉 / VPS / 日志四项验证见上文「装完验证一遍」（GUI 版），内容一致；
通知授权的命令行入口是 `.venv\Scripts\python.exe pc\services\notification_listener.py --grant-access`。

### E. 从源码构建 exe

自己出安装包/便携版（三步，全在 Windows 上跑）：

```powershell
# 1) 装 PyInstaller（先用 setup.bat 建好 .venv，再装进同一个解释器）
.venv\Scripts\python.exe -m pip install pyinstaller

# 2) 构建：清 dist\ build\ → PyInstaller → 校验 exe → 打印体积
build_exe.bat

# 3) 打安装包（需另装 Inno Setup 7，ISCC.exe 在其安装目录下）
ISCC installer.iss
```

产物：`dist\FxxkPush\`（onedir 目录）→ `dist\FxxkPush-Setup-<版本号>.exe` +
压成 zip 就是便携版：

```powershell
powershell -c "Compress-Archive -Path dist\FxxkPush\* -DestinationPath FxxkPush-<版本号>-windows-x64.zip"
```

要点（都是踩过的坑）：

- **必须 Python 3.12**：`pywinrt` / `pythonnet` 对高版本解释器不友好（3.14 报
  cannot create instances），换版本构建出来的包在用户机上也是坏的
- **必须 Inno Setup 7**：`.iss` 里用了 6.x 之后的语法
- **`installer.iss` 里的 `AppVersion` 要手动改**（当前 `0.9.0`）——它决定产物
  文件名与安装包属性，忘了改会编出一个名字带旧版本号的包
- **`console=False` 别改回 `console=True`**：改成控制台子系统后，GUI 启动会弹出
  一个黑框窗口贴在界面后面；兜底由 `fp_console_runtime_hook.py` 做
- 改完 `launcher.py` 的 role 表或新增 `pc/` 下的模块，**必须同步改
  `FxxkPush.spec` 的 hiddenimports** —— 动态 `runpy` 分发扫不到模块名，漏列的
  role 编译期不报错，用户机上才 `ModuleNotFoundError`
- 版本号读的是打进包的 README 首行 H1：改版本要改 README，别只改 `AppVersion`

### F. 排障

- **双击 `GUI.bat` 报「未找到可用的 Python3」**：PATH 里既没有可用的 Python 3
  也没有 `uv` —— 装一个（Python 3.12+ 或 [uv](https://uv.sh)）再试。
- **弹窗「缺少 pywebview（跑一次 setup.bat 安装依赖）」**：PATH 上那个 `python`
  跟 `.venv` 不是同一个解释器（`setup.bat` 只往 `.venv` 里装依赖）。换入口即可：
  `uv run --python 3.12 python fxxkpush_gui.py`（接着用仓库根已有的 `.venv`），
  或 `.venv\Scripts\pythonw.exe fxxkpush_gui.py`；彻底的解法是让 PATH 上那个
  `python` 也装上 `pywebview` + `pythonnet`。
- **窗口起不来、提示 WebView2**：装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
  （Win11 自带），装完重开 `GUI.bat`。
- **「一键启动」过不了健康门 / 仪表盘显示「隧道不通」**：点「跑 setup --check」，
  再看 `pc\logs\ntfy_tunnel.log` —— 多半是 `fp.config.json` 的 `vps` 四段不对
  （密码、端口），或者这个网络把 SSH 也封了（那就得换网络，隧道是绕端口封锁的，
  不是绕 SSH 封锁的）。
- **AI 每条都推**：`ai.base_url` 或 `ai.model` 为空即 fail-open（判不了就全推），
  表单补全这两项保存，再「一键启动（重启生效）」。
- **改了配置没生效**：`apps` 段 mtime 热加载、即时生效；`ai` / `ntfy` / `vision`
  段要重启服务 —— GUI 保存后的「一键启动（重启生效）」按钮，或
  `pc\bootstrap\restart_services.bat`。
- **通知授权过期 / 抓不到 toast**：重跑
  `.venv\Scripts\python.exe pc\services\notification_listener.py --grant-access`
  （向导第 3 步做的也是这件事）。exe 用户用
  `FxxkPush.exe --role listener --grant-access`
- **exe 双击后界面一闪就没 / 没反应**：先用命令行确认服务面是不是好的
  ——`FxxkPush.exe --role check` 能跑通就说明是界面层的问题，多半是 WebView2
  缺失（会在开窗前弹指引）；想看真实报错就
  `FxxkPush.exe --role gui`，`fp_console_runtime_hook.py` 会把 stderr 兜住
- **exe 报「role xxx 的目标文件不存在」**：包漏模块了（多半是 `FxxkPush.spec` 的
  hiddenimports 没跟着新模块改），按上一节重建
- **Program Files 下装了、找不到配置**：可写数据自动降级到了
  `%LOCALAPPDATA%\FxxkPush\data`，去那儿找（`data\` 建不出来时会有一行日志提示）

## 组件

| 文件 | 运行位置 | 作用 |
|---|---|---|
| `deploy/vps_monitor.py` | VPS (systemd) | journalctl 采集 + 硬规则判级 + 灰区落盘，60s 巡检，冷却去重 |
| `deploy/ntfy-server.yml` | VPS | ntfy 服务端配置（token 鉴权，deny-all） |
| `pc/services/ntfy_tunnel.py` | PC (自启) | SSH 本地转发：`127.0.0.1:2586` → VPS ntfy，绕开端口封锁，断线自愈 |
| `pc/services/notification_listener.py` | PC (自启) | UserNotificationListener 抓 toast → fp-pc + JSONL 归档 |
| `pc/services/vision_listener.py` | PC (自启) | 按 `fp.config.json` vision 块清单把窗口藏屏外 → 定时截图 → 云端/本地OCR模型视觉分诊（进程→类名→标题正则三层匹配） |
| `pc/services/pc_subscriber.py` | PC (自启) | 订阅 fp-vps/fp-gray，弹 Windows toast |
| `pc/services/ai_triager.py` | PC (自启) | 消费 fp-pc/fp-gray/fp-vps/fp-feedback → AI 分诊 → fp-phone / 静默，反馈经 `fp_feedback` 改写规则 |
| `pc/core/fp_feedback.py` | PC (由 ai_triager 调用) | 误判反馈闭环：👍/👎 按钮构造、反馈落盘（自带 content 快照 = 微调语料）、源级规则状态机（3 次👎→静默跳过 AI，👍 可撤销） |
| `拖走聊天窗口.bat`（调 `pc/services/vision_listener.py park`） | PC | 一键把清单里的窗口挪到屏幕外（重启后手动归位用） |
| `pc/services/notification_listener.py --grant-access` | PC | 通知权限探测/诊断工具（已并入 listener） |
| `pc/core/pclog.py` | PC | 统一日志：单一格式（ISO 带日期 + level + 服务名 + trace_id）、单一出口（`pc/logs` + stderr）、RotatingFile 2MB×3、自动分级 |
| `pc/bootstrap/start_services.py` | PC | 启动编排：杀旧 → 起隧道 → **健康门 `health=200`** → 起其余 → 校验 6×2 进程 |
| `pc/services/watchdog.py` | PC (自启) | 管道自检：health / 进程 / 连接错误 / **磁盘**四项，连续 5 分钟不健康 → 绕过 AI 直推 fp-phone。告警走**三层降级**：本地隧道 → SSH 到 VPS → 本机 toast（零网络依赖，前两条共享同一个外部网络故障域，17:21 实测同时挂过）。进程死了还会在隧道健康时**自动拉起**；每轮写 `logs/watchdog.hb` 心跳文件，由 `notification_listener` 交叉检查（watchdog 没法监控它自己） |
| `pc/bootstrap/restart_services.bat` | PC | 一键重启：调 `start_services.py`，带顺序与健康门 |
| `vps_exec.py` | PC | SSH 执行助手：跑命令 / `--put` 上传（临时文件+原子替换，断线不会截断目标）/ `--get` |
| `fxxkpush_gui.py` + `gui/` | PC | 图形化控制台（pywebview 液态玻璃 UI）：仪表盘 / 配置合并写入 / 启停 / 自启开关 / 窗口归位 / 一键卸载 |
| `GUI.bat` | PC | GUI 入口：带控制台（可看输出 / 跑 `--smoke` 自检），**关闭 GUI 后控制台自动关闭**、仅失败时暂停；要求 `.venv` 已装依赖 |
| `launcher.py` | PC | **单一多路复用入口**：按 `--role` 分发到各模块（frozen 与源码两种形态共用一个入口）。双击无参数 = GUI；`--roles` 列出全部 role。打包后它是唯一 exe 入口 |
| `pc/core/paths.py` | PC | 路径抽象层（全项目唯一真源）：`APP_DIR` 程序目录 / `RES_DIR` 只读资源 / `DATA_DIR` 可写数据三件正交。frozen 下可写数据落 exe 同级 `data/`，建不出来（Program Files 无写权限）降级 `%LOCALAPPDATA%\FxxkPush\data`；非 frozen 下与改造前逐字节等价 |
| `pc/core/procspawn.py` | PC | 进程拉起与识别的**唯一分叉点**：frozen 下服务是 `[exe --role x]`（一个 role 恒 1 个进程），非 frozen 是 `pythonw.exe + .py`（每服务 2 个进程）。start_services 与 watchdog 共用它，否则两边口径不一致会让 watchdog 恒报「进程缺失」并反复补刀 |
| `FxxkPush.spec` | 构建期 | PyInstaller onedir 配置：`console=False`（无黑窗）、应用图标、`gui/` 与 `README.md` 落进只读 `RES/`；因 `runpy.run_module` 动态分发扫不到模块名 + `pc/` 是命名空间包，12 个模块在 hiddenimports 里逐个显式列出，漏一个那个 role 在用户机上就 `ModuleNotFoundError` |
| `build_exe.bat` | 构建期 | 构建脚本：清 `dist\` `build\` → 调 PyInstaller → 校验 exe 存在 → 打印 exe / `_internal` / 合计体积与占比（GBK + CRLF，只删自己的产物目录） |
| `installer.iss` | 构建期 | Inno Setup 7 安装脚本：默认装用户目录（不用管理员）、桌面快捷方式为可选项、装完建 `data\`、卸载先跑 `--role uninstall --yes` 再按选择清 `data\`。自启项不由它管（GUI 开关写），卸载时只确保它们消失 |
| `fp_console_runtime_hook.py` | 构建期 | PyInstaller runtime hook：`console=False`（GUI 子系统）下异常既没 stdout 也没 traceback，用户只看到「双击闪退」；本 hook 在解释器启动最早期挂上 stderr 兜底，让崩溃能留痕。刻意零业务依赖 |
| `resources/fxxkpush.ico` | 构建期 | 应用图标（`.ico` 必须入库，`.gitignore` 对它开了例外），`FxxkPush.spec` 与 `installer.iss` 都读它 |

## 误判反馈闭环怎么干预

推到手机的**AI 判定结果**带 👍/👎 按钮，反馈落到 `pc/feedback.jsonl`
（自带内容快照 = 未来 27B 微调的语料），并按源累计：同一 src 连吃 3 次
👎 → 之后该源静默跳过 AI（省一次模型调用），静音期间点 👍 立刻撤销。
自动规则之外还有 CLI：

```bash
# 列出所有规则（含计数、是否 manual）
.venv\Scripts\python.exe pc\core\fp_feedback.py list

# 手动静音 / 解除。manual 静音不会被👍 自动撤销，只能这样解
.venv\Scripts\python.exe pc\core\fp_feedback.py mute   某应用
.venv\Scripts\python.exe pc\core\fp_feedback.py unmute 某应用   # 会清零误判计数

# 近 24h 反馈统计
.venv\Scripts\python.exe pc\core\fp_feedback.py stats
```

**作用域只覆盖 Windows 通知（`fp-pc`）**，按钮和闸门都是。原因：`fp-vps`
/ `fp-gray` 的 src 恒等于 topic 名本身，规则粒度会退化成「整条通道」——
对它们静音等于把服务器告警掐死（磁盘爆了手机不响），所以这两条通道既不放
按钮，CLI 的 `mute` 对它们也无效。VPS 侧要禁用某类告警去改
`deploy/vps_monitor.py` 的硬规则。

## 卸载（PC 侧）

两条路，做的是同一件事：

- **GUI（推荐）**：控制页「危险区」→「一键卸载…」，可勾选「同时清除
  `fp.config.json` 与运行数据（含密钥，不可恢复）」，在弹窗输入 `uninstall`
  解锁「确认卸载」按钮
- **命令行**：双击 `uninstall.bat`（或 `python uninstall.py`），交互确认输入
  `yes` 后依次：

1. 停止全部 FxxkPush 服务进程（按命令行匹配 `fuckpush/pc`，不动其它 pythonw）
2. 删除 HKCU Run 的 6 个 `FuckPush_*` 自启项（含 StartupApproved 残留）
3. 把挪到屏外的聊天窗口拉回屏内（`vision_listener unpark`）

数据默认**保留**（`fp.config.json`、日志、状态），重装直接再跑 `setup.bat`；
确定不要了加 `--purge` 连配置（含密钥）与运行数据一并删除（不可恢复，需输入
`purge` 二次确认）。`--dry-run` 只打印将执行的动作；`--yes` 免确认供脚本化。
VPS 侧不受影响，如需停用：`systemctl disable --now fuckpush-monitor ntfy`。

## 配置文件（不入库，自建）

| 文件 | 内容 |
|---|---|
| `fp.config.json` | **PC 侧唯一配置**（读取器 `pc/core/fpconfig.py`）：`ai` = OCR 端点与分诊参数（三处分诊共用）、`ntfy.token` = ntfy token、`vps` = SSH 四段凭据、`vision` = 窗口清单（process→class→title 三层匹配）+ 视觉运行参数、`apps` = 按来源声明 ignore/blacklist/must_push/prompt_hint（自定义软件接入，闸门与视觉双通道消费）。支持 // 注释，填表助手 `vision_listener.py list` |

配置一个文件（都不入库）——仓库根的 `fp.config.json`，PC 侧全部配置只此
一份（支持 `//` 注释，读取器 `pc/core/fpconfig.py`）：

```
{
  "ai":     { "provider": "", "base_url": "云端/本地OCR模型端点",
              "api_key": "sk-你的key", "model": "云端/本地OCR模型",
              "max_tokens": 200, "max_completion_tokens": 600,
              "dedup_window_sec": 1800, "daily_report_time": "22:00" },
  "ntfy":   { "token": "tk_xxx" },
  "vps":    { "host": "你的VPS-IP", "port": 22, "user": "root",
              "password": "…" },
  "vision": {
    "settings": { "poll_min": 30, "my_name": "你的昵称",
                  "active_from": 8, "active_to": 24,
                  "dedup_ttl_sec": 21600, "offscreen_offset": 120 },
    "wechat": { "process": "Weixin.exe", "class": "Qt51514",
                "title": null, "min_w": 400, "label": "微信",
                "prompt_hint": "左侧为会话列表，红色气泡为未读" }
  },
  "apps": {
    "_default": { "ignore": false, "prompt_hint": "", "must_push": [],
                  "blacklist": ["微信支付", "公众号", "服务通知"] },
    "QQ": { "must_push": ["取件码"] }
  }
}
```

五段信息各归其位：① `ai` = 三处分诊共用的 OCR 端点与分诊参数 ②
`ntfy.token` = ntfy token ③ `vps` = SSH 四段凭据 ④ `vision` = 窗口清单 +
视觉运行参数 ⑤ `apps` = 按来源声明分诊规则（`ignore` 静默 / `blacklist`
黑名单 / `must_push` 必推词 / `prompt_hint` 提示词补充）—— 新软件接入
只需在 `apps` 加一个条目，不必改代码。（历史版本中的四份旧配置文件已
删除，内容全部并入本文件。）

配置生效语义：`apps` 段改动**即时生效**（mtime 热加载，黑名单/必推词无需重启）；
`vision` / `ai` / `ntfy` 段改动需**重启服务**才生效（服务启动时取快照）——
GUI 保存后的「一键启动（重启生效）」按钮，或命令行
`pc\bootstrap\restart_services.bat`（见附录）。

> 历史版本中的 `vps.secret` / `pc/ntfy.secret` / `pc/triage_config.json` /
> `pc/vision_targets.json` 已删除（内容全部并入 `fp.config.json`）。

## 已知边界

- 微信/企业微信消息推送有最长 30 分钟延迟（视觉通道；QQ 走 toast 秒级）（轮询间隔，用实时性换零打扰，`fp.config.json` vision 块的 `settings.poll_min` 全局可调，目标条目也可单独写 `poll_min`）
- 微信/企业微信窗口不能最小化到托盘（藏屏幕外可以），否则截图为空；重启电脑后窗口会回到屏内，双击「拖走聊天窗口.bat」归位（vision listener 每轮也会自动归位）
- 屏幕缩放非 100% 时，操作窗口坐标的脚本**必须**先声明 DPI-aware，否则 `GetSystemMetrics` 返回虚拟化尺寸（125% 下 2048 而非物理 2560），算出的"屏幕外"位置会落在屏幕里
- `pc_subscriber` 弹的 toast 会被 `notification_listener` 再抓一次（自己吃自己的尾巴），已用 `pc/toast_echo.jsonl` 指纹在 180 秒窗口内过滤
- pywinrt 3.2.1 需要 Python ≤3.12（3.14 报 cannot create instances）
- 语音/图片类通知：图片通知走视觉模型可判，语音只能看到"发来一条语音"而不知内容
- **httpx 默认 `trust_env=True` 会读 Windows 系统代理**（本地代理客户端的 `127.0.0.1:xxxx` 端口）：长寿命客户端在代理关掉后会永久抱死该端口，所有请求 `WinError 10061` 直到进程重启。本项目所有 httpx 客户端一律 `trust_env=False`，且重连时**重建客户端不复用**
- **ntfy 只透传已知字段**：自定义 header（`X-Fp-Trace`）和未知 JSON 字段（`trace_id`/`sequence_id`）都会被服务端静默丢弃（HTTP 200 但订阅端收不到），只有 `tags` 完整回传 —— 所以 trace_id 寄生在 `tags` 里（`t_<8位hex>`）
- **ntfy 会丢掉 action 的 `body_type`**（实测发出什么收不到什么），手机端最终用哪种 `Content-Type` 因此不可知。解法：action 的 `url` 直接带 topic（`…/fp-feedback`），body 放纯 JSON —— 实测 `application/json` 和 `text/plain` 两种发法都 200、`message` 都是原样 JSON
- **反馈消息的 `title` 和 `tags` 实测均为 `None`**：任何靠 title/tags 反查原推送的匹配都会静默失配，所以 `push_id` 只能寄生在 `message` body 里，推送时的快照存 `triage_state.json` 的 `pushes`（7 天 TTL）
- **`ai_triager.consume` 会把 message 里的 JSON 摊平成 dict 自身**（`verdict` 上浮、`message` 键消失），反馈分流必须传**原始 body** 而不是解析后的 `parsed`，否则读 `ev["message"]` 拿到 `None`
- 精简版 Windows 需验证通知中心可用（本项目所用环境已验证）

## Roadmap

- [x] v0.1：QQ 等系统通知 + VPS 告警 + AI 分诊 + 手机推送
- [x] v0.2：微信/企业微信视觉识别，@所有人/@我 必推，夜间静默（00:00-08:00）
- [x] v0.2.1：SSH 隧道抗端口封锁、通知回流修复、服务文件日志与崩溃留痕、DPI 修正、窗口一键归位
- [x] v0.2.2：@提及硬规则下沉到手机闸门（修复被 AI 覆盖导致 @所有人 不推）、同一未读消息 6 小时内只报一次
- [x] v0.3.0：统一日志 `pc/core/pclog.py`（格式/出口/level/trace_id 贯穿全链路）、启动健康门（`health=200` 才起服务）、管道自检看门狗（故障绕过 AI 直推手机，SSH 独立告警通道）、httpx 系统代理毒化修复（`trust_env=False`）
- [x] v0.4.0：误判反馈闭环（`pc/core/fp_feedback.py`）—— 手机端 👍/👎 按钮 → `fp-feedback` → 反馈归档含内容快照 → 源级规则自动纠正（3 次误判自动静默并跳过 AI，👍 可撤销），闭合「推送 → 人肉判定 → 规则纠正 → 少推」这一环
- [x] v0.5.0：P2 留档清仓（告警中文化、告警合并与恢复重试、视觉采集三态与直推、通知权限定时复查、告警三层降级、fp-vps 通道 fail-open、state 形状校验与原子写、夜间分段心跳）+ PC 侧分包重组（`pc/services`·`pc/core`·`pc/bootstrap`，一次性碎片归并，视觉监听更名 `vision_listener.py`）+ 配置示例脱敏
- [x] v0.6.0：单一配置重构（仓库根 fp.config.json + pc/core/fpconfig.py 读取器，11 个消费方迁移、四份旧配置删除）+ vision 配置驱动（process/class/title 三层匹配与 settings 运行参数块）+ 自定义软件接入（apps 声明式分诊规则，闸门与视觉双通道消费）+ 发布脱敏与 README 基于 ntfy 声明
- [x] v0.7.0：一键上手与稳定性加固 —— 注册自启动单文件（按当前目录探测项目根）+ 双侧一键（VPS `deploy/setup_vps.sh`、PC `setup.bat`/`setup.py` 向导与 `--check`）+ 一键卸载（`uninstall.bat`：停服务 / 摘自启 / 窗口归位，数据默认保留，`--purge` 全清）+ 取消 QQ 视觉适配（QQ 归 toast 主通道，消除最小化误报）+ 运行测试与全链路审查共 20 处修复（P0 SSH 兜底鉴权、硬规则入重发队列、配置 mtime 热加载、非 200 留 ERROR 等）
- [x] v0.8.0：图形化界面（GUI）—— pywebview + 液态玻璃控制台：仪表盘（服务/隧道/自启/看门狗/启动日志）、配置表单合并写入 fp.config（密钥留空保持）、一键启动/停止、自启开关、窗口归位、一键卸载（可选清数据）
- [x] v0.8.1：五轮全量代码审查（114 项发现 / 91 项修复，P0 清零）+ 教程面向 GUI 重写 —— R1 修 28 处（`fail`/`os` 未定义 NameError、GDI/SFTP 句柄泄漏、SFTP 心跳可击穿监控、临时文件双写、空 token 假告警）、R2 修 25 处（GUI 整读 4MB 日志、AI 与 vision 导入期配置快照不自愈、同步 HTTP 阻塞事件循环、trace 泄漏、全断流行两条判据都不认）、R3 修 21 处（`_ai()` 静默返回空导致启动即死 R2 回归）、R4 修 10 处（fp-phone 非 200 零日志、固定临时名 + 固定名并发写坏唯一配置源、各服务补 excepthook）、R5 修 7 处（vision 模型字段非列表导致整轮作废、pclog 建不出目录在 excepthook 之前打死六个服务、GUI 从零写入缺三块配置）
- [x] v0.9.0：可执行 exe 分发版 —— **用户机器不再需要装 Python**。路径抽象层 `pc/core/paths.py`（APP_DIR/RES_DIR/DATA_DIR 三维正交，可写数据落 exe 同级 `data/`，Program Files 无写权限时降级 `%LOCALAPPDATA%`）+ 单一多路复用入口 `launcher.py --role`（12 个 role，frozen 与源码共用一个入口，双击 exe 默认开 GUI）+ 进程分叉点 `pc/core/procspawn.py`（exe 模式一个服务 1 个进程，自启项形态变为 `"FxxkPush.exe" --role x`）+ PyInstaller 6 onedir 打包（`FxxkPush.spec` / `build_exe.bat`，`console=False` 无黑窗、应用图标、runtime hook 兜 stderr）+ Inno Setup 7 安装包（`installer.iss`，用户级安装零提权、桌面快捷方式可选、卸载分「保留数据/清数据」两档、只停删自己那份进程与自启项）+ GUI 开窗前探测 WebView2 缺失并给安装指引与命令行降级路径
- [ ] 钉钉 / 学习通等更多 App 深度适配
- [ ] 视觉截图瘦身与留档（未发版，原排在 v0.9.0 位置，被 exe 分发版占号后顺延）—— 企微全尺寸截图（1233×813 ≈ 132KB/张，微信 544×519 ≈ 45KB）识别前降采样，降低上传体积与识别成本；截图落盘并滚动保留 24 小时，清理时机 = 重新开机时 + 每日凌晨
- [ ] 每日日报（AI 汇总当天事件/误判，22:00 推手机；`fp_feedback.stats()` 已备好误判统计）
- [ ] 误判样本 → 27B 模型微调（语料已在 `pc/feedback.jsonl` 自包含落地，缺训练流程）
- [ ] 反馈的「该推没推」信号（当前只有👎 能点，被静默的消息手机上没有按钮）
- [ ] WebSocket 订阅 + PWA 界面
- [ ] ntfy for Android 界面重写与美化：官方客户端功能够用但界面糙（列表、主题、排版都谈不上好看），按本项目的分诊语义自绘客户端 —— 来源分组、优先级着色、误判可就地纠正
- [x] 自定义软件接入：接入层配置化，新软件用声明式配置即可纳入分诊，不必逐个写死适配代码（已实现：`apps` 块声明规则 + vision 窗口条目，闸门与视觉双通道消费）
- [ ] 可操作 UI：推送从「只能看」升级到「能点」—— 通知内直接给动作（已读回执 / 静音该来源 / 稍后提醒 / 重推），动作回传 PC 执行
- [ ] Go 重写（性能瓶颈出现后）
