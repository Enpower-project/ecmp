"""Electricity price upsert jobs.

Command-line usage for range upsert:

    python -m jobs.electricity_prices.electricity_prices ES 2026-05-01T00:00:00+00:00 2026-05-03T00:00:00+00:00

Command-line usage to copy today's prices to tomorrow:

    python -m jobs.electricity_prices.electricity_prices --copy-current-day ES

Datetime arguments must be in ISO-8601 format and must include an explicit
timezone offset.
"""

import argparse
import os
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from jobs.entsoe_tp.entsoe_tp import tp_api
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert
from app.models import ec
from app.db.session import SessionLocal
from dotenv import load_dotenv


logger = logging.getLogger(__name__)



load_dotenv()

# --- Configure DB URL ---
API_TOKEN = os.getenv("ENTSOE_TOKEN")
SUPPORTED_AREAS = ("AT", "SI", "IR", "ES")
PRICE_AREA_TIMEZONES = {
    "AT": ZoneInfo("Europe/Vienna"),
    "SI": ZoneInfo("Europe/Ljubljana"),
    "IR": ZoneInfo("Europe/Dublin"),
    "ES": ZoneInfo("Europe/Madrid"),
}
# SUPPORTED_AREAS = ("AT", "SI", "IR", "ES")


def _get_zone_id(session: Session, area_code: str):
    stmt = select(ec.PriceZone).where(ec.PriceZone.name == area_code)
    zone = session.scalars(stmt).one_or_none()
    if zone is None:
        raise ValueError(
            f"Price zone '{area_code}' not found in the database. "
            f"Create a price_zone row for '{area_code}' before running the import."
        )
    return zone.zone_id


def _build_price_records(prices, zone_id, category, unit):
    records = []
    for record in prices:
        records.append({
            "ts": datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")),
            "zone_id": zone_id,
            "category": category,
            "value": record["value"],
            "unit": unit,
        })
    return records


def _deduplicate_price_records(records, area_code: str):
    deduplicated = {}

    for record in records:
        key = (record["ts"], record["zone_id"], record["category"])
        deduplicated[key] = record

    duplicate_count = len(records) - len(deduplicated)
    if duplicate_count:
        logger.warning(
            "Dropped %s duplicate electricity price records before upsert for area %s.",
            duplicate_count,
            area_code,
        )

    return list(deduplicated.values())


def _upsert_prices(records, area_code: str):
    if not records:
        logger.warning("No electricity price records returned for area %s.", area_code)
        return

    records = _deduplicate_price_records(records, area_code)

    stmt = insert(ec.EnergyPriceTimeseries).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts", "zone_id", "category"],
        set_={"value": stmt.excluded.value, "unit": stmt.excluded.unit}
    )

    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()

    logger.info("Upserted %s records into energy_price_timeseries for area %s.", len(records), area_code)


def _process_area_prices(fetch_prices, area_code: str, category: str, unit: str):
    try:
        prices = fetch_prices(area_code)
        with SessionLocal() as session:
            zone_id = _get_zone_id(session, area_code)
        records = _build_price_records(prices, zone_id, category, unit)
        _upsert_prices(records, area_code)
    except SQLAlchemyError:
        logger.exception("Database error while upserting electricity prices for area %s.", area_code)


def upsert_day_ahead_prices():
    transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)

    category = 'day_ahead'
    unit = "EUR/kWh"

    for area_code in SUPPORTED_AREAS:
        _process_area_prices(
            transparency_platform.get_day_ahead_prices,
            area_code,
            category,
            unit,
        )


def upsert_current_prices():
    transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)

    category = 'day_ahead'
    unit = "EUR/MWh"

    for area_code in SUPPORTED_AREAS:
        _process_area_prices(
            transparency_platform.get_current_prices,
            area_code,
            category,
            unit,
        )


def upsert_historical_prices():
    transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)

    category = 'day_ahead'
    unit = "EUR/MWh"

    for area_code in SUPPORTED_AREAS:
        _process_area_prices(
            transparency_platform.get_30_day_prices,
            area_code,
            category,
            unit,
        )


def upsert_prices_in_range(area_code: str, start_dt: datetime, end_dt: datetime):
    if area_code not in SUPPORTED_AREAS:
        raise ValueError(f"Unsupported area_code '{area_code}'. Supported values: {SUPPORTED_AREAS}")

    transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)

    category = 'day_ahead'
    unit = "EUR/MWh"

    prices = transparency_platform.get_prices_in_range(area_code, start_dt, end_dt)
    with SessionLocal() as session:
        zone_id = _get_zone_id(session, area_code)
    records = _build_price_records(prices, zone_id, category, unit)
    _upsert_prices(records, area_code)


def copy_current_day_prices_to_next_day(
    area_code: str,
    current_time: datetime | None = None,
    category: str = "day_ahead",
) -> int:
    """Copy an area's current local-day prices into the following local day.

    Existing prices for the destination timestamps are updated, so this function
    can be run repeatedly without creating duplicate rows. ``current_time`` is
    primarily useful for deterministic job runs and tests; it defaults to now
    in UTC.
    """
    if area_code not in SUPPORTED_AREAS:
        raise ValueError(f"Unsupported area_code '{area_code}'. Supported values: {SUPPORTED_AREAS}")

    current_time = current_time or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("current_time must include a timezone offset.")

    area_timezone = PRICE_AREA_TIMEZONES[area_code]
    local_current_time = current_time.astimezone(area_timezone)
    local_today_start = datetime.combine(local_current_time.date(), time.min, area_timezone)
    local_tomorrow_start = local_today_start + timedelta(days=1)
    today_start = local_today_start.astimezone(timezone.utc)
    tomorrow_start = local_tomorrow_start.astimezone(timezone.utc)
    timestamp_offset = tomorrow_start - today_start

    with SessionLocal() as session:
        zone_id = _get_zone_id(session, area_code)
        source_prices = session.scalars(
            select(ec.EnergyPriceTimeseries).where(
                ec.EnergyPriceTimeseries.zone_id == zone_id,
                ec.EnergyPriceTimeseries.category == category,
                ec.EnergyPriceTimeseries.ts >= today_start,
                ec.EnergyPriceTimeseries.ts < tomorrow_start,
            )
        ).all()

    records = [
        {
            "ts": price.ts + timestamp_offset,
            "zone_id": price.zone_id,
            "category": price.category,
            "value": price.value,
            "unit": price.unit,
        }
        for price in source_prices
    ]

    if not records:
        logger.warning(
            "No current-day %s electricity prices available to copy for area %s.",
            category,
            area_code,
        )
        return 0

    _upsert_prices(records, area_code)
    logger.info(
        "Published %s current local-day %s electricity prices for area %s (%s) to %s.",
        len(records),
        category,
        area_code,
        area_timezone.key,
        local_tomorrow_start.date().isoformat(),
    )
    return len(records)


def _parse_cli_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime '{value}'. Use ISO-8601 format with an explicit timezone, "
            "for example 2026-05-01T00:00:00+00:00."
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            f"Invalid datetime '{value}'. A timezone offset is required, "
            "for example 2026-05-01T00:00:00+00:00."
        )

    return parsed


def main():
    """Run a one-off price range upsert or copy today's prices to tomorrow.

    Example:
        python -m jobs.electricity_prices.electricity_prices ES 2026-05-01T00:00:00+00:00 2026-05-03T00:00:00+00:00

        # for duplicating current day prices to the next day:
        python -m jobs.electricity_prices.electricity_prices --copy-current-day ES

    Parameters:
        area_code: One of AT, SI, IR, ES.
        start_dt: Inclusive range start in ISO-8601 format with timezone.
        end_dt: Exclusive range end in ISO-8601 format with timezone.
    """
    parser = argparse.ArgumentParser(
        description="Upsert electricity prices for one area between start_dt and end_dt."
    )
    parser.add_argument(
        "--copy-current-day",
        action="store_true",
        help="Copy the selected area's current local-day prices to the following local day.",
    )
    parser.add_argument(
        "area_code",
        choices=SUPPORTED_AREAS,
        nargs="?",
        help="Price zone code to fetch, upsert, or copy.",
    )
    parser.add_argument(
        "start_dt",
        type=_parse_cli_datetime,
        nargs="?",
        help="Range start datetime in ISO-8601 format with timezone, e.g. 2026-05-01T00:00:00+00:00",
    )
    parser.add_argument(
        "end_dt",
        type=_parse_cli_datetime,
        nargs="?",
        help="Range end datetime in ISO-8601 format with timezone, e.g. 2026-05-03T00:00:00+00:00",
    )

    args = parser.parse_args()
    if args.copy_current_day:
        if args.area_code is None:
            parser.error("area_code is required with --copy-current-day")
        if args.start_dt is not None or args.end_dt is not None:
            parser.error("start_dt and end_dt cannot be used with --copy-current-day")
        copy_current_day_prices_to_next_day(args.area_code)
        return

    if args.area_code is None or args.start_dt is None or args.end_dt is None:
        parser.error("area_code, start_dt, and end_dt are required for a range upsert")

    upsert_prices_in_range(args.area_code, args.start_dt, args.end_dt)


if __name__ == "__main__":
    main()
