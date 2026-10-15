from uuid import UUID
from fastapi import APIRouter, Depends, status, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.auth.dependencies import require_token
from app.db.session import SessionLocal
from app.schemas.grid_elements import GridElementCreateAPI, GridElementCreateDB, GridElementData, GridElementResponse, GridElementsListResponse, GridElementDeleteResponse
from app.crud.grid_elements import get_all_grid_elements_crud, create_grid_element_crud, delete_grid_element_crud
import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/grid_elements", tags=["grid_elements"])


def get_db():
    """
    Provide a database session for the duration of the request.

    Yields:
        Session: Active SQLAlchemy session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=GridElementsListResponse)
async def get_grid_elements(db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    """
    Retrieve all registered grid elements.

    Args:
        db: Injected SQLAlchemy session.
        token: Auth dependency requiring an admin token.

    Returns:
        GridElementsListResponse | JSONResponse: List of grid elements on success,
        or an error response if retrieval fails.
    """
    try:
        # get all grid elements from the MDMS
        grid_elements = get_all_grid_elements_crud(db)
        grid_elements_data_list = [GridElementData.model_validate(ge) for ge in grid_elements]
        return GridElementsListResponse(data=grid_elements_data_list, success=True)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )


@router.post("", response_model=GridElementResponse)
async def create_grid_element(request: GridElementCreateAPI, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    """
    Create a new grid element.

    Args:
        request: Request payload with grid element fields.
        db: Injected SQLAlchemy session.
        token: Auth dependency requiring an admin token.

    Returns:
        GridElementResponse | JSONResponse: The created grid element on success,
        or an error response if validation or persistence fails.
    """
    try:
        parent_uuid = UUID(request.parent_id) if request.parent_id else None

        ge_data = GridElementCreateDB(
            parent_id=request.parent_id,
            code=request.code,
            type=request.type,
            name=request.name,
            voltage_level=request.voltage_level,
            location=request.location)
        
        logger.debug(f"ge_data: {ge_data}")

        ge_data = create_grid_element_crud(db, ge_data)
        
        logger.debug(f"ge_data: {ge_data}")

        return GridElementResponse(data=ge_data, success=True) 
    
    except IntegrityError as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Grid element already exists or violates constraints."}
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )


@router.delete("/{grid_element_id}", response_model=GridElementDeleteResponse)
async def delete_grid_element(grid_element_id: UUID, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    """
    Delete an existing grid element by its identifier.

    Args:
        grid_element_id: Identifier of the grid element to delete.
        db: Injected SQLAlchemy session.
        token: Auth dependency requiring an admin token.

    Returns:
        GridElementDeleteResponse | JSONResponse: Success message on deletion,
        or an error response if the grid element is missing or still referenced.
    """
    try:
        delete_grid_element_crud(db, grid_element_id)
        return GridElementDeleteResponse(message="Grid element deleted successfully")
    except HTTPException as e:
        db.rollback()
        return JSONResponse(
            status_code=e.status_code,
            content={"message": e.detail}
        )
    except IntegrityError:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Grid element cannot be deleted because it is still referenced by other records."}
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )
