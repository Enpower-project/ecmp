import logging
from abc import ABC, abstractmethod


logger = logging.getLogger(__name__)


class LiveTelemetryRepository(ABC):
    """
    Persistence abstraction for incoming live telemetry.
    """

    @abstractmethod
    async def store(self, record: dict) -> None:
        """
        Persist a normalized telemetry record.
        """


class PendingLiveTelemetryRepository(LiveTelemetryRepository):
    """
    Placeholder repository until the dedicated live database is defined.
    """

    async def store(self, record: dict) -> None:
        logger.debug(
            "Skipping live telemetry persistence because no live database is configured yet: %s",
            {
                "asset_id": record["asset_id"],
                "channel": record["channel_name"],
                "timestamp": record["timestamp"],
            },
        )
