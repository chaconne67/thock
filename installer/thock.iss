; Thock installer: per-user install (no admin), starts with Windows, starts right after install.
; Built by .github/workflows/windows-installer.yml from the PyInstaller folder dist\Thock.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C0B7F2E-3D41-4B8A-9E57-2F1D8A6C4E93}
AppName=Thock
AppVerName=Thock {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=chaconne67
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
UninstallDisplayIcon={app}\Thock.exe

[Files]
Source: "..\dist\Thock\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[InstallDelete]
; An update replaces the whole program folder so no old library stays behind.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\Thock"; Filename: "{app}\Thock.exe"
Name: "{userstartup}\Thock"; Filename: "{app}\Thock.exe"

[Run]
Filename: "{app}\Thock.exe"; Description: "Thock 시작"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM Thock.exe"; Flags: runhidden; RunOnceId: "StopThock"

[Code]
// A running Thock holds its files and the single-instance lock, so stop it before copying.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM Thock.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
