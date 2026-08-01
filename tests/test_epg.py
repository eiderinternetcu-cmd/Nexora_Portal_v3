"""Real programme guide — XMLTV ingest, XXE hardening and the EPG endpoint (NX-EPG).

`GET /api/client/catalog/channels/{key}/epg` served a hard-coded dict of three
invented programmes covering three of the 41 channels, and clients rendered it
as the real schedule. These tests pin the replacement:

  - the XMLTV parser reads times, offsets, titles and descriptions correctly
  - RE-INGESTING THE SAME SOURCE CANNOT DUPLICATE THE GRID, and the guarantee is
    proven at the SCHEMA level (a raw INSERT bypassing the service is refused),
    not merely at the service level
  - the parser REFUSES the three XML entity attacks, and the billion-laughs case
    is demonstrated against the stdlib parser first, so the test proves the
    attack is real on this interpreter rather than asserting a tautology
  - the window query returns what overlaps the window, including the programme
    already on air
  - a channel with no ingested guide returns 200 + [] — never 500, never invented
  - an XMLTV channel with no correspondence cannot break the whole ingest
  - the migration creates a real UNIQUE CONSTRAINT, not a bare unique index
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.models.channel import Channel
from app.models.epg_programme import EpgProgramme
from app.services.epg_service import EpgService, EpgSourceError, load_source
from app.services.xmltv_parser import (
    XmltvParseError,
    XmltvSecurityError,
    parse_xmltv,
    parse_xmltv_timestamp,
)

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


# ── Fixtures / helpers ───────────────────────────────────────────────────────


def _xmltv(*programmes: str, root_attrs: str = "") -> bytes:
    body = "\n".join(programmes)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<tv {root_attrs}>\n{body}\n</tv>'
    ).encode()


def _programme(channel: str, start: str, stop: str, title: str, desc: str | None = None) -> str:
    d = f"<desc>{desc}</desc>" if desc else ""
    return (
        f'<programme start="{start}" stop="{stop}" channel="{channel}">'
        f"<title>{title}</title>{d}</programme>"
    )


@pytest.fixture
def guide() -> bytes:
    """Two programmes on `tvn.ec`, back to back, plus one on an unknown channel."""
    return _xmltv(
        _programme("tvn.ec", "20260731080000 -0500", "20260731100000 -0500",
                   "Noticiero", "Las noticias del dia"),
        _programme("tvn.ec", "20260731100000 -0500", "20260731113000 -0500",
                   "Pelicula"),
        _programme("nada.xx", "20260731080000 -0500", "20260731090000 -0500",
                   "Canal sin mapear"),
    )


async def _channels(db) -> dict[str, Channel]:
    """Two channels: one mapped by epg_id, one with no XMLTV correspondence."""
    mapped = Channel(
        channel_key="canal-1", number=1, name="TVN", stream_key="K1",
        is_active=True, epg_id="tvn.ec",
    )
    unmapped = Channel(
        channel_key="canal-2", number=2, name="Sin guia", stream_key="K2",
        is_active=True,
    )
    db.add_all([mapped, unmapped])
    await db.commit()
    return {"mapped": mapped, "unmapped": unmapped}


async def _count(db) -> int:
    return (
        await db.execute(sa.select(sa.func.count()).select_from(EpgProgramme))
    ).scalar()


# ── The parser: correctness ──────────────────────────────────────────────────


async def test_parses_programmes_with_timezone_offsets(guide):
    programmes = parse_xmltv(guide)

    assert len(programmes) == 3
    first = programmes[0]
    assert first.xmltv_channel_id == "tvn.ec"
    assert first.title == "Noticiero"
    assert first.description == "Las noticias del dia"
    # 08:00 at -0500 is 13:00 UTC. Storing local time would put the whole grid
    # five hours out for every subscriber.
    assert first.start_at == datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
    assert first.end_at == datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc)
    assert programmes[1].description is None


async def test_timestamp_without_offset_is_read_as_utc():
    """Documented convention: no offset → UTC, so the same file imports
    identically on every host instead of depending on the server's zone."""
    assert parse_xmltv_timestamp("20260731080000") == datetime(
        2026, 7, 31, 8, 0, tzinfo=timezone.utc
    )
    assert parse_xmltv_timestamp("20260731080000 +0200") == datetime(
        2026, 7, 31, 6, 0, tzinfo=timezone.utc
    )


