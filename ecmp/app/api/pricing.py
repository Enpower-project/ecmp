from uuid import UUID
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.auth.dependencies import require_token
from app.db.session import SessionLocal
from app.schemas.price_zone import PriceZoneCreate, PriceZoneData, PriceZoneListResponse, PriceZoneResponse
from app.crud.pricing import get_all_price_zones, create_price_zone
from app.crud.pricing import ConflictError, PersistenceError
import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/pricing", tags=["pricing"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get(
        "",
        summary="Get a list of price zones",
        description="Get all registered price zones on the platform",
        response_description="JSON string with a list of price zones",
        response_model=PriceZoneListResponse)
def list_price_zones(db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    """
    Retrieve registered price zones.

    Returns:
        PriceZoneListResponse: Response containing a list of price zones and success flag.
    """
    try:
        pzones = get_all_price_zones(db)
        return PriceZoneListResponse(data=pzones, success=True)
    
    except PersistenceError as e:
        logger.exception("Database error while listing price zones")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Database error occurred."},
        )
    
    except Exception as e:
        logger.exception("Unexpected error while listing price zones")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred"},
        )
    

@router.post(
        "",
        summary="Register the new pricing zone",
        description="Create new pricing zone using the provided parameters",
        response_description="JSON string containing the results",
        response_model=PriceZoneResponse)
def register_price_zone(params: PriceZoneCreate, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    """
    Register a new price zone.

    Args:
        params: Price zone creation payload.
        db: SQLAlchemy session.
        token: Auth dependency (admin).

    Returns:
        PriceZoneResponse: The created price zone and success flag.
    """
    try:
        price_zone = create_price_zone(db=db, pzone=params)
        logger.debug("Created Price zone: %s", price_zone)
        return PriceZoneResponse(data=price_zone, success=True) 
    
    except ConflictError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"message": "Price zone already exists or violates constraints."},
        )

    except PersistenceError:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Database error occurred while creating price zone."},
        )

