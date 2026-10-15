from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from app.db.session import SessionLocal
from app.auth.dependencies import require_token
from app.schemas.members import MemberCreateAPI, MemberCreateDB, MemberData, MemberListResponse, MemberResponse
from app.crud.members import get_all_members_crud, create_member_crud
import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/members", tags=["members"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=MemberListResponse)
async def get_members(db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:
        # get all members from MDMS
        members = get_all_members_crud(db)
        members_data_list = [MemberData.model_validate(m) for m in members]
        return MemberListResponse(data=members_data_list, success=True)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )


@router.post("", response_model=MemberResponse)
async def create_member(request: MemberCreateAPI, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:

        # create user in Keycloak
        response = await kc.create_user(
            username=request.name,
            first_name=request.first_name,
            last_name=request.last_name,
            email=request.email,
            email_verified=True,
            credentials=request.credentials,
            enabled=True)

        logger.info(f"user: {response}")

        response = await kc.get_all_users()
        users = response.get("data")

        logger.debug(f"users: {users}")

        # find user by the name and extract KC ID
        kcid = None
        for user in users:
            if user.get("username") == request.name:
                kcid = user.get("id")
                break
        
        logger.debug(f"user_id: {kcid}")

        member_data = MemberCreateDB(
            external_ref=kcid, 
            name=request.name,
            first_name=request.first_name,
            last_name=request.last_name,
            email=request.email,
            type=request.type)
        
        logger.debug(f"member_data: {member_data}")

        member_data = create_member_crud(db, member_data)

        logger.debug(f"member_data: {member_data}")


        return MemberResponse(data=member_data, success=True) 
    
    except IntegrityError as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Member already exists or violates constraints."}
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )
