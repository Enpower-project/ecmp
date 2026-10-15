import uuid
from sqlalchemy import Column, Integer, text, Boolean, DateTime, ForeignKey, Text, CheckConstraint, JSON, Enum, TIMESTAMP, Numeric, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY, ENUM, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base
from app.models import enums


class FlexibilityRequest(Base):
    __tablename__ = "flexibility_request"

    id = Column(UUID(as_uuid=True), primary_key=True, unique=True, default=uuid.uuid4) # internal flex request unique id
    congestion_point = Column(Text, unique=False, nullable=False)    # grid element id targeted by the flex request
    start = Column(TIMESTAMP(timezone=True), server_default=func.now()) # start timestamp of the flexibility request
    end = Column(TIMESTAMP(timezone=True), server_default=func.now()) # end timestamp of the flexibility request
    type = Column(enums.flexibility_type, nullable=False) # type of flexibility request: INCREASE, REDUCE
    amount = Column(Numeric, nullable=False)    # The size of flexibility requested
    uom = Column(enums.unit_of_measure, nullable=False)
    price = Column(Numeric(precision=12, scale=4), nullable=False)
    price_uom = Column(enums.price_unit, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default="now()")    # timestamp when the request was created/registered
    
    