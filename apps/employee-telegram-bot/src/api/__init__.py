from src.api.client import BackendClient, LiveBackendClient
from src.api.stub import StubBackendClient
from src.api.tokens import MemoryTokenStorage, TokenStorage
from src.config.settings import settings


def build_client() -> BackendClient:
    """API_MODE=stub -> фейковые данные, API_MODE=live -> реальный backend-api."""
    if settings.api_mode == "stub":
        return StubBackendClient()
    return LiveBackendClient()


__all__ = [
    "BackendClient",
    "LiveBackendClient",
    "StubBackendClient",
    "TokenStorage",
    "MemoryTokenStorage",
    "build_client",
]
