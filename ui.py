"""
Productive-OS — Native Window UI
Opens the dashboard (http://localhost:8123) in a pywebview window.

This module is imported from main.py and runs in a background thread.
Closing the window does NOT stop the backend engine.
"""

import time
import urllib.request


WAIT_TIMEOUT_SEC = 20


def wait_for_server(url: str, timeout: int = WAIT_TIMEOUT_SEC) -> bool:
    """Poll the HTTP server until it's accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def open_window(port: int = 8123):
    """
    Launch the native pywebview window pointing to the Dashboard.
    Blocks until the window is closed.
    """
    url = f"http://localhost:{port}"
    # Wait for the Python HTTP server to spin up
    if not wait_for_server(url):
        print(f"  [!] Timeout waiting for UI server at {url}")

    try:
        import webview  # pywebview package

        window = webview.create_window(
            title="Productive-OS",
            url=url,
            width=1300,
            height=840,
            min_size=(960, 620),
            # Allow JavaScript to call Python APIs if needed in the future
            js_api=None,
        )

        # webview.start() blocks until the window is closed.
        # Daemon thread ensures this doesn't keep the process alive.
        webview.start(debug=False)

    except ImportError:
        # pywebview not installed — open in default browser
        print("[UI] pywebview not installed. Opening in browser...")
        import webbrowser
        webbrowser.open(url)

    except Exception as e:
        print(f"[UI] Window error: {e}")
        # Fallback to browser
        import webbrowser
        webbrowser.open(url)
