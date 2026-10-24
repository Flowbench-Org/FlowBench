; FlowBench Installer Script (Inno Setup 6.7 or later)
; 中英双语安装程序
#define MyAppName "FlowBench"
#define MyAppVersion "1.2.6"
#define MyAppPublisher "FlowBench"
#define MyAppExeName "FlowBench.exe"
#define MyAppDirName "FlowBench"
#define MyAppAUMID "FlowBench.App"

[Setup]
AppId={{652A3085-2CA0-4898-B675-C69E34D5AE6D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer
OutputBaseFilename=FlowBench-Setup-{#MyAppVersion}
SetupIconFile=app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[CustomMessages]
english.RunApp=Run FlowBench
chinesesimplified.RunApp=运行 FlowBench
english.DesktopIcon=Create a desktop shortcut
chinesesimplified.DesktopIcon=创建桌面快捷方式(&D)
english.AdditionalTasks=Additional tasks:
chinesesimplified.AdditionalTasks=附加任务：
english.ProgramComment=FlowBench Network Stress Testing Tool
chinesesimplified.ProgramComment=FlowBench 网络压力测试工具

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalTasks}"

[Files]
Source: "dist\{#MyAppDirName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs


[Code]
const
  OldAppId = '{8F4A2C1E-9B3D-4E6A-B5C7-1A2B3C4D5E6F}';

function GetOldUninstallString(): String;
var
  UnInstPath: String;
  UnInstallString: String;
begin
  UnInstPath := 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\' + OldAppId + '_is1';
  Result := '';
  if not RegQueryStringValue(HKLM, UnInstPath, 'UninstallString', UnInstallString) then
    RegQueryStringValue(HKCU, UnInstPath, 'UninstallString', UnInstallString);
  Result := UnInstallString;
end;

function InitializeSetup(): Boolean;
var
  UninstallString: String;
  ResultCode: Integer;
  PromptText: String;
begin
  Result := True;
  UninstallString := GetOldUninstallString();
  if UninstallString <> '' then
  begin
    if ActiveLanguage = 'chinesesimplified' then
      PromptText := '检测到旧版本（NetPulse / 旧版 FlowBench）。安装程序将先卸载旧版本（包括旧目录中的 NetPulse.exe），再把 FlowBench 安装到新目录。是否继续？'
    else
      PromptText := 'Detected a previous installation (NetPulse / old FlowBench). It will be uninstalled first, then FlowBench will be installed to a new folder. Continue?';
    if MsgBox(PromptText, mbConfirmation, MB_YESNO) = IDYES then
    begin
      UninstallString := RemoveQuotes(UninstallString);
      if Exec(UninstallString, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      begin
        if ResultCode <> 0 then
        begin
          if ActiveLanguage = 'chinesesimplified' then
            MsgBox('旧版本卸载失败，安装将中止。', mbError, MB_OK)
          else
            MsgBox('Failed to uninstall the old version. Setup will exit.', mbError, MB_OK);
          Result := False;
        end;
      end
      else
      begin
        if ActiveLanguage = 'chinesesimplified' then
          MsgBox('无法运行旧版本卸载程序，安装将中止。', mbError, MB_OK)
        else
          MsgBox('Could not run the old uninstaller. Setup will exit.', mbError, MB_OK);
        Result := False;
      end;
    end
    else
      Result := False;
  end;
end;

[Icons]
Name: "{autoprograms}\FlowBench"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "{cm:ProgramComment}"; AppUserModelID: "{#MyAppAUMID}"
Name: "{autodesktop}\FlowBench"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "{cm:ProgramComment}"; Tasks: desktopicon; AppUserModelID: "{#MyAppAUMID}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:RunApp}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
