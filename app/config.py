from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "NexoraAPI"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = "change-this-secret"

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "nexora-api"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "nexora"
    postgres_user: str = "nexora"
    postgres_password: str = "nexora_secret"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # Security
    max_login_attempts: int = 5
    login_lockout_minutes: int = 15
    rate_limit_per_minute: int = 60

    # Feature flags — P0 rollout (default OFF: validate+warn, do not block)
    entitlement_enforce: bool = False   # True → playback denies channels not in plan_channels
    jwt_require_aud: bool = False        # True → strict iss/aud/type per surface; False → legacy-compatible
    signed_url_enforce: bool = False     # True → playback_url carries ?token= and /stream/* requires it
    device_secret_enforce: bool = False  # True → playback requires an activated device (secret verified); False → legacy auto-register
    catalog_entitlement_filter: bool = False  # True → /client/catalog/channels lists only the plan's channels; False → full active catalog

    # STB surface hardening. The /api/stb/* device endpoints historically took the
    # caller's identity (subscriber_id / device_id) from the REQUEST BODY with no
    # token at all, so anyone reaching /api/stb/auth/play could mint a playback
    # token for ANY subscriber — an IDOR that also bypassed the entitlement gate.
    # Default True = CLOSED: a valid STB token (type=stb_access, aud=nexora-stb)
    # is required and its `sub`/`dev` claims are authoritative.
    # Set to False ONLY as a temporary, documented escape hatch for legacy STB
    # firmware that cannot send a token: it re-opens the IDOR and must be paired
    # with network-level restriction of /api/stb/*. Even with the flag off, a
    # token that IS presented is still fully validated, and the entitlement gate
    # (ENTITLEMENT_ENFORCE) runs on both paths.
    stb_auth_enforce: bool = True

    # IPTV concurrency
    heartbeat_ttl_seconds: int = 180        # auto-disconnect after 3 missed heartbeats
    playback_token_expire_seconds: int = 60 # short-lived: 30-120s for HLS/Flussonic

    # Pre-prod hardening (C-PROD-1 / C-PROD-2)
    stream_auth_cache_ttl_seconds: int = 180   # segment grant cache TTL (manifest seeds it)
    playback_ip_binding_mode: str = "off"      # off | soft | strict (default off — no break)

    # Reissue hardening. GET /api/client/playback/{channel_id} does NOT re-evaluate
    # entitlement, so a plan losing a channel mid-session keeps working until the
    # IPTV session expires (4h). Turning this on re-runs EntitlementService on every
    # reissue (~1 per 45s per stream, ~5 extra DB reads each); it only DENIES when
    # entitlement_enforce is also on, otherwise it just logs. Default off = today.
    playback_reissue_entitlement_check: bool = False

    # Grant hardening (M1). Bound the absolute life of a segment grant so a revoked
    # session cannot keep an in-flight stream alive indefinitely via renewal.
    stream_grant_max_lifetime_seconds: int = 0   # 0 = unbounded (legacy); >0 = absolute cap from first seed
    stream_grant_token_fallback: bool = True     # token present-but-expired falls back to a valid grant (continuity)

    # Client (subscriber) tokens — longer-lived for mobile/TV apps
    client_access_token_expire_hours: int = 24
    client_refresh_token_expire_days: int = 90

    # STB
    stb_portal_url: str = "http://172.27.99.151/nexora_portal"

    # Flussonic Media Server — read-only integration
    # Credentials live ONLY here. Never returned to clients.
    # ec-main (primary node — Ecuador)
    flussonic_base_url: str = ""
    flussonic_readonly_user: str = ""
    flussonic_readonly_password: str = ""
    flussonic_readonly: bool = True

    # co-main (secondary node — Colombia)
    flussonic_co_main_base_url: str = ""
    flussonic_co_main_user: str = ""
    flussonic_co_main_password: str = ""

    # ec-quito (Ecuador — Quito Astra). base_url should be SAME-ORIGIN in prod
    # (https://<domain>/stream/ec-quito); credentials live ONLY here.
    flussonic_ec_quito_base_url: str = ""
    flussonic_ec_quito_user: str = ""
    flussonic_ec_quito_password: str = ""

    # Multi-domain playback. The web player is served from several domains
    # (nexoraplay.net, tvdigital.laredtelco.com, …) while flussonic_*_base_url
    # holds a single fixed origin, so from a second domain the HLS request would
    # cross origin and the browser blocks it. The playback_url origin is
    # therefore derived from the incoming request — but the Host header is
    # CLIENT-CONTROLLED, so it may only SELECT an entry from this allowlist and
    # the emitted origin is the CONFIGURED entry, never the raw header (Host
    # header injection would otherwise point a subscriber's stream — and its
    # playback token — at an attacker's domain).
    # Comma-separated hostnames, matched case-insensitively; an entry may carry
    # a port ("host:8443"). Empty (default) = feature OFF: playback_url is byte
    # identical to the pre-multidomain behavior. A host that is not listed also
    # falls back to the fixed base.
    playback_host_allowlist: str = ""

    # Management API base URLs (health/list). In prod the *_base_url above are the
    # same-origin /stream/* paths (gated by auth_request), which the management API
    # can't be reached through — set these to the REAL Flussonic origin so node
    # health/alerting is accurate. Empty → fall back to the same-origin base.
    flussonic_mgmt_base_url: str = ""
    flussonic_co_main_mgmt_base_url: str = ""
    flussonic_ec_quito_mgmt_base_url: str = ""

    @property
    def playback_allowed_hosts(self) -> dict[str, str]:
        """{lowercased host → configured host} for PLAYBACK_HOST_ALLOWLIST.

        Lookup table used to match an incoming Host against the allowlist while
        emitting the operator-configured spelling (never the client's bytes).
        """
        out: dict[str, str] = {}
        for raw in self.playback_host_allowlist.split(","):
            host = raw.strip()
            if host:
                out.setdefault(host.lower(), host)
        return out

    @property
    def database_url(self) -> str:
        """Async URL for SQLAlchemy create_async_engine (psycopg3)."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync URL for Alembic offline mode."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
