; Thock installer: per-user install (no admin). It asks before closing a running Thock and offers to start Thock at the end; starting with Windows is a choice in Thock's settings.
; Built by .github/workflows/windows-installer.yml from the PyInstaller folder dist\Thock.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C0B7F2E-3D41-4B8A-9E57-2F1D8A6C4E93}
AppName=Thock
AppVerName=Thock {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=AI Shift
DefaultDirName={localappdata}\Programs\Thock
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Thock-setup-x64
Compression=lzma2
SolidCompression=yes
; A running Thock is closed through Windows Restart Manager: the wizard lists it and asks first; a silent update
; (thock/update.py) closes it without asking, and the [Run] entry starts the new one.
CloseApplications=force
UninstallDisplayIcon={app}\Thock.exe

[Files]
Source: "..\dist\Thock\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[InstallDelete]
; An update replaces the whole program folder so no old library stays behind.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\Thock"; Filename: "{app}\Thock.exe"

[Run]
Filename: "{app}\Thock.exe"; Description: "Thock 시작"; Flags: nowait postinstall

[Code]
// Removing Thock while it runs would leave its files behind: ask the user to quit it first.
function InitializeUninstall(): Boolean;
begin
  Result := True;
  while Result and CheckForMutexes('Local\VoiceTypeSingleton') do
    Result := SuppressibleMsgBox('Thock이 실행 중입니다. 작업 표시줄 위 Thock 막대를 오른쪽 클릭해 "Thock 종료"를 누른 뒤 확인을 눌러 주세요.',
      mbInformation, MB_OKCANCEL, IDCANCEL) = IDOK;
end;

// Thock's own "start with Windows" entry (settings) goes with it.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Thock');
end;
