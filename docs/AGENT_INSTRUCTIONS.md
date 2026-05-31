# Productive-OS: AI Agent Operating Instructions

## Context
You are working on "Productive-OS", a system-level Windows productivity tool. It uses low-level Windows APIs to enforce study modes, block games, and track time.

## Tech Stack & Architecture
* **Core Engine (Backend):** Python 3.8+ running as an Administrator via Windows Task Scheduler.
* **Key Python Libraries:** `ctypes`, `winreg`, `wmi`, `psutil`, `websockets`, `sqlite3`.
* **Game Detection:** Event-driven using WMI (`Win32_ProcessStartTrace`) and `SetWinEventHook` (No polling).
* **Frontend (UI):** Strictly Vanilla HTML, CSS (Glassmorphism), Vanilla JS, and Chart.js. Rendered natively using `pywebview`.
* **Communication:** WebSockets (Port 8765) and HTTP server (Port 8123).
* **Database:** SQLite (`productive_os.db`) for tracking telemetry and token economy.

## Strict Rules for the AI Agent
1. **Frontend Constraints:** DO NOT suggest or use React, Vue, Tailwind, or any frontend frameworks. Stick entirely to Vanilla JS and the existing custom CSS system.
2. **Win32 API Handling:** When modifying system-level code (`ctypes`, `wmi`), explicitly handle Windows exceptions, memory leaks, and handle closures.
3. **Concurrency:** The Python backend relies on threading and asyncio. NEVER write blocking synchronous code that halts the WMI event listener or WebSocket event loop.
4. **Database Schema:** Do not alter the SQLite schema unless explicitly asked, and if you do, provide a clear migration strategy.
5. **No Bloat:** Keep code execution under 100ms for game detection. Do not add heavy dependencies.
6. **Tone:** Provide direct code solutions without long corporate explanations.