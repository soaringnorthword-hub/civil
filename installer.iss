; 挡墙台账登记助手 安装包脚本
#define MyAppName "挡墙台账登记助手"
#define MyAppVersion "1.1.1"
#define MyAppExeName "TzLedger.exe"

[Setup]
AppId={{8E3C2A71-4C5B-4E9A-9F1D-2B7A6C0D5E31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=一工区
DefaultDirName={autopf}\TzLedger
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=挡墙台账登记助手_v{#MyAppVersion}_安装包
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableDirPage=no
ShowLanguageDialog=no

[Languages]
Name: "cn"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupAppTitle=安装程序
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载程序
UninstallAppFullTitle=卸载 %1
SetupLdrStartupMessage=即将安装 %1。是否继续？
WelcomeLabel1=欢迎使用 %1 安装向导
WelcomeLabel2=本程序把报检照片里的内容自动识别后登记到挡墙施工台账。%n%n安装过程不会修改你的任何台账文件。%n%n点击“下一步”继续。
WizardNext=下一步(&N) >
WizardBack=< 上一步(&B)
WizardCancel=取消
WizardClose=关闭
WizardFinish=完成
WizardYes=是(&Y)
WizardNo=否(&N)
SelectDirLabel3=安装程序将把 %1 安装到下面的文件夹。
SelectDirBrowseLabel=要继续请点击“下一步”；如需更换文件夹，请点击“浏览”。
DiskSpaceMBLabel=至少需要 %1 MB 可用磁盘空间。
SelectTasksLabel2=请选择安装程序要执行的附加任务，然后点击“下一步”：
SelectStartMenuFolderCaption=开始菜单文件夹
ReadyLabel1=安装程序已准备好在你的计算机上安装 %1。
ReadyLabel2a=点击“安装”开始安装；如需检查或更改设置，请点击“上一步”。
ReadyLabel2b=点击“安装”开始安装。
PreparingDesc=安装程序正在准备安装 %1。
InstallingLabel=正在安装 %1，请稍候…
FinishedHeadingLabel=正在完成 %1 安装
FinishedLabelNoIcons=%1 已安装完成。
FinishedLabel=%1 已安装完成。%n%n可以直接双击桌面上的“挡墙台账登记助手”图标启动。
ClickFinish=点击“完成”退出安装程序。
ButtonInstall=安装(&I)
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonCancel=取消
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&B)...
ButtonYes=是(&Y)
ButtonNo=否(&N)
ConfirmUninstall=确定要完全删除 %1 及其所有组件吗？
UninstalledAll=%1 已成功卸载。
UninstalledMost=%1 卸载完成。%n%n有些文件无法删除，需要手动删除。
ExitSetupMessage=安装尚未完成。如果现在退出，程序不会被安装。%n%n确定要退出安装程序吗？

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "quicklaunchicon"; Description: "创建快速启动栏图标"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "dist\TzLedger\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\使用说明.txt"; Description: "查看使用说明"; Flags: shellexec nowait postinstall skipifsilent unchecked
