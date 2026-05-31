"""
Productive-OS — Local Development Builder
==========================================
Builds a DEV-only standalone installer for local testing.

    python build.py

Pipeline:
  1. Validates environment (PyInstaller, pywebview, Inno Setup)
  2. Runs PyInstaller → dist/Productive-OS-dev/  (FOLDER mode, no --onefile)
  3. Generates dev_setup.iss dynamically
  4. Compiles via ISCC.exe → installer/Productive-OS-Dev-Setup.exe

Rules:
  - Source: LOCAL FILES ONLY. No git pull, no remote fetching.
  - Install path: {autopf}\\Atharvotech\\Productive-OS-Dev
  - Startup: Windows Scheduled Task at /rl HIGHEST /sc onlogon
    (NOT a Registry Run key — which triggers UAC prompts for elevated apps)
  - Runs taskkill on Productive-OS-dev.exe before extraction
    (safe reinstall during iterative testing, NOT a full uninstall)
  - Does NOT overwrite a production install.
"""

import os
import sys
import subprocess
import textwrap

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_POINT   = os.path.join(BASE_DIR, "main.py")
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")
EXTENSION_DIR = os.path.join(BASE_DIR, "extension")
CORE_DIR      = os.path.join(BASE_DIR, "core")
DIST_DIR      = os.path.join(BASE_DIR, "dist")
BUILD_DIR     = os.path.join(BASE_DIR, "build")
INSTALLER_DIR = os.path.join(BASE_DIR, "installer")

APP_NAME      = "Productive-OS (Dev Build)"
EXE_NAME      = "Productive-OS-dev"          # dist/Productive-OS-dev/ folder
ISS_FILE      = os.path.join(BUILD_DIR, "dev_setup.iss")
OUTPUT_NAME   = "Productive-OS-Dev-Setup"    # final installer filename

# Common Inno Setup search locations
ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    r"C:\Program Files\Inno Setup 5\ISCC.exe",
]

SEP = ";" if sys.platform == "win32" else ":"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _banner(text: str):
    bar = "=" * 62
    print(f"\n{bar}\n  {text}\n{bar}")

def _step(text: str):
    print(f"\n[*] {text}")

def _ok(text: str):
    print(f"  ✅  {text}")

def _fail(text: str):
    print(f"  ❌  {text}")
    sys.exit(1)


def _find_iscc() -> str:
    """Return the path to ISCC.exe, or exit with a helpful message."""
    for path in ISCC_CANDIDATES:
        if os.path.isfile(path):
            return path
    _fail(
        "Inno Setup (ISCC.exe) not found.\n"
        "  Download from: https://jrsoftware.org/isdownload.php\n"
        "  Then re-run this script."
    )


def _check_deps():
    """Ensure PyInstaller and pywebview are importable."""
    _step("Checking dependencies")
    try:
        import PyInstaller  # noqa: F401
        _ok("PyInstaller found")
    except ImportError:
        _fail("PyInstaller not found. Run: pip install pyinstaller")

    try:
        import webview  # noqa: F401
        _ok("pywebview found")
    except ImportError:
        _fail("pywebview not found. Run: pip install pywebview")


# ─── Stage 1: PyInstaller ─────────────────────────────────────────────────────

