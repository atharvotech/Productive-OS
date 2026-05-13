"""
Productive-OS — Production GitHub Builder
==========================================
Fetches the latest release from GitHub and builds a production installer.

    python installer.py

Pipeline:
  1. Validates environment (git, PyInstaller, Inno Setup)
  2. git pull → clones/updates https://github.com/atharvotech/Productive-OS
     into a clean _build_src/ staging directory
  3. Runs PyInstaller from the staged source → dist/Productive-OS.exe
  4. Generates prod_setup.iss dynamically
  5. Compiles via ISCC.exe → installer/Productive-OS-Setup.exe

Rules:
  - Source: ALWAYS pulled from GitHub (latest commit on main branch)
  - Install path: {autopf}\\Atharvotech\\Productive-OS
  - Auto-update logic: runs taskkill /F /IM Productive-OS.exe before
    extraction so users can silently upgrade over an existing install.
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

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
STAGE_DIR     = os.path.join(BASE_DIR, "_build_src")   # staging clone
DIST_DIR      = os.path.join(BASE_DIR, "dist")
BUILD_DIR     = os.path.join(BASE_DIR, "build")
INSTALLER_DIR = os.path.join(BASE_DIR, "installer")

APP_NAME      = "Productive-OS"
EXE_NAME      = "Productive-OS"                         # dist/Productive-OS.exe
ISS_FILE      = os.path.join(BASE_DIR, "prod_setup.iss")
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


# ─── Stage 2: PyInstaller ────────────────────────────────────────────────────

def _run_pyinstaller(stage_root: str) -> str:
    _step("Running PyInstaller (production build from staged source)")

    entry      = os.path.join(stage_root, "main.py")
    dashboard  = os.path.join(stage_root, "dashboard")
    extension  = os.path.join(stage_root, "extension")
    core       = os.path.join(stage_root, "core")
    ui_module  = os.path.join(stage_root, "ui.py")

    for path in [entry, dashboard, extension, core]:
        if not os.path.exists(path):
            _fail(f"Required path missing from staged source: {path}")

    os.makedirs(DIST_DIR, exist_ok=True)
    os.makedirs(INSTALLER_DIR, exist_ok=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        entry,
        f"--name={EXE_NAME}",
        "--onefile",
        "--noconsole",
        "--uac-admin",
        "--clean",
        "--noconfirm",

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
        f"--specpath={BASE_DIR}",
    ]

    result = subprocess.run(args, cwd=stage_root)
    if result.returncode != 0:
        _fail("PyInstaller failed. Review the output above.")

    exe_path = os.path.join(DIST_DIR, f"{EXE_NAME}.exe")
    if not os.path.isfile(exe_path):
        _fail(f"Expected output not found: {exe_path}")

    _ok(f"Executable: {exe_path}")
    return exe_path


# ─── Stage 3: Generate prod_setup.iss ────────────────────────────────────────

def _get_version(stage_root: str) -> str:
    """Try to extract version from README or fall back to date-based tag."""
    readme = os.path.join(stage_root, "README.md")
    if os.path.isfile(readme):
        with open(readme, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "v3." in line and "Productive-OS" in line:
                    import re
                    m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", line)
                    if m:
                        return m.group(1) + ".0" if m.group(1).count(".") == 1 else m.group(1)
    return datetime.now().strftime("3.%m%d.0")   # e.g. 3.0513.0


def _generate_iss(exe_path: str, version: str):
    _step("Generating prod_setup.iss")

    iss = textwrap.dedent(f"""\
        ; Productive-OS Production — Inno Setup Script
        ; Auto-generated by installer.py — DO NOT EDIT MANUALLY

        #define MyAppName      "Productive-OS"
        #define MyAppVersion   "{version}"
        #define MyAppPublisher "Atharvotech"
        #define MyAppURL       "https://github.com/atharvotech/Productive-OS"
        #define MyAppExeName   "Productive-OS.exe"
        #define MyAppId        "{{A0B1C2D3-E4F5-6789-BCDE-F01234567890}"

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
        ; ── License ──────────────────────────────────────────────────────
        ; LicenseFile forces an 'I Accept / I Do Not Accept' page.
        ; The user CANNOT click Next until they select 'I Accept'.
        LicenseFile={os.path.join(BASE_DIR, 'EULA.txt')}
        DisableProgramGroupPage=no
        AllowCancelDuringInstall=yes
        ShowLanguageDialog=no
        ; Allow silent install over existing version (auto-update flow)
        CloseApplications=no

        [Languages]
        Name: "english"; MessagesFile: "compiler:Default.isl"

        [Tasks]
        Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
        Name: "startupicon"; Description: "Launch Productive-OS at &Windows startup"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

        [Files]
        Source: "{exe_path}"; DestDir: "{{app}}"; Flags: ignoreversion

        [Icons]
        Name: "{{autoprograms}}\\Atharvotech\\Productive-OS"; Filename: "{{app}}\\{{#MyAppExeName}}"
        Name: "{{autodesktop}}\\Productive-OS"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

        [Registry]
        ; Optional startup registry entry (only if user chose the startup task)
        Root: HKCU; Subkey: "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"; \\
          ValueType: string; ValueName: "Productive-OS"; \\
          ValueData: "{{app}}\\{{#MyAppExeName}}"; \\
          Flags: uninsdeletevalue; Tasks: startupicon

        [Run]
        Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "Launch Productive-OS"; Flags: nowait postinstall skipifsilent

        [UninstallDelete]
        Type: filesandordirs; Name: "{{app}}"

        [Code]
        var
          ResultCode: Integer;

        procedure CurStepChanged(CurStep: TSetupStep);
        begin
          // ── Auto-Update: Kill the running instance before overwriting ────
          // This is the seamless upgrade path: users just run the new setup
          // and it cleanly replaces the old version without "file in use" errors.
          if CurStep = ssInstall then
          begin
            Exec('cmd.exe',
              '/C taskkill /F /IM Productive-OS.exe > nul 2>&1',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
            // Brief pause to let the OS release file handles
            Sleep(1200);
          end;
        end;

        procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
        begin
          // Kill process before uninstaller tries to delete the exe
          if CurUninstallStep = usUninstall then
          begin
            Exec('cmd.exe',
              '/C taskkill /F /IM Productive-OS.exe > nul 2>&1',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
            Sleep(800);
          end;
        end;

        // Show a "Upgrading..." label on the progress page when an existing
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

    exe_path   = _run_pyinstaller(stage_root)
    iss_path   = _generate_iss(exe_path, version)
    installer  = _compile_iss(iss_path, iscc)

    _banner("PRODUCTION BUILD COMPLETE")
    print(f"  Version   : {version}")
    print(f"  Installer : {installer}")
    print()
    print("  Distribution steps:")
    print("    1. Upload installer/Productive-OS-Setup.exe to GitHub Releases")
    print("    2. Users run it with admin rights to install or upgrade")
    print()
    print("  Auto-update behaviour (for existing installs):")
    print("    • Setup kills the running Productive-OS.exe via taskkill")
    print("    • Overwrites files in place — no uninstall required")
    print("    • All user settings in SQLite are preserved")
    print()


if __name__ == "__main__":
    build()
