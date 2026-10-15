from uuid import UUID
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.auth.dependencies import require_token
from app.db.session import SessionLocal
from app.schemas.meters import MetersListResponse, MeterData, MeterResponse, MeterCreate
from app.crud.meters import get_all_meters_crud, create_meter_crud
import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/meters", tags=["meters"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=MetersListResponse)
async def get_meters(db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:
        # get all grid elements from the MDMS
        meters = get_all_meters_crud(db)
        logger.debug(f"meters: {meters}")
        meters_data_list = [MeterData.model_validate(sp) for sp in meters]
        logger.debug(f"meters_data_list: {meters_data_list}")
        return MetersListResponse(data=meters_data_list, success=True)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )


@router.post("", response_model=MeterResponse)
async def create_meter(request: MeterCreate, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:

        meter_data = MeterCreate(
            sp_id=request.sp_id,
            serial_no=request.serial_no,
            model=request.model,
            installed_at=request.installed_at)
        
        logger.debug(f"meter_data: {meter_data}")

        meter_data = create_meter_crud(db, meter_data)
        
        logger.debug(f"meter_data: {meter_data}")

        return MeterResponse(data=meter_data, success=True) 
    
    except IntegrityError as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Meter already exists or violates constraints."}
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )
