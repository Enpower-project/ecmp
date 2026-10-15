from uuid import UUID
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.auth.dependencies import require_token
from app.db.session import SessionLocal
from app.schemas.channels import ChannelsListResponse, ChannelData, ChannelResponse, ChannelCreate
from app.crud.channels import get_all_channels_crud, create_channel_crud
import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/channels", tags=["channels"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=ChannelsListResponse)
async def get_channels(db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:
        channels = get_all_channels_crud(db)
        channels_data_list = [ChannelData.model_validate(ch) for ch in channels]
        return ChannelsListResponse(data=channels_data_list, success=True)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )


@router.post("", response_model=ChannelResponse)
async def create_channel(request: ChannelCreate, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:

        channel_data = ChannelCreate(
            meter_id=request.meter_id,
            role=request.role,
            uom=request.uom,
            interval_minutes=request.interval_minutes,
            scaling_factor=request.scaling_factor)
        
        logger.debug(f"channel_data: {channel_data}")

        channel_data = create_channel_crud(db, channel_data)
        
        logger.debug(f"channel_data: {channel_data}")

        return ChannelResponse(data=channel_data, success=True) 
    
    except IntegrityError as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Channel already exists or violates constraints."}
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )
