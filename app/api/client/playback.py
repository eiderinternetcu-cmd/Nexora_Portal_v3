"""Client playback authorization endpoints.

playback_url priority (never exposes Flussonic credentials):
  1. Flussonic HLS URL built from stream_key (when Flussonic is configured)
  2. channel.source_url from local DB (fallback manual URL)
  3. None  → frontend uses VITE_NEXORA_PLAYBACK_URL_TEMPLATE or shows error
"""
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, Request
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.redis_client import get_redis
from app.core.dependencies import get_current_subscriber, get_client_ip as _get_ip
from app.core.exceptions import NexoraException
from app.models.subscriber import Subscriber
from app.schemas.client import PlaybackAuthorizeRequest, PlaybackResponse
from app.services.stream_auth_service import StreamAuthService
from app.services.metrics_service import MetricsService
from app.services.channel_service import ChannelService
from app.services.parental_service import ParentalService
from app.integrations.flussonic_client import get_flussonic_node_client
from app.integrations.flussonic_registry import resolve_playback_node
from app.models.channel import Channel

router = APIRouter(prefix="/playback", tags=["Client Playback"])

settings = get_settings()


def _maybe_sign(playback_url: str | None, token: str) -> str | None:
    """Append ?token= to the playback_url only when SIGNED_URL_ENFORCE is on.

    With the flag off the URL is returned unchanged (current behavior preserved);
    the token still travels in the response body for the player to use.
    """
    if not playback_url or not settings.signed_url_enforce:
        return playback_url
    sep = "&" if "?" in playback_url else "?"
    return f"{playback_url}{sep}token={token}"


# _get_ip is app.core.dependencies.get_client_ip (NX-AUTH). It used to be a local
# copy that read the FIRST X-Forwarded-For value, i.e. whatever the client typed —
# which here also fed the 'cip' claim the /stream/* gate binds a token to.


_FORWARDED_SCHEMES = ("http", "https")


def _request_origin(request: Request | None) -> tuple[str, str] | None:
    """Return (scheme, host) to serve playback from, or None to keep the fixed base.

    SECURITY — Host and X-Forwarded-Host are CLIENT-CONTROLLED. Building the
    playback_url from an arbitrary Host is Host header injection: a request with
    `Host: evil.tld` would hand the subscriber a stream URL on the attacker's
    domain, and the player would send the playback token there. So the header is
    never copied into the URL; it only SELECTS an entry from
    PLAYBACK_HOST_ALLOWLIST and the value returned is the CONFIGURED spelling.
    Unlisted host, missing header or empty allowlist → None → fixed base_url.
    """
    if request is None:
        return None

    allowed = settings.playback_allowed_hosts
    if not allowed:
        return None

    # X-Forwarded-Host first (nginx sets it explicitly alongside Host); a chained
    # proxy may append, so only the first hop is considered.
    raw_host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or ""
    host = allowed.get(raw_host.split(",")[0].strip().lower())
    if not host:
        return None

    # Behind nginx the request reaches the app over plain http, so the real
    # client scheme comes from X-Forwarded-Proto.
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    if proto not in _FORWARDED_SCHEMES:
        proto = request.url.scheme
    return proto, host


def _rewrite_origin(url: str | None, origin: tuple[str, str] | None) -> str | None:
    """Swap scheme+host of an absolute URL, keeping path/query/fragment intact.

    The path configured in the node base_url (e.g. /stream/ec-main) is part of
    the path, so it survives: https://a.tld/stream/ec-main/K1/index.m3u8 with
    origin ("https", "b.tld") → https://b.tld/stream/ec-main/K1/index.m3u8.
    """
    if not url or origin is None:
        return url

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url  # relative URL — already origin-agnostic, resolved by the browser

    scheme, host = origin
    if parts.scheme == "https" and scheme != "https":
        scheme = "https"  # never downgrade a TLS base to cleartext
    if parts.scheme == scheme and parts.netloc.lower() == host.lower():
        return url  # same origin → return the original string byte for byte
    return urlunsplit((scheme, host, parts.path, parts.query, parts.fragment))


