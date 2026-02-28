"""
Health check endpoint — boolean status only, no internal state leakage.
"""

from fastapi import APIRouter
from db.database import get_version

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health():
    return {"status": "ok", "version": get_version()}
