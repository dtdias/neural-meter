; Inno Setup 6 - instalador per-user do NeuralMeter.
; Compilar: scripts/build_installer.py (passa /DAppVersion=<__version__>).
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B1F4D52-3C8E-4A7B-9E21-5D0A7C93F1B4}
AppName=NeuralMeter
AppVersion={#AppVersion}
AppPublisher=NeuralMeter
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\NeuralMeter
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=NeuralMeter-Setup-{#AppVersion}
SetupIconFile=..\src\neuralmeter\assets\neuralmeter.ico
UninstallDisplayIcon={app}\NeuralMeter.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; Flags: unchecked
Name: "autostart"; Description: "Iniciar com o Windows"; Flags: unchecked

[Files]
Source: "..\dist\NeuralMeter.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\NeuralMeter"; Filename: "{app}\NeuralMeter.exe"
Name: "{autodesktop}\NeuralMeter"; Filename: "{app}\NeuralMeter.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NeuralMeter"; ValueData: """{app}\NeuralMeter.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\NeuralMeter.exe"; Description: "Iniciar NeuralMeter"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM NeuralMeter.exe /F"; Flags: runhidden; RunOnceId: "KillNeuralMeter"

; Dados do usuario em %LOCALAPPDATA%\NeuralMeter sao mantidos na desinstalacao.
