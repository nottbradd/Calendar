; The installer identity and shell AppUserModelID must stay stable across versions.
[Setup]
AppId={{E1509736-CE1A-42CA-AD11-F9F885ECD5B4}
AppName=Calender
AppVersion={#AppVersion}
AppPublisher=nottbradd
AppPublisherURL=https://nottbradd.github.io/Calender/
AppSupportURL=https://github.com/nottbradd/Calender/issues
AppUpdatesURL=https://github.com/nottbradd/Calender/releases/latest
DefaultDirName={localappdata}\Programs\Calender
DefaultGroupName=Calender
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
SetupMutex=Local\Calender-Setup-E1509736
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=Calender
UninstallDisplayIcon={app}\Calender.exe
SetupIconFile=..\build\Calender.ico
OutputDir={#ReleaseDir}
OutputBaseFilename=Calender-{#AppVersion}-Setup
VersionInfoVersion={#AppVersion}.0
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
DisableWelcomePage=no

[Tasks]
Name: desktopicon; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\Calender"; Filename: "{app}\Calender.exe"; WorkingDir: "{app}"; AppUserModelID: "Calender.Desktop"
Name: "{autodesktop}\Calender"; Filename: "{app}\Calender.exe"; WorkingDir: "{app}"; AppUserModelID: "Calender.Desktop"; Tasks: desktopicon

[Run]
Filename: "{app}\Calender.exe"; Description: "Open Calender"; Flags: nowait postinstall skipifsilent
