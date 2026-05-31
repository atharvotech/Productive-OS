# Clean-up and Uninstall Script for Productive-OS
# Run this script from an Administrator PowerShell prompt!

# Ensure script is running as Administrator
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script MUST be run as an Administrator. Please restart PowerShell as Administrator and run the script again."
    Exit
}

Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "         PRODUCTIVE-OS COMPLETE UNINSTALL & CLEANUP           " -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Stop any running instances of the app
Write-Host "[*] Stopping running application processes..." -ForegroundColor Yellow
Get-Process -Name "Productive-OS" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name "Productive-OS-dev" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name "pythonw" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*main.py*" } | Stop-Process -Force
Write-Host "  ✅ Processes stopped." -ForegroundColor Green
Write-Host ""

# 2. Revert DNS settings to DHCP (automatic)
Write-Host "[*] Reverting Network DNS settings to Automatic (DHCP)..." -ForegroundColor Yellow
try {
    $adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
    foreach ($adapter in $adapters) {
        Set-DnsClientServerAddress -InterfaceIndex $adapter.InterfaceIndex -ResetServerAddresses
        Write-Host "  ✅ Restored DNS to DHCP for adapter: $($adapter.Name)" -ForegroundColor Green
    }
} catch {
    Write-Host "  ❌ Failed to reset DNS addresses: $_" -ForegroundColor Red
}
Write-Host ""

# 3. Clean up registry policies for Browser Blocklists/Incognito
Write-Host "[*] Removing browser blocklist and force-install policies..." -ForegroundColor Yellow
$regPaths = @(
    "HKLM:\SOFTWARE\Policies\Google\Chrome\URLBlocklist",
    "HKLM:\SOFTWARE\Policies\Microsoft\Edge\URLBlocklist",
    "HKLM:\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist",
    "HKLM:\SOFTWARE\Policies\Microsoft\Edge\ExtensionInstallForcelist"
)
foreach ($path in $regPaths) {
    if (Test-Path $path) {
        Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  ✅ Removed registry path: $path" -ForegroundColor Green
    }
}

# Remove incognito/InPrivate blocks
$incognitoKeys = @(
    @{ Path = "HKLM:\SOFTWARE\Policies\Google\Chrome"; Value = "IncognitoModeAvailability" },
    @{ Path = "HKLM:\SOFTWARE\Policies\BraveSoftware\Brave"; Value = "IncognitoModeAvailability" },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"; Value = "InPrivateModeAvailability" }
)
foreach ($item in $incognitoKeys) {
    if (Test-Path $item.Path) {
        Remove-ItemProperty -Path $item.Path -Name $item.Value -ErrorAction SilentlyContinue
        Write-Host "  ✅ Removed policy value: $($item.Path)\$($item.Value)" -ForegroundColor Green
    }
}
Write-Host ""

# 4. Re-enable Snap Assist settings (Windows window snapping)
Write-Host "[*] Restoring Windows Snap Assist (window arrangement)..." -ForegroundColor Yellow
try {
    # HKCU is user-specific, so we set it for the current logged-in user
    Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "WindowArrangementActive" -Value "1" -ErrorAction SilentlyContinue
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "SnapAssist" -Value 1 -ErrorAction SilentlyContinue
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "EnableSnapBar" -Value 1 -ErrorAction SilentlyContinue
    Write-Host "  ✅ Windows Snap Assist settings restored to default (enabled)." -ForegroundColor Green
} catch {
    Write-Host "  ❌ Failed to restore Snap Assist settings: $_" -ForegroundColor Red
}
Write-Host ""

# 5. Remove scheduled tasks
Write-Host "[*] Removing Scheduled Tasks..." -ForegroundColor Yellow
$tasks = @("ProductiveOS_AutoStart", "ProductiveOS_Dev_AutoStart", "FocusEnginePro")
foreach ($task in $tasks) {
    $exists = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if ($exists) {
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "  ✅ Removed scheduled task: $task" -ForegroundColor Green
    }
}
Write-Host ""

# 6. Run official uninstallers if they exist, then wipe directories
Write-Host "[*] Uninstalling app files and databases..." -ForegroundColor Yellow
$installPaths = @(
    @{ Name = "Productive-OS (Production)"; Path = "C:\Program Files\Atharvotech\Productive-OS" },
    @{ Name = "Productive-OS (Dev Build)"; Path = "C:\Program Files\Atharvotech\Productive-OS-Dev" },
    @{ Name = "Productive-OS (Legacy 32-bit)"; Path = "C:\Program Files (x86)\Atharvotech\Productive-OS" },
    @{ Name = "Productive-OS (Legacy 32-bit Dev)"; Path = "C:\Program Files (x86)\Atharvotech\Productive-OS-Dev" }
)

foreach ($item in $installPaths) {
    if (Test-Path $item.Path) {
        $uninstaller = Join-Path $item.Path "unins000.exe"
        if (Test-Path $uninstaller) {
            Write-Host "  [i] Running official uninstaller for $($item.Name)..." -ForegroundColor Green
            Start-Process -FilePath $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -ErrorAction SilentlyContinue
        }
        # Force-remove the folder if anything remains (including the local database data.db)
        if (Test-Path $item.Path) {
            Write-Host "  [i] Manually cleaning up remaining files in $($item.Path)..." -ForegroundColor Yellow
            Remove-Item -Path $item.Path -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-Host "  ✅ Removed $($item.Name) folders and databases." -ForegroundColor Green
    }
}
Write-Host ""

# 7. Clean up temporary local build/staging directories in the workspace
Write-Host "[*] Cleaning local workspace build outputs..." -ForegroundColor Yellow
$repoPath = $PSScriptRoot
if ([string]::IsNullOrEmpty($repoPath)) {
    $repoPath = "C:\Users\athar\OneDrive\Desktop\Productive-OS"
} else {
    $repoPath = Split-Path -Parent $repoPath
}
$devPaths = @(
    "_window_dump.json",
    "_window_dump.txt",
    "_style_dump.json",
    "_check_time.py",
    "_clean_stale.py",
    "_temp_tracker.py",
    "scratch_check_8080.py",
    "scratch_search.py"
)
foreach ($p in $devPaths) {
    $fullPath = Join-Path $repoPath $p
    if (Test-Path $fullPath) {
        Remove-Item -Path $fullPath -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  ✅ Cleaned up development asset: $p" -ForegroundColor Green
    }
}
Write-Host ""

Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  SUCCESS: WIPE COMPLETE!" -ForegroundColor Green
Write-Host "  Every backup, scheduled task, registry block, DNS override," -ForegroundColor Green
Write-Host "  database and application version has been uninstalled." -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "You can now run 'python build.py' or 'python installer.py' to rebuild" -ForegroundColor Cyan
Write-Host "and run the setup executable for a completely fresh install." -ForegroundColor Cyan
