"""
Productive-OS — PyInstaller Build Script
==========================================

Run this script ONCE manually to produce dist/Productive-OS.exe:

    python build.py

Requirements (install first if not already):
    pip install pyinstaller pywebview

The resulting .exe will:
  - Show a native Windows UAC prompt for admin rights (--uac-admin)
  - Open the dashboard in a native application window (pywebview)
  - Run the engine in the background when opened via Windows Search
  - NOT show any black terminal/console window (--noconsole)

Output:
    dist/Productive-OS.exe   ← the standalone executable

After building:
  1. Run dist/Productive-OS.exe (accept UAC prompt)
  2. On first run you'll see the Setup page in the dashboard window
  3. Set your admin password to activate the engine
  4. Pin to Start Menu or Taskbar for easy access
  5. The engine will auto-start at login via Windows Task Scheduler
"""

import os
import sys
import subprocess

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ENTRY_POINT = os.path.join(BASE_DIR, "main.py")
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")
EXTENSION_DIR = os.path.join(BASE_DIR, "extension")
CORE_DIR = os.path.join(BASE_DIR, "core")

APP_NAME = "Productive-OS"

# ─── Build ────────────────────────────────────────────────────────────────────

def build():
    print("=" * 60)
    print(f"  🔨 Building {APP_NAME}.exe")
    print("=" * 60)
    print()

    # Validate that PyInstaller is available
    try:
        import PyInstaller  # noqa
    except ImportError:
        print("[!] PyInstaller not found. Install it with:")
        print("    pip install pyinstaller")
        sys.exit(1)

    # Validate that pywebview is available
    try:
        import webview  # noqa
    except ImportError:
        print("[!] pywebview not found. Install it with:")
        print("    pip install pywebview")
        sys.exit(1)

    # Separator character for --add-data (Windows uses semicolon)
    sep = ";" if sys.platform == "win32" else ":"

    args = [
        sys.executable, "-m", "PyInstaller",
        ENTRY_POINT,
        f"--name={APP_NAME}",
        "--onefile",              # Single .exe
        "--noconsole",            # No terminal window
        "--uac-admin",            # Request admin via OS UAC (no re-launch hack)
        "--clean",                # Clean build cache
        "--noconfirm",            # Overwrite without asking

        # Embed the dashboard, extension, and core directories
        f"--add-data={DASHBOARD_DIR}{sep}dashboard",
        f"--add-data={EXTENSION_DIR}{sep}extension",
        f"--add-data={CORE_DIR}{sep}core",

        # Include the UI module explicitly
        f"--add-data={os.path.join(BASE_DIR, 'ui.py')}{sep}.",

        # Hidden imports that PyInstaller sometimes misses
        "--hidden-import=websockets",
        "--hidden-import=bcrypt",
        "--hidden-import=psutil",
        "--hidden-import=webview",
        "--hidden-import=winreg",

        # Build output directory
        f"--distpath={os.path.join(BASE_DIR, 'dist')}",
        f"--workpath={os.path.join(BASE_DIR, 'build')}",
        f"--specpath={BASE_DIR}",
    ]

    print("[*] Running PyInstaller...")
    print()
    result = subprocess.run(args, cwd=BASE_DIR)

    print()
    if result.returncode == 0:
        exe_path = os.path.join(BASE_DIR, "dist", f"{APP_NAME}.exe")
        print("=" * 60)
        print(f"  ✅ Build successful!")
        print()
        print(f"  Executable: {exe_path}")
        print()
        print("  Next steps:")
        print(f"  1. Run:  dist\\{APP_NAME}.exe")
        print("  2. Accept the UAC admin prompt")
        print("  3. The Focus Engine dashboard will open")
        print("  4. Complete the first-run setup in the dashboard")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  ❌ Build FAILED. Check the output above for errors.")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    build()
