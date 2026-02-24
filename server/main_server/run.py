from __future__ import annotations

import argparse
import os

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the FastAPI server")
    parser.add_argument("--host", default=os.getenv("TASHO_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TASHO_PORT", "8000")))
    parser.add_argument("--log-level", default=os.getenv("TASHO_LOG_LEVEL", "warning"))
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run(
        "fastapi_server:app",
        host=str(args.host).strip() or "0.0.0.0",
        port=int(args.port),
        log_level=str(args.log_level).strip() or "warning",
        reload=bool(args.reload),
    )


if __name__ == "__main__":
    main()
