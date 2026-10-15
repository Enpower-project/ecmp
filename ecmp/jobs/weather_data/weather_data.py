import os
import logging
from datetime import datetime
from jobs.openmeteo.openmeteo.openmeteo import OpenMeteo
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.models import ec
from app.db.session import SessionLocal
# from dotenv import load_dotenv


logger = logging.getLogger(__name__)



def upsert_historical_weather_data():
    om = OpenMeteo()

    # get latitude and longitude of the pilot location
    with SessionLocal() as session:
        stmt = select(ec.WeatherLocation).where(ec.WeatherLocation.name=="Greenpoint Location")
        loc = session.scalars(stmt).one_or_none()
        latitude = loc.latitude
        longitude = loc.longitude 
        location_id = loc.location_id

    data = om.weather_history(latitude, longitude, history=10)
    resample_15min = data.resample('15T').ffill()

    # logger.debug(f"data: {resample_15min}")

    column_names = ['temperature', 'radiation']
    for column_name in column_names:

        variable = None
        if column_name == 'temperature':
            variable = 'temperature'
        else:
            variable = 'solar_radiation'

        records = [
            {"ts": datetime.fromisoformat(ts.isoformat().replace("Z", "+00:00")), "location_id": location_id, "variable": variable, "value": val} 
            for ts, val in resample_15min[column_name].items()
        ]

        stmt = insert(ec.WeatherTimeseries).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ts", "location_id", "variable"],
            set_={"value": stmt.excluded.value}
        )

        with SessionLocal() as session:
            session.execute(stmt)
            session.commit()
        
        logger.info(f"Upserted {len(records)} records into weather_timeseries.")
    


# load_dotenv()

# # --- Configure DB URL ---
# API_TOKEN = os.getenv("ENTSOE_TOKEN")


# def upsert_day_ahead_prices():
#     transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)
#     day_ahead_prices = transparency_platform.get_day_ahead_prices('AT')

#     # logger.debug(f"series: {day_ahead_prices}")

#     # get price zone ID
#     zone_id ='1073cc08-7d75-4806-ad5e-8742dce0ef36'
#     category = 'day_ahead'
#     unit = "EUR/kWh"

#     # prepare bulk insert
#     records = []
#     for record in day_ahead_prices:
#         records.append({
#             "ts": datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")),
#             "zone_id": zone_id,
#             "category": category,
#             "value": record["value"],
#             "unit": unit,
#         })

#     stmt = insert(mdms.EnergyPriceTimeseries).values(records)
#     stmt = stmt.on_conflict_do_update(
#         index_elements=["ts", "zone_id", "category"],
#         set_={"value": stmt.excluded.value}
#     )

#     with SessionLocal() as session:
#         session.execute(stmt)
#         session.commit()
    
#     logger.info(f"Upserted {len(records)} records into energy_price_timeseries.")


# def upsert_current_prices():
#     transparency_platform = tp_api.TransparencyPlatform(API_TOKEN)
#     current_prices = transparency_platform.get_current_prices('AT')

#     # logger.debug(f"series: {current_prices}")

#     # get price zone ID
#     zone_id ='1073cc08-7d75-4806-ad5e-8742dce0ef36'
#     category = 'day_ahead'
#     unit = "EUR/MWh"

#     # prepare bulk insert
#     records = []
#     for record in current_prices:
#         records.append({
#             "ts": datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")),
#             "zone_id": zone_id,
#             "category": category,
#             "value": record["value"],
#             "unit": unit,
#         })

#     stmt = insert(mdms.EnergyPriceTimeseries).values(records)
#     stmt = stmt.on_conflict_do_update(
#         index_elements=["ts", "zone_id", "category"],
#         set_={"value": stmt.excluded.value}
#     )

#     with SessionLocal() as session:
#         session.execute(stmt)
#         session.commit()
    
#     logger.info(f"Upserted {len(records)} records into energy_price_timeseries.")