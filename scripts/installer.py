"""
Productive-OS — Production GitHub Builder
==========================================
Fetches the latest release from GitHub and builds a production installer.

    python installer.py

Pipeline:
  1. Validates environment (git, PyInstaller, Inno Setup)
  2. git pull → clones/updates https://github.com/atharvotech/Productive-OS
     into a clean _build_src/ staging directory
  3. Runs PyInstaller (folder-based, NO --onefile) → dist/Productive-OS/
  4. Generates prod_setup.iss dynamically
  5. Compiles via ISCC.exe → installer/Productive-OS-Setup.exe

Rules:
  - Source: ALWAYS pulled from GitHub (latest commit on main branch)
  - Install path: {autopf}\\Atharvotech\\Productive-OS
  - Startup: Windows Scheduled Task (schtasks /rl HIGHEST /sc onlogon)
    — NOT a Registry Run key (which blocks or prompts UAC on every boot)
  - Auto-update logic: silently kills old process before extraction.
  - Completely separate from the dev build — different exe name,
    different install path, different App ID.
"""

import os
import sys
import shutil
import subprocess
import textwrap
from datetime import datetime

# ─── Config ───────────────────────────────────────────────────────────────────

REPO_URL      = "https://github.com/atharvotech/Productive-OS"
REPO_BRANCH   = "main"

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_DIR     = os.path.join(BASE_DIR, "_build_src")   # staging clone
DIST_DIR      = os.path.join(BASE_DIR, "dist")
BUILD_DIR     = os.path.join(BASE_DIR, "build")
INSTALLER_DIR = os.path.join(BASE_DIR, "installer")

APP_NAME      = "Productive-OS"
EXE_NAME      = "Productive-OS"                         # dist/Productive-OS/ folder
ISS_FILE      = os.path.join(BUILD_DIR, "prod_setup.iss")
OUTPUT_NAME   = "Productive-OS-Setup"

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
    for path in ISCC_CANDIDATES:
        if os.path.isfile(path):
            return path
    _fail(
        "Inno Setup (ISCC.exe) not found.\n"
        "  Download from: https://jrsoftware.org/isdownload.php\n"
        "  Then re-run this script."
    )


def _check_git():
    """Ensure git is on PATH."""
    try:
        subprocess.run(
            ["git", "--version"],
            check=True, capture_output=True
        )
        _ok("git found")
    except (FileNotFoundError, subprocess.CalledProcessError):
        _fail("git not found on PATH. Install Git for Windows: https://git-scm.com")


def _check_deps():
    _step("Checking dependencies")
    _check_git()
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


# ─── Stage 1: Fetch source from GitHub ───────────────────────────────────────

def _fetch_source() -> str:
    """Clone or update the repository into STAGE_DIR. Returns the staged root."""
    _step(f"Fetching latest source from GitHub\n  URL: {REPO_URL}\n  Branch: {REPO_BRANCH}")

    if os.path.isdir(os.path.join(STAGE_DIR, ".git")):
        # Repo already cloned — fetch + hard reset to latest
        print("  [git] Repository exists — pulling latest changes...")
        cmds = [
            ["git", "-C", STAGE_DIR, "fetch", "--all", "--prune"],
            ["git", "-C", STAGE_DIR, "checkout", REPO_BRANCH],
            ["git", "-C", STAGE_DIR, "reset", "--hard", f"origin/{REPO_BRANCH}"],
        ]
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=False)
            if r.returncode != 0:
                _fail(f"git command failed: {' '.join(cmd)}")
    else:
        # Fresh clone
        if os.path.exists(STAGE_DIR):
            shutil.rmtree(STAGE_DIR)
        print(f"  [git] Cloning into {STAGE_DIR}...")
        r = subprocess.run(
            ["git", "clone", "--branch", REPO_BRANCH, "--depth", "1",
             REPO_URL, STAGE_DIR]
        )
        if r.returncode != 0:
            _fail("git clone failed. Check your internet connection and repo URL.")

    # Log the commit we just pulled
    r = subprocess.run(
        ["git", "-C", STAGE_DIR, "log", "--oneline", "-1"],
        capture_output=True, text=True
    )
    commit = r.stdout.strip() if r.returncode == 0 else "unknown"
    _ok(f"Staged: {commit}")
    return STAGE_DIR


