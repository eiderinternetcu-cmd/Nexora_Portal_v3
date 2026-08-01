"""Channel schemas.

SECRET FIELDS — stream_key / source_url  (P0.8)
-----------------------------------------------
Both are secrets and NO listing returns them in the clear:

  * `stream_key` + the Flussonic host is a directly playable URL, which is a
    complete bypass of the entitlement gate (EntitlementService only runs on the
    /api/client and /api/stb playback routes).
  * `source_url` is a stored, COMPLETE origin URL. On pull sources it carries the
    provider's `user:password@` embedded, and its PATH routinely contains the
    stream_key itself — see scripts/backfill_channel_source_urls_same_origin.py,
    whose whole job was rewriting `http://<origin-ip>:8002/<stream_key>/index.m3u8`
    into a same-origin path. So masking only the credentials and keeping host and
    path would hand back both the stream_key AND the direct-origin URL: the mask
    has to drop the host and the path, not just star out the userinfo.

Masked, not omitted
-------------------
The panel — and whoever is debugging a dead channel — still needs to know whether
a source is configured at all, whether it is same-origin or absolute, whether it
is HTTPS, and whether it has credentials baked in. That is exactly the shape the
offline auditor (scripts/audit_channel_source_urls.py) classifies on, minus the
exploitable parts. Dropping the fields entirely would push routine "is this even
configured?" questions through the audited reveal endpoint and drown its trail in
noise, which makes the audit log useless for the thing it is there for.

The mask is NOT reversible: it encodes shape, never value.

The real values are served by exactly one route:
GET /api/admin/channels/{id}/secrets — admin only, always audited.
"""
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

MASK = "***"


def mask_stream_key(raw: str | None) -> str:
    """Presence, never the value.

    An EMPTY stream_key is a real catalog bug (nothing to play), so it stays
    visible as an empty string instead of being disguised as a set secret.
    """
    return MASK if raw else ""


def mask_source_url(raw: str | None) -> str | None:
    """Non-reversible shape of a source URL.

        None                              -> None                       (not configured)
        ""                                -> ""                         (configured empty = bug)
        "/stream/co-main/K1/index.m3u8"   -> "/***"                     (same-origin relative)
        "https://host/stream/K1/x.m3u8"   -> "https://***/***"          (absolute, TLS)
        "http://u:p@1.2.3.4:8002/K1/x"    -> "http://***:***@***/***"   (plain HTTP + credentials)

    Preserved: configured-or-not, relative-vs-absolute, scheme, credentials-embedded.
    Dropped:   userinfo, host, port, path, query, fragment — i.e. everything that
               reconstructs a playable URL or the stream_key.
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        scheme, netloc = parts.scheme, parts.netloc
        has_userinfo = bool(parts.username or parts.password)
    except ValueError:
        # Unparseable — reveal nothing at all rather than guess. A mask must
        # never be the reason a listing 500s.
        return MASK
    if not scheme and not netloc:
        return "/***" if raw.startswith("/") else MASK
    userinfo = f"{MASK}:{MASK}@" if has_userinfo else ""
    return f"{scheme or MASK}://{userinfo}{MASK}/{MASK}"


class StreamStatusOut(BaseModel):
    """Flussonic stream status — safe to surface to admin/reseller.

    Identified by `channel_key` (the PUBLIC key). It used to echo back the
    channel's `stream_key`, which made this endpoint a per-channel oracle for the
    very secret the listing is masking.
    """
    channel_key: str
    alive: bool
    client_count: int
    input_alive: bool
    flussonic_configured: bool


class ChannelPublic(BaseModel):
    """Client-facing — stream_key is NOT exposed."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel_key: str
    number: int
    name: str
    category: str | None = None
    logo_url: str | None = None
    requires_subscription: bool


class ChannelPublicParental(ChannelPublic):
    """ChannelPublic + the parental marker (NX-PARENTAL).

    A SUBCLASS rather than an extra field on ChannelPublic, because the catalog
    payload must stay byte-identical to today while PARENTAL_CONTROL_ENFORCE is
    off — a new key in the JSON is a change, however additive it looks, and
    tests/test_catalog_entitlements.py pins the exact key set. The route picks
    the schema per request (see app/api/client/catalog.py).

    A censored channel is LISTED, not hidden: hiding it leaves holes in the grid
    and in the channel numbering for content the household is paying for, and
    hiding is not a control anyway (ChannelService makes the same argument for
    CATALOG_ENTITLEMENT_FILTER). The marker is what lets a client blur the tile
    and prompt for the PIN.

    Listing one leaks nothing playable: this schema inherits ChannelPublic's
    fields and adds a boolean, so `stream_key` and `source_url` — the two values
    that reconstruct a direct URL, see the module docstring — remain absent, and
    `channel_key` alone plays nothing. The only route that turns a channel_key
    into a playable token is /playback/*, which is where the gate is.
    """
    censored: bool


class ChannelAdminOut(BaseModel):
    """Channel row for admin/reseller — listings AND detail. Never a raw secret.

    The `_masked` suffix is deliberate: any caller still reading `stream_key` or
    `source_url` off one of these rows now breaks loudly instead of silently
    rendering "***" and looking fine.

    `from_attributes` is deliberately NOT set either, so an ORM Channel returned
    straight from a route fails response validation instead of being serialised
    with its secrets intact.
    """

    id: uuid.UUID
    channel_key: str
    number: int
    name: str
    category: str | None
    logo_url: str | None
    stream_key_masked: str
    source_type: str
    source_url_masked: str | None
    epg_id: str | None
    is_active: bool
    requires_subscription: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_channel(cls, ch) -> "ChannelAdminOut":
        """The ONLY way to build this schema — masking cannot be forgotten."""
        return cls(
            id=ch.id,
            channel_key=ch.channel_key,
            number=ch.number,
            name=ch.name,
            category=ch.category,
            logo_url=ch.logo_url,
            stream_key_masked=mask_stream_key(ch.stream_key),
            source_type=ch.source_type,
            source_url_masked=mask_source_url(ch.source_url),
            epg_id=ch.epg_id,
            is_active=ch.is_active,
            requires_subscription=ch.requires_subscription,
            created_at=ch.created_at,
            updated_at=ch.updated_at,
        )


class ChannelSecretsOut(BaseModel):
    """The real values. Served ONLY by GET /channels/{id}/secrets (admin, audited).

    A separate schema on purpose: a secret can then never ride along inside a
    listing payload by accident, only through the one route that logs it.
    """
    channel_id: uuid.UUID
    channel_key: str
    stream_key: str
    source_url: str | None
