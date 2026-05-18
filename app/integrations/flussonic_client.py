"""
Flussonic Media Server — read-only HTTP client.

SECURITY MODEL:
  - Credentials (user/password) live ONLY in backend .env.
  - This module never returns credentials or auth headers to callers.
  - WRITE operations are explicitly blocked — calling them raises RuntimeError.
  - The only data sent to the frontend is: playback_url, token, expires_in.

READ-ONLY operations allowed:
  - list_streams()         → GET /flussonic/api/v3/streams
  - get_stream(name)       → GET /flussonic/api/v3/streams/{name}
  - get_stream_status(name)→ simplified alive/client_count dict
  - stream_hls_url(name)   → constructs HLS URL (no API call, no credentials)

WRITE operations (create/update/delete/restart/reload) are blocked.
"""
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_API_PREFIX = "/flussonic/api/v3"
_TIMEOUT = 8.0  # seconds — don't block the response path for long


@dataclass(frozen=True)
class StreamStatus:
    name: str
    alive: bool
    client_count: int
    input_alive: bool


class _WriteBlocker:
    """Mixin that blocks any accidental write method calls."""

    def _deny_write(self, operation: str) -> None:
        raise RuntimeError(
            f"FlussonicClient is READ-ONLY. "
            f"Operation '{operation}' is not permitted. "
            f"Manage streams directly in the Flussonic admin panel."
        )

    # Explicit stubs for common write operations so IDEs autocomplete safely.
    def create_stream(self, *_, **__):  # type: ignore[no-untyped-def]
        self._deny_write("create_stream")

    def update_stream(self, *_, **__):  # type: ignore[no-untyped-def]
        self._deny_write("update_stream")

    def delete_stream(self, *_, **__):  # type: ignore[no-untyped-def]
        self._deny_write("delete_stream")

    def restart_stream(self, *_, **__):  # type: ignore[no-untyped-def]
        self._deny_write("restart_stream")

    def reload_config(self, *_, **__):  # type: ignore[no-untyped-def]
        self._deny_write("reload_config")


class FlussonicClient(_WriteBlocker):
    """
    Async Flussonic API client — read-only.

    Instantiate via get_flussonic_client() which reads credentials from config.
    Never instantiate directly with hardcoded credentials.
    """

    def __init__(self, base_url: str, user: str, password: str) -> None:
        self._base = base_url.rstrip("/")
        # Auth tuple is private — never returned to callers or logged.
        self.__auth = (user, password)

    # ── Configuration ─────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """True when base_url and credentials are set in config."""
        return bool(self._base and self.__auth[0] and self.__auth[1])

    # ── URL helpers (no API call, no credentials) ──────────────────────────────

    def stream_hls_url(self, stream_name: str) -> str:
        """
        Build the public HLS playlist URL for a stream.
        No auth is embedded — credentials are never sent to the client.
        If Flussonic backend-auth is configured, callers should append
        ?token={nexora_playback_jwt} to this URL.
        """
        return f"{self._base}/{stream_name}/index.m3u8"

    def stream_dash_url(self, stream_name: str) -> str:
        """Build the public DASH manifest URL."""
        return f"{self._base}/{stream_name}/manifest.mpd"

    # ── Read-only API calls ────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self.__auth,
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    async def list_streams(self) -> list[dict]:
        """
        List all streams from Flussonic.
        Returns raw dicts — caller should not pass these to the frontend.
        """
        async with self._client() as client:
            resp = await client.get(f"{self._base}{_API_PREFIX}/streams")
            resp.raise_for_status()
            data = resp.json()
            # Flussonic wraps the list in {"streams": [...]}
            return data.get("streams", data) if isinstance(data, dict) else data

    async def get_stream(self, name: str) -> dict | None:
        """
        Get full stream details from Flussonic.
        Returns None if the stream does not exist.
        """
        async with self._client() as client:
            resp = await client.get(f"{self._base}{_API_PREFIX}/streams/{name}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            # Some versions wrap: {"stream": {...}} others return the object directly.
            return data.get("stream", data) if isinstance(data, dict) else data

    async def get_stream_status(self, name: str) -> StreamStatus | None:
        """
        Return a safe, minimal status object for a stream.
        Returns None if stream is not found in Flussonic.
        This is the only method whose result is safe to surface to admins.
        """
        try:
            info = await self.get_stream(name)
        except httpx.HTTPError as exc:
            logger.warning("Flussonic unreachable for stream %s: %s", name, exc)
            return None

        if info is None:
            return None

        return StreamStatus(
            name=info.get("name", name),
            alive=bool(info.get("alive", False)),
            client_count=int(info.get("client_count", 0)),
            input_alive=bool(info.get("input", {}).get("alive", False)),
        )

    async def check_connectivity(self) -> bool:
        """
        Ping Flussonic to verify credentials and reachability.
        Returns True if the server responds with a 2xx.
        Used for health checks — does not expose any stream data.
        """
        try:
            async with self._client() as client:
                resp = await client.get(
                    f"{self._base}{_API_PREFIX}/streams",
                    params={"limit": "1"},
                )
                return resp.is_success
        except httpx.HTTPError:
            return False


# ── Singleton factory ──────────────────────────────────────────────────────────

_client_instance: FlussonicClient | None = None


def get_flussonic_client() -> FlussonicClient:
    """
    Return the module-level FlussonicClient instance.
    Reads credentials from app config (which reads from .env).
    Safe to call from FastAPI dependencies — credentials are never returned.
    """
    global _client_instance
    if _client_instance is None:
        from app.config import get_settings
        s = get_settings()
        _client_instance = FlussonicClient(
            base_url=s.flussonic_base_url,
            user=s.flussonic_readonly_user,
            password=s.flussonic_readonly_password,
        )
    return _client_instance
