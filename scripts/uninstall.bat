@echo off
:: Ensure admin privileges
NET SESSION >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [!] ERROR: This script must be run as Administrator.
    echo Please right-click this file and select "Run as administrator".
    echo.
    pause
    exit /b
)

echo ==============================================================
echo          PRODUCTIVE-OS COMPLETE UNINSTALL ^& CLEANUP           
echo ==============================================================
echo.

echo [*] Stopping running processes...
taskkill /F /IM Productive-OS.exe
taskkill /F /IM Productive-OS-dev.exe
echo   [OK] Processes stopped.

echo.
echo [*] Reverting DNS to automatic (DHCP)...
for /f "tokens=3*" %%i in ('netsh interface show interface ^| findstr "Connected"') do (
    netsh interface ip set dns name="%%j" source=dhcp >nul 2>&1
    echo   [OK] DNS reset to DHCP for adapter: %%j
)

echo.
echo [*] Cleaning up browser registry policies...
reg delete "HKLM\SOFTWARE\Policies\Google\Chrome\URLBlocklist" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Policies\Microsoft\Edge\URLBlocklist" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Policies\Microsoft\Edge\ExtensionInstallForcelist" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Policies\Google\Chrome" /v "IncognitoModeAvailability" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Policies\BraveSoftware\Brave" /v "IncognitoModeAvailability" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Policies\Microsoft\Edge" /v "InPrivateModeAvailability" /f >nul 2>&1
echo   [OK] Registry policies removed.

echo.
echo [*] Restoring Windows Snap settings...
reg add "HKCU\Control Panel\Desktop" /v WindowArrangementActive /t REG_SZ /d 1 /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" /v SnapAssist /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" /v EnableSnapBar /t REG_DWORD /d 1 /f >nul 2>&1
echo   [OK] Snap settings restored.

echo.
echo [*] Deleting scheduled tasks...
schtasks /Delete /TN "ProductiveOS_AutoStart" /F >nul 2>&1
schtasks /Delete /TN "ProductiveOS_Dev_AutoStart" /F >nul 2>&1
schtasks /Delete /TN "FocusEnginePro" /F >nul 2>&1
echo   [OK] Scheduled tasks deleted.

echo.
echo [*] Uninstalling apps and databases...
if exist "C:\Program Files\Atharvotech\Productive-OS\unins000.exe" (
    echo Running production silent uninstaller...
    start /wait "" "C:\Program Files\Atharvotech\Productive-OS\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
)
if exist "C:\Program Files\Atharvotech\Productive-OS-Dev\unins000.exe" (
    echo Running dev silent uninstaller...
    start /wait "" "C:\Program Files\Atharvotech\Productive-OS-Dev\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
)

:: Force delete directory paths
rd /s /q "C:\Program Files\Atharvotech\Productive-OS" >nul 2>&1
rd /s /q "C:\Program Files\Atharvotech\Productive-OS-Dev" >nul 2>&1
rd /s /q "C:\Program Files (x86)\Atharvotech\Productive-OS" >nul 2>&1
rd /s /q "C:\Program Files (x86)\Atharvotech\Productive-OS-Dev" >nul 2>&1
echo   [OK] App folders and databases removed.

echo.
echo [*] Cleaning temporary debug logs in repository...
del /f /q "%~dp0..\_window_dump.json" >nul 2>&1
del /f /q "%~dp0..\_window_dump.txt" >nul 2>&1
del /f /q "%~dp0..\_style_dump.json" >nul 2>&1
del /f /q "%~dp0..\_check_time.py" >nul 2>&1
del /f /q "%~dp0..\_clean_stale.py" >nul 2>&1
del /f /q "%~dp0..\_temp_tracker.py" >nul 2>&1
del /f /q "%~dp0..\scratch_check_8080.py" >nul 2>&1
del /f /q "%~dp0..\scratch_search.py" >nul 2>&1
echo   [OK] Debug dumps cleaned.

echo.
echo ==============================================================
echo   SUCCESS: Productive-OS is completely wiped from this system!
echo ==============================================================
echo.
pause
