# AGENTS.md
Guide for coding agents working in this repository.

## Scope
- Stack: Python FastAPI service with MySQL (PyMySQL).
- Entry point: `fastapi_server.py`.
- App setup: `server_core/app_factory.py`.
- Routers: `server_core/routers/*.py`.
- DB helpers: `server_core/db.py`.
- Schema bootstrap: `server_core/schema.py`.
- Logging helper: `server_core/log.py`.
- No committed `tests/` directory yet.
- No committed `pyproject.toml`, `setup.cfg`, `tox.ini`, or `requirements.txt`.

## Cursor and Copilot Rules
- `.cursorrules`: not found.
- `.cursor/rules/`: not found.
- `.github/copilot-instructions.md`: not found.
- Follow this file and existing code patterns.

## Setup
Use Python 3.11+.
```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install fastapi uvicorn pymysql pydantic pytest ruff black mypy
```
Notes:
- Most endpoints require DB connectivity.
- Schema is ensured during startup lifespan.
- DB credentials are currently hardcoded in `server_core/db.py`.

## Run Commands
No separate build step exists; this is a runtime API service.

Run server (preferred):
```bash
python3 -m uvicorn fastapi_server:app --host 0.0.0.0 --port 8000 --log-level warning
```
Run server (alternate):
```bash
python3 fastapi_server.py
```
Health checks:
```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/health
```
Useful env vars:
- `TASHO_HOST`, `TASHO_PORT`, `TASHO_LOG_LEVEL`
- `TASHO_SUPPRESS_NOISE`
- `TASHO_ALLOW_CAM_COMMANDS`

## Lint / Format / Type Check
No tool config is committed, so use explicit CLI defaults.

Lint:
```bash
ruff check .
```
Lint with autofix:
```bash
ruff check . --fix
```
Format:
```bash
black .
```
Type check (best effort):
```bash
mypy fastapi_server.py server_core
```

## Test Commands (including single test)
Project currently has no committed tests; use `pytest` for new tests.

Run all tests:
```bash
pytest -q
```
Run one file:
```bash
pytest -q tests/test_pinky_router.py
```
Run one function:
```bash
pytest -q tests/test_pinky_router.py::test_queue_command_dedupes_docking
```
Run by keyword:
```bash
pytest -q -k docking
```
Stop on first failure:
```bash
pytest -q -x
```

## Code Style

### Imports
- Keep `from __future__ import annotations` at the top.
- Order imports: stdlib -> third-party -> local.
- Prefer explicit imports; avoid wildcard imports.
- Prefer relative imports inside `server_core` where practical.

### Formatting
- Write Black-compatible Python.
- Keep handlers and helpers focused and short.
- Keep SQL in triple-quoted strings.
- Use uppercase SQL keywords.
- Prefer readability over compact tricks.

### Types
- Add type hints for new functions and returns.
- Use one nullability style consistently within each file (`Optional[T]` or `T | None`).
- Use Pydantic models for stable request schemas.
- Prefer concrete containers over raw `Any` when possible.

### Naming
- Use `snake_case` for functions and variables.
- Use `UPPER_SNAKE_CASE` for constants.
- Use `PascalCase` for classes and request models.
- Keep endpoint names action-oriented (`get_*`, `post_*`, `*_command`).

### API Behavior
- Follow response convention: `{"ok": True, ...}` or `{"ok": False, "error": ...}`.
- Preserve compatibility aliases for existing routes.
- Validate bounds and required fields via `Query`, `Body`, and Pydantic.
- Keep route prefixes unchanged unless explicitly requested.

### Database Access
- Use helper functions from `server_core/db.py`.
- Always pass parameters separately; never interpolate user input into SQL.
- Keep DB side effects explicit and minimal.
- Preserve command dedupe checks and queue state transitions.

### Error Handling
- Fail safely for non-critical operations (especially logging paths).
- Catch specific exceptions where practical.
- Do not expose stack traces in API responses.
- Normalize invalid statuses/inputs as existing routes do.

### Logging
- Use `log_event(...)` for operational records.
- Keep event names uppercase and stable.
- Keep `detail` concise and parseable (`key=value` style preferred).
- Use warning/error levels for infrastructure issues.

### Compatibility and Safety
- Do not remove legacy endpoints without explicit request.
- Keep existing env-var behavior stable.
- Keep schema assumptions aligned with `ensure_schema()` and router SQL.
- Avoid breaking clients that depend on existing JSON keys.

## Agent Workflow
1. Read relevant router/db/schema files first.
2. Make targeted edits only.
3. Run formatter and lint on touched files.
4. Run focused tests (or smoke checks if tests do not exist).
5. Report commands run and key results.

If adding tests, prefer:
- `tests/` directory.
- `test_*.py` naming.
- Fast tests with DB calls mocked where possible.

## Current Gaps
- No committed test suite.
- No pinned dependency manifest.
- No CI workflow.
- No strict lint/type config.
