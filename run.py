#!/usr/bin/env python
"""
Local dev launcher: starts the FastAPI server with uvicorn.
Open http://localhost:8000 in your browser.

Usage:
  python run.py              # default port 8000
  python run.py --port 3000  # custom port
"""

import sys
import os

# Load .env if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Simple fallback if python-dotenv not installed
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

import uvicorn

if __name__ == "__main__":
    port = 8000
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        port = int(sys.argv[idx + 1])

    print(f"\n  Energy Tracker running at http://localhost:{port}\n")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
