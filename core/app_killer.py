"""
Focus Engine Pro — Smart App Killer (v2, Event-Driven)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Two complementary mechanisms, zero polling:

  1. WMI Win32_ProcessStartTrace  — blocks until any new .exe launches;
     instantly checks and kills it if it is a game in focus mode.
  2. SetWinEventHook EVENT_SYSTEM_FOREGROUND — kills a blacklisted window
     the instant it becomes foreground, before the user can interact.

No CPU/GPU heuristics — safe for DaVinci Resolve, Blender, etc.
"""

import os
import re
import time
import ctypes
import ctypes.wintypes
import threading
import psutil
from core import database as db

# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------
_user32 = ctypes.windll.user32
GetWindowThreadProcessId = _user32.GetWindowThreadProcessId


class AppKiller:
    """Event-driven game detection and process termination."""

    # ── Known game publishers (exe CompanyName metadata) ──────────────────
    GAME_PUBLISHERS = [
        "riot games", "epic games", "valve", "rockstar games",
        "ubisoft", "electronic arts", "activision", "blizzard",
        "bethesda", "cd projekt", "square enix", "capcom",
        "bandai namco", "sega", "konami", "2k games",
        "deep silver", "thq nordic", "paradox interactive", "supergiant games",
        "mojang", "mihoyo", "hoyoverse", "garena",
        "tencent", "netease games", "krafton", "nexon",
        "ncsoft", "wargaming", "gameloft", "king",
        "supercell", "innersloth", "mediatonic", "grinding gear games",
    ]

    # ── Regex patterns matched against exe name OR full path ──────────────
    GAME_EXE_PATTERNS = [
        r"(?i)\bvalorant\b", r"(?i)\bfortnite\b", r"(?i)\bgta[v5\-_ ]",
        r"(?i)\bcsgo\b|\bcs2\b", r"(?i)\bpubg\b", r"(?i)\bapex(?:legends)?",
        r"(?i)\boverwatch\b", r"(?i)\bminecraft\b", r"(?i)\broblox(?:player)?\b",
        r"(?i)\bcall.?of.?duty\b|\bcod\b", r"(?i)\bdota\b",
        r"(?i)\bleague.?of.?legends\b|\bleagueclient\b",
        r"(?i)\brocket.?league\b", r"(?i)\brainbow.?six\b|\br6\b",
        r"(?i)\bdestiny\b", r"(?i)\bwarframe\b", r"(?i)\brust\b(?!c)",
        r"(?i)\bamong.?us\b", r"(?i)\bfall.?guys\b",
        r"(?i)\bgenshin\b", r"(?i)\bhonkai\b", r"(?i)\belden.?ring\b",
        r"(?i)\bdark.?souls\b", r"(?i)\bcyberpunk\b", r"(?i)\bwitcher\b",
        r"(?i)\bbattlefield\b|\bbf[0-9]", r"(?i)\bfifa\b|\bea.?fc\b|\bfc[0-9]",
        r"(?i)\bnba.?2k\b", r"(?i)\bmadden\b", r"(?i)\bcivilization\b",
        r"(?i)freefire|free.?fire.?max", r"(?i)\bpubgmobile\b",
        r"(?i)\bclash.?(?:of.?clans|royale)\b",
        r"(?i)\bworldofwarcraft\b|\bwow\.exe\b",
        r"(?i)\bdiablo\b", r"(?i)\bhearthstone\b", r"(?i)\bstarcraft\b",
        r"(?i)\bheroesofthestorm\b", r"(?i)\bpathofexile\b",
        r"(?i)\bterrariacontent\b|\bterraria\b",
        # Google Play Games process pattern
        r"(?i)\bgoogleplaygames\b", r"(?i)\bgamemanagerservice\b",
    ]

    # ── Android emulators / game launchers — always blocked in focus mode ─
    GAMING_LAUNCHERS = {
        # Google Play Games for PC
        "googleplaygames.exe", "gamemanagerservice.exe", "crosvm.exe",
        # BlueStacks
        "hd-player.exe", "bluestacks.exe", "bstksvc.exe",
        "bstkagent.exe", "bluestackshelper.exe",
        # LDPlayer
        "ldplayer.exe", "dnplayer.exe", "dnmultiplayer.exe", "ldvboxheadless.exe",
        # NoxPlayer
        "nox.exe", "noxvmmhandle.exe", "noxvm.exe", "noxvmhandle.exe",
        # MuMu Player
        "mumumanager.exe", "mumuvmm.exe", "mumuplayer.exe", "mumuvmmheadless.exe",
        # MEmu
        "memu.exe", "memusvc.exe", "memuplayer.exe",
        # GameLoop (Tencent)
        "gameloop.exe", "txgameassistant.exe", "txgamedaemon.exe",
    }

    # ── Apps that must NEVER be killed ────────────────────────────────────
    STUDY_APPS = {
        "code.exe", "code - insiders.exe", "devenv.exe",
        "pycharm64.exe", "pycharm.exe", "idea64.exe", "idea.exe",
        "sublime_text.exe", "notepad++.exe", "atom.exe",
        "windowsterminal.exe", "powershell.exe", "cmd.exe",
        "git-bash.exe", "bash.exe", "wsl.exe",
        "cursor.exe", "windsurf.exe",
        "winword.exe", "excel.exe", "powerpnt.exe",
        "onenote.exe", "teams.exe", "outlook.exe",
        "msedge.exe", "chrome.exe", "firefox.exe", "brave.exe",
        "explorer.exe", "notepad.exe",
        "spotify.exe", "discord.exe",
        # Creative / professional apps — heavy CPU/GPU is normal here
        "resolve.exe",                 # DaVinci Resolve
        "davinci resolve.exe",
        "blender.exe",
        "premiere pro.exe", "afterfx.exe",
        "photoshop.exe", "illustrator.exe", "indesign.exe",
        "figma.exe", "xd.exe",
        "obs64.exe", "obs32.exe",
        "audacity.exe",
        "fl64.exe", "fl32.exe",        # FL Studio
        "ableton live.exe",
        "reaper.exe",
        "maya.exe", "3dsmax.exe", "cinema 4d.exe",
        "unity.exe", "unityeditor.exe", "unityeditor64.exe",
        "unrealEditor.exe",            # Unreal Engine editor (not a shipped game)
    }

    DEFAULT_WHITELIST = {
        "steam.exe", "steamwebhelper.exe", "steamservice.exe",
        "desktopmate.exe",
        "epicgameslauncher.exe", "unrealcefsubprocess.exe",
        "touchpointanalyticsclientsservice.exe",
        "svchost.exe", "services.exe", "csrss.exe", "lsass.exe",
        "wininit.exe", "winlogon.exe", "dwm.exe", "taskhostw.exe",
        "runtimebroker.exe", "sihost.exe", "fontdrvhost.exe",
        "searchhost.exe", "startmenuexperiencehost.exe",
        "textinputhost.exe", "shellexperiencehost.exe",
        "applicationframehost.exe", "systemsettings.exe",
        "securityhealthservice.exe", "msmpeng.exe",
        "smartscreen.exe", "ctfmon.exe", "conhost.exe",
        "dllhost.exe", "msiexec.exe", "wmiprvse.exe",
        # Never kill our own Python process
        "python.exe", "pythonw.exe",
    }

    SAFE_EXE_PATHS = [
        r"c:\windows",
        r"c:\program files\common files",
        r"c:\program files (x86)\common files",
        r"c:\programdata\microsoft",
    ]

    # ── Init ──────────────────────────────────────────────────────────────

    def __init__(self):
        self.steam_game_exes: set = set()
        self.google_play_game_exes: set = set()
        self.user_whitelist: set = set()
        self.user_blacklist: set = set()
        self.gaming_start_time = None
        self.warned_at_20 = False
        self._running = False
        self._wmi_thread = None
        self._hook_thread = None

        self._load_settings()
        self._scan_steam_library()
        self._scan_google_play_games()

    # ── Settings ──────────────────────────────────────────────────────────

    def _load_settings(self):
        try:
            wl = db.get_setting("whitelisted_apps", "")
            bl = db.get_setting("blocked_apps_custom", "")
            if wl:
                self.user_whitelist = {x.strip().lower() for x in wl.split(",") if x.strip()}
            if bl:
                self.user_blacklist = {x.strip().lower() for x in bl.split(",") if x.strip()}
        except Exception:
            pass

    def reload_settings(self):
        self._load_settings()

    # ── Library Scanners ──────────────────────────────────────────────────

    def _scan_steam_library(self):
        """Scan Steam steamapps/common for installed game executables."""
        safe_exes = {
            "steam.exe", "steamwebhelper.exe", "steamservice.exe",
            "uninstall.exe", "uninst.exe", "setup.exe",
            "crashhandler.exe", "crashhandler64.exe",
            "vc_redist.exe", "vc_redist.x64.exe", "vc_redist.x86.exe",
            "dxsetup.exe", "dotnetfx.exe",
        }
        for sp in self._find_steam_paths():
            common_dir = os.path.join(sp, "steamapps", "common")
            if not os.path.isdir(common_dir):
                continue
            for game_folder in os.listdir(common_dir):
                game_path = os.path.join(common_dir, game_folder)
                if not os.path.isdir(game_path):
                    continue
                for root, _dirs, files in os.walk(game_path):
                    for f in files:
                        if f.lower().endswith(".exe"):
                            self.steam_game_exes.add(f.lower())
                    if root.count(os.sep) - game_path.count(os.sep) > 3:
                        break
        self.steam_game_exes -= safe_exes
        self.steam_game_exes -= self.DEFAULT_WHITELIST
        if self.steam_game_exes:
            print(f"  [+] Steam scanner: {len(self.steam_game_exes)} game exes indexed")

    def _find_steam_paths(self) -> list:
        paths = []
        default = r"C:\Program Files (x86)\Steam"
        if os.path.isdir(default):
            paths.append(default)
        try:
            import winreg
            key = winreg.OpenKeyEx(winreg.HKEY_LOCAL_MACHINE,
                                   r"SOFTWARE\WOW6432Node\Valve\Steam",
                                   0, winreg.KEY_READ)
            val, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            if val and os.path.isdir(val) and val not in paths:
                paths.append(val)
        except Exception:
            pass
        # Additional library folders from VDF
        for sp in list(paths):
            vdf = os.path.join(sp, "steamapps", "libraryfolders.vdf")
            if os.path.isfile(vdf):
                try:
                    with open(vdf, "r", encoding="utf-8") as f:
                        content = f.read()
                    for match in re.findall(r'"path"\s+"([^"]+)"', content):
                        p = match.replace("\\\\", "\\")
                        if os.path.isdir(p) and p not in paths:
                            paths.append(p)
                except Exception:
                    pass
        return paths

    def _scan_google_play_games(self):
        """Scan Google Play Games PC installation for game executables."""
        gpg_root = os.path.expandvars(r"%LOCALAPPDATA%\Google\Play Games")
        if not os.path.isdir(gpg_root):
            return
        self.google_play_game_exes.add("googleplaygames.exe")
        for root, _dirs, files in os.walk(gpg_root):
            for f in files:
                if f.lower().endswith(".exe"):
                    self.google_play_game_exes.add(f.lower())
            if root.count(os.sep) - gpg_root.count(os.sep) > 5:
                break
        # Remove anything that is genuinely safe
        self.google_play_game_exes -= self.DEFAULT_WHITELIST
        self.google_play_game_exes -= self.STUDY_APPS
        print(f"  [+] Google Play Games scanner: {len(self.google_play_game_exes)} exes indexed")

    # ── Game Detection ─────────────────────────────────────────────────────

    def is_game_process(self, proc) -> bool:
        """
        Determine whether a process is a game.
        No CPU/GPU heuristics — safe for DaVinci Resolve, Blender, etc.
        """
        try:
            name = proc.name().lower()
            exe_path = ""
            try:
                exe_path = proc.exe().lower()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

        # ── Whitelists (never kill these) ────────────────────────────────
        all_whitelist = self.STUDY_APPS | self.DEFAULT_WHITELIST | self.user_whitelist
        if name in all_whitelist:
            return False

        if exe_path:
            for safe in self.SAFE_EXE_PATHS:
                if exe_path.startswith(safe):
                    return False

        # ── Definite game signals ─────────────────────────────────────────
        if name in self.user_blacklist:
            return True

        if name in self.GAMING_LAUNCHERS:
            return True

        if name in self.google_play_game_exes:
            return True

        if name in self.steam_game_exes:
            return True

        for pattern in self.GAME_EXE_PATTERNS:
            if re.search(pattern, name) or (exe_path and re.search(pattern, exe_path)):
                return True

        # ── Exe metadata: CompanyName ────────────────────────────────────
        if exe_path and os.path.isfile(exe_path):
            publisher = self._get_exe_publisher(exe_path)
            if publisher:
                pub_lower = publisher.lower()
                for known_pub in self.GAME_PUBLISHERS:
                    if known_pub in pub_lower:
                        return True

        return False

    def _get_exe_publisher(self, exe_path: str) -> str:
        try:
            import win32api
            lc = win32api.GetFileVersionInfo(exe_path, r"\VarFileInfo\Translation")
            if lc:
                lang, cp = lc[0]
                return win32api.GetFileVersionInfo(
                    exe_path, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\CompanyName"
                ) or ""
        except Exception:
            pass
        return ""

    # ── Kill helpers ───────────────────────────────────────────────────────

    def _kill_proc(self, proc, reason: str = ""):
        """Terminate + force-kill a process. Logs the action."""
        try:
            name = proc.name()
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except psutil.TimeoutExpired:
                proc.kill()
            tokens = db.get_token_balance()
            db.log_killed_process(name, reason or f"focus mode active, tokens={tokens}")
            print(f"  [💀] Killed: {name}  ({reason})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def _handle_game_detected(self, proc):
        """Called whenever a game process is found — kill or track depending on mode."""
        focus_mode = db.get_setting("focus_mode", "off")

        # ── CRITICAL FIX: "study" mode must also kill games ──────────────
        if focus_mode in ("study", "on"):
            self._kill_proc(proc, f"game detected in {focus_mode} mode")
            return

        # Mode is off — just track gaming time for auto-focus logic
        if self.gaming_start_time is None:
            self.gaming_start_time = time.time()

        gaming_minutes = (time.time() - self.gaming_start_time) / 60
        threshold = int(db.get_setting("auto_focus_threshold_min", "30"))
        token_balance = db.get_token_balance()

        if gaming_minutes >= threshold and token_balance <= 0:
            db.set_setting("focus_mode", "study")
            print(f"  [⚠] AUTO FOCUS: Gaming {int(gaming_minutes)}min, 0 tokens → Study Mode")
            self._kill_proc(proc, "auto focus triggered")
            return

        if gaming_minutes >= 20 and not self.warned_at_20:
            self.warned_at_20 = True
            db.set_setting("gaming_warning",
                           f"You've been gaming for {int(gaming_minutes)} minutes!")
            print(f"  [⚠] Gaming warning: {int(gaming_minutes)} minutes")

    # ── Event-Driven Engine ────────────────────────────────────────────────

    def start(self):
        """Start event-driven monitoring. Non-blocking — returns immediately."""
        self._running = True
        # Scan processes already running at startup
        self._startup_scan()
        # WMI: catches new process launches
        self._wmi_thread = threading.Thread(
            target=self._wmi_process_watcher, daemon=True, name="AppKiller-WMI"
        )
        self._wmi_thread.start()
        # WinEvent hook: kills a game window the instant it becomes foreground
        self._hook_thread = threading.Thread(
            target=self._foreground_hook_loop, daemon=True, name="AppKiller-Hook"
        )
        self._hook_thread.start()
        print("  [+] App killer active (WMI process-start + WinEvent foreground hook)")

    def stop(self):
        self._running = False

    def _startup_scan(self):
        """One-time scan of already-running processes."""
        count = 0
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if self.is_game_process(proc):
                    count += 1
                    self._handle_game_detected(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if count:
            print(f"  [+] Startup scan: actioned {count} game process(es)")

    # ── WMI: fires once per new process launch ────────────────────────────

    def _wmi_process_watcher(self):
        """
        Block-waits on WMI Win32_ProcessStartTrace.
        The watcher() call sleeps inside the OS until any .exe starts —
        idle CPU usage is effectively zero.
        Falls back to a 3-second polling loop if WMI/pythoncom is absent.
        """
        try:
            import pythoncom
            import wmi as _wmi
            pythoncom.CoInitialize()
            try:
                c = _wmi.WMI()
                watcher = c.Win32_ProcessStartTrace.watch_for()
                print("  [+] WMI Win32_ProcessStartTrace watcher running")
                while self._running:
                    try:
                        event = watcher(timeout_ms=2000)
                        if event:
                            self._on_process_started(event.ProcessName,
                                                     event.ProcessID)
                    except _wmi.x_wmi_timed_out:
                        pass  # No new process in last 2 s — perfectly normal
                    except Exception:
                        time.sleep(0.5)
            finally:
                pythoncom.CoUninitialize()

        except ImportError:
            print("  [!] wmi/pythoncom not found — using 3 s poll fallback")
            self._poll_fallback()
        except Exception as e:
            print(f"  [!] WMI watcher crashed ({e}) — using 3 s poll fallback")
            self._poll_fallback()

    def _on_process_started(self, exe_name: str, pid: int):
        """Called by WMI watcher for every new process launch."""
        try:
            proc = psutil.Process(pid)
            if self.is_game_process(proc):
                self._handle_game_detected(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def _poll_fallback(self):
        """3-second polling fallback when WMI is unavailable."""
        _seen: set = set()
        while self._running:
            try:
                for proc in psutil.process_iter(["pid", "name"]):
                    try:
                        pid = proc.pid
                        if pid in _seen:
                            continue
                        _seen.add(pid)
                        if self.is_game_process(proc):
                            self._handle_game_detected(proc)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                # Prune dead PIDs every 30 s to avoid unbounded growth
                if len(_seen) > 2000:
                    alive = {p.pid for p in psutil.process_iter(["pid"])}
                    _seen &= alive
            except Exception:
                pass
            time.sleep(3)

    # ── WinEvent hook: kills game the instant it gets foreground focus ─────

    def _foreground_hook_loop(self):
        """
        Installs a WinEvent hook for EVENT_SYSTEM_FOREGROUND (0x0003).
        When any window gains focus, we check if it belongs to a game;
        if so, kill it immediately — before the user even sees a frame.
        This loop also runs the Win32 message pump required by WinEvent hooks.
        """
        WinEventProcType = ctypes.WINFUNCTYPE(
            None,
            ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LONG, ctypes.wintypes.LONG,
            ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
        )

        def _on_foreground(hook, event, hwnd, id_obj, id_child, thread, time_ms):
            if not self._running or not hwnd:
                return
            focus_mode = db.get_setting("focus_mode", "off")
            if focus_mode not in ("study", "on"):
                return
            try:
                pid = ctypes.wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                proc = psutil.Process(pid.value)
                if self.is_game_process(proc):
                    self._kill_proc(proc, "game window gained foreground in focus mode")
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                pass

        cb_ptr = WinEventProcType(_on_foreground)
        WINEVENT_OUTOFCONTEXT = 0x0000
        EVENT_SYSTEM_FOREGROUND = 0x0003

        hook = _user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
            None, cb_ptr, 0, 0, WINEVENT_OUTOFCONTEXT
        )
        if not hook:
            print("  [!] SetWinEventHook failed — foreground kill disabled")
            return

        print("  [+] WinEvent foreground hook installed")
        msg = ctypes.wintypes.MSG()
        while self._running:
            result = _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if result > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)

        _user32.UnhookWinEvent(hook)

    # ── Public helpers ─────────────────────────────────────────────────────

    def get_gaming_minutes(self) -> float:
        if self.gaming_start_time is None:
            return 0.0
        return (time.time() - self.gaming_start_time) / 60

    def reset_gaming_session(self):
        self.gaming_start_time = None
        self.warned_at_20 = False
        db.set_setting("gaming_warning", "")

    def get_blacklist(self) -> dict:
        return {
            "user_blacklist": sorted(self.user_blacklist),
            "user_whitelist": sorted(self.user_whitelist),
            "steam_games_detected": len(self.steam_game_exes),
            "google_play_games_detected": len(self.google_play_game_exes),
            "default_whitelist": sorted(self.DEFAULT_WHITELIST),
        }

    def update_blacklist(self, apps: list):
        self.user_blacklist = {a.strip().lower() for a in apps if a.strip()}
        db.set_setting("blocked_apps_custom", ",".join(sorted(self.user_blacklist)))

    def update_whitelist(self, apps: list):
        self.user_whitelist = {a.strip().lower() for a in apps if a.strip()}
        db.set_setting("whitelisted_apps", ",".join(sorted(self.user_whitelist)))

    # ── Legacy compatibility shim ──────────────────────────────────────────
    def hunt_and_kill(self):
        """
        Kept for backward-compatibility only.
        The new engine is fully event-driven; this is a no-op.
        Call start() once at engine startup instead.
        """
        pass