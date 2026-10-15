from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from app.db.session import engine
import asyncio
import logging
import yaml
from app.utils.audit import audit_logger_middleware, start_audit_worker
import app.api.members as members
import app.api.allocations as allocations
import app.api.service_points as service_points
import app.api.grid_elements as grid_elements
import app.api.meters as meters
import app.api.channels as channels
import app.api.assets as assets
import app.api.simulation as simulation
import app.api.flexibility as flexibility
import app.api.forecast as forecast
import app.api.pricing as pricing
import app.api.status as status

# import app.api.migrate as migrate
# jobs
from jobs.electricity_prices.electricity_prices import (
    upsert_day_ahead_prices, 
    upsert_current_prices,
    upsert_historical_prices
)
from jobs.co2_emissions.co2_emissions import(
    upsert_latest_co2_emission,
    upsert_today_co2_emissions,
    upsert_yesterday_co2_emissions
)
from jobs.gp_eudt.upsert_data import upsert_eudt_meter_data
from jobs.gp_sonnen.upsert_historical_data import upsert_sonnen_historical_data_from_files
from jobs.weather_data.weather_data import upsert_historical_weather_data
from jobs.eds_publisher.eds_publisher import upsert_kpi_report
from jobs.mqtt_logger.mqtt_logger import MQTTLogger, MQTTSettings
    

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from app.keycloak_instance import kc


scheduler = BackgroundScheduler()   #timezone="UTC")

def load_config():
    with open("/app/jobs/mqtt_logger/config/config.yaml", 'r') as f:
        return yaml.safe_load(f)

config = load_config()
mqtt_settings = MQTTSettings(
    host = config['ecmp']['host'],
    port = config['ecmp']['port'],
    certs = config['ecmp']['cert'],
    root_topic = config['ecmp']['root_topic']
)
asts = {}
for asset in config['assets']:
    asts[asset['id']] = asset

logger.debug(asts)

logger.debug("Starting MQTT logger")

mqtt_proxy = MQTTLogger(mqtt_settings=mqtt_settings, assets=asts)




@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    await kc.init()
    await start_audit_worker()
    scheduler.add_job(upsert_day_ahead_prices, CronTrigger(minute="*/15"))
    scheduler.add_job(upsert_historical_prices, CronTrigger(minute="*/1"))
    # scheduler.add_job(upsert_day_ahead_prices, CronTrigger(hour="12-23", minute="*/15"))
    scheduler.add_job(upsert_current_prices, CronTrigger(minute="*/15"))
    scheduler.add_job(upsert_latest_co2_emission, CronTrigger(minute="*/15"))
    scheduler.add_job(upsert_today_co2_emissions, CronTrigger(minute="*/15"))
    # scheduler.add_job(upsert_yesterday_co2_emissions, CronTrigger(minute="*/15"))
    # scheduler.add_job(upsert_eudt_meter_data, CronTrigger(hour="*/12"))
    scheduler.add_job(upsert_eudt_meter_data, CronTrigger(minute="*/15"))
    # scheduler.add_job(upsert_sonnen_historical_data_from_files, CronTrigger(hour="*/12"))
    scheduler.add_job(upsert_sonnen_historical_data_from_files, CronTrigger(minute="*/15"))
    scheduler.add_job(upsert_historical_weather_data, CronTrigger(minute="*/15"))
    # scheduler.add_job(upsert_kpi_report, CronTrigger(minute="*/1"))

    scheduler.start()
    asyncio.create_task(mqtt_proxy.run())
    yield
    # shutdown
    scheduler.shutdown()



# start app
app = FastAPI(lifespan=lifespan)

app.middleware("http")(audit_logger_middleware)


# register routers
app.include_router(members.router)
app.include_router(allocations.router)
app.include_router(grid_elements.router)
app.include_router(service_points.router)
app.include_router(meters.router)
app.include_router(channels.router)
app.include_router(assets.router)
app.include_router(simulation.router)
app.include_router(flexibility.router)
app.include_router(forecast.router)
app.include_router(pricing.router)
app.include_router(status.router)
# app.include_router(migrate.router)



