from __future__ import annotations

from datetime import datetime
from typing import Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models import ec
from app.schemas.allocations import (
    AllocationLinkData,
    ProducerAllocationConfigurationCreate,
    ProducerAllocationConfigurationData,
)


def _get_service_point_in_community(db: Session, ec_id: UUID, sp_id: UUID) -> ec.ServicePoint:
    """Resolve a service point and verify that it belongs to a community.

    Args:
        db: Active SQLAlchemy session.
        ec_id: Energy community identifier used to scope the lookup.
        sp_id: Service point identifier expected to be linked to the
            given energy community.

    Returns:
        ec.ServicePoint: ORM service point object linked to the given
        community.

    Raises:
        HTTPException: If the service point is not linked to the given
        energy community.
    """
    service_point = (
        db.query(ec.ServicePoint)
        .join(ec.CommunityServicePoint, ec.CommunityServicePoint.sp_id == ec.ServicePoint.sp_id)
        .filter(
            ec.CommunityServicePoint.ec_id == ec_id,
            ec.ServicePoint.sp_id == sp_id,
        )
        .one_or_none()
    )
    if service_point is None:
        raise HTTPException(
            status_code=404,
            detail=f"Service point {sp_id} is not linked to energy community {ec_id}.",
        )
    return service_point


def _close_overlapping_coefficients(
    links: Iterable[ec.ServicePointAllocationLink],
    valid_from: datetime,
) -> None:
    """Close older coefficient windows that overlap a new validity start.

    The function updates already-loaded coefficient rows in memory by
    setting ``valid_to`` to ``valid_from`` when an older coefficient is
    still open or extends beyond the start of a new configuration.

    Args:
        links: Allocation-link ORM objects with preloaded coefficient
            rows.
        valid_from: Start timestamp of the new coefficient window.

    Returns:
        None
    """
    for link in links:
        for coefficient in link.coefficients:
            overlaps_new_window = coefficient.valid_from < valid_from and (
                coefficient.valid_to is None or coefficient.valid_to > valid_from
            )
            if overlaps_new_window:
                coefficient.valid_to = valid_from


def get_allocation_links_by_producer_crud(
    db: Session,
    ec_id: UUID,
    producer_sp_id: UUID,
) -> list[AllocationLinkData]:
    """Retrieve allocation links for one producer inside one community.

    The returned links include their coefficient history and are ordered
    by recipient service point identifier.

    Args:
        db: Active SQLAlchemy session.
        ec_id: Energy community identifier used to scope the lookup.
        producer_sp_id: Producer service point identifier used as the
            allocation source.

    Returns:
        list[AllocationLinkData]: Serialized allocation links for the
        given producer service point.
    """
    links = (
        db.query(ec.ServicePointAllocationLink)
        .options(selectinload(ec.ServicePointAllocationLink.coefficients))
        .filter(
            ec.ServicePointAllocationLink.ec_id == ec_id,
            ec.ServicePointAllocationLink.producer_sp_id == producer_sp_id,
        )
        .order_by(ec.ServicePointAllocationLink.recipient_sp_id)
        .all()
    )
    return [AllocationLinkData.model_validate(link) for link in links]


