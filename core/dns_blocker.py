"""
Focus Engine Pro — DNS Blocker & Incognito Guard
Manages Windows DNS settings to block adult/distraction content,
and uses the Registry to disable incognito mode in Chromium browsers.
Requires Administrator privileges to operate.
"""

import subprocess
import winreg


# ── Safe, family-safe public DNS servers ──────────────────────────────────
# AdGuard Family DNS — blocks malware + adult content (Less likely to be blocked by ISPs)
SAFE_DNS_PRIMARY   = "94.140.14.15"
SAFE_DNS_SECONDARY = "94.140.15.16"

# Google public DNS — used as the restore target (neutral)
DEFAULT_DNS_PRIMARY   = "8.8.8.8"
DEFAULT_DNS_SECONDARY = "8.8.4.4"


# ── Registry paths for incognito blocking ────────────────────────────────
_INCOGNITO_POLICIES = {
    "chrome": (
        r"SOFTWARE\Policies\Google\Chrome",
        "IncognitoModeAvailability",
    ),
    "brave": (
        r"SOFTWARE\Policies\BraveSoftware\Brave",
        "IncognitoModeAvailability",
    ),
    "msedge": (
        r"SOFTWARE\Policies\Microsoft\Edge",
        "InPrivateModeAvailability",
    ),
}

# Value 1 = incognito/InPrivate disabled
_INCOGNITO_BLOCK_VALUE = 1


class DNSBlocker:
    """Manages DNS settings and browser incognito blocking."""

    def __init__(self):
        self._enabled = False

    # ── Public state ──────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """Return True if safe DNS is currently active."""
        return self._enabled

    # ── DNS management ────────────────────────────────────────────────────

    def enable_safe_mode(self):
        """
        Set all active network adapters to Cloudflare Family DNS.
        Blocks adult content and malware at the DNS level across all browsers.
        """
        adapters = self._get_network_adapters()
        if not adapters:
            print("  [!] DNS Blocker: No active network adapters found.")
            return

        success = False
        for adapter in adapters:
            try:
                # Set primary DNS
                subprocess.run(
                    ["netsh", "interface", "ip", "set", "dns",
                     f"name={adapter}", "static", SAFE_DNS_PRIMARY],
                    capture_output=True, check=True
                )
                # Set secondary DNS
                subprocess.run(
                    ["netsh", "interface", "ip", "add", "dns",
                     f"name={adapter}", SAFE_DNS_SECONDARY, "index=2"],
                    capture_output=True, check=True
                )
                success = True
                print(f"  [+] DNS Blocker: Safe DNS set on adapter '{adapter}'")
            except subprocess.CalledProcessError as e:
                print(f"  [!] DNS Blocker: Failed to set DNS on '{adapter}': {e}")

        if success:
            self._enabled = True

    def disable_safe_mode(self):
        """
        Restore network adapters to automatic (DHCP) DNS.
        Removes the safe DNS restriction.
        """
        adapters = self._get_network_adapters()
        for adapter in adapters:
            try:
                subprocess.run(
                    ["netsh", "interface", "ip", "set", "dns",
                     f"name={adapter}", "dhcp"],
                    capture_output=True, check=True
                )
                print(f"  [+] DNS Blocker: DNS restored to DHCP on '{adapter}'")
            except subprocess.CalledProcessError as e:
                print(f"  [!] DNS Blocker: Failed to restore DNS on '{adapter}': {e}")

        self._enabled = False

    # ── Incognito blocking ────────────────────────────────────────────────

    def block_incognito(self):
        """
        Write Registry policies to disable incognito/InPrivate in Chrome,
        Brave, and Edge. Requires write access to HKEY_LOCAL_MACHINE.
        """
        for browser, (key_path, value_name) in _INCOGNITO_POLICIES.items():
            self._write_registry_dword(
                winreg.HKEY_LOCAL_MACHINE, key_path,
                value_name, _INCOGNITO_BLOCK_VALUE
            )
            print(f"  [+] Incognito blocked: {browser}")

    def unblock_incognito(self):
        """
        Remove Registry policies that disable incognito mode.
        Restores the ability to use private/incognito browsing.
        """
        for browser, (key_path, value_name) in _INCOGNITO_POLICIES.items():
            self._delete_registry_value(
                winreg.HKEY_LOCAL_MACHINE, key_path, value_name
            )
            print(f"  [+] Incognito unblocked: {browser}")

    # ── Helpers: Registry ─────────────────────────────────────────────────

    @staticmethod
    def _write_registry_dword(hive, key_path: str, value_name: str, value: int):
        """Create a DWORD registry value, creating parent keys as needed."""
        try:
            key = winreg.CreateKeyEx(
                hive, key_path, 0,
                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
            )
            winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, value)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"  [!] Registry write failed ({key_path}\\{value_name}): {e}")

    @staticmethod
    def _delete_registry_value(hive, key_path: str, value_name: str):
        """Delete a registry value. Silently ignores if it doesn't exist."""
        try:
            key = winreg.OpenKey(
                hive, key_path, 0,
                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY
            )
            winreg.DeleteValue(key, value_name)
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass  # Key or value doesn't exist — fine
        except Exception as e:
            print(f"  [!] Registry delete failed ({key_path}\\{value_name}): {e}")

    # ── Helpers: Network adapters ─────────────────────────────────────────

    @staticmethod
    def _get_network_adapters() -> list:
        """
        Return a list of active network adapter names by parsing
        'netsh interface show interface'.
        Only includes adapters that are 'Connected'.
        """
        adapters = []
        try:
            result = subprocess.run(
                ["netsh", "interface", "show", "interface"],
                capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                # Output columns: Admin State | State | Type | Interface Name
                parts = line.strip().split()
                if len(parts) >= 4 and "Connected" in line:
                    # Interface name is everything after the 3rd column
                    # (handles multi-word names like "Wi-Fi 2")
                    name = line.strip()
                    # Find the "Connected" keyword and take everything after it
                    idx = name.find("Connected")
                    if idx != -1:
                        # Skip the word "Connected" and any trailing columns (Dedicated, etc.)
                        remainder = name[idx + len("Connected"):].strip()
                        # The last token after type field is the name
                        # More robust: split the original line into 4 parts max
                        cols = line.strip().split(None, 3)
                        if len(cols) >= 4:
                            adapter_name = cols[3].strip()
                            if adapter_name:
                                adapters.append(adapter_name)
        except Exception as e:
            print(f"  [!] DNS Blocker: Could not enumerate adapters: {e}")

        return adapters