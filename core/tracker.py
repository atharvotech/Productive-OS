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

    def __init__(self):
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

    def start(self):
        """Start the tracker in a daemon thread."""
        load_whitelists_from_db()
        self._load_spotify_history()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

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

    def __init__(self, app_killer=None):
        self.app_killer = app_killer
        self._running = False
        self._thread = None
        self._accumulated = {}
        self._recent_activities = []
        self._last_flush = time.time()
        self._current_mode = db.get_setting("focus_mode", "off")
        self._spotify_log = []
        self._last_spotify_track = ""
        self._spotify_listen_seconds = 0
        self._spotify_db_saved_seconds = 0
        self._study_accumulator = 0
        self._gaming_accumulator = 0
        self.on_spotify_change = None

    def _on_mode_changed(self, old_mode, new_mode):
        """Called when focus_mode changes. Handles Registry settings and broadcasts immediately."""
        try:
            import winreg
            import ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE)
            
            SPI_SETSNAPSIZING = 0x008F
            SPI_SETDOCKMOVING = 0x0091
            SPIF_UPDATEINIFILE = 0x01
            SPIF_SENDWININICHANGE = 0x02
            SPIF_FLAGS = SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE
            
            disable = (new_mode == "study")
            val = "0" if disable else "1"
            winreg.SetValueEx(key, "WindowArrangementActive", 0, winreg.REG_SZ, val)
            winreg.CloseKey(key)

            # Apply instantly using SystemParametersInfo
            param_val = 0 if disable else 1
            ctypes.windll.user32.SystemParametersInfoW(SPI_SETSNAPSIZING, param_val, 0, SPIF_FLAGS)
            ctypes.windll.user32.SystemParametersInfoW(SPI_SETDOCKMOVING, param_val, 0, SPIF_FLAGS)
            
            # Broadcast registry change to Explorer for hotkey suspension
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            res = ctypes.c_ulong()
            ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "WindowArrangementActive", SMTO_ABORTIFHUNG, 5000, ctypes.byref(res))
            print(f"[*] Snap Assist {'Disabled' if disable else 'Enabled'} globally.")
        except Exception as e:
            print(f"[!] Registry error toggling snap assist: {e}")

    def stop(self):
        self._running = False

    def _run_loop(self):
        """Main tracking loop — runs every 2 seconds."""
        POLL_INTERVAL = 2
        FLUSH_INTERVAL = 30  # Write to DB every 30 seconds
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

                # Study Mode: Only one app at a time - programmatic minimizing removed due to UWP app instability.
                # (We will rely on registry snap assist disabling if requested).

                # Get all visible windows to track concurrently
                active_windows = get_visible_windows() if not is_minimized else [info]
                if not active_windows:
                    active_windows = [info]

                # Log total active time explicitly (1 second per 1 real second!)
                # We log this under 'Focus_Engine_Global_Active' to ensure the total time doesn't multiply!
                if not is_minimized and info["app"] != "explorer.exe" and info["title"]:
                    global_key = ("Focus_Engine_Global_Active", "system")
                    if global_key not in self._accumulated:
                        self._accumulated[global_key] = {"seconds": 0, "last_title": "Global Time"}
                    self._accumulated[global_key]["seconds"] += POLL_INTERVAL

                for win in active_windows:
                    app = win["app"]
                    title = win["title"]
                    is_maximized = win.get("is_maximized", True)
                    win_is_minimized = win.get("is_minimized", False)

                    if win_is_minimized or not app or app == "Unknown" or title == "":
                        category = "idle"
                        app = "explorer.exe"
                        title = "Desktop"
                    else:
                        category = classify_activity(app, title, is_maximized, mode=self._current_mode)

                    if app.lower() in SYSTEM_BACKGROUND_PROCESSES:
                        continue

                    friendly = get_friendly_name(app)
                    if friendly == "Explorer":
                        friendly = "Desktop"

                    # ── Screen-time accumulation ──
                    if app.lower() not in BROWSER_APPS and category != "idle":
                        key = (friendly, category)
                        if key not in self._accumulated:
                            self._accumulated[key] = {"seconds": 0, "last_title": title}
                        self._accumulated[key]["seconds"] += POLL_INTERVAL
                        self._accumulated[key]["last_title"] = title

                    # ── Update live recent-activity list ──
                    if win["hwnd"] == fg_hwnd:
                        self._update_recent_activities(friendly, category, title, POLL_INTERVAL)

                # Spotify tracking (use foreground info for logic flow to avoid duplicate triggers)
                app = info["app"]
                title = info["title"]

                # ── Spotify tracking (even when Spotify is in background) ──────
                spotify_title = None
                if app.lower() == SPOTIFY_EXE:
                    spotify_title = title
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
