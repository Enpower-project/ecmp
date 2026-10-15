from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime, timezone
from ..models import ec
from fastapi import HTTPException
from app.schemas.meters import MeterData, MeterCreate
from uuid import UUID
import logging



logger = logging.getLogger(__name__)


def get_all_meters_crud(db: Session):
    """
    Retrieve all meters.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        list[ec.Meter]: All persisted meter ORM objects.
    """
    return db.query(ec.Meter).all()


def get_meters_by_sp_id_crud(db: Session, sp_id: UUID):
    """
    Retrieve all meters connected to a service point.

    Args:
        db: Active SQLAlchemy session.
        sp_id: Service point identifier.

    Returns:
        list[ec.Meter]: Meter ORM objects linked to the given service point.
    """
    return (
        db.query(ec.Meter)
        .filter(ec.Meter.sp_id == sp_id)
        .order_by(ec.Meter.installed_at)
        .all()
    )


def create_meter_crud(db: Session, meter: MeterCreate):
    """
    Create and persist a new meter.

    Args:
        db: Active SQLAlchemy session.
        meter: Validated meter creation payload.

    Returns:
        MeterData: Newly created meter serialized through the response schema.
    """
    db_meter = ec.Meter(**meter.model_dump())
    db.add(db_meter)
    db.commit()
    db.refresh(db_meter)
    return MeterData.model_validate(db_meter)


