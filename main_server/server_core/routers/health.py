from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])

@router.get("/health")
def health_root():
    return {"ok": True, "service": "tasho_fastapi"}

@router.get("/api/health")
def health_api():
    return {"ok": True, "service": "tasho_fastapi"}