async def test_first_title_wins_when_the_feed_repeats_it_per_language():
    doc = _xmltv(
        '<programme start="20260731080000" stop="20260731090000" channel="a">'
        '<title lang="es">Noticiero</title><title lang="en">News</title></programme>'
    )
    assert parse_xmltv(doc)[0].title == "Noticiero"


async def test_one_unusable_programme_does_not_lose_the_others():
    """A third-party feed WILL contain a broken row. It must cost that row only."""
    doc = _xmltv(
        _programme("a", "BASURA", "20260731090000", "Timestamp roto"),
        '<programme start="20260731080000" channel="a"><title>Sin stop</title></programme>',
        '<programme start="20260731080000" stop="20260731090000" channel="a"></programme>',
        _programme("a", "20260731100000", "20260731110000", "Bueno"),
    )
    programmes = parse_xmltv(doc)
    assert [p.title for p in programmes] == ["Bueno"]


async def test_programme_ending_before_it_starts_is_rejected():
    doc = _xmltv(_programme("a", "20260731100000", "20260731090000", "Al reves"))
    assert parse_xmltv(doc) == []


async def test_non_xmltv_document_is_rejected():
    with pytest.raises(XmltvParseError):
        parse_xmltv(b"<rss><channel/></rss>")
    with pytest.raises(XmltvParseError):
        parse_xmltv(b"<tv><programme")  # malformed XML


# ── The parser: XXE and entity hardening ─────────────────────────────────────
#
# The source is a third-party URL parsed unattended, in a background task, inside
# the production network. See app/services/xmltv_parser.py for the full write-up.

_XXE_FILE_READ = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE tv [<!ENTITY secret SYSTEM "file:///etc/passwd">]>\n'
    b'<tv><programme start="20260731080000" stop="20260731090000" channel="a">'
    b"<title>&secret;</title></programme></tv>"
)

_BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n'
    b"<!DOCTYPE tv [\n"
    b'  <!ENTITY a "aaaaaaaaaa">\n'
    b'  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    b'  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
    b"]>\n"
    b'<tv><programme start="20260731080000" stop="20260731090000" channel="a">'
    b"<title>&c;</title></programme></tv>"
)


async def test_external_entity_file_disclosure_is_refused():
    """ATTACK 1 — the classic XXE.

    Without this refusal the parser resolves `file:///…`, substitutes the file's
    contents into the document, and we store it as a programme title — which
    `GET /catalog/channels/{key}/epg` then serves to any authenticated
    subscriber. In this container the payload of interest is /app/.env
    (POSTGRES_PASSWORD, SECRET_KEY, the Flussonic credentials), i.e. full
    compromise of the playback gate exfiltrated through a public API response.
    """
    with pytest.raises(XmltvSecurityError):
        parse_xmltv(_XXE_FILE_READ)


async def test_external_entity_pointing_at_a_real_readable_file_leaks_nothing(tmp_path):
    """The same attack aimed at a file that provably EXISTS and is readable.

    A refusal test can pass for the wrong reason — the parser might merely be
    failing to open a path that is not there. Here the target exists and holds a
    known marker, so the assertions can be positive: the parse is refused AND the
    marker never appears in the exception text or anywhere else.
    """
    secret = tmp_path / "dot-env"
    secret.write_text("POSTGRES_PASSWORD=MARKER_c0ffee_DO_NOT_LEAK\n", encoding="utf-8")
    assert secret.read_text(encoding="utf-8")  # the file really is readable

    uri = secret.as_uri()
    doc = (
        '<?xml version="1.0"?>\n'
        f'<!DOCTYPE tv [<!ENTITY x SYSTEM "{uri}">]>\n'
        '<tv><programme start="20260731080000" stop="20260731090000" channel="a">'
        "<title>&x;</title></programme></tv>"
    ).encode()

    with pytest.raises(XmltvSecurityError) as excinfo:
        parse_xmltv(doc)
    assert "MARKER_c0ffee" not in str(excinfo.value)


