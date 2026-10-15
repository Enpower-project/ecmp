from sqlalchemy.orm import Session
from ..models import ec
from app.schemas.assets import AssetData, AssetCreate
from uuid import UUID
import logging


logger = logging.getLogger(__name__)


def get_all_assets_crud(db: Session):
    """
    Retrieve all assets.

    Args:
        db: Active SQLAlchemy session.

    Returns:
        list[ec.Asset]: All persisted asset ORM objects.
    """
    return db.query(ec.Asset).all()


def get_assets_by_sp_id_crud(db: Session, sp_id: UUID):
    """
    Retrieve all assets connected to a service point.

    Args:
        db: Active SQLAlchemy session.
        sp_id: Service point identifier.

    Returns:
        list[ec.Asset]: Asset ORM objects linked to the given service point.
    """
    return (
        db.query(ec.Asset)
        .filter(ec.Asset.sp_id == sp_id)
        .order_by(ec.Asset.installed_at)
        .all()
    )


def create_asset_crud(db: Session, asset: AssetCreate):
    """
    Create and persist a new asset.

    Args:
        db: Active SQLAlchemy session.
        asset: Validated asset creation payload.

    Returns:
        AssetData: Newly created asset serialized through the response schema.
    """
    db_asset = ec.Asset(**asset.model_dump())
    db.add(db_asset)
    db.commit()
    db.refresh(db_asset)
    return AssetData.model_validate(db_asset)
