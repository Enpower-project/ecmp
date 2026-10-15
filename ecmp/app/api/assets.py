from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from app.db.session import SessionLocal
from app.auth.dependencies import require_token
from app.schemas.assets import AssetSetLevel
import msgpack
import yaml
import uuid
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from dataclasses import dataclass

import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/assets", tags=["assets"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
        logger.debug(f"Connected with result code {reason_code}")


def load_config():
    with open("/app/jobs/mqtt_logger/config/config.yaml", 'r') as f:
        return yaml.safe_load(f)


@dataclass
class MQTTSettings:
    host: str
    port: int
    certs: str
    root_topic: str


config = load_config()
mqtt_settings = MQTTSettings(
    host = config['ecmp']['host'],
    port = config['ecmp']['port'],
    certs = config['ecmp']['cert'],
    root_topic = config['ecmp']['root_topic']
)


@router.post("",)
async def set_output(request: AssetSetLevel, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    try:
        client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
        # Assign callbacks
        client.on_connect = _on_mqtt_connect
        client.tls_set(
            ca_certs=mqtt_settings.certs + 'ca-chain.cert.pem',
            certfile=mqtt_settings.certs + 'client.cert.pem',
            keyfile=mqtt_settings.certs + 'client.key.pem'
        )
        client.connect(mqtt_settings.host, port=mqtt_settings.port)
        client.loop_start()
        # create control message
        usg_id = '59b80f8d-d3ef-4033-abde-143319e3ac41'
        asset_id = uuid.UUID(str(request.asset_id)).bytes
        msg = {
                "aid": asset_id,
                "k": request.key,
                "v": request.value,
                "u": request.unit
        }
        client.publish(f"enpower/{usg_id}/control", payload=msgpack.packb(msg, use_bin_type=True), qos=0, retain=False)
        # send control message
        client.loop_stop()
        client.disconnect()

        logger.debug("Control message published")

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "Asset level setting set"}
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred", "detail": str(e)}
        )
