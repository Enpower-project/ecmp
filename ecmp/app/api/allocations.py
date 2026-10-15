from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_token
from app.crud.allocations import (
    get_allocation_links_by_producer_crud,
    upsert_producer_allocation_configuration_crud,
)
from app.db.session import SessionLocal
from app.schemas.allocations import (
    AllocationLinksListResponse,
    ProducerAllocationConfigurationCreate,
    ProducerAllocationConfigurationResponse,
)


router = APIRouter(prefix="/allocations", tags=["allocations"])


def get_db():
    """Provide a request-scoped database session.

    Yields:
        Session: Active SQLAlchemy session closed after the request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=AllocationLinksListResponse)
async def get_allocation_links(
    ec_id: UUID = Query(..., description="Energy community identifier."),
    producer_sp_id: UUID = Query(..., description="Producer service point identifier."),
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
):
    """List allocation links for one producer service point.

    The endpoint returns all producer-to-recipient allocation links for
    the given producer within the given energy community, including the
    stored coefficient history for each link.

    Args:
        ec_id: Energy community identifier used to scope the lookup.
        producer_sp_id: Producer service point identifier whose
            allocation links should be returned.
        db: Active SQLAlchemy session.
        token: Authenticated admin token.

    Returns:
        AllocationLinksListResponse: Allocation links and coefficient
        histories for the requested producer service point.
    """
    try:
        data = get_allocation_links_by_producer_crud(
            db=db,
            ec_id=ec_id,
            producer_sp_id=producer_sp_id,
        )
        return AllocationLinksListResponse(success=True, data=data)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)},
        )


@router.post("/producer", response_model=ProducerAllocationConfigurationResponse)
async def upsert_producer_allocation_configuration(
    request: ProducerAllocationConfigurationCreate,
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
):
    """Create or update allocation coefficients for one producer.

    The request is scoped to a single energy community and one producer
    service point. Recipient service points and their coefficient window
    are validated and then upserted through the CRUD layer. The same
    endpoint supports static and dynamic coefficients depending on the
    supplied validity interval.

    Args:
        request: Producer allocation configuration payload.
        db: Active SQLAlchemy session.
        token: Authenticated admin token.

    Returns:
        ProducerAllocationConfigurationResponse: Stored configuration for
        the requested producer and validity window.
    """
    try:
        data = upsert_producer_allocation_configuration_crud(db=db, config=request)
        return ProducerAllocationConfigurationResponse(success=True, data=data)
    except HTTPException as e:
        db.rollback()
        return JSONResponse(status_code=e.status_code, content={"message": e.detail})
    except IntegrityError:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Allocation configuration violates database constraints."},
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)},
        )