def _resolve_playback_url(
    channel: Channel | None,
    stream_key: str | None,
    request: Request | None = None,
    node_id: str | None = None,
) -> str | None:
    """Build the HLS URL using the channel's assigned Flussonic node.

    Priority:
      1. FlussonicClient for channel.flussonic_node → stream_key URL
      2. channel.source_url (stored fallback — full URL from import)
      3. None → frontend shows error
    Flussonic credentials are never included in the returned URL.

    The player is served from several domains, so the ORIGIN of the result is
    taken from the request (allowlisted — see _request_origin) while the PATH
    configured in the node base_url is preserved. Without a request, an empty
    allowlist or an unlisted Host the fixed base_url is used, unchanged.

    `node_id` is the node the CALLER already resolved for this request (P2.2
    failover) and it must be the SAME value that went into the token's `node`
    claim — otherwise the URL points at one node while the token authorizes
    another. Omitted → the channel's declared node, i.e. the previous behavior.
    """
    if channel is None:
        return None

    node_id = node_id or channel.flussonic_node or "ec-main"
    client = get_flussonic_node_client(node_id)
    origin = _request_origin(request)

    if stream_key and client and client.is_configured:
        hls_path = channel.hls_path or "index.m3u8"
        return _rewrite_origin(client.stream_hls_url(stream_key, hls_path), origin)

    # source_url is a stored, complete URL, so it gets a STRICTER rule: rewrite it
    # only when its current host is itself allowlisted (an origin we own).
    #   - relative ("/stream/co-main/X/index.m3u8" — the importer's default target,
    #     CHANNEL_SOURCE_URL_MODE=relative) has no origin at all: the browser
    #     resolves it against the page, so it already works on every domain.
    #   - legacy direct-origin values ("http://181.78.246.211:8002/...") point at a
    #     real third-party origin with its own path layout; grafting our domain onto
    #     them would yield a URL nginx cannot serve — a 404 replacing a working
    #     fallback — so they are left exactly as stored.
    #   - only an absolute same-origin value ("https://nexoraplay.net/stream/...")
    #     names an origin we control, and it is the single shape that breaks when
    #     the page is served from a second domain, so it is the only one swapped.
    # …and it is only a fallback for the channel's OWN node. source_url is a
    # stored URL that names the declared node in its path
    # ("/stream/co-main/X/index.m3u8"); after a failover the token says another
    # node, so returning it here would contradict the token we just minted. No
    # URL is better than a URL the gate will refuse.
    if node_id != (channel.flussonic_node or "ec-main"):
        return None

    src = channel.source_url
    if src and urlsplit(src).netloc.lower() in settings.playback_allowed_hosts:
        return _rewrite_origin(src, origin)
    return src


