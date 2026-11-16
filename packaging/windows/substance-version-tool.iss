; Inno Setup script for Substance Version Tool. Compiled by packaging/build-windows.sh:
;   ISCC /DAppVersion=0.2.0 /DSourceDir=<PyInstaller folder> /DOutputDir=dist /DIconFile=<.ico> substance-version-tool.iss
;
; Installs for the current user without admin rights (into %LOCALAPPDATA%\Programs), or for
; all users when started as administrator. Painter stays the program that opens .spp files;
; the optional context-menu entry only adds "Convert with Substance Version Tool".

#ifndef AppVersion
  #error Pass /DAppVersion=X.Y.Z (and SourceDir, OutputDir, IconFile) to ISCC
#endif

#define AppName "Substance Version Tool"
#define AppExe "Substance Version Tool.exe"

[Setup]
; Never change AppId: Windows uses it to recognise upgrades and the uninstaller.
AppId={{D6F5F0B6-BB15-43B8-96C1-CBF64738A00F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Shadax
AppPublisherURL=https://github.com/ShadaxTheWizard/substance-version-tool
AppSupportURL=https://github.com/ShadaxTheWizard/substance-version-tool/issues
; The installer's own version resource. SignPath checks product name and version when signing.
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoCompany=Shadax
VersionInfoDescription={#AppName} Setup
VersionInfoCopyright=Copyright (c) 2026 Shadax. MIT License.
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; commandline: the app's updater passes /CURRENTUSER or /ALLUSERS to match the existing install.
PrivilegesRequiredOverridesAllowed=commandline dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Substance_Version_Tool-{#AppVersion}-windows-x64-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Shown before installing: what the app sends over the network (only the optional update check).
InfoBeforeFile=privacy.txt
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "updates"; Description: "Check GitHub for a new version when the app starts (see the privacy note)"; GroupDescription: "Updates:"
Name: "contextmenu"; Description: "Add ""Convert with Substance Version Tool"" to the right-click menu of .spp files"; GroupDescription: "Explorer:"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An upgrade replaces the bundled libraries wholesale, so no stale ones are left behind.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The update-check choice, where the app reads it (QSettings). Only written if no choice was
; made yet, so a later update does not undo a change made in the app's Help menu.
Root: HKA; Subkey: "Software\substance-version-tool\{#AppName}"; ValueType: string; ValueName: "check_updates"; ValueData: "yes"; Flags: createvalueifdoesntexist; Tasks: updates
Root: HKA; Subkey: "Software\substance-version-tool\{#AppName}"; ValueType: string; ValueName: "check_updates"; ValueData: "no"; Flags: createvalueifdoesntexist; Tasks: not updates
; Uninstalling removes the app's settings as well.
Root: HKA; Subkey: "Software\substance-version-tool"; Flags: uninsdeletekey
; SystemFileAssociations adds a verb without taking over the .spp file type.
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.spp\shell\SubstanceVersionTool"; ValueType: string; ValueName: ""; ValueData: "Convert with Substance Version Tool"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.spp\shell\SubstanceVersionTool"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"",0"; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.spp\shell\SubstanceVersionTool\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; After an update started from the app (updater.py runs this installer with /SILENT /RELAUNCH).
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: CmdLineParamExists('/RELAUNCH')

[Code]
function CmdLineParamExists(const Value: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Value) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;
