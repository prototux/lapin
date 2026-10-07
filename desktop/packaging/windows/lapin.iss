; Inno Setup script of the Windows installer, run by packaging/build.py:
;   iscc /DAppVersion=1.2.3 /DSourceDir=...\Lapin /DIconFile=...\lapin.ico
;        /DOutputDir=... /DOutputName=... lapin.iss
; Installs for the current user only (no admin rights) in %LOCALAPPDATA%\Programs\Lapin.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E0C7B53-3F4B-4C2B-9C61-7A1F5D3A2B10}
AppName=Lapin
AppVersion={#AppVersion}
AppVerName=Lapin {#AppVersion}
AppPublisher=Lapin
AppPublisherURL=https://github.com/prototux/lapin
DefaultDirName={localappdata}\Programs\Lapin
DefaultGroupName=Lapin
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Lapin.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Lapin"; Filename: "{app}\Lapin.exe"
Name: "{group}\{cm:UninstallProgram,Lapin}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Lapin"; Filename: "{app}\Lapin.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Lapin.exe"; Description: "{cm:LaunchProgram,Lapin}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\Lapin.exe"; Parameters: "--quit"; Flags: runhidden skipifdoesntexist; RunOnceId: "QuitLapin"

[Registry]
; removes the "Start at login" entry the app may have created
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Lapin"; Flags: uninsdeletevalue dontcreatekey
