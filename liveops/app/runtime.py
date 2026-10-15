from app.repositories.live_telemetry import PendingLiveTelemetryRepository
from app.services.community_status import CommunityStatusStore


status_store = CommunityStatusStore()
telemetry_repository = PendingLiveTelemetryRepository()
