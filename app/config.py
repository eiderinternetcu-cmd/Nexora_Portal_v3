import ipaddress
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

    # ── Admin login lockout (NX-AUTH) ────────────────────────────────────────
    # Hardened, per-USERNAME lockout for /api/{v1,admin}/auth/login.
    #
    # Why per username and not per IP: the IP axis is already covered by
    # RateLimitMiddleware (10 req/min on the login path), and the IP it sees
    # comes from X-Forwarded-For — a client-controlled header. An IP-keyed
    # lockout is therefore both EVADABLE (rotate the header) and ABUSABLE (spoof
    # a victim's address to lock *them* out of the admin panel). The username is
    # the axis a brute force actually has to commit to, so that is what we count.
    #
    # Default False = today's behavior byte for byte: the legacy username+IP
    # counters in AuthService stay in charge and still answer 423. Flipping this
    # to True swaps in the per-username policy below, whose refusal is
    # INDISTINGUISHABLE from a wrong password (same 401, same body), so it leaks
    # neither account existence nor lockout state.
    #
    # The two Redis namespaces are disjoint (`nexora:lockout:{user}` legacy vs
    # `nexora:lockout:admin:{user}` hardened), so toggling the flag never
    # inherits a stale counter in either direction.
    login_lockout_enabled: bool = False
    login_lockout_max_attempts: int = 5        # consecutive failures that trigger the block
    login_lockout_window_seconds: int = 900    # failures counted within this window (from the 1st)
    login_lockout_duration_seconds: int = 900  # how long the account stays blocked

    # Browser CORS. The player and the admin panel are normally served from the
    # SAME origin as the API (nginx proxies /api/), so no preflight happens and
    # this list never comes into play; it only matters when a frontend lives on
    # a different origin, where a missing entry surfaces as an opaque login
    # failure with no clear hint. Comma-separated absolute origins
    # (scheme://host[:port]), matched exactly by the CORS middleware.
    # Default = the historical development origins, so leaving the variable
    # unset keeps today's behavior byte for byte.
    # NOTE: "*" together with credentials is invalid per the CORS spec (browsers
    # reject the response), so a real deployment must list real origins here.
    cors_allow_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://172.27.99.151:5173,"
        "http://localhost:4173,"
        "http://127.0.0.1:4173"
    )

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

    # ── Playback node binding (NX-NODE) ──────────────────────────────────────
    # The /stream/* gate binds a playback token to its stream_key AND to its
    # Flussonic node, but the two checks were never symmetric:
    #
    #     sk   → `payload.get("sk") != stream_key`            → fails CLOSED
    #     node → `payload.get("node") not in (None, node)`    → fails OPEN
    #
    # A token WITHOUT a 'node' claim was therefore valid on EVERY node, while a
    # token without 'sk' was refused. The tolerance was load-bearing when it was
    # written: the STB surface resolved neither claim (see app/api/stb/playback.py
    # `_resolve_channel`) and `create_token` left node optional "so legacy callers
    # keep their current token shape". Both emitters were fixed earlier on this
    # branch, and every remaining issuing path (client /authorize, client reissue,
    # STB /play, STB /token, the health probe) sets 'sk' and 'node' together or
    # neither — so a node-less token is also sk-less and is ALREADY refused one
    # line earlier by the stream_key check. Closing the asymmetry is expected to
    # be a no-op; the flag exists because "expected" is not "observed", and
    # playback tokens live 60s, so a rollback takes effect within a minute.
    #
    # False (default) = today byte for byte: a token with no 'node' claim passes
    # on any node. True = node is checked exactly like stream_key.
    playback_node_binding_enforce: bool = False

    # Node allowlist for the /stream/* gate. The node is a PATH SEGMENT of the
    # incoming URL (/stream/<node>/<stream_key>/…), i.e. attacker-controlled, and
    # nothing in app/ ever validated it — an unknown node was carried straight
    # into token evaluation and into the Redis grant keyspace
    # (nexora:stream_grant:{node}:…). Listing the real nodes here rejects an
    # unknown one BEFORE any token is decoded.
    # Comma-separated node ids, matched exactly (they are lowercase slugs, e.g.
    # "ec-main,co-main,ec-quito"). Empty (default) = OFF, no validation, today's
    # behavior.
    playback_node_allowlist: str = ""

    # ── Client IP resolution (NX-AUTH) ───────────────────────────────────────
    # Everything keyed by "client IP" (login lockout, per-IP rate limiting, audit
    # rows, playback IP binding) used to read the FIRST value of X-Forwarded-For,
    # a header the client fully controls. That makes the per-IP brake both
    # EVADABLE (send a fresh fake IP per attempt and no bucket ever fills) and
    # ABUSABLE (send a victim's address and lock THEM out of the admin panel for
    # LOGIN_LOCKOUT_MINUTES). A multi-hop X-Forwarded-For must also be read from
    # the RIGHT, not the left: the leftmost hop is whatever the client typed, the
    # rightmost is the one the closest trusted proxy appended.
    #
    #   legacy (default) → the historical behavior, unchanged.
    #   edge             → forwarded headers are honored ONLY when the TCP peer is
    #                      listed in TRUSTED_PROXY_CIDRS: X-Real-IP first (the
    #                      edge overwrites it with $remote_addr), then the LAST
    #                      hop of X-Forwarded-For, then the peer itself. From an
    #                      untrusted peer the headers are ignored entirely and the
    #                      peer address is used, so spoofing buys nothing.
    #
    # With no proxy in front (local development) the peer is not trusted, no
    # forwarded header is honored and the peer address is used — the environment
    # keeps working, it just stops believing headers.
    client_ip_source: str = "legacy"     # legacy | edge

    # Comma-separated IPs or CIDRs of the proxies allowed to assert a client IP.
    # In Docker this is the compose network the nginx container sits on (e.g.
    # "172.18.0.0/16"); behind a CDN add the provider's published ranges. Empty
    # (default) = no peer is trusted, which in `edge` mode means forwarded
    # headers are never honored.
    trusted_proxy_cidrs: str = ""

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

    # ── Node health probing (P0.5) ────────────────────────────────────────────
    # The `api` container has NO route to the Flussonic origins
    # (181.78.246.211:8002, 38.210.187.13:8002 → timeout / HTTP 000); only Nginx
    # (the edge) does, which is why playback works while the management-API probe
    # reports every node down and floods the alerting with false "node down".
    #   origin      → today's probe: GET <mgmt_base_url>/flussonic/api/v3/streams
    #                 from the backend (unreachable in the current topology).
    #   hls_signed  → the monitor mints a playback token with the SAME issuer the
    #                 /stream/* gate validates and asks the EDGE for a signed HLS
    #                 manifest (<edge>/stream/<node>/<stream>/index.m3u8?token=…),
    #                 expecting 2xx. One end-to-end signal covering gate + node +
    #                 stream. It reports no stream_count (no management API).
    # Default "origin" = byte-identical to today, so deploying changes nothing
    # until the flag is flipped.
    node_probe_mode: str = "origin"                      # origin | hls_signed
    node_probe_edge_base_url: str = "http://nexora_nginx"  # edge as seen from the api container
    node_probe_timeout_seconds: float = 8.0
    # Optional pin, CSV of "node_id:stream_key" (e.g. "ec-main:tvn,co-main:caracol").
    # Empty (default) → the probe picks the lowest-numbered active channel of the
    # node from the catalog.
    node_probe_streams: str = ""

    @property
    def node_probe_stream_map(self) -> dict[str, str]:
        """NODE_PROBE_STREAMS as {node_id → stream_key}.

        Same CSV discipline as PLAYBACK_HOST_ALLOWLIST: parsed here, never
        derived from request data. Malformed entries are ignored.
        """
        out: dict[str, str] = {}
        for raw in self.node_probe_streams.split(","):
            node, _, stream = raw.partition(":")
            node, stream = node.strip(), stream.strip()
            if node and stream:
                out.setdefault(node, stream)
        return out

    @property
    def playback_allowed_nodes(self) -> set[str]:
        """PLAYBACK_NODE_ALLOWLIST as a set of node ids.

        Same CSV discipline as PLAYBACK_HOST_ALLOWLIST: parsed here, never
        derived from request data. Empty set = feature off.
        """
        return {raw.strip() for raw in self.playback_node_allowlist.split(",") if raw.strip()}

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        """TRUSTED_PROXY_CIDRS as networks. A bare address becomes a /32 (/128).

        Unparseable entries are dropped rather than raised: a typo in this
        variable must not take the API down at import time, and dropping an entry
        fails SAFE (that proxy simply stops being trusted, so its forwarded
        headers are ignored and the peer address is used instead).
        """
        out: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for raw in self.trusted_proxy_cidrs.split(","):
            entry = raw.strip()
            if not entry:
                continue
            try:
                out.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                continue
        return out

    @property
    def is_production(self) -> bool:
        """True when APP_ENV marks a production deployment.

        APP_ENV already exists and is authoritative for the environment
        (`production` in .env.production, `staging` in .env.staging,
        `development` locally), while DEBUG is an independent verbosity switch
        that is false in staging too. Used to close /docs, /redoc and
        /openapi.json, which otherwise publish the full API map — including the
        internal stream-auth contract — to anyone on the Internet.
        """
        return self.app_env.strip().lower() == "production"

    @property
    def cors_allowed_origins(self) -> list[str]:
        """CORS_ALLOW_ORIGINS as an ordered, de-duplicated list of origins.

        Same discipline as PLAYBACK_HOST_ALLOWLIST: CSV in the environment,
        parsed here, never derived from a client-controlled header.
        """
        out: list[str] = []
        seen: set[str] = set()
        for raw in self.cors_allow_origins.split(","):
            origin = raw.strip().rstrip("/")
            if origin and origin.lower() not in seen:
                seen.add(origin.lower())
                out.append(origin)
        return out

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