def _install_stage_deps(stage_root: str):
    """Install requirements from the staged source into the current venv."""
    req = os.path.join(stage_root, "requirements.txt")
    if os.path.isfile(req):
        _step("Installing staged requirements")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req, "--quiet"],
            cwd=stage_root
        )
        if r.returncode != 0:
            _fail("pip install failed for staged requirements.")
        _ok("Dependencies up to date")


# ─── Stage 2: PyInstaller (FOLDER mode — no --onefile) ───────────────────────

def _run_pyinstaller(stage_root: str):
    """
    Build a FOLDER distribution (no --onefile flag).

    Without --onefile PyInstaller creates:
        dist/Productive-OS/           ← the folder Inno Setup will package
        dist/Productive-OS/Productive-OS.exe  ← the main entry-point

    This eliminates the 5-10 second self-extraction delay that
    --onefile causes at every startup.
    """
    _step("Running PyInstaller (production build, folder mode, from staged source)")

    entry      = os.path.join(stage_root, "main.py")
    dashboard  = os.path.join(stage_root, "dashboard")
    extension  = os.path.join(stage_root, "extension")
    core       = os.path.join(stage_root, "core")
    ui_module  = os.path.join(stage_root, "ui.py")

    for path in [entry, dashboard, extension, core]:
        if not os.path.exists(path):
            _fail(f"Required path missing from staged source: {path}")

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
        entry,
        f"--name={EXE_NAME}",
        # NOTE: --onefile intentionally OMITTED. Folder mode = instant startup.
        # The Inno Setup [Files] section below handles the whole folder.
        "--noconsole",
        "--clean",
        "--noconfirm",
        f"--icon={os.path.join(BASE_DIR, 'scripts', 'logo.ico')}",

        # Embed runtime assets from staged source
        f"--add-data={dashboard}{SEP}dashboard",
        f"--add-data={extension}{SEP}extension",
        f"--add-data={core}{SEP}core",
        f"--add-data={ui_module}{SEP}.",

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

        # Write outputs back to the main project's dirs
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
        f"--specpath={BUILD_DIR}",
    ]

    result = subprocess.run(args, cwd=stage_root)
    if result.returncode != 0:
        _fail("PyInstaller failed. Review the output above.")

    # Folder mode: dist/Productive-OS/Productive-OS.exe
    exe_dir  = os.path.join(DIST_DIR, EXE_NAME)
    exe_path = os.path.join(exe_dir, f"{EXE_NAME}.exe")

    if not os.path.isfile(exe_path):
        _fail(f"Expected output not found: {exe_path}")

    _ok(f"Executable: {exe_path}")
    return exe_dir, exe_path


# ─── Stage 3: Generate prod_setup.iss ────────────────────────────────────────

