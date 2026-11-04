; The installer identity and shell AppUserModelID must stay stable across versions.
[Setup]
AppId={{E1509736-CE1A-42CA-AD11-F9F885ECD5B4}
AppName=Calendar
AppVersion={#AppVersion}
AppPublisher=nottbradd
AppPublisherURL=https://nottbradd.github.io/Calendar/
AppSupportURL=https://github.com/nottbradd/Calendar/issues
AppUpdatesURL=https://github.com/nottbradd/Calendar/releases/latest
DefaultDirName={localappdata}\Programs\Calendar
DefaultGroupName=Calendar
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
SetupMutex=Local\Calender-Setup-E1509736
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=Calendar
UninstallDisplayIcon={app}\Calendar.exe
SetupIconFile=..\build\Calendar.ico
OutputDir={#ReleaseDir}
OutputBaseFilename=Calendar-{#AppVersion}-Setup
VersionInfoVersion={#AppVersion}.0
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
DisableWelcomePage=no

[Tasks]
Name: desktopicon; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

; Match both the registered installation and its original files. A standalone
; copy or similarly named shortcut in an unrelated location belongs to its user.
[InstallDelete]
Type: files; Name: "{autoprograms}\Calender.lnk"; Check: IsOldShortcut('{autoprograms}\Calender.lnk')
Type: files; Name: "{autodesktop}\Calender.lnk"; Check: IsOldShortcut('{autodesktop}\Calender.lnk')
Type: files; Name: "{app}\Calender.exe"; Check: IsLegacyUpgrade

[Icons]
Name: "{autoprograms}\Calendar"; Filename: "{app}\Calendar.exe"; WorkingDir: "{app}"; AppUserModelID: "Calender.Desktop"
Name: "{autodesktop}\Calendar"; Filename: "{app}\Calendar.exe"; WorkingDir: "{app}"; AppUserModelID: "Calender.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\Calendar.exe"; Description: "Open Calendar"; Flags: nowait postinstall skipifsilent

[Code]
function IsLegacyUpgrade: Boolean;
var
  PreviousDir, PreviousName: String;
  Key: String;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E1509736-CE1A-42CA-AD11-F9F885ECD5B4}_is1';
  Result := RegQueryStringValue(HKCU, Key, 'DisplayName', PreviousName) and
    (PreviousName = 'Calender') and RegQueryStringValue(HKCU, Key, 'InstallLocation', PreviousDir) and
    (CompareText(AddBackslash(PreviousDir), AddBackslash(ExpandConstant('{app}'))) = 0);
end;

function IsOldShortcut(const Link: String): Boolean;
var
  Shell, Shortcut: Variant;
begin
  Result := False;
  if IsLegacyUpgrade and FileExists(ExpandConstant(Link)) then
    try
      Shell := CreateOleObject('WScript.Shell');
      Shortcut := Shell.CreateShortcut(ExpandConstant(Link));
      Result := CompareText(Shortcut.TargetPath, ExpandConstant('{app}\Calender.exe')) = 0;
    except
      Log('Could not verify a legacy shortcut target; leaving it in place.');
    end;
end;
