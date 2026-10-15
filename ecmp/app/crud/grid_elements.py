from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime, timezone
from ..models import ec
from fastapi import HTTPException
from app.schemas.grid_elements import GridElementCreateDB, GridElementData
from uuid import UUID
import logging



logger = logging.getLogger(__name__)


def get_all_grid_elements_crud(db: Session):
    """
    Retrieve all grid elements from the database.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        list[ec.GridElement]: All persisted grid element ORM objects.
    """
    return db.query(ec.GridElement).all()


def create_grid_element_crud(db: Session, grid_element: GridElementCreateDB):
    """
    Create and persist a new grid element.

    Args:
        db: Active SQLAlchemy session.
        grid_element: Validated payload containing the grid element fields to store.

    Returns:
        GridElementData: The created grid element mapped to the response schema.
    """
    db_ge = ec.GridElement(**grid_element.model_dump())
    db.add(db_ge)
    db.commit()
    db.refresh(db_ge)
    return GridElementData.model_validate(db_ge)


def delete_grid_element_crud(db: Session, grid_element_id: UUID):
    """
    Delete a grid element by its identifier.

    Args:
        db: Active SQLAlchemy session.
        grid_element_id: Primary key of the grid element to remove.

    Raises:
        HTTPException: If the grid element does not exist.
    """
    db_ge = db.query(ec.GridElement).filter(
        ec.GridElement.grid_element_id == grid_element_id
    ).one_or_none()

    if db_ge is None:
        raise HTTPException(status_code=404, detail="Grid element not found")

    db.delete(db_ge)
    db.commit()


