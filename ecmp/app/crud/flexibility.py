from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from datetime import datetime
from app.models import flex, ec
from app.schemas.flexibility import FlexibilityRequestCreate, FlexibilityRequestData
from uuid import UUID
import logging


logger = logging.getLogger(__name__)


class ConflictError(Exception): ...
class PersistenceError(Exception): ...


def get_flex_requests(
        db: Session,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> list[FlexibilityRequestData]:
    """
    Retrieve FlexibilityRequest records with pagination.

    Args:
        db: Database session.
        skip: Number of records to skip (offset).
        limit: Maximum number of records to return.

    Returns:
        Sequence[FlexibilityRequest]
    """
    records = (
        db.query(flex.FlexibilityRequest)
        .order_by(flex.FlexibilityRequest.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return [FlexibilityRequestData.model_validate(r) for r in records]


def get_flex_requests_for_community(
        db: Session,
        *,
        ec_id: UUID,
        start_datetime: datetime | None = None,
        end_datetime: datetime | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[FlexibilityRequestData]:
    """
    Retrieve flexibility requests for all low-voltage substations
    inside a selected energy community.

    The filtering is based on the request ``congestion_point`` matching a
    grid element ``code``. For energy-community filtering, the grid element
    must be linked to at least one service point in the community.

    Args:
        db: Database session.
        ec_id: Energy community identifier to filter by.
        start_datetime: Optional lower bound of a filter interval.
        end_datetime: Optional upper bound of a filter interval.
        skip: Number of records to skip (offset).
        limit: Maximum number of records to return.

    Returns:
        list[FlexibilityRequestData]: Matching flexibility requests.
    """
    query = (
        db.query(flex.FlexibilityRequest)
        .join(
            ec.GridElement,
            ec.GridElement.code == flex.FlexibilityRequest.congestion_point,
        )
        .join(
            ec.ServicePoint,
            ec.ServicePoint.grid_element_id == ec.GridElement.grid_element_id,
        )
        .join(
            ec.CommunityServicePoint,
            ec.CommunityServicePoint.sp_id == ec.ServicePoint.sp_id,
        )
        .filter(ec.CommunityServicePoint.ec_id == ec_id)
    )

    if start_datetime is not None and end_datetime is not None:
        query = query.filter(
            flex.FlexibilityRequest.start < end_datetime,
            flex.FlexibilityRequest.end >= start_datetime,
        )

    records = (
        query.distinct()
        .order_by(flex.FlexibilityRequest.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return [FlexibilityRequestData.model_validate(r) for r in records]


# def get_flex_requests_per_congestion_per_date(db: Session, congestion_point: str, date: datetime):



def write_flex_request(db: Session, flexreq: FlexibilityRequestCreate) -> FlexibilityRequestData:
    """
    Persist a new FlexibilityRequest to the database and return it as a
    validated FlexibilityRequestData model.

    This function creates a new FlexibilityRequest ORM instance from the
    provided `FlexibilityRequestCreate` input model, adds it to the current
    database session, commits the transaction, and refreshes the instance
    to ensure any database-generated fields (e.g., primary key, timestamps)
    are populated.

    Args:
        db (Session):
            Active SQLAlchemy database session used for persistence.
        flexreq (FlexibilityRequestCreate):
            Pydantic model containing validated input data for creating
            a new FlexibilityRequest.

    Returns:
        FlexibilityRequestData:
            A validated Pydantic representation of the newly created
            FlexibilityRequest, including any database-generated fields.

    Raises:
        ConflictError:
            If a database constraint violation occurs.
        PersistenceError:
            If a general database error occurs during persistence.
    """
    db_flreq = flex.FlexibilityRequest(**flexreq.model_dump())

    try:
        db.add(db_flreq)
        db.commit()
        db.refresh(db_flreq)
    except IntegrityError as e:
        db.rollback()
        raise ConflictError("Constraint violation") from e
    except SQLAlchemyError as e:
        db.rollback()
        raise PersistenceError("DB error") from e

    return FlexibilityRequestData.model_validate(db_flreq)



def get_flex_request_by_id(db: Session, request_id: UUID) -> FlexibilityRequestData | None:
    """
    Retrieve a FlexibilityRequest by its unique identifier and return it as a
    validated FlexibilityRequestData model.

    This function queries the database for a FlexibilityRequest record matching
    the provided `request_id`. If a matching record is found, it is converted
    into a `FlexibilityRequestData` Pydantic model using `model_validate`.
    If no record exists, the function returns None.

    Args:
        db (Session): Active SQLAlchemy database session used to perform the query.
        request_id (UUID): Unique identifier of the FlexibilityRequest to retrieve.

    Returns:
        FlexibilityRequestData | None:
            - A validated `FlexibilityRequestData` instance if a record is found.
            - None if no matching record exists.

    Notes:
        - Assumes `FlexibilityRequestData.model_validate` supports validation
          from an ORM model instance.
    """

    req = db.query(flex.FlexibilityRequest).filter(
        flex.FlexibilityRequest.id == request_id
    ).one_or_none()

    return FlexibilityRequestData.model_validate(req) if req else None
