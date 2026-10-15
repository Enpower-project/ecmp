import uuid
from sqlalchemy import Column, Integer, text, Boolean, DateTime, ForeignKey, Text, CheckConstraint, JSON, Enum, TIMESTAMP, Numeric, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY, ENUM, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base
from app.models import enums

