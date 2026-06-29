#!/usr/bin/env python3
"""
Paper Library Launcher - double-click to start.
Opens http://localhost:3737 in your browser.
Close this window to stop the server.
"""

import subprocess
import shutil
import sys
import webbrowser
import time
import os
from pathlib import Path

PORT = 3737
SERVER = Path(__file__).parent / "paper-library-server.js"

try:
    if not SERVER.exists():
        print(f"Error: {SERVER} not found.")
        input("Press Enter to exit.")
        sys.exit(1)

    if not shutil.which("node"):
        print("Error: Node.js is not installed or not in PATH.")
        print("Install it from https://nodejs.org")
        input("Press Enter to exit.")
        sys.exit(1)

    print("=" * 50)
    print("  Paper Library")
    print(f"  http://localhost:{PORT}")
    print()
    print("  Close this window to stop the server.")
    print("=" * 50)
    print()

    proc = subprocess.Popen(
        ["node", str(SERVER)],
        cwd=str(SERVER.parent),
        env={**os.environ, "PORT": str(PORT)},
    )

    time.sleep(1.5)

    if proc.poll() is not None:
        print("Server failed to start.")
        print("Check that Node.js is installed and working.")
        input("Press Enter to exit.")
        sys.exit(1)

    webbrowser.open(f"http://localhost:{PORT}")
    proc.wait()

except Exception as e:
    print(f"\nError: {e}")
    input("Press Enter to exit.")
except KeyboardInterrupt:
    pass
finally:
    try:
        proc.terminate()
    except Exception:
        pass
