; Tavinor-Setup.exe, built by packaging/build.py (which passes AppVersion,
; AppDir, OutDir and Root). Installs for the current user only: no admin
; prompt, and Tavinor's data stays in %APPDATA%\Tavinor.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B2C4E-8A3D-4E7B-9C2F-7A51D3E9B640}
AppName=Tavinor
AppVersion={#AppVersion}
AppPublisher=PandaOnDiet
AppPublisherURL=https://github.com/PandaOnDiet/Tavinor
DefaultDirName={localappdata}\Programs\Tavinor
DefaultGroupName=Tavinor
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=Tavinor-Setup-{#AppVersion}
SetupIconFile={#Root}\tavinor.ico
UninstallDisplayIcon={app}\Tavinor.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
CloseApplications=force

[Tasks]
Name: "startup"; Description: "Start Tavinor when I sign in to Windows"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Desktop shortcut"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Tavinor"; Filename: "{app}\Tavinor.exe"
Name: "{group}\Uninstall Tavinor"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Tavinor"; Filename: "{app}\Tavinor.exe"; Tasks: desktopicon

[Run]
; Tavinor's own flag writes the same Startup entry its Settings switch uses.
Filename: "{app}\Tavinor.exe"; Parameters: "--install-startup"; Flags: runhidden waituntilterminated; Tasks: startup
Filename: "{app}\Tavinor.exe"; Description: "Start Tavinor now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\Tavinor.exe"; Parameters: "--remove-startup"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveStartup"