@router.post("/authorize", response_model=PlaybackResponse)
async def authorize_playback(
    data: PlaybackAuthorizeRequest,
    request: Request,
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Full playback authorization.

    Validates subscriber + active subscription + device + concurrent connection slot.
    channel_id is a channel_key from the catalog; stream_key is resolved internally.

    Response contains:
      - token: short-lived JWT (60s) for Flussonic backend-auth
      - playback_url: HLS URL (no credentials embedded)
      - expires_in: token TTL in seconds

    Credentials are never included in the response.
    """
    ch = None
    stream_key: str | None = None
    if data.channel_id:
        ch = await ChannelService(db).get_active_by_key(data.channel_id)
        stream_key = ch.stream_key

    # NX-PARENTAL. This is THE point of control: the PIN is checked here, on the
    # server, before StreamAuthService is called — so a denial mints no token,
    # opens no connection slot and creates no IPTV session, exactly like the
    # entitlement gate inside authorize(). A PIN validated by the app would be a
    # suggestion; this request is replayable by hand with any client token.
    # No-op unless PARENTAL_CONTROL_ENFORCE is on AND the channel is censored.
    #
    # Deliberately OUTSIDE the metrics try/except below: "the user has not typed
    # the PIN yet" is a normal step of the parental flow, not a playback failure,
    # and counting it would bury real failures under the zapping of every
    # household with an adult tier.
    await ParentalService(db, redis).require_channel_access(
        subscriber, data.device_id, ch
    )

    node = ch.flussonic_node if ch is not None else None

    svc = StreamAuthService(db, redis)
    metrics = MetricsService(redis)
    try:
        # NX-FLU. Resolve the node ONCE, here, before any token exists. The value
        # returned feeds BOTH svc.authorize(node=…) — which writes it into the
        # token's `node` claim — and the /stream/<node>/ path of the playback_url
        # built below, so a failover can never hand out a URL on one node with a
        # token bound to another (which is what PLAYBACK_NODE_BINDING_ENFORCE
        # refuses, and what the flag being off would silently allow).
        # No-op unless FLUSSONIC_FAILOVER_MODE is on. Inside the try so a
        # 503 NODE_UNAVAILABLE is counted like any other playback failure.
        node = await resolve_playback_node(redis, node, stream_key)
        result = await svc.authorize(
            subscriber_id=subscriber.id,
            device_id_str=data.device_id,
            channel_id=stream_key,
            ip=_get_ip(request),
            user_agent=request.headers.get("User-Agent"),
            channel_key=data.channel_id,  # public key → EntitlementService
            node=node,
        )
    except NexoraException as exc:
        await metrics.record_playback_failure(exc.detail)
        raise
    await metrics.record_playback_success()
    await db.commit()

    base_url = _resolve_playback_url(ch, stream_key, request, node_id=node)
    return PlaybackResponse(
        token=result.token,
        expires_in=result.expires_in,
        channel_id=data.channel_id,  # echo channel_key back — never stream_key
        subscriber_id=str(result.subscriber_id),
        playback_url=_maybe_sign(base_url, result.token),
    )


@router.get("/{channel_id}", response_model=PlaybackResponse)
async def reissue_playback_token(
    channel_id: str,
    request: Request,
    device_id: str = Query(..., min_length=6, max_length=128),
    subscriber: Subscriber = Depends(get_current_subscriber),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Reissue a playback token for a device with an active IPTV session.

    Lighter than /authorize — skips subscription/plan reload.
    Call /authorize first if no active session exists.

    Response contains the same safe fields as /authorize. The token carries the
    same bindings as /authorize (chn/sk/node/cip) and the playback_url is signed
    under SIGNED_URL_ENFORCE exactly like /authorize — a reissued token must not
    be weaker than the one it replaces (a player renews every ~45s, so most
    tokens in flight are reissues).
    """
    ch = await ChannelService(db).get_active_by_key(channel_id)

    # NX-PARENTAL. Gated exactly like /authorize, and NOT behind a separate
    # opt-in the way the entitlement recheck is (PLAYBACK_REISSUE_ENTITLEMENT_CHECK):
    # this route mints a token for whatever channel it is asked for, so gating
    # only /authorize would leave the adult tier one path parameter away for any
    # client holding an open session. There is no continuity argument for
    # tolerating it either — the unlock grant slides on every check, so a
    # legitimately unlocked viewer renews indefinitely while watching.
    await ParentalService(db, redis).require_channel_access(subscriber, device_id, ch)

    # NX-FLU, same contract as /authorize: one resolution feeding both the new
    # token's `node` claim and the URL. This is also where failover actually
    # takes effect for a session already playing — the player reissues every
    # ~45s, so a node that goes down is picked up within one token lifetime
    # WITHOUT the gate ever having to accept a token issued for another node.
    node = await resolve_playback_node(redis, ch.flussonic_node, ch.stream_key)

    svc = StreamAuthService(db, redis)
    result = await svc.create_token(
        subscriber_id=subscriber.id,
        device_id_str=device_id,
        channel_id=ch.stream_key,
        channel_key=channel_id,   # public key → 'chn' claim (+ optional entitlement recheck)
        node=node,
        ip=_get_ip(request),
    )

    base_url = _resolve_playback_url(ch, ch.stream_key, request, node_id=node)
    return PlaybackResponse(
        token=result.token,
        expires_in=result.expires_in,
        channel_id=channel_id,
        subscriber_id=str(result.subscriber_id),
        playback_url=_maybe_sign(base_url, result.token),
    )