async def test_billion_laughs_is_refused_and_the_attack_is_real_on_this_interpreter():
    """ATTACK 3 — exponential entity expansion.

    The first half of this test is the important half: it proves against the
    STDLIB parser that the payload really does expand here, so the second half is
    not asserting a tautology. `xml.etree.ElementTree` is what a reasonable
    author would have reached for, it expands the entity in full, and a deeper
    version of the same document exhausts the process — which is the API process
    that also serves playback authorisation.
    """
    import xml.etree.ElementTree as ET

    expanded = ET.fromstring(_BILLION_LAUGHS)
    stdlib_title = expanded[0][0].text
    assert len(stdlib_title) == 1000, (
        "the stdlib parser did not expand the entity, so this payload no longer "
        "demonstrates the attack — revisit the parser hardening"
    )

    with pytest.raises(XmltvSecurityError):
        parse_xmltv(_BILLION_LAUGHS)


async def test_any_internal_dtd_subset_is_refused():
    """The internal subset is the ONLY place a document can declare entities, so
    it is refused wholesale rather than by trying to spot a malicious one."""
    doc = b'<!DOCTYPE tv [<!ENTITY harmless "hola">]><tv></tv>'
    with pytest.raises(XmltvSecurityError):
        parse_xmltv(doc)


async def test_external_dtd_is_tolerated_but_never_fetched():
    """ATTACK 2 — SSRF via an external DTD.

    Real XMLTV generators emit `<!DOCTYPE tv SYSTEM "xmltv.dtd">`, so refusing
    every DOCTYPE would reject legitimate feeds. The danger was never the
    declaration but fetching what it points at, and that is switched off
    (SetParamEntityParsing(NEVER)). The system id below is a port nothing listens
    on: if the parser tried to resolve it the call would raise a connection
    error instead of returning a parsed programme.
    """
    doc = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE tv SYSTEM "http://127.0.0.1:1/evil.dtd">\n'
        b'<tv><programme start="20260731080000" stop="20260731090000" channel="a">'
        b"<title>OK</title></programme></tv>"
    )
    assert [p.title for p in parse_xmltv(doc)] == ["OK"]


async def test_oversized_source_is_refused_before_parsing(monkeypatch):
    import app.services.xmltv_parser as parser_mod

    monkeypatch.setattr(parser_mod, "EPG_MAX_SOURCE_BYTES", 64)
    with pytest.raises(XmltvSecurityError):
        parse_xmltv(b"<tv>" + b"x" * 500 + b"</tv>")


# ── Ingest: mapping ──────────────────────────────────────────────────────────


async def test_ingest_maps_via_channels_epg_id(db_session, guide):
    channels = await _channels(db_session)
    result = await EpgService(db_session).ingest(guide)
    await db_session.commit()

    assert result.parsed == 3
    assert result.upserted == 2
    rows = (
        await db_session.execute(
            select(EpgProgramme).order_by(EpgProgramme.start_at)
        )
    ).scalars().all()
    assert [r.title for r in rows] == ["Noticiero", "Pelicula"]
    assert all(r.channel_id == channels["mapped"].id for r in rows)


async def test_channel_key_is_accepted_as_a_fallback_mapping(db_session):
    """A feed whose ids already equal our channel keys needs no configuration."""
    db_session.add(
        Channel(channel_key="canal-7", number=7, name="Siete", stream_key="K7",
                is_active=True)  # epg_id is NULL
    )
    await db_session.commit()

    doc = _xmltv(_programme("CANAL-7", "20260731080000", "20260731090000", "Por convencion"))
    result = await EpgService(db_session).ingest(doc)
    await db_session.commit()

    assert result.upserted == 1  # matched case-insensitively


