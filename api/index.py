"""Vercel entry point.

Vercel's Python runtime serves any `app` in an `api/*.py` file as an ASGI
application, so this just re-exports the same FastAPI app the CLI and local
`uvicorn server.app:app` use — no logic lives here. `vercel.json` rewrites every
route to this function. The parent dir is put on the path so `server` and
`retention_agent` import the same way they do locally.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.app import app  # noqa: E402,F401
