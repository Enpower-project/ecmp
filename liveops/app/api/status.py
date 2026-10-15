from fastapi import APIRouter, Depends

from app.auth.dependencies import require_token
from app.runtime import status_store


router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status(token=Depends(require_token("admin"))):
    """
    Return a basic health payload for the LiveOps service.

    Returns:
        dict: Service name and availability state.
    """
    return {
        "service": "liveops",
        "success": True,
        "status": "ok",
        "scope": "live energy community operations",
    }


@router.get("/status/community")
async def get_community_status(token=Depends(require_token("admin"))):
    """
    Return the latest in-memory energy community status built from live telemetry.

    Returns:
        dict: Community snapshot keyed by asset and channel.
    """
    snapshot = await status_store.get_snapshot()
    return {
        "service": "liveops",
        "success": True,
        "data": snapshot,
    }