async def test_explicit_epg_id_beats_the_channel_key_convention(db_session):
    """Two channels, where one's channel_key equals the other's epg_id. The
    explicit configuration must win, deterministically, whatever order the rows
    come back in."""
    by_convention = Channel(channel_key="shared-id", number=1, name="A", stream_key="KA", is_active=True)
    by_config = Channel(channel_key="canal-b", number=2, name="B", stream_key="KB",
                        is_active=True, epg_id="shared-id")
    db_session.add_all([by_convention, by_config])
    await db_session.commit()

    doc = _xmltv(_programme("shared-id", "20260731080000", "20260731090000", "Quien gana"))
    await EpgService(db_session).ingest(doc)
    await db_session.commit()

    row = (await db_session.execute(select(EpgProgramme))).scalar_one()
    assert row.channel_id == by_config.id


async def test_unmapped_xmltv_channel_cannot_break_the_ingest(db_session, guide):
    """A provider carrying channels we do not is the normal case, not an error.
    40 channels must not lose their guide because of the 41st."""
    await _channels(db_session)
    result = await EpgService(db_session).ingest(guide)
    await db_session.commit()

    assert result.unmapped_channel_ids == ["nada.xx"]
    assert result.skipped_unmapped == 1
    assert await _count(db_session) == 2  # the mapped ones landed anyway


async def test_ingest_with_no_matching_channel_at_all_is_empty_not_an_error(db_session):
    await _channels(db_session)
    doc = _xmltv(_programme("desconocido", "20260731080000", "20260731090000", "X"))
    result = await EpgService(db_session).ingest(doc)
    await db_session.commit()

    assert result.parsed == 1 and result.upserted == 0
    assert await _count(db_session) == 0


# ── Ingest: idempotency (the no-duplicates requirement) ──────────────────────


async def test_reingesting_the_same_source_does_not_duplicate_the_grid(db_session, guide):
    await _channels(db_session)
    svc = EpgService(db_session)

    await svc.ingest(guide)
    await db_session.commit()
    first = await _count(db_session)

    await svc.ingest(guide)
    await db_session.commit()
    await svc.ingest(guide)
    await db_session.commit()

    assert first == 2
    assert await _count(db_session) == 2


async def test_reingest_refreshes_a_revised_programme_instead_of_duplicating_it(db_session):
    """ON CONFLICT DO UPDATE, not DO NOTHING: providers revise titles and end
    times between refreshes, and DO NOTHING would pin the guide to whatever the
    first ingest happened to see."""
    await _channels(db_session)
    svc = EpgService(db_session)

    await svc.ingest(_xmltv(_programme(
        "tvn.ec", "20260731080000 -0500", "20260731100000 -0500", "Titulo viejo")))
    await db_session.commit()

    await svc.ingest(_xmltv(_programme(
        "tvn.ec", "20260731080000 -0500", "20260731103000 -0500",
        "Titulo nuevo", "Descripcion nueva")))
    await db_session.commit()

    row = (await db_session.execute(select(EpgProgramme))).scalar_one()
    assert row.title == "Titulo nuevo"
    assert row.description == "Descripcion nueva"
    assert row.end_at == datetime(2026, 7, 31, 15, 30, tzinfo=timezone.utc)
    assert await _count(db_session) == 1


async def test_no_duplicates_is_enforced_by_the_SCHEMA_not_only_by_the_service(db_session):
    """The guarantee must survive a bug in EpgService, two ingests racing, or
    whatever replaces that code later — which is the lesson `plan_channels`
    taught this project (migrations 005/008). So: bypass the service entirely and
    insert the duplicate row directly. Postgres has to be the one that refuses.
    """
    channels = await _channels(db_session)
    start = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)
    common = dict(
        channel_id=channels["mapped"].id,
        start_at=start,
        end_at=start + timedelta(hours=1),
    )
    db_session.add(EpgProgramme(id=uuid.uuid4(), title="Primero", **common))
    await db_session.commit()

    db_session.add(EpgProgramme(id=uuid.uuid4(), title="Duplicado", **common))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# ── Retention ────────────────────────────────────────────────────────────────


