from contextlib import asynccontextmanager
import asyncio
from contextlib import suppress

from fastapi import FastAPI

from app.api.status import router as status_router
from app.keycloak_instance import kc
from app.runtime import status_store, telemetry_repository
from jobs.mqtt_interface.mqtt_interface import (
    LiveTelemetryMQTTInterface,
    MQTTSettings,
    load_config,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await kc.init()
    config = load_config()
    mqtt_settings = MQTTSettings(
        host=config["liveops"]["host"],
        port=config["liveops"]["port"],
        certs=config["liveops"]["cert"],
        root_topic=config["liveops"]["root_topic"],
    )
    assets = {asset["id"]: asset for asset in config.get("assets", [])}
    mqtt_interface = LiveTelemetryMQTTInterface(
        mqtt_settings=mqtt_settings,
        assets=assets,
        repository=telemetry_repository,
        status_store=status_store,
    )
    app.state.mqtt_interface = mqtt_interface
    app.state.mqtt_task = asyncio.create_task(mqtt_interface.run())
    yield
    await mqtt_interface.shutdown()
    app.state.mqtt_task.cancel()
    with suppress(asyncio.CancelledError):
        await app.state.mqtt_task


app = FastAPI(
    title="LiveOps",
    description=(
        "Live energy community operations service for real-time monitoring, "
        "dispatch, and operational control."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(status_router)
