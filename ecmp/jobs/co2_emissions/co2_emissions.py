import os
import logging
from datetime import datetime
from jobs.entsoe_tp.entsoe_tp import tp_api
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.models import ec
from app.db.session import SessionLocal
from dotenv import load_dotenv


logger = logging.getLogger(__name__)



load_dotenv()

# --- Configure DB URL ---
API_TOKEN = os.getenv("ENTSOE_TOKEN")


def upsert_yesterday_co2_emissions():
    transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)
    emissions = transparency_platform.get_yesterday_co2_emission('AT')

    # get emission zone ID
    zone_id = None
    with SessionLocal() as session:
        stmt = select(ec.EmissionZone).where(ec.EmissionZone.name=='AT')
        zone = session.scalars(stmt).one_or_none()
        zone_id = zone.zone_id
        
    unit = "g/kWh"

    # prepare bulk insert
    records = []
    for record in emissions:
        records.append({
            "ts": datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")),
            "zone_id": zone_id,
            "value": record["emission"],
            "unit": unit,
        })

    stmt = insert(ec.CO2EmissionsTimeseries).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts", "zone_id"],
        set_={"value": stmt.excluded.value}
    )

    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()
    
    logger.info(f"Upserted {len(records)} records into co2_emissions_timeseries.")


    
def upsert_today_co2_emissions():
    transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)
    emissions = transparency_platform.get_today_co2_emission('AT')

    # get emission zone ID
    zone_id = None
    with SessionLocal() as session:
        stmt = select(ec.EmissionZone).where(ec.EmissionZone.name=='AT')
        zone = session.scalars(stmt).one_or_none()
        zone_id = zone.zone_id

    unit = "g/kWh"

    # prepare bulk insert
    records = []
    for record in emissions:
        records.append({
            "ts": datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")),
            "zone_id": zone_id,
            "value": record["emission"],
            "unit": unit,
        })

    stmt = insert(ec.CO2EmissionsTimeseries).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts", "zone_id"],
        set_={"value": stmt.excluded.value}
    )

    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()
    
    logger.info(f"Upserted {len(records)} records into co2_emissions_timeseries.")



def upsert_latest_co2_emission():
    transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)
    emissions = transparency_platform.get_latest_co2_emission('AT')

    # get emission zone ID
    zone_id = None
    with SessionLocal() as session:
        stmt = select(ec.EmissionZone).where(ec.EmissionZone.name=='AT')
        zone = session.scalars(stmt).one_or_none()
        zone_id = zone.zone_id

    unit = "g/kWh"

    # prepare bulk insert
    records = []
    records.append({
        "ts": datetime.fromisoformat(emissions["timestamp"].replace("Z", "+00:00")),
        "zone_id": zone_id,
        "value": emissions["emission"],
        "unit": unit,
    })

    stmt = insert(ec.CO2EmissionsTimeseries).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts", "zone_id"],
        set_={"value": stmt.excluded.value}
    )

    with SessionLocal() as session:
        session.execute(stmt)
        session.commit()
    
    logger.info(f"Upserted {len(records)} records into co2_emissions_timeseries.")




