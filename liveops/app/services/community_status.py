import asyncio
from copy import deepcopy


class CommunityStatusStore:
    """
    Hold the latest energy community state derived from live telemetry.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._snapshot = {
            "last_update": None,
            "assets": {},
        }


    async def ingest(self, record: dict) -> None:
        """
        Merge a normalized telemetry record into the current community snapshot.
        """
        async with self._lock:
            asset = self._snapshot["assets"].setdefault(
                record["asset_id"],
                {
                    "asset_id": record["asset_id"],
                    "asset_name": record.get("asset_name"),
                    "asset_hrid": record.get("asset_hrid"),
                    "channels": {},
                },
            )
            asset["channels"][record["channel_name"]] = {
                "channel_name": record["channel_name"],
                "channel_hrid": record.get("channel_hrid"),
                "value": record["value"],
                "unit": record.get("unit"),
                "timestamp": record["timestamp"],
                "received_at": record["received_at"],
                "topic": record["topic"],
            }
            self._snapshot["last_update"] = record["received_at"]


    async def get_snapshot(self) -> dict:
        """
        Return a safe copy of the current community status snapshot.
        """
        async with self._lock:
            return deepcopy(self._snapshot)
