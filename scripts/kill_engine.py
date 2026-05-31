import psutil
import shutil
import os
import time

exe_name = "Productive-OS-dev.exe"
print(f"[*] Looking for {exe_name}...")

killed = 0
for proc in psutil.process_iter(['pid', 'name']):
    if proc.info['name'] == exe_name:
        print(f"  -> Found running process: {proc.info['pid']}")
        try:
            proc.kill()
            killed += 1
            print("     Killed successfully.")
        except psutil.AccessDenied:
            print("     [ERROR] Access Denied! Process is running as Admin.")
        except Exception as e:
            print(f"     [ERROR] {e}")

if killed > 0:
    time.sleep(1.5)

dist_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "Productive-OS-dev")
if os.path.exists(dist_path):
    print(f"[*] Cleaning {dist_path}...")
    try:
        shutil.rmtree(dist_path)
        print("  -> Cleaned successfully.")
    except Exception as e:
        print(f"  -> [ERROR] Failed to clean dist folder: {e}")
        
print("[*] Done.")
