"""Top-level API router."""

from fastapi import APIRouter

from .routes_auth import router as auth_router
from .routes_catalog import router as catalog_router
from .routes_curricula import router as curricula_router
from .routes_plans import router as plans_router
from .routes_profile import router as profile_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(profile_router)
api_router.include_router(catalog_router)
api_router.include_router(curricula_router)
api_router.include_router(plans_router)


@api_router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "term": "2026-fall"}