def _get_version(stage_root: str) -> str:
    """Try to extract version from README or fall back to date-based tag."""
    readme = os.path.join(stage_root, "README.md")
    if not os.path.isfile(readme):
        readme = os.path.join(stage_root, "readMe.md")
    if os.path.isfile(readme):
        with open(readme, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "v3." in line and "Productive-OS" in line:
                    import re
                    m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", line)
                    if m:
                        return m.group(1) + ".0" if m.group(1).count(".") == 1 else m.group(1)
    return datetime.now().strftime("3.%m%d.0")   # e.g. 3.0513.0


def _generate_iss(exe_dir: str, exe_path: str, version: str):
    """
    Generate prod_setup.iss.

    Key changes vs old version:
      • [Files] now copies the entire dist folder (recursesubdirs),
        not a single exe — required because we dropped --onefile.
      • [Registry] startup block REMOVED — Registry Run keys cannot
        launch elevated apps silently; they either prompt UAC on every
        boot or are silently blocked by Windows.
      • [Code] CreateScheduledTask() creates a schtasks entry at
        /rl HIGHEST /sc onlogon, which bypasses UAC and starts
        the engine silently in the background on every login.
      • The Uninstall [Code] removes the scheduled task cleanly.
    """
    _step("Generating prod_setup.iss")

    iss = textwrap.dedent(f"""\
        ; Productive-OS Production — Inno Setup Script
        ; Auto-generated by installer.py — DO NOT EDIT MANUALLY

        #define MyAppName      "Productive-OS"
        #define MyAppVersion   "{version}"
        #define MyAppPublisher "Atharvotech"
        #define MyAppURL       "https://github.com/atharvotech/Productive-OS"
        #define MyAppExeName   "Productive-OS.exe"
        #define MyAppId        "{{8c74dee1-567d-45be-9e0a-f9c2981e5aa2}}"
        #define TaskName       "ProductiveOS_AutoStart"

        [Setup]
        AppId={{{{#MyAppId}}}}
        AppName={{#MyAppName}}
        AppVersion={{#MyAppVersion}}
        AppPublisher={{#MyAppPublisher}}
        AppPublisherURL={{#MyAppURL}}
        AppSupportURL={{#MyAppURL}}/issues
        AppUpdatesURL={{#MyAppURL}}/releases
        DefaultDirName={{autopf}}\\Atharvotech\\Productive-OS
        DefaultGroupName=Atharvotech\\Productive-OS
        OutputDir={INSTALLER_DIR}
        OutputBaseFilename={OUTPUT_NAME}
        Compression=lzma2/ultra64
        SolidCompression=yes
        WizardStyle=modern
        PrivilegesRequired=admin
        ArchitecturesInstallIn64BitMode=x64compatible
        UninstallDisplayName={{#MyAppName}}
        UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}
        SetupIconFile={os.path.join(BASE_DIR, 'scripts', 'logo.ico')}
        ; ── License ──────────────────────────────────────────────────────
        ; LicenseFile forces an 'I Accept / I Do Not Accept' page.
        ; The user CANNOT click Next until they select 'I Accept'.
        LicenseFile={os.path.join(BASE_DIR, 'docs', 'EULA.txt')}
        DisableProgramGroupPage=no
        AllowCancelDuringInstall=yes
        ShowLanguageDialog=no
        ; Allow silent install over existing version (auto-update flow)
        CloseApplications=no

        [Languages]
        Name: "english"; MessagesFile: "compiler:Default.isl"

        [Tasks]
        Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

        ; ── Files ─────────────────────────────────────────────────────────────
        ; Folder-based distribution (no --onefile):
        ;   Source is the entire PyInstaller output directory.
        ;   Inno Setup recursively copies every file into {{app}}.
        [Files]
        Source: "{exe_dir}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

        [Icons]
        Name: "{{autoprograms}}\\Atharvotech\\Productive-OS"; Filename: "{{app}}\\{{#MyAppExeName}}"
        Name: "{{autodesktop}}\\Productive-OS"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

        [Run]
        Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "Launch Productive-OS"; Flags: nowait postinstall skipifsilent shellexec

        [UninstallDelete]
        Type: filesandordirs; Name: "{{app}}"

        [Code]
        var
          ResultCode: Integer;

        // ── Helper: create/replace the scheduled task for auto-start ──────────
        //
        // Uses schtasks /rl HIGHEST /sc onlogon so the engine:
        //   • Starts automatically at every Windows logon
        //   • Runs with highest available privileges (no UAC prompt, no blocking)
        //   • Launches with --background so no window opens on boot
        //
        // This replaces the old Registry Run key approach, which either
        // triggered a UAC prompt on every boot or was silently blocked by
        // Windows UAC policy when elevation was required.
        procedure CreateAutoStartTask();
        var
          ExePath, TaskArgs, CmdLine: String;
        begin
          ExePath  := ExpandConstant('{{app}}\\{{#MyAppExeName}}');
          TaskArgs := '--background';

          // schtasks /Create /F overwrites any existing task of the same name.
          CmdLine := '/C schtasks /Create'
                   + ' /TN "' + ExpandConstant('{{#TaskName}}') + '"'
                   + ' /TR "\"' + ExePath + '\" ' + TaskArgs + '"'
                   + ' /SC ONLOGON'
                   + ' /RL HIGHEST'
                   + ' /F'
                   + ' >nul 2>&1';

          Exec('cmd.exe', CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

          if ResultCode = 0 then
            Log('Auto-start scheduled task created successfully.')
          else
            Log('WARNING: Failed to create auto-start scheduled task (exit code ' + IntToStr(ResultCode) + ').');
        end;

        // ── Helper: remove the scheduled task on uninstall ────────────────────
        procedure RemoveAutoStartTask();
        var
          CmdLine: String;
        begin
          CmdLine := '/C schtasks /Delete'
                   + ' /TN "' + ExpandConstant('{{#TaskName}}') + '"'
                   + ' /F'
                   + ' >nul 2>&1';
          Exec('cmd.exe', CmdLine, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
          Log('Auto-start scheduled task removed.');
        end;

        procedure CurStepChanged(CurStep: TSetupStep);
        begin
          if CurStep = ssInstall then
          begin
            // ── Auto-Update: Kill the running instance before overwriting ──────
            // This is the seamless upgrade path: users just run the new setup
            // and it cleanly replaces the old version without "file in use" errors.
            Exec('cmd.exe',
              '/C taskkill /F /IM Productive-OS.exe >nul 2>&1',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
            // Brief pause to let the OS release file handles
            Sleep(1200);
          end;

          if CurStep = ssPostInstall then
          begin
            // Create the scheduled task AFTER all files are extracted.
            CreateAutoStartTask();
            // Start the background engine immediately so we don't have to wait for a reboot
            Exec('cmd.exe', '/C schtasks /Run /TN "' + ExpandConstant('{{#TaskName}}') + '" >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
          end;
        end;

        procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
        begin
          if CurUninstallStep = usUninstall then
          begin
            // Kill process before uninstaller tries to delete files
            Exec('cmd.exe',
              '/C taskkill /F /IM Productive-OS.exe >nul 2>&1',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
            Sleep(800);
            // Remove the scheduled task so it doesn't fire after uninstall
            RemoveAutoStartTask();
          end;
        end;

        // Show an "Upgrading..." label on the progress page when an existing
        // install is detected, so users know this is an update, not a fresh install.
        function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
          MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
        var
          Info: String;
        begin
          Info := '';
          if DirExists(ExpandConstant('{{app}}')) then
            Info := 'Existing installation detected — this will upgrade Productive-OS.' + NewLine + NewLine;
          Result := Info + MemoDirInfo + NewLine + MemoGroupInfo + NewLine + MemoTasksInfo;
        end;
    """)

    with open(ISS_FILE, "w", encoding="utf-8") as f:
        f.write(iss)

    _ok(f"ISS script written: {ISS_FILE}")
    return ISS_FILE


# ─── Stage 4: Compile with ISCC ──────────────────────────────────────────────

def _compile_iss(iss_path: str, iscc: str) -> str:
    _step(f"Compiling production installer with ISCC")

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
    _banner("Productive-OS — PRODUCTION BUILD PIPELINE")
    print(f"  Mode   : GitHub source ({REPO_URL})")
    print(f"  Branch : {REPO_BRANCH}")
    print(f"  Output : installer/{OUTPUT_NAME}.exe")

    _check_deps()
    iscc = _find_iscc()

    stage_root = _fetch_source()
    _install_stage_deps(stage_root)

    version    = _get_version(stage_root)
    _ok(f"Detected version: {version}")

    exe_dir, exe_path = _run_pyinstaller(stage_root)
    iss_path          = _generate_iss(exe_dir, exe_path, version)
    installer         = _compile_iss(iss_path, iscc)

    _banner("PRODUCTION BUILD COMPLETE")
    print(f"  Version   : {version}")
    print(f"  Installer : {installer}")
    print()
    print("  Distribution steps:")
    print("    1. Upload installer/Productive-OS-Setup.exe to GitHub Releases")
    print("    2. Users run it with admin rights to install or upgrade")
    print()
    print("  Auto-start behaviour (post-install):")
    print("    • Scheduled Task 'ProductiveOS_AutoStart' created via schtasks")
    print("    • Runs at every logon with /rl HIGHEST — no UAC prompt, no blocking")
    print("    • Engine starts silently in background (--background flag)")
    print()
    print("  Auto-update behaviour (for existing installs):")
    print("    • Setup kills the running Productive-OS.exe via taskkill")
    print("    • Overwrites files in place — no uninstall required")
    print("    • All user settings in SQLite are preserved")
    print("    • Scheduled task is re-created to reflect new exe path")
    print()


if __name__ == "__main__":
    build()
