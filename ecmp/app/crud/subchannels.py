from sqlalchemy.orm import Session
from ..models import ec
from app.schemas.subchannels import SubChannelData, SubChannelCreate
from uuid import UUID
import logging


logger = logging.getLogger(__name__)


def get_all_subchannels_crud(db: Session):
    """
    Retrieve all subchannels.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        list[ec.SubChannel]: All persisted subchannel ORM objects.
    """
    return db.query(ec.SubChannel).all()


def get_subchannels_by_submeter_id_crud(db: Session, submeter_id: UUID):
    """
    Retrieve all subchannels connected to a submeter.

    Args:
        db: Active SQLAlchemy session.
        submeter_id: Submeter identifier.

    Returns:
        list[ec.SubChannel]: Subchannel ORM objects linked to the given
        submeter.
    """
    return (
        db.query(ec.SubChannel)
        .filter(ec.SubChannel.submeter_id == submeter_id)
        .order_by(ec.SubChannel.role)
        .all()
    )


def create_subchannel_crud(db: Session, subchannel: SubChannelCreate):
    """
    Create and persist a new subchannel.

    Args:
        db: Active SQLAlchemy session.
        subchannel: Validated subchannel creation payload.

    Returns:
        SubChannelData: Newly created subchannel serialized through the
        response schema.
    """
    db_subchannel = ec.SubChannel(**subchannel.model_dump())
    db.add(db_subchannel)
    db.commit()
    db.refresh(db_subchannel)
    return SubChannelData.model_validate(db_subchannel)
