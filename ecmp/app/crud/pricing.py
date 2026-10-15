from sqlalchemy.orm import Session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from datetime import datetime, timezone
from app.models import ec

from app.schemas.price_zone import PriceZoneCreate, PriceZoneData
from uuid import UUID
import logging


logger = logging.getLogger(__name__)


class ConflictError(Exception): ...
class PersistenceError(Exception): ...


def get_all_price_zones(
        db: Session,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> list[PriceZoneData]:
    """
    Retrieve Price Zone records with pagination.

    Args:
        db: Database session.
        skip: Number of records to skip (offset).
        limit: Maximum number of records to return.

    Returns:
        Sequence[ProceZoneData]
    """
    records = (
        db.query(ec.PriceZone)
        .order_by(ec.PriceZone.name.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return [PriceZoneData.model_validate(r) for r in records]


def create_price_zone(db: Session, pzone: PriceZoneCreate) -> PriceZoneData:
    """
    Register a new energy PriceZone to the database and return it as a
    validated PriceZoneData model.

    This function creates a new PriceZone ORM instance from the
    provided `FlexibilityRequestCreate` input model, adds it to the current
    database session, commits the transaction, and refreshes the instance
    to ensure any database-generated fields (e.g., primary key)
    are populated.

    Args:
        db (Session):
            Active SQLAlchemy database session used for persistence.
        pzone (PriceZoneCreate):
            Pydantic model containing validated input data for creating
            a new PriceZone.

    Returns:
        PriceZoneData:
            A validated Pydantic representation of the newly created
            PriceZone, including any database-generated fields.

    Raises:
        ConflictError:
            If a database constraint violation occurs.
        PersistenceError:
            If a general database error occurs during persistence.
    """
    db_pzone = ec.PriceZone(**pzone.model_dump())

    try:
        db.add(db_pzone)
        db.commit()
        db.refresh(db_pzone)
    except IntegrityError as e:
        db.rollback()
        raise ConflictError("Constraint violation") from e
    except SQLAlchemyError as e:
        db.rollback()
        raise PersistenceError("DB error") from e

    return PriceZoneData.model_validate(db_pzone)
