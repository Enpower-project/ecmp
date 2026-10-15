from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime, timezone
from ..models import ec
from fastapi import HTTPException
from app.schemas.channels import ChannelData, ChannelCreate
from uuid import UUID
import logging


logger = logging.getLogger(__name__)


def get_all_channels_crud(db: Session):
    """
    Retrieve all channels.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        list[ec.Channel]: All persisted channel ORM objects.
    """
    return db.query(ec.Channel).all()


def get_channels_by_meter_id_crud(db: Session, meter_id: UUID):
    """
    Retrieve all channels connected to a meter.

    Args:
        db: Active SQLAlchemy session.
        meter_id: Meter identifier.

    Returns:
        list[ec.Channel]: Channel ORM objects linked to the given meter.
    """
    return (
        db.query(ec.Channel)
        .filter(ec.Channel.meter_id == meter_id)
        .order_by(ec.Channel.role)
        .all()
    )


def create_channel_crud(db: Session, channel: ChannelCreate):
    """
    Create and persist a new channel.

    Args:
        db: Active SQLAlchemy session.
        channel: Validated channel creation payload.

    Returns:
        ChannelData: Newly created channel serialized through the response
        schema.
    """
    db_channel = ec.Channel(**channel.model_dump())
    db.add(db_channel)
    db.commit()
    db.refresh(db_channel)
    return ChannelData.model_validate(db_channel)


