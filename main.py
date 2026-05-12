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
"""

import os
import sys
import time
import ctypes
import threading
import argparse

# ─── Admin Elevation (dev-mode only) ─────────────────────────────────────────

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate_dev(background: bool):
    """
    Re-launch as admin when running as a plain .py script.
    Uses SW_HIDE to suppress the console window on the re-launched process.
    Shows a blocking message box on failure so the user sees what went wrong.
    """
    import subprocess

    script = os.path.abspath(sys.argv[0])
    extra_args = "--background" if background else ""

    # Use python.exe (with visible console) in dev mode so any startup
    # errors are visible. pythonw.exe would hide all errors silently.
    launcher = sys.executable  # always python.exe in dev mode

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            launcher,
            f'"{script}" {extra_args}'.strip(),
            None,
            0,  # SW_HIDE — run the elevated process with no visible window
        )
        # ShellExecuteW returns ≤32 on failure
        if result <= 32:
            raise OSError(f"ShellExecuteW returned error code {result}")
        # Current non-admin process exits; elevated process takes over
        sys.exit(0)
    except Exception as e:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Productive-OS requires Administrator privileges.\n\nError: {e}\n\nPlease right-click the app and select 'Run as Administrator'.",
            "Elevation Failed",
            0x10,  # MB_ICONERROR
        )
        sys.exit(1)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _base_dir() -> str:
    """Return project root — works both in dev and when frozen by PyInstaller."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


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

    # ── Prevent Multiple Instances ────────────────────────────────────────
    import ctypes
    import winerror
    # Create a system-wide mutex
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "ProductiveOS_Singleton_Mutex_v3")
    if ctypes.windll.kernel32.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        print("[*] Productive-OS is already running. Exiting.")
        sys.exit(0)

    # ── Elevation check (dev mode only; .exe uses --uac-admin) ────────────
    if not getattr(sys, "frozen", False):
        if not is_admin():
            print("[*] Requesting Administrator privileges...")
            elevate_dev(background=args.background)
            return  # Current process exits; elevated child takes over

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
        port=8765,
        on_shutdown=trigger_shutdown,
    )
    # Start HTTP Server on an alternative port to avoid 8080 conflicts
    HTTP_PORT = 8123
    try:
        start_http_server(port=HTTP_PORT)
    except Exception as e:
        print(f"[!] Failed to start HTTP server on {HTTP_PORT}: {e}")

    # ── Open dashboard UI (unless --background) ───────────────────────────
    if not args.background:
        from ui import open_window
        # Pass the correct port to UI
        ui_thread = threading.Thread(target=open_window, args=(HTTP_PORT,), daemon=True)
        ui_thread.start()

    # ── Block until shutdown is requested ─────────────────────────────────
    shutdown.wait()

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
    import os
    os._exit(0)


if __name__ == "__main__":
    main()