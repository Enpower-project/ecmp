from uuid import UUID
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.auth.dependencies import require_token
from app.db.session import SessionLocal
from app.api import community_utils as cu
from app.schemas.service_points import ServicePointsListResponse, ServicePointData, ServicePointResponse, ServicePointCreate
from app.crud.service_points import get_all_service_points_crud, create_service_point_crud, get_service_points_by_ec_id_crud
import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/service_points", tags=["service_points"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=ServicePointsListResponse)
async def get_service_points(db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:
        # get all grid elements from the MDMS
        service_points = get_all_service_points_crud(db)
        service_points_data_list = [ServicePointData.model_validate(sp) for sp in service_points]
        return ServicePointsListResponse(data=service_points_data_list, success=True)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )


@router.get("/community", response_model=ServicePointsListResponse)
async def get_service_points_for_authenticated_community(
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
):
    try:
        ec_id = cu.get_energy_community_id_from_token(db=db, token_data=token)
        service_points = get_service_points_by_ec_id_crud(db=db, ec_id=ec_id)
        service_points_data_list = [ServicePointData.model_validate(sp) for sp in service_points]
        return ServicePointsListResponse(data=service_points_data_list, success=True)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )


@router.post("", response_model=ServicePointResponse)
async def create_service_point(request: ServicePointCreate, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:

        sp_data = ServicePointCreate(
            member_id=request.member_id,
            code=request.code,
            role=request.role,
            voltage_level=request.voltage_level,
            address=request.address,
            timezone=request.timezone,
            active=request.active,
            grid_element_id=request.grid_element_id)
        
        logger.debug(f"sp_data: {sp_data}")

        sp_data = create_service_point_crud(db, sp_data)
        
        logger.debug(f"sp_data: {sp_data}")

        return ServicePointResponse(data=sp_data, success=True) 
    
    except IntegrityError as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Service point already exists or violates constraints."}
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )
