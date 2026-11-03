#!/usr/bin/env python3
"""
AgentMesh Desktop App Entrypoint
Launches the high-performance WebKitGTK container with server daemon supervision.
"""

import sys
import os
import subprocess

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    desktop_app = os.path.join(script_dir, "app_desktop.py")
    
    # Delegate cleanly to app_desktop.py
    os.execv(sys.executable, [sys.executable, desktop_app] + sys.argv[1:])

if __name__ == "__main__":
    main()