def _run_pyinstaller():
    _step("Running PyInstaller (local source only, folder mode)")

    # Kill any running instance to avoid PermissionError during PyInstaller COLLECT phase
    result = subprocess.run(["taskkill", "/F", "/IM", f"{EXE_NAME}.exe"], capture_output=True, text=True)
    if result.returncode != 0 and ("Access is denied" in result.stderr or "Access is denied" in result.stdout):
        print(f"\n[!] ERROR: '{EXE_NAME}.exe' is running as Administrator!")
        print(f"[!] Your terminal does not have permission to stop it.")
        print(f"[!] Please open Task Manager, end the process, and try again.\n")
        sys.exit(1)
    
    import time
    time.sleep(1)

    os.makedirs(DIST_DIR, exist_ok=True)
    os.makedirs(INSTALLER_DIR, exist_ok=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        ENTRY_POINT,
        f"--name={EXE_NAME}",
        # "--onefile",  # REMOVED: --onefile causes 5-10 second extraction delays on startup!
        "--noconsole",
        "--clean",
        "--noconfirm",

        # Embed runtime assets
        f"--add-data={DASHBOARD_DIR}{SEP}dashboard",
        f"--add-data={EXTENSION_DIR}{SEP}extension",
        f"--add-data={CORE_DIR}{SEP}core",
        f"--add-data={os.path.join(BASE_DIR, 'ui.py')}{SEP}.",

        # Hidden imports
        "--hidden-import=websockets",
        "--hidden-import=bcrypt",
        "--hidden-import=psutil",
        "--hidden-import=webview",
        "--hidden-import=winreg",
        "--hidden-import=wmi",
        "--hidden-import=win32com",
        "--hidden-import=win32com.client",
        "--hidden-import=win32timezone",
        "--hidden-import=pythoncom",

        # Output locations
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
        f"--specpath={BUILD_DIR}",
    ]

    result = subprocess.run(args, cwd=BASE_DIR)
    if result.returncode != 0:
        _fail("PyInstaller failed. Review the output above.")

    # Without --onefile, PyInstaller creates a folder named EXE_NAME inside DIST_DIR
    exe_dir  = os.path.join(DIST_DIR, EXE_NAME)
    exe_path = os.path.join(exe_dir, f"{EXE_NAME}.exe")

    if not os.path.isfile(exe_path):
        _fail(f"Expected output not found: {exe_path}")

    _ok(f"Executable: {exe_path}")
    return exe_dir, exe_path


# ─── Stage 2: Generate dev_setup.iss ─────────────────────────────────────────

def _generate_iss(exe_dir: str, exe_path: str):
    _step("Generating dev_setup.iss")

    # Read version from main.py comment, or default
    version = "3.6.0"

    iss = textwrap.dedent(f"""\
        ; Productive-OS Dev Build — Inno Setup Script
        ; Auto-generated by build.py — DO NOT EDIT MANUALLY

        #define MyAppName      "Productive-OS (Dev Build)"
        #define MyAppVersion   "{version}"
        #define MyAppPublisher "Atharvotech"
        #define MyAppURL       "https://github.com/atharvotech/Productive-OS"
        #define MyAppExeName   "Productive-OS-dev.exe"
        #define MyAppId        "b0e8a225-b4e7-4303-ad11-79cd3476a9ec"
        #define TaskName       "ProductiveOS_Dev_AutoStart"

        [Setup]
        AppId={{{{b0e8a225-b4e7-4303-ad11-79cd3476a9ec}}}}
        AppName={{#MyAppName}}
        AppVersion={{#MyAppVersion}}
        AppPublisher={{#MyAppPublisher}}
        AppPublisherURL={{#MyAppURL}}
        AppSupportURL={{#MyAppURL}}/issues
        AppUpdatesURL={{#MyAppURL}}/releases
        DefaultDirName={{autopf}}\\Atharvotech\\Productive-OS-Dev
        DefaultGroupName=Atharvotech\\Productive-OS Dev
        OutputDir={INSTALLER_DIR}
        OutputBaseFilename={OUTPUT_NAME}
        Compression=lzma2/ultra64
        SolidCompression=yes
        WizardStyle=modern
        PrivilegesRequired=admin
        ArchitecturesInstallIn64BitMode=x64compatible
        UninstallDisplayName={{#MyAppName}}
        UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
        SetupIconFile=
        ; ── License ──────────────────────────────────────────────────────
        LicenseFile={os.path.join(BASE_DIR, 'docs', 'EULA.txt')}
        DisableProgramGroupPage=no
        AllowCancelDuringInstall=yes
        ShowLanguageDialog=no

        [Languages]
        Name: "english"; MessagesFile: "compiler:Default.isl"

        [Tasks]
        Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

        ; ── Files ────────────────────────────────────────────────────────────
        ; Folder-based distribution (no --onefile): copy entire output directory.
        [Files]
        Source: "{exe_dir}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

        [Icons]
        Name: "{{autoprograms}}\\Atharvotech\\Productive-OS Dev"; Filename: "{{app}}\\{{#MyAppExeName}}"
        Name: "{{autodesktop}}\\Productive-OS Dev"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

        [Run]
        Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "Launch Productive-OS (Dev)"; Flags: nowait postinstall skipifsilent shellexec

        [UninstallDelete]
        Type: filesandordirs; Name: "{{app}}"

        [Code]
        var
          ResultCode: Integer;

        // ── Auto-start via Scheduled Task (replaces old Registry Run key) ──────
        //
        // Registry Run keys silently block or prompt UAC for elevated apps.
        // schtasks /rl HIGHEST /sc onlogon bypasses UAC and starts the engine
        // silently in the background (--background) on every Windows logon.
        procedure CreateAutoStartTask();
        var
          ExePath, TaskArgs, CmdLine: String;
        begin
          ExePath  := ExpandConstant('{{app}}\\{{#MyAppExeName}}');
          TaskArgs := '--background';

          CmdLine := '/C schtasks /Create'
                   + ' /TN "' + ExpandConstant('{{#TaskName}}') + '"'
                   + ' /TR "\"' + ExePath + '\" ' + TaskArgs + '"'
                   + ' /SC ONLOGON'
                   + ' /RL HIGHEST'
                   + ' /F'
                   + ' >nul 2>&1';

          Exec('cmd.exe', CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

          if ResultCode = 0 then
            Log('Dev auto-start scheduled task created.')
          else
            Log('WARNING: Failed to create dev auto-start task (exit code ' + IntToStr(ResultCode) + ').');
        end;

        procedure RemoveAutoStartTask();
        var
          CmdLine: String;
        begin
          CmdLine := '/C schtasks /Delete'
                   + ' /TN "' + ExpandConstant('{{#TaskName}}') + '"'
                   + ' /F'
                   + ' >nul 2>&1';
          Exec('cmd.exe', CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
          Log('Dev auto-start scheduled task removed.');
        end;

        procedure CurStepChanged(CurStep: TSetupStep);
        begin
          // Kill any running dev instance before extracting files.
          // This prevents "file in use" errors during iterative reinstalls.
          if CurStep = ssInstall then
          begin
            Exec('cmd.exe',
              '/C taskkill /F /IM {EXE_NAME}.exe >nul 2>&1',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
          end;

          if CurStep = ssPostInstall then
          begin
            CreateAutoStartTask();
            // Start the background engine immediately so we don't have to wait for a reboot
            Exec('cmd.exe', '/C schtasks /Run /TN "' + ExpandConstant('{{#TaskName}}') + '" >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
          end;
        end;

        procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
        begin
          // Kill process before uninstaller tries to delete the exe
          if CurUninstallStep = usUninstall then
          begin
            Exec('cmd.exe',
              '/C taskkill /F /IM {EXE_NAME}.exe >nul 2>&1',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
            RemoveAutoStartTask();
          end;
        end;
    """)

    with open(ISS_FILE, "w", encoding="utf-8") as f:
        f.write(iss)

    _ok(f"ISS script written: {ISS_FILE}")
    return ISS_FILE


