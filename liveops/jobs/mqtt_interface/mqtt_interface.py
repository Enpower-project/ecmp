import asyncio
import logging
import msgpack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import uuid

import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
import yaml


logger = logging.getLogger(__name__)


@dataclass
class MQTTSettings:
    host: str
    port: int
    certs: str
    root_topic: str


class LiveTelemetryMQTTInterface:
    """
    Subscribe to live telemetry topics, normalize messages, persist them, and
    update the in-process community status store.
    """

    def __init__(self, mqtt_settings: MQTTSettings, assets: dict, repository, status_store):
        self.hostname = mqtt_settings.host
        self.port = mqtt_settings.port
        self._ca = mqtt_settings.certs + "ca-chain.cert.pem"
        self._client_key = mqtt_settings.certs + "client.key.pem"
        self._client_cert = mqtt_settings.certs + "client.cert.pem"
        self._root_topic = mqtt_settings.root_topic
        self.assets = assets
        self.repository = repository
        self.status_store = status_store
        self._client = None
        self.loop = None
        self.queue = asyncio.Queue()

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        logger.info("Connected to MQTT broker with result code %s", reason_code)
        client.subscribe(f"{self._root_topic}/telemetry")

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            payload = msgpack.unpackb(msg.payload, raw=False)
            asyncio.run_coroutine_threadsafe(self.queue.put((msg.topic, payload)), self.loop)
        except Exception:
            logger.exception("Failed to decode MQTT payload from topic %s", msg.topic)

    def _build_record(self, topic: str, data: dict) -> dict:
        parts = topic.split("/")
        if len(parts) != 2 or parts[1] != "telemetry":
            raise ValueError(f"Unexpected topic format: {topic}")

        asset_id = str(uuid.UUID(bytes=data["aid"]))
        if self.assets and asset_id not in self.assets:
            raise ValueError(f"Received telemetry for unmanaged asset {asset_id}")

        asset_config = self.assets.get(asset_id, {})
        channel_name = data.get("k")
        timestamp = datetime.fromtimestamp(data["ts"] / 1_000_000, tz=timezone.utc).isoformat()
        received_at = datetime.now(timezone.utc).isoformat()

        return {
            "asset_id": asset_id,
            "asset_name": asset_config.get("name"),
            "asset_hrid": asset_config.get("hrid"),
            "channel_name": channel_name,
            "channel_hrid": asset_config.get("channels", {}).get(channel_name),
            "value": float(data.get("v")),
            "unit": data.get("u"),
            "timestamp": timestamp,
            "received_at": received_at,
            "topic": topic,
            "payload": data,
        }

    async def msg_handler(self):
        """
        Consume MQTT messages, normalize them, persist them, and update the
        in-memory community status.
        """
        while True:
            topic, data = await self.queue.get()
            try:
                record = self._build_record(topic, data)
                await self.repository.store(record)
                await self.status_store.ingest(record)
            except Exception:
                logger.exception("Error processing live telemetry message from topic %s", topic)
            finally:
                self.queue.task_done()

    async def run(self):
        """
        Start the MQTT client and process incoming telemetry indefinitely.
        """
        self.loop = asyncio.get_running_loop()
        self._client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_mqtt_connect
        self._client.on_message = self._on_mqtt_message
        self._client.tls_set(
            ca_certs=self._ca,
            certfile=self._client_cert,
            keyfile=self._client_key,
        )
        self._client.connect(self.hostname, port=self.port)
        self._client.loop_start()
        await self.msg_handler()

    async def shutdown(self):
        """
        Stop the MQTT network loop.
        """
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()


def load_config():
    """
    Load the MQTT interface configuration from the LiveOps config file.
    """
    config_path = Path(__file__).resolve().parent / "config" / "config.yaml"
    with config_path.open("r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)
