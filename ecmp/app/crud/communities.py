from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime, timezone
from ..models import ec
from fastapi import HTTPException
from app.schemas.communities import CommunityData, CommunityMembershipData
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


def get_all_communities(db: Session):
    return db.query(ec.EnergyCommunity).all()


def get_community_id_by_member_id_crud(db: Session, member_id: UUID):
    community_membership = db.query(ec.CommunityMembership).filter(
        ec.CommunityMembership.member_id == member_id
    ).one_or_none()
    if community_membership is None:
        raise HTTPException(status_code=404, detail="Energy community not found")
    
    return CommunityMembershipData.model_validate(community_membership)


def get_community_by_id_crud(db: Session, ec_id: UUID):
    community = db.query(ec.EnergyCommunity).filter(
        ec.EnergyCommunity.ec_id == ec_id
    ).one_or_none()
    if community is None:
        raise HTTPException(status_code=404, detail="Energy community not found")
    
    return CommunityData.model_validate(community)

