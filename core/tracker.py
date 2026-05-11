"""
Focus Engine Pro — Activity Tracker
Polls the foreground window every 2 seconds, classifies activity,
logs to database, and manages token earn/deduct logic.
Tracks Spotify via window title even when in background.
"""

import os
import re
import sys
import time
import ctypes
import ctypes.wintypes
import threading
import datetime
import psutil
from core import database as db


# ─── Windows API declarations ─────────────────────────────────────────────

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GetForegroundWindow = user32.GetForegroundWindow
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
IsZoomed = user32.IsZoomed  # Returns non-zero if window is maximized
IsIconic = user32.IsIconic   # Returns non-zero if window is minimized
EnumWindows = user32.EnumWindows
IsWindowVisible = user32.IsWindowVisible
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)


# ─── Friendly App Name Mapping ────────────────────────────────────────────

FRIENDLY_NAMES = {
    # Browsers
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave Browser",
    "opera.exe": "Opera",
    "vivaldi.exe": "Vivaldi",
    # IDEs / Editors
    "code.exe": "VS Code",
    "code - insiders.exe": "VS Code Insiders",
    "cursor.exe": "Cursor",
    "windsurf.exe": "Windsurf",
    "devenv.exe": "Visual Studio",
    "pycharm64.exe": "PyCharm",
    "pycharm.exe": "PyCharm",
    "idea64.exe": "IntelliJ IDEA",
    "idea.exe": "IntelliJ IDEA",
    "sublime_text.exe": "Sublime Text",
    "notepad++.exe": "Notepad++",
    "atom.exe": "Atom",
    "notepad.exe": "Notepad",
    # Terminal
    "windowsterminal.exe": "Windows Terminal",
    "powershell.exe": "PowerShell",
    "cmd.exe": "Command Prompt",
    "wt.exe": "Windows Terminal",
    # Office
    "winword.exe": "Microsoft Word",
    "excel.exe": "Microsoft Excel",
    "powerpnt.exe": "PowerPoint",
    "onenote.exe": "OneNote",
    "outlook.exe": "Outlook",
    "teams.exe": "Microsoft Teams",
    # Communication
    "discord.exe": "Discord",
    "slack.exe": "Slack",
    "zoom.exe": "Zoom",
    "telegram.exe": "Telegram",
    "whatsapp.exe": "WhatsApp",
    # Entertainment / Media
    "spotify.exe": "Spotify",
    "vlc.exe": "VLC Media Player",
    "mpv.exe": "mpv",
    # System
    "explorer.exe": "File Explorer",
    "searchhost.exe": "Windows Search",
    "startmenuexperiencehost.exe": "Start Menu",
    "shellexperiencehost.exe": "System Shell",
    "applicationframehost.exe": "Windows App",
    "systemsettings.exe": "Windows Settings",
    "taskmgr.exe": "Task Manager",
    # Design
    "figma.exe": "Figma",
    "photoshop.exe": "Photoshop",
    # PDF
    "acrobat.exe": "Adobe Acrobat",
    "acrord32.exe": "Adobe Reader",
    # Gaming platforms
    "steam.exe": "Steam",
    "steamwebhelper.exe": "Steam",
    "epicgameslauncher.exe": "Epic Games",
    "riotclientux.exe": "Riot Client",
}

# System/background processes that should never appear in tracking
SYSTEM_BACKGROUND_PROCESSES = {
    "svchost.exe", "csrss.exe", "lsass.exe", "services.exe",
    "dwm.exe", "conhost.exe", "sihost.exe", "fontdrvhost.exe",
    "ctfmon.exe", "runtimebroker.exe", "dllhost.exe",
    "searchindexer.exe", "securityhealthservice.exe",
    "compactoverlay.exe", "textinputhost.exe",
    "smartscreen.exe", "wmiprvse.exe", "spoolsv.exe",
    "taskhostw.exe", "audiodg.exe", "ntoskrnl.exe",
}

# Browser processes — the Chrome extension logs their time via web_time.
# Skipping them in screen_time prevents double-counting in category totals.
BROWSER_APPS = {
    "chrome.exe", "msedge.exe", "firefox.exe",
    "brave.exe", "opera.exe", "vivaldi.exe",
}


def get_friendly_name(exe_name: str) -> str:
    """Convert raw .exe name to a human-readable name."""
    lower = exe_name.lower()
    if lower in FRIENDLY_NAMES:
        return FRIENDLY_NAMES[lower]
    # Strip .exe (case-insensitive) and clean up separators
    base = re.sub(r'(?i)\.exe$', '', exe_name)
    clean = base.replace("_", " ").replace("-", " ").replace(".", " ").strip()
    return clean.title() if clean else exe_name


def get_foreground_info() -> dict:
    """Get info about the currently focused window."""
    hwnd = GetForegroundWindow()
    if not hwnd:
        return {"app": "explorer.exe", "title": "Desktop", "pid": 0,
                "is_maximized": False, "is_minimized": False}

    # Get window title
    length = GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value

    # Get PID
    pid = ctypes.wintypes.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    # Get process name from PID
    app_name = "Unknown"
    try:
        proc = psutil.Process(pid.value)
        app_name = proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    # Check if window is maximized / minimized
    is_maximized = bool(IsZoomed(hwnd))
    is_minimized = bool(IsIconic(hwnd))

    return {
        "hwnd": hwnd,
        "app": app_name,
        "title": title,
        "pid": pid.value,
        "is_maximized": is_maximized,
        "is_minimized": is_minimized,
    }

def get_visible_windows() -> list:
    """Return a list of all visibly large windows on the screen."""
    windows = []
    def callback(hwnd, _):
        if not IsWindowVisible(hwnd) or IsIconic(hwnd):
            return True
            
        length = GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
            
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 200 or height < 200:
            return True
            
        pid = ctypes.wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        
        try:
            proc = psutil.Process(pid.value)
            app_name = proc.name()
        except Exception:
            app_name = "Unknown"
            
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        windows.append({
            "hwnd": hwnd,
            "app": app_name,
            "title": buf.value,
            "is_maximized": bool(IsZoomed(hwnd)),
            "is_minimized": False
        })
        return True
        
    EnumWindows(WNDENUMPROC(callback), 0)
    return windows


# ─── Spotify Background Detection ─────────────────────────────────────────

def find_spotify_title() -> str:
    """Find the Spotify window title even when Spotify is in the background.
    Uses EnumWindows to find any visible Spotify window."""
    spotify_title = ""

    def callback(hwnd, _):
        nonlocal spotify_title
        if not IsWindowVisible(hwnd):
            return True
        # Get PID for this window
        pid = ctypes.wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            proc = psutil.Process(pid.value)
            if proc.name().lower() == "spotify.exe":
                length = GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    # Spotify title with music: "Song - Artist"
                    # Skip empty or generic titles
                    if title and title.lower() not in ("spotify", ""):
                        spotify_title = title
                        return False  # Stop enumeration
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return True

    try:
        EnumWindows(WNDENUMPROC(callback), 0)
    except Exception:
        pass
    return spotify_title


# ─── Activity Classification ──────────────────────────────────────────────

# Category keyword maps
STUDY_APPS = {
    "code.exe", "code - insiders.exe", "devenv.exe",
    "pycharm64.exe", "pycharm.exe", "idea64.exe", "idea.exe",
    "sublime_text.exe", "notepad++.exe", "atom.exe",
    "windowsterminal.exe", "powershell.exe", "cmd.exe",
    "cursor.exe", "windsurf.exe",
    "winword.exe", "excel.exe", "powerpnt.exe",
    "onenote.exe", "teams.exe",
    "acrobat.exe", "acrord32.exe",  # PDF readers
}

STUDY_TITLE_KEYWORDS = [
    "stack overflow", "stackoverflow", "github", "geeksforgeeks",
    "leetcode", "hackerrank", "docs.python", "docs.microsoft",
    "mdn web docs", "w3schools", "tutorialspoint", "coursera",
    "udemy", "khan academy", "edx", "codecademy",
    "jupyter", "notebook", "colab",
    ".py", ".js", ".html", ".css", ".java", ".cpp",
]

SOCIAL_MEDIA_KEYWORDS = [
    "instagram", "facebook", "twitter", "snapchat", "tiktok",
    "reddit", "whatsapp web", "telegram web", "pinterest",
    "tumblr", "linkedin",
]

ENTERTAINMENT_KEYWORDS = [
    "netflix", "disney+", "hotstar", "prime video", "hulu",
    "twitch", "crunchyroll", "youtube",
    "hbo max", "peacock", "paramount+",
]

GAMING_KEYWORDS = [
    "steam", "epic games", "riot", "valorant", "fortnite",
    "gta", "minecraft", "roblox", "league of legends",
]

SPOTIFY_EXE = "spotify.exe"

# Titles that should NEVER be classified as study (our own dashboard, etc.)
SELF_DASHBOARD_KEYWORDS = [
    "focus engine", "localhost:8080", "127.0.0.1:8080",
    "productive-os", "beproductive",
]

# Whitelisted websites loaded from DB
_whitelisted_websites = []
_whitelisted_yt_channels = []

def load_whitelists_from_db():
    """Load whitelisted websites and YouTube channels from settings."""
    global _whitelisted_websites, _whitelisted_yt_channels
    try:
        wl = db.get_setting("whitelisted_websites", "")
        if wl:
            _whitelisted_websites = [w.strip().lower() for w in wl.split(",") if w.strip()]
        ch = db.get_setting("whitelisted_channels", "")
        if ch:
            _whitelisted_yt_channels = [c.strip().lower() for c in ch.split(",") if c.strip()]
    except Exception:
        pass


def classify_activity(app_name: str, window_title: str, is_maximized: bool = True,
                      mode: str = "off") -> str:
    """
    Classify window activity into categories.
    In Study Mode: study only counts when window is maximized.
    In Productive/Off: study counts regardless of maximized state.
    Returns: 'study' | 'gaming' | 'social' | 'entertainment' | 'idle' | 'productivity' | 'other'
    """
    app_lower = app_name.lower()
    title_lower = window_title.lower()

    # Skip system background processes entirely
    if app_lower in SYSTEM_BACKGROUND_PROCESSES:
        return "idle"

    # Lockscreen / screensaver — true idle
    if app_lower in ("lockapp.exe", "logonui.exe"):
        return "idle"

    # No window title = can't determine what user is doing
    if app_lower == "unknown" or not window_title.strip():
        return "idle"

    # Desktop / File Explorer with no meaningful window
    if app_lower == "explorer.exe":
        if "desktop" in title_lower or not window_title.strip():
            return "other"  # Desktop counts as screen time, not idle
        # File Explorer with an actual folder open
        return "productivity"

    # Study mode = strict maximized requirement for study apps
    study_eligible = is_maximized if mode == "study" else True

    # Study apps (IDEs, editors)
    if app_lower in STUDY_APPS:
        return "study" if study_eligible else "productivity"

    # Browser — classify by page title
    if app_lower in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"):
        # Exclude our own dashboard
        for self_kw in SELF_DASHBOARD_KEYWORDS:
            if self_kw in title_lower:
                return "productivity"

        # Check whitelisted websites first
        for domain in _whitelisted_websites:
            if domain in title_lower:
                return "study" if study_eligible else "productivity"

        # Check YouTube with whitelisted channels
        if "youtube" in title_lower:
            for ch in _whitelisted_yt_channels:
                if ch in title_lower:
                    return "study" if study_eligible else "productivity"

        # Check study keywords
        for kw in STUDY_TITLE_KEYWORDS:
            if kw in title_lower:
                return "study" if study_eligible else "productivity"
        # Check social media
        for kw in SOCIAL_MEDIA_KEYWORDS:
            if kw in title_lower:
                return "social"
        # Check entertainment
        for kw in ENTERTAINMENT_KEYWORDS:
            if kw in title_lower:
                return "entertainment"
        # Check gaming
        for kw in GAMING_KEYWORDS:
            if kw in title_lower:
                return "gaming"
        # Default browser = productivity
        return "productivity"

    # Spotify
    if app_lower == SPOTIFY_EXE:
        return "entertainment"

    # Productivity apps
    if app_lower in ("winword.exe", "excel.exe", "powerpnt.exe", "onenote.exe",
                     "outlook.exe", "teams.exe", "zoom.exe", "slack.exe"):
        return "productivity"

    # Check title for gaming
    for kw in GAMING_KEYWORDS:
        if kw in title_lower:
            return "gaming"

    return "other"


# ─── Spotify Title Parser ─────────────────────────────────────────────────

def parse_spotify_title(title: str) -> dict:
    """
    Spotify window title format: "Artist Name - Song Name" or "Spotify Free" / "Spotify Premium".
    We split and assign correctly.
    """
    if not title or title.lower() in ("spotify", "spotify free", "spotify premium"):
        return {"playing": False, "track": "", "artist": ""}

    parts = title.split(" - ", 1)
    if len(parts) == 2:
        return {"playing": True, "track": parts[1].strip(), "artist": parts[0].strip()}
    return {"playing": True, "track": title, "artist": ""}


# ─── Activity Tracker Thread ──────────────────────────────────────────────

