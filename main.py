"""
Focus Engine Pro — Master Orchestrator
Runs all subsystems: DNS Blocker, App Killer, Activity Tracker,
WebSocket API, HTTP Dashboard Server, and Watchdog.

Modes:
  python main.py             → Start engine + open dashboard UI window
  python main.py --background → Start engine only (no UI, used by Task Scheduler)

Admin elevation:
  - When packaged as .exe: PyInstaller --uac-admin handles it at OS level.
  - In dev mode (python main.py): auto re-launches via ShellExecuteW if not admin.
    The re-launched console is hidden (SW_HIDE) so it runs like a background service.

IPC (Single-Instance):
  - A global mutex ("ProductiveOS_Singleton_Mutex_v3") prevents two engine
    instances from running simultaneously.
  - When a second instance is launched (e.g. user double-clicks the shortcut
    while the background engine is already running via Task Scheduler), it:
      1. Tries to focus an existing "Productive-OS" window via Win32.
      2. If no window exists, sends GET /ipc/show-window to the running engine.
      3. The engine's main thread receives the signal and calls open_window().
      4. The second instance then exits immediately.
  - This design ensures pywebview.start() always runs on the main thread of
    the engine process, which is required on Windows.

Auto-Update:
  - A background thread checks the GitHub Releases API every 6 hours.
  - If a newer version is detected, it downloads Productive-OS-Setup.exe
    into %TEMP% and runs it silently (/VERYSILENT /SUPPRESSMSGBOXES
    /FORCECLOSEAPPLICATIONS). The installer's [Code] section kills this
    process and replaces all files; the scheduled task restarts the new
    version on next logon.
"""

import os
import sys
import time
import ctypes
import threading
import argparse

# ─── Version & Constants ──────────────────────────────────────────────────────

APP_VERSION          = "3.6.0"
HTTP_PORT            = 8123
WS_PORT              = 8765
MUTEX_NAME           = "ProductiveOS_Singleton_Mutex_v3"
GITHUB_REPO          = "atharvotech/Productive-OS"
GITHUB_API_URL       = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_CHECK_SECS    = 6 * 3600   # Check for updates every 6 hours


