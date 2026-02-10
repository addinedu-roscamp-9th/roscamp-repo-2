from __future__ import annotations

"""
Entry point for uvicorn:

  python -m uvicorn fastapi_server:app --host 0.0.0.0 --port 8000 --log-level warning

This file stays tiny on purpose.
All real logic lives in server_core/.
"""

from server_core.app_factory import create_app

app = create_app()