async def test_retention_prunes_programmes_that_already_ended(db_session):
    channels = await _channels(db_session)
    old_start = datetime.now(timezone.utc) - timedelta(days=10)
    db_session.add(
        EpgProgramme(
            id=uuid.uuid4(), channel_id=channels["mapped"].id, title="Antiguo",
            start_at=old_start, end_at=old_start + timedelta(hours=1),
        )
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)
    doc = _xmltv(_programme(
        "tvn.ec",
        now.strftime("%Y%m%d%H%M%S"),
        (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S"),
        "Actual",
    ))
    result = await EpgService(db_session).ingest(doc, retention_days=3)
    await db_session.commit()

    assert result.pruned == 1
    titles = (await db_session.execute(select(EpgProgramme.title))).scalars().all()
    assert titles == ["Actual"]


# ── The window query ─────────────────────────────────────────────────────────


async def test_window_query_returns_overlaps_including_what_is_on_air_now(db_session):
    """Overlap, not containment. The programme a viewer most wants is the one
    airing RIGHT NOW, which started BEFORE the window opened — asking for
    programmes that *begin* inside the window would drop exactly that one."""
    channels = await _channels(db_session)
    base = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def _row(offset_h: int, hours: int, title: str) -> EpgProgramme:
        return EpgProgramme(
            id=uuid.uuid4(), channel_id=channels["mapped"].id, title=title,
            start_at=base + timedelta(hours=offset_h),
            end_at=base + timedelta(hours=offset_h + hours),
        )

    db_session.add_all([
        _row(-4, 1, "Terminado"),      # ends before the window
        _row(-1, 3, "En emision"),     # started before, still running → OVERLAPS
        _row(2, 1, "Dentro"),          # entirely inside
        _row(8, 1, "Despues"),         # starts after the window closes
    ])
    await db_session.commit()

    programmes = await EpgService(db_session).programmes_in_window(
        channels["mapped"].id, base, base + timedelta(hours=4)
    )
    assert [p.title for p in programmes] == ["En emision", "Dentro"]


async def test_window_query_is_scoped_to_one_channel(db_session):
    channels = await _channels(db_session)
    base = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    for channel, title in ((channels["mapped"], "Mio"), (channels["unmapped"], "Ajeno")):
        db_session.add(EpgProgramme(
            id=uuid.uuid4(), channel_id=channel.id, title=title,
            start_at=base, end_at=base + timedelta(hours=1),
        ))
    await db_session.commit()

    programmes = await EpgService(db_session).programmes_in_window(
        channels["mapped"].id, base, base + timedelta(hours=2)
    )
    assert [p.title for p in programmes] == ["Mio"]


# ── Source loading ───────────────────────────────────────────────────────────


async def test_load_source_reads_a_local_file(tmp_path, guide):
    path = tmp_path / "guide.xml"
    path.write_bytes(guide)
    assert await load_source(str(path)) == guide


async def test_load_source_reports_a_missing_file_as_a_source_error(tmp_path):
    with pytest.raises(EpgSourceError):
        await load_source(str(tmp_path / "no-such-file.xml"))


async def test_load_source_refuses_an_empty_configuration():
    with pytest.raises(EpgSourceError):
        await load_source("   ")


# ── The endpoint ─────────────────────────────────────────────────────────────


def _app(world_session, subscriber, redis):
    from app.main import app
    from app.database import get_db
    from app.redis_client import get_redis
    from app.core.dependencies import get_current_subscriber

    app.dependency_overrides[get_db] = lambda: world_session
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_subscriber] = lambda: subscriber
    return app


