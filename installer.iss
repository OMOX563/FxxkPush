; FxxkPush 安装脚本（Inno Setup 7）
; 产物：dist\FxxkPush-Setup-0.8.2.exe（约 25MB，含卸载器）
; 用法：ISCC.exe installer.iss
;
; 关键设计决策（改前先读）：
;  1) **默认装到用户目录**（{localappdata}\Programs\FxxkPush），不是 Program Files。
;     paths.DATA_DIR = exe 同级 data/，Program Files 默认不可写 → 首次运行会
;     降级到 %LOCALAPPDATA%\FxxkPush\data，配置和日志与应用分家两处。
;     用户级安装零提权、data/ 就在程序旁边，用户找得到。
;     允许用户在向导里改路径；若他坚持选 Program Files，首次运行会走降级路径
;     （paths.py 已兜），功能不缺，只是文件分散。
;  2) **卸载分两档**：默认「保留配置与历史」，勾选才清（--purge 语义）。
;     调我们自己的 exe --role uninstall --purge 删 data/ 里的运行数据，
;     Inno 只删程序文件 —— 单点真源，不在 .iss 里复述一遍删除清单。
;  3) 自启**不由 Inno 管**。HKCU Run 那 6 项是 exe --role <name> 形态，
;     由 GUI 的「自启开关」写（procspawn 口径）。Inno 只在卸载时确保它们消失。
;  4) AppId 用固定 GUID：升级时 Inno 认得是同一个产品，不会并排装两份。

#define AppName "FxxkPush"
#define AppVersion "0.8.2"
#define AppExeName "FxxkPush.exe"
#define AppPublisher "OMOX563"
#define AppURL "https://github.com/2986885060/FxxkPush"

[Setup]
AppId={{9E4C1F2A-6B3D-4A17-9C5E-2D8F7B1A4E30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={localappdata}\Programs\FxxkPush
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=dist
OutputBaseFilename=FxxkPush-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 没装 Python 也要能用 —— 本安装包自带解释器，不做任何 Python 检测
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
SetupIconFile=resources\fxxkpush.ico
ChangesEnvironment=no
DisableWelcomePage=no

[Languages]
; 注意：chinese 用的默认 .isl 就是英文。装出中文界面需要把
; Languages\chinese.isl（官方简体中文包）放进本目录后改回
; MessagesFile: "Languages\chinese.isl"。当前刻意不指向一个不存在的文件 ——
; Inno 会在编译期直接报错，比装完发现是英文好。
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
; dist\FxxkPush\{#AppExeName} + _internal\ 全量（构建产物，勿手改）
; 本 iss 位于**仓库根**，构建产物在同级 dist\（不是 ..\dist —— 那是上一级）
Source: "dist\FxxkPush\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; 停服务 + 摘自启（我们自己的 uninstall 负责这些，Inno 只调用）
Filename: "{app}\{#AppExeName}"; Parameters: "--role uninstall --yes"; \
    Flags: runhidden waituntilterminated; RunOnceId: "StopFxxkPush"

[Code]
var
  PurgeData: Boolean;

{ 静默模式检测：Inno 的 /VERYSILENT /SUPPRESSMSGBOXES 下 MsgBox 无人应答，
  会一直挂着等输入 —— 实测把卸载卡死 300 秒被外层强杀（程序文件没删、注册表
  自启项却已被摘，是最糟的中间态）。故静默时不问，一律按「保留数据」处理：
  静默卸载本就是脚本/无人值守场景，宁可留数据也不该卡死。交互安装时仍会问。 }
function IsSilent: Boolean;
begin
  Result := UninstallSilent;
end;

{ 卸载时是否顺带清掉配置与历史数据（data/ 目录）。默认否 —— 配置里存着
  用户的 API key 与 VPS 凭据，重装还要用。 }
function AskPurge: Boolean;
var
  Resp: Integer;
begin
  if IsSilent() then
  begin
    PurgeData := False;
    Result := False;      { 继续卸载，保留 data }
    Exit;
  end;
  Resp := MsgBox('是否同时删除配置与历史数据？' + #13#10 + #13#10 +
    '选择「否」：保留配置（下次启动继续用，含 API key 与服务器凭据）。' + #13#10 +
    '选择「是」：删除 data/ 下全部配置、日志与消息历史，此操作不可撤销。',
    mbConfirmation, MB_YESNOCANCEL);
  if Resp = IDCANCEL then
  begin
    Result := True;   { 取消卸载 }
    Exit;
  end;
  PurgeData := (Resp = IDYES);
  Result := False;
end;

function InitializeUninstall: Boolean;
begin
  Result := not AskPurge;
end;

{ 清 data/ —— 自有 uninstall 的 --purge 已删配置/日志/归档，Inno 只兜整个目录，
  避免升级后残留半写的 .tmp / 空目录。 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and PurgeData then
    DelTree(ExpandConstant('{app}\data'), True, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { 装完若 data 目录不存在则建一个，让用户一眼看出「运行数据在这里」 }
  if CurStep = ssPostInstall then
    CreateDir(ExpandConstant('{app}\data'));
end;