import os
import asyncio
import logging
import json
import msgpack
import yaml
import uuid
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from datetime import datetime, timezone
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.models import ec
from app.db.session import SessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class MQTTSettings:
    host: str
    port: int
    certs: str
    root_topic: str

@dataclass
class DBSettings:
    name: str
    host: str
    port: int
    user: str
    pwd: str

@dataclass
class SensorInfo:
    sensor_name: str
    sensor_id: str
    asset_id: str
    asset_uid: str



class MQTTLogger:
    def __init__(self, mqtt_settings: MQTTSettings, assets):
        self.hostname = mqtt_settings.host
        self.port = mqtt_settings.port
        self._ca = mqtt_settings.certs + 'ca-chain.cert.pem'
        self._client_key = mqtt_settings.certs + 'client.key.pem'
        self._client_cert = mqtt_settings.certs + 'client.cert.pem'
        self._root_topic = mqtt_settings.root_topic
        self.assets = assets
        self.asset_info = {}
        self.pool = None
        self._client = None
        self._loop = None
        self.queue = asyncio.Queue()


    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        logger.info(f"Connected with result code {reason_code}")
        # subscribe to all related topics based on USG ID
        client.subscribe(f"enpower/telemetry")


    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload = msgpack.unpackb(msg.payload, raw=False)
            logger.debug(f"Received message: {msg.topic} -> {payload}")
            # Put the data into the asyncio queue
            asyncio.run_coroutine_threadsafe(self.queue.put((msg.topic, payload)), self.loop)
        except Exception as e:
            logger.exception(f"Exception: {e}")
    

    async def get_asset_info(self):
        """
        asset_info: {
            '85dee1c6-4aa4-47a9-a2ab-eb7db8307bdb': {
                'id': '85dee1c6-4aa4-47a9-a2ab-eb7db8307bdb', 
                'name': 'BSS139188_raw', 
                'uid': '139188_raw', 
                'sensors': {
                    '0f5f54e0-a1ac-4c6e-897e-90653c09ed93': {
                        'id': '0f5f54e0-a1ac-4c6e-897e-90653c09ed93', 
                        'name': 'charging', 
                        'uid': '27000'
                        }, 
                    '64072547-533c-45e1-b2fb-5f7fad510afc': {
                        'id': '64072547-533c-45e1-b2fb-5f7fad510afc', 
                        'name': 'discharging', 
                        'uid': '27001'
                        }, 
                    'b54c6445-c838-4f95-b3cd-6f7bf8b9b400': {
                        ...
                        },
                    ...
                }
            }
        """
        for asset_id in self.assets:
            # get asset info from DB by asset ID
            aid = uuid.UUID(asset_id)
            logger.debug(f"asset_id: {aid}")

            with SessionLocal() as session:
                stmt = select(ec.Asset).where(ec.Asset.id==aid)
                asset = session.scalars(stmt).one_or_none()
                logger.debug(f"asset: {asset}")
                if asset is None:
                    logger.error(f"Asset not found in DB for ID: {asset_id}")
                    continue  # skip this asset, or handle differently
                self.asset_info[asset_id] = {
                    'id': asset.id,
                    'hrid': asset.hrid,
                    'channels': {} 
                    }
                # get submeter for the selected asset
                stmt = select(ec.SubMeter).where(ec.SubMeter.asset_id==asset_id)
                subm = session.scalars(stmt).one_or_none()
                submeter_id = subm.id
                # get all subchannels for the selected submeter
                stmt = select(ec.SubChannel).where(ec.SubChannel.submeter_id==submeter_id)
                subchns = session.scalars(stmt).all()
                for subchn in subchns:
                    self.asset_info[asset_id]['channels'][str(subchn.id)] = {
                        'id': str(subchn.id),
                        'name': subchn.role,
                        'hrid': subchn.hrid
                    }
            
        logger.debug(f"asset_info: {self.asset_info}")


    async def msg_handler(self):
        """
        Create worker that reads from the message queue and writes to the DB
        """
        while True:
            try:
                message = await self.queue.get()
                logger.debug(message)
                try:
                    topic = message[0]
                    data = message[1]

                    parts = topic.split('/')
                    if 2 != len(parts):
                        raise ValueError(f"Unexpected topic format: {topic}")

                    asset_id = str(uuid.UUID(bytes=data['aid']))
                    channel_type = parts[1]

                    # measurement timestamp transformation
                    timestamp = datetime.fromtimestamp(data.get("ts") / 1_000_000, tz=timezone.utc)
                    timestamp = timestamp.replace(tzinfo=None)
                    # system/server/update timestamp
                    now = datetime.now(timezone.utc)
                    dt= now.isoformat(timespec='microseconds').replace('+00:00', 'Z')
                    updated_at = datetime.strptime(dt, "%Y-%m-%dT%H:%M:%S.%fZ")

                    # find subreadings_raw ids

                    if 'telemetry' == channel_type:
                        asset_uid = self.asset_info[asset_id]['hrid']

                        records = []
                        
                        # for report in data['data']:
                        channel_name = data.get("k")
                        # get channel ID
                        channel_id = None
                        for channel in self.asset_info[asset_id]['channels']:
                            if self.asset_info[asset_id]['channels'][channel]['name'] == channel_name:
                                channel_id = channel

                        value = float(data.get("v"))
                        unit = data.get("u")

                        logger.debug(f"{timestamp}, {channel_id}, {value}, {unit}")

                        records.append({"id": channel_id, "ts": timestamp, "value": value})


                        stmt = insert(ec.SubReadingRAW).values(records)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["id", "ts"],
                            set_={"value": stmt.excluded.value}
                        )

                        with SessionLocal() as session:
                            session.execute(stmt)
                            session.commit()

                except Exception as e:
                    logging.exception(f"Error processing message: {message}")
                finally:
                    self.queue.task_done()
                    
            except Exception as queue_error:
                logging.exception("Error retrieving message from queue")
                


    async def run(self):
        self.loop = asyncio.get_running_loop()
        self._client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)

        # Assign callbacks
        self._client.on_connect = self._on_mqtt_connect
        self._client.on_message = self._on_mqtt_message

        self._client.tls_set(
            ca_certs=self._ca,
            certfile=self._client_cert,
            keyfile=self._client_key
        )

        # Connect to the broker (change host/port as needed)
        self._client.connect(self.hostname, port=self.port)
        self._client.loop_start()

        # download and populate asset/sensor info dictionary
        await self.get_asset_info()

        # Start 5 DB workers
        # workers = [asyncio.create_task(self.db_worker(self.pool)) for _ in range(5)]

        # # Run forever
        # await asyncio.gather(*workers)
        await self.msg_handler()
        
        # gracefully close the pool
        # await self.pool.close()
        # will not happen due to process_queue infinite loop
        self._client.loop_stop()


def load_config():
    with open("code/config/config.yaml", 'r') as f:
        return yaml.safe_load(f)


if '__main__' == __name__:
    config = load_config()

    mqtt_settings = MQTTSettings(
        host = config['ecmp']['host'],
        port = config['ecmp']['port'],
        certs = config['ecmp']['cert'],
        root_topic = config['ecmp']['root_topic']
    )

    assets = {}
    for asset in config['assets']:
        assets[asset['id']] = asset

    logger.debug(assets)

    logger.debug("Starting MQTT logger")

    mqtt_proxy = MQTTLogger(mqtt_settings=mqtt_settings, assets=assets)

    asyncio.run(mqtt_proxy.run())