async def _get_epg(db, subscriber, redis, channel_key: str, **params):
    app = _app(db, subscriber, redis)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(
                f"/api/client/catalog/channels/{channel_key}/epg", params=params
            )
    finally:
        app.dependency_overrides.clear()


async def test_endpoint_serves_the_legacy_mock_when_the_flag_is_off(
    entitlement_world, redis_client, monkeypatch
):
    """Default OFF must be today's behaviour byte for byte — canal-1 is one of
    the three keys the mock happened to cover."""
    import app.api.client.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod.settings, "epg_enabled", False)
    response = await _get_epg(
        entitlement_world["session"], entitlement_world["subscriber"],
        redis_client, "canal-1",
    )
    assert response.status_code == 200
    assert [e["title"] for e in response.json()] == ["Morning Show"]


async def test_endpoint_serves_ingested_programmes_when_the_flag_is_on(
    entitlement_world, redis_client, monkeypatch
):
    import app.api.client.catalog as catalog_mod

    db = entitlement_world["session"]
    channel = entitlement_world["ch_in"]          # channel_key == "canal-1"
    now = datetime.now(timezone.utc)
    db.add(EpgProgramme(
        id=uuid.uuid4(), channel_id=channel.id, title="Programa real",
        description="De verdad",
        start_at=now - timedelta(minutes=30), end_at=now + timedelta(minutes=30),
    ))
    await db.commit()

    monkeypatch.setattr(catalog_mod.settings, "epg_enabled", True)
    response = await _get_epg(
        db, entitlement_world["subscriber"], redis_client, "canal-1"
    )
    assert response.status_code == 200
    body = response.json()
    assert [e["title"] for e in body] == ["Programa real"]
    assert body[0]["description"] == "De verdad"
    assert "Morning Show" not in str(body)  # the mock is gone, not a fallback


async def test_channel_with_no_guide_returns_empty_list_not_500_and_not_invented(
    entitlement_world, redis_client, monkeypatch
):
    """The requirement, stated directly: an empty list is honest, invented data
    is not, and it must be a 200 — the CHANNEL exists, only its guide is unknown,
    and a 404 would read as "no such channel" and break the grid."""
    import app.api.client.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod.settings, "epg_enabled", True)
    response = await _get_epg(
        entitlement_world["session"], entitlement_world["subscriber"],
        redis_client, "canal-1",
    )
    assert response.status_code == 200
    assert response.json() == []


async def test_unknown_channel_still_404s(entitlement_world, redis_client, monkeypatch):
    import app.api.client.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod.settings, "epg_enabled", True)
    response = await _get_epg(
        entitlement_world["session"], entitlement_world["subscriber"],
        redis_client, "no-existe",
    )
    assert response.status_code == 404


async def test_endpoint_honours_an_explicit_time_window(
    entitlement_world, redis_client, monkeypatch
):
    import app.api.client.catalog as catalog_mod

    db = entitlement_world["session"]
    channel = entitlement_world["ch_in"]
    base = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    for offset, title in ((0, "Dentro"), (10, "Fuera")):
        db.add(EpgProgramme(
            id=uuid.uuid4(), channel_id=channel.id, title=title,
            start_at=base + timedelta(hours=offset),
            end_at=base + timedelta(hours=offset + 1),
        ))
    await db.commit()

    monkeypatch.setattr(catalog_mod.settings, "epg_enabled", True)
    response = await _get_epg(
        db, entitlement_world["subscriber"], redis_client, "canal-1",
        start=base.isoformat(), end=(base + timedelta(hours=3)).isoformat(),
    )
    assert response.status_code == 200
    assert [e["title"] for e in response.json()] == ["Dentro"]


# ── Flags ────────────────────────────────────────────────────────────────────


