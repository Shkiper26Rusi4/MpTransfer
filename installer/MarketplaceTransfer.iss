[Code]
// User data lives outside {app}; no uninstall action ever removes that folder.

#ifndef AppVersion
  #define AppVersion "0.4.0"
#endif
[Setup]
#ifdef TestBuild
AppId={{A8768EE5-DC4F-46A4-A9EC-95AF4DA56646}
AppName=Marketplace Transfer (isolated test)
DefaultDirName={localappdata}\Programs\MarketplaceTransferTest
OutputDir=..\work\install-tests
OutputBaseFilename=MarketplaceTransfer_TestSetup
#else
AppId={{A8768EE5-DC4F-46A4-A9EC-95AF4DA56645}
AppName=Marketplace Transfer
DefaultDirName={localappdata}\Programs\MarketplaceTransferNew
OutputDir=..\outputs
OutputBaseFilename=MarketplaceTransfer_Setup
#endif
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
UninstallDisplayIcon={app}\MarketplaceTransfer.exe
MinVersion=10.0
CloseApplications=no
RestartApplications=no
AppMutex=Local\MarketplaceTransfer.Application
DefaultGroupName=Marketplace Transfer
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\assets\app.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Files]
Source: "..\outputs\MarketplaceTransfer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
#ifndef TestBuild
Name: "{userdesktop}\Marketplace Transfer"; Filename: "{app}\MarketplaceTransfer.exe"
Name: "{group}\Marketplace Transfer"; Filename: "{app}\MarketplaceTransfer.exe"
Name: "{group}\Восстановление Marketplace Transfer"; Filename: "{app}\MarketplaceTransferUpdater.exe"; Parameters: "--recover-latest"
[Registry]
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.marketplace.transfer"; ValueType: string; ValueData: "{localappdata}\MarketplaceTransferNew\com.marketplace.transfer.json"; Flags: uninsdeletekey
#endif
[Run]
Filename: "{app}\MarketplaceTransfer.exe"; Description: "Запустить Marketplace Transfer"; Flags: nowait postinstall skipifsilent