def upsert_producer_allocation_configuration_crud(
    db: Session,
    config: ProducerAllocationConfigurationCreate,
) -> ProducerAllocationConfigurationData:
    """Create or update a producer allocation configuration.

    The configuration is scoped to one energy community and one producer
    service point. The function validates service-point membership and
    roles, enforces unique recipients within the request, ensures that
    the coefficient sum does not exceed ``1.0``, closes overlapping
    previous coefficient windows, and then upserts the requested
    producer-to-recipient links and coefficient rows.

    Static allocation is represented by a coefficient row with an open
    ``valid_to`` value. Dynamic allocation is represented by multiple
    coefficient rows with different validity windows for the same link.

    Args:
        db: Active SQLAlchemy session.
        config: Validated producer allocation configuration payload.

    Returns:
        ProducerAllocationConfigurationData: Serialized representation of
        the stored configuration for the requested producer and validity
        window.

    Raises:
        HTTPException: If the community does not exist, if service points
        are not linked to the community, if roles are invalid, if
        recipients are duplicated, if producer and recipient are the same,
        or if the validity window or coefficient sum is invalid.
    """
    if config.valid_to is not None and config.valid_to <= config.valid_from:
        raise HTTPException(status_code=400, detail="valid_to must be later than valid_from.")
    if not config.allocations:
        raise HTTPException(status_code=400, detail="At least one allocation recipient is required.")

    community = db.query(ec.EnergyCommunity).filter(ec.EnergyCommunity.ec_id == config.ec_id).one_or_none()
    if community is None:
        raise HTTPException(status_code=404, detail=f"Energy community {config.ec_id} not found.")

    producer_service_point = _get_service_point_in_community(
        db=db,
        ec_id=config.ec_id,
        sp_id=config.producer_sp_id,
    )
    if producer_service_point.role != "producer":
        raise HTTPException(status_code=400, detail="Producer service point must have role 'producer'.")

    recipient_ids = [allocation.recipient_sp_id for allocation in config.allocations]
    if len(recipient_ids) != len(set(recipient_ids)):
        raise HTTPException(status_code=400, detail="Recipient service points must be unique within one request.")

    total_coefficient = sum(allocation.coefficient for allocation in config.allocations)
    if total_coefficient > 1.0 + 1e-9:
        raise HTTPException(status_code=400, detail="The sum of allocation coefficients must not exceed 1.0.")

    for recipient_id in recipient_ids:
        recipient_service_point = _get_service_point_in_community(
            db=db,
            ec_id=config.ec_id,
            sp_id=recipient_id,
        )
        if recipient_service_point.sp_id == producer_service_point.sp_id:
            raise HTTPException(status_code=400, detail="Producer and recipient service points must be different.")
        if recipient_service_point.role not in {"consumer", "prosumer"}:
            raise HTTPException(
                status_code=400,
                detail="Recipient service points must have role 'consumer' or 'prosumer'.",
            )

    existing_links = (
        db.query(ec.ServicePointAllocationLink)
        .options(selectinload(ec.ServicePointAllocationLink.coefficients))
        .filter(
            ec.ServicePointAllocationLink.ec_id == config.ec_id,
            ec.ServicePointAllocationLink.producer_sp_id == config.producer_sp_id,
        )
        .all()
    )
    existing_links_by_recipient = {link.recipient_sp_id: link for link in existing_links}
    _close_overlapping_coefficients(existing_links, config.valid_from)

    upserted_link_ids: list[UUID] = []
    for allocation in config.allocations:
        link = existing_links_by_recipient.get(allocation.recipient_sp_id)
        if link is None:
            link = ec.ServicePointAllocationLink(
                ec_id=config.ec_id,
                producer_sp_id=config.producer_sp_id,
                recipient_sp_id=allocation.recipient_sp_id,
                active=True,
            )
            db.add(link)
            db.flush()
            existing_links_by_recipient[allocation.recipient_sp_id] = link

        coefficient_row = next(
            (
                coefficient
                for coefficient in link.coefficients
                if coefficient.valid_from == config.valid_from
            ),
            None,
        )
        if coefficient_row is None:
            coefficient_row = ec.ServicePointAllocationCoefficient(
                allocation_link_id=link.id,
                valid_from=config.valid_from,
                valid_to=config.valid_to,
                coefficient=allocation.coefficient,
            )
            db.add(coefficient_row)
        else:
            coefficient_row.valid_to = config.valid_to
            coefficient_row.coefficient = allocation.coefficient

        upserted_link_ids.append(link.id)

    db.commit()

    refreshed_links = (
        db.query(ec.ServicePointAllocationLink)
        .options(selectinload(ec.ServicePointAllocationLink.coefficients))
        .filter(ec.ServicePointAllocationLink.id.in_(upserted_link_ids))
        .order_by(ec.ServicePointAllocationLink.recipient_sp_id)
        .all()
    )

    return ProducerAllocationConfigurationData(
        ec_id=config.ec_id,
        producer_sp_id=config.producer_sp_id,
        valid_from=config.valid_from,
        valid_to=config.valid_to,
        allocations=[AllocationLinkData.model_validate(link) for link in refreshed_links],
    )