async def test_flags_default_to_off_so_deploying_changes_nothing(monkeypatch):
    from app.config import Settings

    for var in ("EPG_ENABLED", "EPG_INGEST_ENABLED", "EPG_SOURCE_URL"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.epg_enabled is False
    assert settings.epg_ingest_enabled is False
    assert settings.epg_source_url == ""


async def test_ingest_loop_does_not_start_without_a_configured_source(monkeypatch):
    """"The ingest must not start on its own without being configured." With the
    flag on but no source the loop RETURNS rather than looping, so an
    unconfigured deployment never reaches out to the network on a timer."""
    import app.main as main_mod
    from app.config import Settings

    cfg = Settings(_env_file=None)
    cfg.epg_ingest_enabled = True
    cfg.epg_source_url = ""
    monkeypatch.setattr(main_mod, "get_settings", lambda: cfg)
    # Returns immediately; if it looped, the startup sleep would hang this test.
    assert await main_mod._epg_ingest_loop() is None

    cfg.epg_ingest_enabled = False
    cfg.epg_source_url = "https://example.invalid/guide.xml"
    assert await main_mod._epg_ingest_loop() is None


# ── Migration ────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")
def test_migration_creates_a_real_unique_constraint_not_a_bare_index():
    """The regression this branch has already had to repair TWICE (008, 010).

    EpgService upserts with `INSERT … ON CONFLICT ON CONSTRAINT
    uq_epg_programmes_channel_start`, which Postgres resolves through
    `pg_constraint`. A unique INDEX of the same name enforces the same
    uniqueness, looks identical in psql, and is INVISIBLE there — every ingest
    would die with `UndefinedObject`. Asserted against a database built by the
    real migration, because `tests/conftest.py` builds its schema from the ORM
    and therefore cannot see this class of bug at all.

    Note this runs `alembic upgrade 013`, not `upgrade head`: four lanes are
    adding schema off revision 009 on this branch, so the repository has several
    heads until the lead linearises them and `head` is ambiguous by design.
    """
    url = make_url(TEST_DATABASE_URL)
    db_name = f"nexora_epgmig_{uuid.uuid4().hex[:12]}"
    admin = create_engine(
        url.set(drivername="postgresql+psycopg", database="postgres"),
        isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool,
    )
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    try:
        env = dict(os.environ)
        env.update(
            POSTGRES_HOST=url.host, POSTGRES_PORT=str(url.port),
            POSTGRES_USER=url.username, POSTGRES_PASSWORD=url.password,
            POSTGRES_DB=db_name,
        )
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "013"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            f"`alembic upgrade 013` failed:\n{proc.stdout}\n{proc.stderr}"
        )

        engine = create_engine(
            url.set(drivername="postgresql+psycopg", database=db_name),
            poolclass=sa.pool.NullPool,
        )
        try:
            with engine.connect() as conn:
                contype = conn.execute(text(
                    "SELECT contype FROM pg_constraint "
                    "WHERE conname = 'uq_epg_programmes_channel_start' "
                    "AND conrelid = 'epg_programmes'::regclass"
                )).scalar()
                indexes = {
                    row[0] for row in conn.execute(text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename = 'epg_programmes'"
                    ))
                }
                fk_delete_rule = conn.execute(text(
                    "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
                    "JOIN information_schema.table_constraints tc "
                    "  ON tc.constraint_name = rc.constraint_name "
                    "WHERE tc.table_name = 'epg_programmes'"
                )).scalar()

            assert contype == "u", (
                "uq_epg_programmes_channel_start is not a UNIQUE CONSTRAINT in a "
                f"migration-built database (pg_constraint.contype={contype!r}, "
                "expected 'u'). `ON CONFLICT ON CONSTRAINT` cannot see a bare "
                "unique index — see migrations 008 and 010."
            )
            # The retention sweep's index, and the constraint's own backing index
            # which doubles as the (channel_id, start_at) read path.
            assert "ix_epg_programmes_end_at" in indexes
            assert "uq_epg_programmes_channel_start" in indexes
            # Deleting a channel must take its guide with it, in the database.
            assert fk_delete_rule == "CASCADE"
        finally:
            engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                     "WHERE datname = :db AND pid <> pg_backend_pid()"),
                {"db": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()