# ─── Admin Elevation (dev-mode only) ─────────────────────────────────────────

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate_dev(background: bool):
    """
    Re-launch as admin. Works for both .py scripts and frozen .exe.
    Uses SW_HIDE to suppress the console window on the re-launched process.
    """
    extra_args = "--background" if background else ""

    if getattr(sys, "frozen", False):
        launcher = sys.executable
        args_str = extra_args
    else:
        launcher = sys.executable  # python.exe
        script = os.path.abspath(sys.argv[0])
        args_str = f'"{script}" {extra_args}'.strip()

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            launcher,
            args_str,
            None,
            0,  # SW_HIDE
        )
        if result <= 32:
            raise OSError(f"ShellExecuteW returned error code {result}")
        sys.exit(0)
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Productive-OS requires Administrator privileges to start the engine.\n\nError: {e}",
            "Elevation Failed",
            0x10,
        )
        sys.exit(1)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _base_dir() -> str:
    """Return project root — works both in dev and when frozen by PyInstaller."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))



# ─── Auto-Update (background thread) ─────────────────────────────────────────

def _parse_version(tag: str):
    """Parse 'v3.6.0' or '3.6.0' into a comparable tuple of ints."""
    tag = tag.lstrip("v").strip()
    try:
        return tuple(int(x) for x in tag.split("."))
    except Exception:
        return (0, 0, 0)


def _start_auto_updater():
    """
    Spawn a daemon thread that periodically checks for a newer GitHub Release.

    On finding one it:
      1. Downloads Productive-OS-Setup.exe to %TEMP%.
      2. Runs it with /VERYSILENT /SUPPRESSMSGBOXES /FORCECLOSEAPPLICATIONS.
         The installer's [Code] section will kill this process and overwrite
         all files. The scheduled task will restart the new version on next logon.
    """
    def _updater_loop():
        import json
        import tempfile
        import urllib.request
        import urllib.error

        current = _parse_version(APP_VERSION)
        print(f"  [AutoUpdate] Current version: {APP_VERSION} — checking every {UPDATE_CHECK_SECS // 3600}h")

        # Stagger the first check by 60 s so it doesn't interfere with startup
        time.sleep(60)

        while True:
            try:
                req = urllib.request.Request(
                    GITHUB_API_URL,
                    headers={"User-Agent": f"Productive-OS/{APP_VERSION}"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                tag     = data.get("tag_name", "0.0.0")
                latest  = _parse_version(tag)
                assets  = data.get("assets", [])

                print(f"  [AutoUpdate] Latest release on GitHub: {tag}")

                if latest > current:
                    # Find the installer asset
                    installer_asset = next(
                        (a for a in assets if a["name"].endswith(".exe")),
                        None,
                    )
                    if installer_asset:
                        download_url = installer_asset["browser_download_url"]
                        tmp_dir      = tempfile.mkdtemp(prefix="pos_update_")
                        tmp_exe      = os.path.join(tmp_dir, "Productive-OS-Setup.exe")

                        print(f"  [AutoUpdate] Newer version {tag} found. Downloading from {download_url} …")
                        urllib.request.urlretrieve(download_url, tmp_exe)
                        print(f"  [AutoUpdate] Download complete: {tmp_exe}")

                        # Run silently — this will kill and replace the current process.
                        # The Inno Setup [Code] taskkill step terminates us; the
                        # scheduled task restarts the updated engine on next logon.
                        import subprocess
                        subprocess.Popen(
                            [
                                tmp_exe,
                                "/VERYSILENT",
                                "/SUPPRESSMSGBOXES",
                                "/FORCECLOSEAPPLICATIONS",
                                "/NORESTART",
                            ],
                            creationflags=subprocess.DETACHED_PROCESS
                            | subprocess.CREATE_NEW_PROCESS_GROUP,
                            close_fds=True,
                        )
                        print("  [AutoUpdate] Installer launched. Engine will be replaced.")
                        # Nothing more to do — installer will kill this process.
                        return
                    else:
                        print("  [AutoUpdate] No .exe asset found in release — skipping.")
                else:
                    print("  [AutoUpdate] Already on latest version.")

            except urllib.error.URLError as e:
                print(f"  [AutoUpdate] Network error: {e}")
            except Exception as e:
                print(f"  [AutoUpdate] Unexpected error: {e}")

            time.sleep(UPDATE_CHECK_SECS)

    t = threading.Thread(target=_updater_loop, daemon=True, name="AutoUpdater")
    t.start()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # ── Parse args ────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Focus Engine Pro")
    parser.add_argument(
        "--background",
        action="store_true",
        help="Start engine only (no dashboard window). Used by Task Scheduler.",
    )
    args, _ = parser.parse_known_args()

    # ── Single-Instance: focus existing window or send IPC ────────────────
    import ctypes
    import winerror

    # 1. If the native window is already visible, just focus it and exit.
    if not args.background:
        hwnd = ctypes.windll.user32.FindWindowW(None, "Productive-OS")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            print("[*] Dashboard is already open. Focused existing window.")
            sys.exit(0)

    # 2. Acquire the singleton mutex.
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    err = ctypes.windll.kernel32.GetLastError()
    if err in (winerror.ERROR_ALREADY_EXISTS, 5):  # 5 = ERROR_ACCESS_DENIED
        # The background engine is already running.
        if not args.background:
            # We skip all UAC/admin checks. This lightweight process just
            # launches the UI which connects to the existing background engine.
            print("[*] Background engine detected. Opening dashboard UI locally.")
            from ui import open_window
            open_window(HTTP_PORT)
        sys.exit(0)

    # ── Elevation check (We need admin to start the engine) ───────────────
    if not is_admin():
        # Spawn the background engine elevated (this uses SW_HIDE)
        elevate_dev(background=True)
        # Now the background engine is starting up. 
        if not args.background:
            print("[*] Background engine spawned. Opening dashboard UI locally.")
            time.sleep(1)  # wait a moment for the background HTTP server to start
            from ui import open_window
            open_window(HTTP_PORT)
        sys.exit(0)

    # ── Engine startup ────────────────────────────────────────────────────
    from core import database as db
    db.init_db()

    from core.auth import AuthManager
    auth = AuthManager()
    # First-run setup is handled in the dashboard (no terminal prompts needed)

    from core.watchdog import Watchdog
    watchdog = Watchdog()
    if not watchdog.is_task_scheduled():
        watchdog.create_scheduled_task()
    watchdog.protect_process()

    from core.dns_blocker import DNSBlocker
    dns = DNSBlocker()
    current_mode = db.get_setting("focus_mode", "off")
    if current_mode == "study":
        dns.enable_safe_mode()
        dns.block_incognito()
    elif db.get_setting("dns_blocking", "on") == "on":
        dns.enable_safe_mode()

    from core.app_killer import AppKiller
    killer = AppKiller()
    killer.start()  # Launches WMI process-start watcher + WinEvent foreground hook

    from core.tracker import ActivityTracker
    tracker = ActivityTracker()
    tracker.start()

    auth.lock_config_files()

    # ── Shared shutdown event ─────────────────────────────────────────────
    shutdown = threading.Event()

    def trigger_shutdown():
        shutdown.set()

    # ── WebSocket + HTTP servers ──────────────────────────────────────────
    from core.api_server import start_api_server, start_http_server

    api = start_api_server(
        auth=auth,
        app_killer=killer,
        tracker=tracker,
        dns_blocker=dns,
        port=WS_PORT,
        on_shutdown=trigger_shutdown,
    )
    try:
        start_http_server(port=HTTP_PORT)
    except Exception as e:
        print(f"[!] Failed to start HTTP server on {HTTP_PORT}: {e}")

    # ── Auto-updater (production only — skip in dev/script mode) ─────────
    if getattr(sys, "frozen", False):
        _start_auto_updater()

    # ── Open dashboard UI (unless --background) ───────────────────────────
    if not args.background:
        from ui import open_window
        # Run pywebview on the main thread (required on Windows).
        open_window(HTTP_PORT, shutdown_event=shutdown)
        # open_window() returns when the window is closed.
        # The engine continues running in background — don't shut down here.

    # ── Main thread event loop ────────────────────────────────────────────
    print("[*] Engine is running. Main thread entering event loop.")
    while not shutdown.is_set():
        time.sleep(0.5)

    # ── Graceful shutdown ─────────────────────────────────────────────────
    tracker.stop()
    dns.disable_safe_mode()
    dns.unblock_incognito()
    auth.unlock_config_files()

    import datetime
    try:
        db.update_daily_summary(datetime.date.today().isoformat())
    except Exception:
        pass

    print("[*] Shutdown complete. Terminating process.")
    os._exit(0)


if __name__ == "__main__":
    main()