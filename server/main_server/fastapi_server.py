from __future__ import annotations

"""FastAPI entry point.

기존 사용법(그대로 유지):
  python -m uvicorn fastapi_server:app --host 0.0.0.0 --port 8000 --log-level warning

추가(선택): 그냥 이 파일을 실행해도 서버가 뜨도록 했습니다.
  python3 fastapi_server.py

환경변수(선택):
  TASHO_HOST=0.0.0.0
  TASHO_PORT=8000
  TASHO_LOG_LEVEL=warning
"""

import os

from server_core.app_factory import create_app

app = create_app()

# -------------------------------------------------------------------
# Optional: direct run helper (does not change uvicorn -m usage)
# -------------------------------------------------------------------
if __name__ == "__main__":
    host = os.getenv("TASHO_HOST", "0.0.0.0").strip() or "0.0.0.0"
    try:
        port = int(os.getenv("TASHO_PORT", "8000").strip() or "8000")
    except Exception:
        port = 8000
    log_level = os.getenv("TASHO_LOG_LEVEL", "warning").strip() or "warning"

    import uvicorn

    uvicorn.run("fastapi_server:app", host=host, port=port, log_level=log_level, reload=False)