class ActivityTracker:
    """Polls foreground window every 2 seconds, accumulates and flushes to DB."""

    def __init__(self, app_killer=None):
        self.app_killer = app_killer
        self._running = False
        self._thread = None
        self._accumulated = {}  # (app, category) -> {seconds, last_title}
        self._spotify_log = []  # In-memory Spotify history (current session)
        self._last_spotify_track = ""  # Last logged track for dedup
        self._spotify_listen_seconds = 0  # Accumulated listening seconds for current track
        self._spotify_db_saved_seconds = 0  # How many seconds already flushed to DB for current track
        self._last_flush = time.time()
        self._study_accumulator = 0   # Seconds of study since last token earn
        self._gaming_accumulator = 0  # Seconds of gaming since last token deduct
        self._study_token_fraction = 0.0
        self._gaming_token_fraction = 0.0
        self._token_earn_rate = int(db.get_setting("token_earn_rate", "30"))
        self._token_deduct_rate = int(db.get_setting("token_deduct_rate", "15"))
        self.on_flush = None           # Callback after data flush (for WS push)
        self.on_spotify_change = None  # Callback when Spotify track changes (immediate WS push)
        self._current_mode = db.get_setting("focus_mode", "off")
        self._recent_activities = []   # [{app, category, title, seconds, last_seen}] ordered by recency

        # Study video token tracking (from extension media heartbeat)
        self._study_video_seconds = 0  # Accumulates verified study watch time
        self._study_video_token_awarded = 0  # Total seconds at which last token was awarded

        # Window manipulation tracking
        self._modified_windows = {}   # {hwnd: original_style} — ALL stripped windows
        self._snap_locked = False
        self._enforcer_thread = None
        self._extensions_locked = False

    def start(self):
        """Start the tracker in a daemon thread."""
        load_whitelists_from_db()
        self._load_spotify_history()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # WinEvent hook thread for instant Study Mode window enforcement
        self._enforcer_thread = threading.Thread(target=self._study_enforcer_loop, daemon=True)
        self._enforcer_thread.start()

    def _load_spotify_history(self):
        """Load today's Spotify tracks from DB so history persists across restarts."""
        try:
            today = datetime.date.today().isoformat()
            db_tracks = db.get_spotify_tracks(today, limit=50)
            if db_tracks:
                # Convert DB format to in-memory format
                self._spotify_log = [
                    {"time": t["time"], "track": t["track"],
                     "artist": t["artist"], "duration": t["duration"]}
                    for t in db_tracks
                ]
                # Set last track as the most recently played
                latest = db_tracks[0]
                self._last_spotify_track = f"{latest['track']} - {latest['artist']}"
                self._spotify_listen_seconds = latest["duration"]
                self._spotify_db_saved_seconds = latest["duration"]  # Already in DB
        except Exception:
            pass

    # ── Extension Page Lock (prevent disabling extension) ─────────────────
    def _lock_extension_pages(self, lock: bool):
        """Block or unblock chrome://extensions and edge://extensions via browser policy.
        
        Uses URLBlocklist registry policy to prevent students from accessing
        the extensions management page and disabling the Focus Engine extension.
        """
        import winreg

        # Policies for Chrome and Edge
        policies = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Google\Chrome\URLBlocklist"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Edge\URLBlocklist"),
        ]
        blocked_urls = [
            "chrome://extensions",
            "chrome://extensions/*",
            "edge://extensions",
            "edge://extensions/*",
        ]

        try:
            if lock:
                for hive, path in policies:
                    try:
                        key = winreg.CreateKey(hive, path)
                        for i, url in enumerate(blocked_urls):
                            winreg.SetValueEx(key, str(i + 100), 0, winreg.REG_SZ, url)
                        winreg.CloseKey(key)
                    except PermissionError:
                        print(f"[!] No permission to write policy: {path}")
                self._extensions_locked = True
                print("[*] Extension pages LOCKED (chrome://extensions blocked)")
            else:
                for hive, path in policies:
                    try:
                        key = winreg.OpenKey(hive, path, 0,
                                            winreg.KEY_SET_VALUE | winreg.KEY_READ)
                        # Remove only our entries (keys 100-103)
                        for i in range(100, 104):
                            try:
                                winreg.DeleteValue(key, str(i))
                            except FileNotFoundError:
                                pass
                        winreg.CloseKey(key)
                    except (FileNotFoundError, PermissionError):
                        pass
                self._extensions_locked = False
                print("[*] Extension pages UNLOCKED")
        except Exception as e:
            print(f"[!] Extension lock error: {e}")


    # ── Registry Snap Lock ────────────────────────────────────────────────
    def _write_snap_registry(self, disable: bool):
        """Write snap-related registry keys WITHOUT restarting Explorer.
        Used for periodic re-enforcement during Study Mode.
        """
        import winreg
        val_str = "0" if disable else "1"
        val_int = 0 if disable else 1

        k1 = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(k1, "WindowArrangementActive", 0, winreg.REG_SZ, val_str)
        winreg.CloseKey(k1)

        k2 = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced")
        winreg.SetValueEx(k2, "SnapAssist", 0, winreg.REG_DWORD, val_int)
        winreg.SetValueEx(k2, "EnableSnapBar", 0, winreg.REG_DWORD, val_int)
        winreg.CloseKey(k2)

    def _apply_snap_lock(self, enable: bool):
        """Enable or disable Windows Snap Assist via Registry + Explorer restart.

        We do NOT use SystemParametersInfoW (undocumented SPI codes corrupt state).
        Registry + Explorer restart is the only reliable method.
        """
        try:
            import subprocess

            self._write_snap_registry(disable=enable)

            # Restart Explorer to apply changes immediately
            subprocess.run(["taskkill", "/F", "/IM", "explorer.exe"],
                           capture_output=True, timeout=5)
            time.sleep(0.5)
            subprocess.Popen("explorer.exe")

            self._snap_locked = enable
            print(f"[*] Snap Assist {'LOCKED' if enable else 'RESTORED'} (Explorer restarted)")
        except Exception as e:
            print(f"[!] Snap lock error: {e}")

    # ── Per-Window Enforce ───────────────────────────────────────────────<br/>
    def _enforce_study_window(self, hwnd: int):
        """Force a window to be maximized and strip its resize/min/max buttons.
        
        Skips style stripping on UWP/modern apps (ApplicationFrameHost children)
        since they render their own chrome and ignore WS_MINIMIZEBOX.
        Strips styles only ONCE per hwnd (tracked in self._modified_windows).
        """
        if not hwnd:
            return
        try:
            u32 = ctypes.windll.user32

            # Step 1: Force maximize ONLY if currently not maximized.
            if not bool(u32.IsZoomed(hwnd)):
                u32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE

            # Step 2: Strip title bar buttons — only once per hwnd, skip UWP
            if hwnd not in self._modified_windows:
                # Detect UWP: class is "ApplicationFrameWindow" or "Windows.UI.Core.CoreWindow"
                buf = ctypes.create_unicode_buffer(64)
                u32.GetClassNameW(hwnd, buf, 64)
                is_uwp = buf.value in ("ApplicationFrameWindow", "Windows.UI.Core.CoreWindow",
                                       "WinUIDesktopWin32WindowClass")

                style = u32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
                if style and not is_uwp:
                    stripped = style & ~0x00020000 & ~0x00010000 & ~0x00040000
                    if stripped != style:
                        self._modified_windows[hwnd] = style  # Save original
                        u32.SetWindowLongW(hwnd, -16, stripped)
                        u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)

        except Exception as e:
            print(f"[!] enforce_study_window error: {e}")

    # ── Restore Everything ───────────────────────────────────────────────
    def _restore_all_windows(self):
        """Restore ALL windows that had their styles stripped during Study Mode.
        
        Two-pass approach:
          1. Restore tracked windows from _modified_windows (exact original style)
          2. Sweep ALL visible windows and re-add WS_MINIMIZEBOX/MAXIMIZEBOX/THICKFRAME
             if they're missing — catches Edge InPrivate, new windows, etc.
        """
        u32 = ctypes.windll.user32
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        WS_THICKFRAME  = 0x00040000
        REQUIRED_FLAGS = WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_THICKFRAME

        # Pass 1: Restore tracked windows with their exact saved style
        for hwnd, original_style in self._modified_windows.items():
            try:
                u32.SetWindowLongW(hwnd, -16, original_style)
                u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)  # SWP_FRAMECHANGED
            except Exception:
                pass
        tracked_count = len(self._modified_windows)
        self._modified_windows.clear()

        # Pass 2: Sweep ALL visible windows and fix any that are missing flags
        sweep_count = 0
        try:
            for win in get_visible_windows():
                hwnd = win["hwnd"]
                try:
                    style = u32.GetWindowLongW(hwnd, -16)
                    if not style:
                        continue
                    # Only fix windows that should have these flags but don't
                    # (Skip windows that naturally lack them, like system trays)
                    # Check: if ANY of the three flags are missing but the window
                    # has WS_CAPTION (0x00C00000), it's a regular app window
                    has_caption = bool(style & 0x00C00000)
                    missing_flags = REQUIRED_FLAGS & ~style
                    if has_caption and missing_flags:
                        fixed_style = style | REQUIRED_FLAGS
                        u32.SetWindowLongW(hwnd, -16, fixed_style)
                        u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
                        # Force title bar redraw
                        u32.RedrawWindow(hwnd, None, None, 0x0401)  # RDW_FRAME | RDW_INVALIDATE
                        sweep_count += 1
                except Exception:
                    pass
        except Exception:
            pass

        total = tracked_count + sweep_count
        if total:
            print(f"[*] Restored styles on {tracked_count} tracked + {sweep_count} swept window(s)")

    def _on_mode_changed(self, old_mode: str, new_mode: str):
        """Called whenever focus_mode changes in the DB."""
        if new_mode == "study":
            self._apply_snap_lock(True)
            self._lock_extension_pages(True)
            print("[*] Study Mode ON — window enforcement active")
        elif new_mode == "productive":
            # Productive mode: no window enforcement, but lock extension pages
            if old_mode == "study":
                self._restore_all_windows()
                self._apply_snap_lock(False)
            self._lock_extension_pages(True)
            print("[*] Productive Mode ON — extension pages locked")
        else:
            # Mode is "off" — lift ALL restrictions
            if old_mode == "study":
                self._restore_all_windows()
                self._apply_snap_lock(False)
            self._lock_extension_pages(False)
            print("[*] Mode OFF — all restrictions lifted")

    def stop(self):
        self._running = False
        self._restore_all_windows()
        if self._snap_locked:
            self._apply_snap_lock(False)
        if self._extensions_locked:
            self._lock_extension_pages(False)

    # ── WinEvent-Driven Study Enforcer ───────────────────────────────────
    def _study_enforcer_loop(self):
        """WinEvent hook-based instant Study Mode enforcer."""
        PROTECTED = {
            "explorer.exe", "searchhost.exe", "searchapp.exe", "searchui.exe",
            "startmenuexperiencehost.exe", "applicationframehost.exe",
            "shellexperiencehost.exe", "systemsettings.exe",
            "taskmgr.exe", "lockapp.exe", "winlogon.exe",
            "textinputhost.exe",
        }
        SYSTEM_WINDOW_CLASSES = {
            "Shell_TrayWnd", "NotifyIconOverflowWindow", "Windows.UI.Core.CoreWindow",
            "ApplicationFrameWindow", "TopLevelWindowForOverflowXamlIsland",
            "SearchPane", "Windows.UI.Search", "MultitaskingViewFrame",
            "WinUIDesktopWin32WindowClass",
        }
        u32 = ctypes.windll.user32
        SW_MAXIMIZE = 3

        # Cache: hwnd -> bool (is_protected). Reset when mode changes.
        _known_protected = set()
        _known_safe = set()

        def is_protected_hwnd(hwnd):
            """Fast protected check with caching to avoid psutil in hot path."""
            if hwnd in _known_protected:
                return True
            if hwnd in _known_safe:
                return False
            try:
                # Check window class name first (no psutil needed, very fast)
                buf = ctypes.create_unicode_buffer(64)
                u32.GetClassNameW(hwnd, buf, 64)
                if buf.value in SYSTEM_WINDOW_CLASSES:
                    _known_protected.add(hwnd)
                    return True

                pid = ctypes.wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                proc = psutil.Process(pid.value)
                app = proc.name().lower()
                if app in PROTECTED or app in SYSTEM_BACKGROUND_PROCESSES:
                    _known_protected.add(hwnd)
                    return True
                _known_safe.add(hwnd)
                return False
            except Exception:
                return True  # Treat unknown as protected (safe default)

        def force_maximize(hwnd):
            """Maximize a window. Try ShowWindow first, fall back to SetWindowPos."""
            if not hwnd or not IsWindowVisible(hwnd):
                return
            if bool(u32.IsZoomed(hwnd)):
                return  # Already maximized, nothing to do
            if is_protected_hwnd(hwnd):
                return
            # Method 1: Standard ShowWindow
            u32.ShowWindow(hwnd, SW_MAXIMIZE)

        def win_event_callback(hook, event, hwnd, id_obj, id_child, thread, time_ms):
            """Fires instantly on any matching window state change."""
            if not self._running or self._current_mode != "study" or not hwnd:
                return
            try:
                force_maximize(hwnd)

                # On foreground change, sweep ALL visible non-maximized windows
                # This catches "restore then quickly switch to another app"
                if event == 0x0003:  # EVENT_SYSTEM_FOREGROUND
                    for w in get_visible_windows():
                        if not w["is_maximized"]:
                            force_maximize(w["hwnd"])
            except Exception:
                pass

        WinEventProcType = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD, ctypes.wintypes.HWND,
            ctypes.wintypes.LONG, ctypes.wintypes.LONG,
            ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
        )
        callback_ptr = WinEventProcType(win_event_callback)

        WINEVENT_OUTOFCONTEXT = 0x0000

        # Separate hooks for each event type we care about:
        hooks = []
        for evt in [
            0x0003,   # EVENT_SYSTEM_FOREGROUND — window activated
            0x0016,   # EVENT_SYSTEM_MINIMIZESTART — minimize attempted
            0x0017,   # EVENT_SYSTEM_MINIMIZEEND — minimize completed (restore)
            0x000A,   # EVENT_SYSTEM_MOVESIZEEND — drag/resize finished
        ]:
            h = u32.SetWinEventHook(evt, evt, None, callback_ptr, 0, 0, WINEVENT_OUTOFCONTEXT)
            if h:
                hooks.append(h)

        # Message pump — WinEvent hooks require a message loop on their thread
        # Also periodically re-enforce snap registry keys to prevent manual override
        msg = ctypes.wintypes.MSG()
        last_snap_check = time.time()
        SNAP_RECHECK = 10  # Re-write snap keys every 10 seconds during study mode

        while self._running:
            result = u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)  # PM_REMOVE
            if result > 0:
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
            else:
                # Periodically re-enforce snap lock during study mode
                now = time.time()
                if self._current_mode == "study" and now - last_snap_check >= SNAP_RECHECK:
                    try:
                        self._write_snap_registry(disable=True)
                    except Exception:
                        pass
                    last_snap_check = now
                time.sleep(0.01)

        for h in hooks:
            u32.UnhookWinEvent(h)

    def _run_loop(self):
        """Main tracking loop — runs every 2 seconds."""
        POLL_INTERVAL = 2
        FLUSH_INTERVAL = 10  # Write to DB every 10 seconds for responsive dashboard
        TOKEN_INTERVAL = 60  # Check tokens every minute
        MODE_REFRESH = 30    # Re-read mode from DB every 30s

        last_token_check = time.time()
        last_mode_check = time.time()
        current_date_str = datetime.date.today().isoformat()

        while self._running:
            try:
                now = time.time()

                # Refresh mode periodically
                if now - last_mode_check >= MODE_REFRESH:
                    new_mode = db.get_setting("focus_mode", "off")
                    if new_mode != self._current_mode:
                        self._on_mode_changed(self._current_mode, new_mode)
                        self._current_mode = new_mode
                    last_mode_check = now

                # Clear recent activity list if a new day begins
                today_str = datetime.date.today().isoformat()
                if today_str != current_date_str:
                    current_date_str = today_str
                    self._recent_activities.clear()
                    # Also write pending spotify session properly to old date? Let's just reset DB cached seconds
                    self._spotify_db_saved_seconds = 0

                info = get_foreground_info()
                fg_hwnd = info.get("hwnd", 0)
                is_minimized = info.get("is_minimized", False)

                # ── Study Mode: Enforce single maximized window ───────────────
                if self._current_mode == "study" and fg_hwnd and not is_minimized:
                    app_lower = info["app"].lower()
                    protected = {
                        "explorer.exe", "searchhost.exe", "searchapp.exe", "searchui.exe",
                        "startmenuexperiencehost.exe", "applicationframehost.exe",
                        "shellexperiencehost.exe", "systemsettings.exe",
                        "taskmgr.exe", "lockapp.exe", "winlogon.exe",
                        "textinputhost.exe",
                    }
                    # Also skip by window class (system overlays have well-known class names)
                    buf = ctypes.create_unicode_buffer(64)
                    ctypes.windll.user32.GetClassNameW(fg_hwnd, buf, 64)
                    system_classes = {
                        "Shell_TrayWnd", "NotifyIconOverflowWindow", "Windows.UI.Core.CoreWindow",
                        "ApplicationFrameWindow", "TopLevelWindowForOverflowXamlIsland",
                        "SearchPane", "Windows.UI.Search",
                        "WinUIDesktopWin32WindowClass",
                    }
                    if app_lower not in protected and app_lower not in SYSTEM_BACKGROUND_PROCESSES \
                            and buf.value not in system_classes:
                        self._enforce_study_window(fg_hwnd)
                elif self._current_mode != "study":
                    # Mode turned off — restore all modified windows
                    if self._modified_windows:
                        self._restore_all_windows()


                # ── Track time for the FOREGROUND window only ─────────────────
                app = info["app"]
                title = info["title"]
                is_maximized = info.get("is_maximized", True)

                if is_minimized or not app or app == "Unknown" or title == "":
                    category = "idle"
                    friendly = "Desktop"
                else:
                    category = classify_activity(app, title, is_maximized, mode=self._current_mode)
                    friendly = get_friendly_name(app)
                    if friendly == "Explorer":
                        friendly = "Desktop"

                if app.lower() not in SYSTEM_BACKGROUND_PROCESSES:
                    # ── Screen-time accumulation (foreground app only) ──
                    if app.lower() not in BROWSER_APPS and category != "idle":
                        key = (friendly, category)
                        if key not in self._accumulated:
                            self._accumulated[key] = {"seconds": 0, "last_title": title}
                        self._accumulated[key]["seconds"] += POLL_INTERVAL
                        self._accumulated[key]["last_title"] = title

                    # ── Update live recent-activity list ──
                    self._update_recent_activities(friendly, category, title, POLL_INTERVAL)

                # ── Spotify tracking ───────────────────────────────────────
                # Detect current track (foreground OR background) for display,
                # and accumulate listen seconds whenever music is playing.
                fg_app = info["app"]
                fg_title = info["title"]
                spotify_is_foreground = fg_app.lower() == SPOTIFY_EXE

                spotify_title = None
                if spotify_is_foreground:
                    spotify_title = fg_title
                else:
                    bg_title = find_spotify_title()
                    if bg_title:
                        spotify_title = bg_title

                if spotify_title:
                    sp_info = parse_spotify_title(spotify_title)
                    if sp_info["playing"]:
                        track_key = f"{sp_info['track']} - {sp_info['artist']}"
                        if track_key != self._last_spotify_track:
                            # New track — save previous track to DB immediately
                            if self._spotify_log and self._spotify_listen_seconds > 0:
                                prev = self._spotify_log[-1]
                                delta = self._spotify_listen_seconds - self._spotify_db_saved_seconds
                                if delta > 0:
                                    try:
                                        db.log_spotify_track(
                                            datetime.date.today().isoformat(),
                                            prev["track"], prev["artist"], delta
                                        )
                                    except Exception:
                                        pass
                                self._spotify_log[-1]["duration"] = self._spotify_listen_seconds

                            # Add new track entry
                            self._spotify_log.append({
                                "time": datetime.datetime.now().isoformat(),
                                "track": sp_info["track"],
                                "artist": sp_info["artist"],
                                "duration": 0,
                            })
                            self._last_spotify_track = track_key
                            self._spotify_listen_seconds = POLL_INTERVAL
                            self._spotify_db_saved_seconds = 0

                            # Fire immediate WS push for new track
                            if self.on_spotify_change:
                                try:
                                    self.on_spotify_change()
                                except Exception:
                                    pass
                        else:
                            # Same track — count listen time (foreground AND background)
                            self._spotify_listen_seconds += POLL_INTERVAL
                            if self._spotify_log:
                                self._spotify_log[-1]["duration"] = self._spotify_listen_seconds

                # ── Token accumulators ─────────────────────────────────────────
                if category == "study":
                    self._study_accumulator += POLL_INTERVAL
                elif category == "gaming":
                    self._gaming_accumulator += POLL_INTERVAL

                # ── Periodic flush to DB ───────────────────────────────────────
                if now - self._last_flush >= FLUSH_INTERVAL:
                    self._flush_to_db()
                    self._last_flush = now

                # ── Token calculation every minute ─────────────────────────────
                if now - last_token_check >= TOKEN_INTERVAL:
                    self._process_tokens()
                    last_token_check = now

            except Exception as e:
                print(f"  [!] Tracker error: {e}")

            time.sleep(POLL_INTERVAL)

    def _update_recent_activities(self, app: str, category: str, title: str, poll_interval: int = 2):
        """
        Maintain an ordered recent-activity list.
        If app already in list: update seconds + title, move to position 0.
        If new: insert at position 0. Cap at 20 entries.
        """
        MAX_ITEMS = 20
        now_iso = datetime.datetime.now().isoformat()

        existing_idx = None
        for i, entry in enumerate(self._recent_activities):
            if entry["app"] == app:
                existing_idx = i
                break

        if existing_idx is not None:
            entry = self._recent_activities.pop(existing_idx)
            entry["seconds"] += poll_interval
            entry["title"] = title
            entry["category"] = category
            entry["last_seen"] = now_iso
            self._recent_activities.insert(0, entry)
        else:
            self._recent_activities.insert(0, {
                "app": app,
                "category": category,
                "title": title,
                "seconds": poll_interval,
                "last_seen": now_iso,
            })
            if len(self._recent_activities) > MAX_ITEMS:
                self._recent_activities.pop()

    def _flush_to_db(self):
        """Write accumulated screen-time and Spotify data to SQLite."""
        try:
            for (app_friendly, category), data in self._accumulated.items():
                if data["seconds"] > 0:
                    db.log_screen_time(app_friendly, data["last_title"], category, data["seconds"])
            self._accumulated.clear()

            # Save Spotify delta to DB (only seconds not yet persisted)
            if self._spotify_log and self._last_spotify_track:
                current = self._spotify_log[-1]
                delta = self._spotify_listen_seconds - self._spotify_db_saved_seconds
                if delta > 0:
                    try:
                        db.log_spotify_track(
                            datetime.date.today().isoformat(),
                            current["track"], current["artist"], delta
                        )
                        self._spotify_db_saved_seconds = self._spotify_listen_seconds
                    except Exception:
                        pass

            # Update daily summary
            today = datetime.date.today().isoformat()
            try:
                db.update_daily_summary(today)
            except Exception:
                pass

            # Notify API server to push updated data to clients
            if self.on_flush:
                try:
                    self.on_flush()
                except Exception:
                    pass
        except Exception as e:
            print(f"  [!] Tracker flush error (will retry next cycle): {e}")

    def _process_tokens(self):
        """Earn tokens for study, deduct for gaming."""
        if self._study_accumulator >= 60:
            minutes_studied = self._study_accumulator / 60
            self._study_token_fraction += minutes_studied * (self._token_earn_rate / 60)
            whole_tokens = int(self._study_token_fraction)
            if whole_tokens > 0:
                db.earn_tokens(whole_tokens, "study_time")
                self._study_token_fraction -= whole_tokens
            self._study_accumulator = 0

        if self._gaming_accumulator >= 60:
            minutes_gamed = self._gaming_accumulator / 60
            self._gaming_token_fraction += minutes_gamed * (self._token_deduct_rate / 60)
            whole_tokens = int(self._gaming_token_fraction)
            if whole_tokens > 0:
                db.spend_tokens(whole_tokens, "gaming_time")
                self._gaming_token_fraction -= whole_tokens
            self._gaming_accumulator = 0

    def on_study_media_tick(self, seconds: int):
        """Called by api_server when extension confirms study video is playing.
        
        Conditions already verified by the extension:
          1. Mode is study
          2. Window is maximized/fullscreen
          3. Media (<video>/<audio>) is actively playing
          4. Domain is study-classified
        
        Awards 1 token every 120 seconds (2 minutes) of verified watch time.
        """
        self._study_video_seconds += seconds
        next_threshold = self._study_video_token_awarded + 120

        if self._study_video_seconds >= next_threshold:
            tokens_to_award = (self._study_video_seconds - self._study_video_token_awarded) // 120
            if tokens_to_award > 0:
                db.earn_tokens(tokens_to_award, "study_video")
                self._study_video_token_awarded += tokens_to_award * 120
                print(f"[*] Study video token: +{tokens_to_award} (total watch: {self._study_video_seconds}s)")

    def get_current_activity(self) -> dict:
        """Return what's happening right now (for live dashboard feed)."""
        info = get_foreground_info()
        is_minimized = info.get("is_minimized", False)

        if is_minimized:
            app_display = "Desktop"
            title = "Desktop (minimized window)"
            category = "idle"
        else:
            app_display = get_friendly_name(info["app"])
            title = info["title"]
            category = classify_activity(info["app"], info["title"],
                                         info.get("is_maximized", True),
                                         mode=self._current_mode)

        spotify = None
        if info["app"].lower() == SPOTIFY_EXE:
            spotify = parse_spotify_title(info["title"])
        else:
            bg_title = find_spotify_title()
            if bg_title:
                spotify = parse_spotify_title(bg_title)

        return {
            "app": app_display,
            "title": title,
            "category": category,
            "spotify": spotify,
            "gaming_minutes": 0,
        }

    def get_recent_activities(self) -> list:
        """Return the recent-activity list ordered by most recent first."""
        return self._recent_activities[:20]

    def get_spotify_history(self) -> list:
        """Return Spotify listening history from DB (persisted across restarts).
        Falls back to in-memory log if DB is unavailable.
        """
        today = datetime.date.today().isoformat()
        try:
            db_tracks = db.get_spotify_tracks(today, limit=50)
            if db_tracks:
                # Prepend current playing track if not yet in DB
                if self._spotify_log and self._last_spotify_track:
                    current = self._spotify_log[-1]
                    in_db = any(
                        t["track"] == current["track"] and t["artist"] == current["artist"]
                        for t in db_tracks
                    )
                    if not in_db:
                        db_tracks.insert(0, {
                            "time": current["time"],
                            "track": current["track"],
                            "artist": current["artist"],
                            "duration": self._spotify_listen_seconds,
                        })
                return db_tracks
        except Exception:
            pass
        return self._spotify_log[-50:]