# ─── Stage 3: Compile with ISCC ──────────────────────────────────────────────

def _compile_iss(iss_path: str, iscc: str):
    _step(f"Compiling installer with ISCC: {os.path.basename(iscc)}")

    result = subprocess.run([iscc, iss_path], cwd=BASE_DIR)
    if result.returncode != 0:
        _fail("ISCC compilation failed. Review the output above.")

    out = os.path.join(INSTALLER_DIR, f"{OUTPUT_NAME}.exe")
    if not os.path.isfile(out):
        _fail(f"Expected installer not found: {out}")

    _ok(f"Installer: {out}")
    return out


# ─── Entry ────────────────────────────────────────────────────────────────────

def build():
    _banner("Productive-OS — DEV BUILD PIPELINE")
    print("  Mode   : Local source only (no git pull)")
    print(f"  Output : installer/{OUTPUT_NAME}.exe")

    _check_deps()
    iscc = _find_iscc()

    exe_dir, exe_path = _run_pyinstaller()
    iss_path = _generate_iss(exe_dir, exe_path)
    installer = _compile_iss(iss_path, iscc)

    _banner("DEV BUILD COMPLETE")
    print(f"  Installer : {installer}")
    print()
    print("  To install (run as admin):")
    print(f"    {installer}")
    print()
    print("  The installer will:")
    print("    • Kill any running Productive-OS-dev.exe (safe reinstall)")
    print(f"    • Install to: %ProgramFiles%\\Atharvotech\\Productive-OS-Dev")
    print("    • Create Start Menu entry: Atharvotech > Productive-OS Dev")
    print("    • Register 'ProductiveOS_Dev_AutoStart' scheduled task")
    print("      (starts engine silently at logon, no UAC prompt)")
    print()


if __name__ == "__main__":
    build()
