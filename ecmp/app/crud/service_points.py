from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime, timezone
from ..models import ec
from fastapi import HTTPException
from app.schemas.service_points import ServicePointData, ServicePointCreate
from uuid import UUID
import logging



logger = logging.getLogger(__name__)


def get_all_service_points_crud(db: Session):
    """
    Retrieve all service points.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        list[ec.ServicePoint]: All persisted service point ORM objects.
    """
    return db.query(ec.ServicePoint).all()


def get_service_points_by_ec_id_crud(db: Session, ec_id: UUID):
    """
    Retrieve all service points assigned to an energy community.

    The lookup is performed through the `community_service_points` association
    table and returns service points ordered by their code.

    Args:
        db: Active SQLAlchemy session.
        ec_id: Energy community identifier.

    Returns:
        list[ec.ServicePoint]: Service point ORM objects linked to the given
        energy community.
    """
    return (
        db.query(ec.ServicePoint)
        .join(ec.CommunityServicePoint, ec.CommunityServicePoint.sp_id == ec.ServicePoint.sp_id)
        .filter(ec.CommunityServicePoint.ec_id == ec_id)
        .order_by(ec.ServicePoint.code)
        .all()
    )


def create_service_point_crud(db: Session, service_point: ServicePointCreate):
    """
    Create and persist a new service point.

    Args:
        db: Active SQLAlchemy session.
        service_point: Validated service point creation payload.

    Returns:
        ServicePointData: Newly created service point serialized through the
        response schema.
    """
    db_sp = ec.ServicePoint(**service_point.model_dump())
    db.add(db_sp)
    db.commit()
    db.refresh(db_sp)
    return ServicePointData.model_validate(db_sp)



