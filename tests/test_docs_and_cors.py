"""Schema exposure (/docs, /redoc, /openapi.json) and CORS origins.

Two audited findings:

1. The OpenAPI schema was published unconditionally, so production served the
   full API map — including /internal/stream-auth/validate and its parameter
   contract — to anyone on the Internet (and to raw-IP requests). It must be
   closed when APP_ENV marks production, and the internal router must stay out
   of the schema even where the schema IS published.
2. The CORS origin list was hardcoded to localhost + a dev IP, with no
   production domain and no way to configure it. It must come from
   CORS_ALLOW_ORIGINS (CSV, same discipline as PLAYBACK_HOST_ALLOWLIST), with
   the historical development origins as the default so an unset variable
   changes nothing.
"""
import httpx
import pytest
from httpx import ASGITransport

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.asyncio

# The origins main.py shipped hardcoded before the change. The default value of
# CORS_ALLOW_ORIGINS must still produce exactly these.
LEGACY_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://172.27.99.151:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]


def _settings(**overrides) -> Settings:
    """Settings with the environment-dependent knobs pinned explicitly.

    `debug` is always pinned so the local .env (DEBUG=true) cannot flip the CORS
    branch under the tests.
    """
    base: dict = {"app_env": "production", "debug": False}
    base.update(overrides)
    return Settings(**base)


def _client(config: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=create_app(config)), base_url="http://test"
    )


# ── 1. Schema exposure ────────────────────────────────────────────────────────

async def test_docs_are_closed_in_production():
    async with _client(_settings(app_env="production")) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            r = await c.get(path)
            assert r.status_code == 404, f"{path} -> {r.status_code} (must be 404 in production)"


@pytest.mark.parametrize("app_env", ["development", "staging", "PRODUCTION_LIKE_TYPO"])
async def test_docs_stay_open_outside_production(app_env):
    """Only APP_ENV=production closes the schema; nothing else changes today."""
    async with _client(_settings(app_env=app_env)) as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            r = await c.get(path)
            assert r.status_code == 200, f"{path} -> {r.status_code} (must stay open in {app_env})"


async def test_production_detection_uses_app_env():
    assert _settings(app_env="production").is_production is True
    assert _settings(app_env="  Production  ").is_production is True
    assert _settings(app_env="staging").is_production is False
    assert _settings(app_env="development").is_production is False
    # DEBUG is an independent switch and must not decide the environment.
    assert _settings(app_env="production", debug=True).is_production is True


async def test_internal_stream_auth_is_never_in_the_schema():
    """Even where the schema is published, the edge-internal gate stays out."""
    app = create_app(_settings(app_env="development"))
    schema = app.openapi()
    paths = set(schema["paths"])

    assert paths, "schema unexpectedly empty — the assertion below would be vacuous"
    assert not [p for p in paths if p.startswith("/internal/")], sorted(paths)
    # The route is still MOUNTED (nginx auth_request depends on it), just hidden.
    assert any(
        getattr(r, "path", "") == "/internal/stream-auth/validate" for r in app.routes
    ), "the internal route must remain reachable, only hidden from the schema"
    # Sanity: public surface is still described.
    assert "/health" in paths


async def test_internal_route_is_still_served_when_schema_is_closed():
    """Closing the schema must not unmount anything: /internal/* still answers
    (401 for a missing token here), it just is not advertised."""
    async with _client(_settings(app_env="production")) as c:
        r = await c.get("/internal/stream-auth/validate")
        assert r.status_code == 401, (
            f"the edge auth_request gate must stay mounted and deny, got {r.status_code}: {r.text}"
        )


# ── 2. CORS origins ───────────────────────────────────────────────────────────

async def test_cors_default_keeps_development_origins():
    """Env-independent: assert the declared default, not a parsed .env."""
    default = Settings.model_fields["cors_allow_origins"].default
    assert [o.strip() for o in default.split(",") if o.strip()] == LEGACY_DEV_ORIGINS
    assert _settings().cors_allowed_origins == LEGACY_DEV_ORIGINS


async def test_cors_origins_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(
        "CORS_ALLOW_ORIGINS",
        " https://nexoraplay.net , https://tvdigital.laredtelco.com/ ,, https://nexoraplay.net ",
    )
    cfg = Settings(app_env="production", debug=False)
    assert cfg.cors_allowed_origins == [
        "https://nexoraplay.net",
        "https://tvdigital.laredtelco.com",
    ]


async def test_configured_origin_gets_cors_headers():
    cfg = _settings(cors_allow_origins="https://nexoraplay.net,https://tv.example.com")
    async with _client(cfg) as c:
        r = await c.get("/health", headers={"Origin": "https://tv.example.com"})
        assert r.headers.get("access-control-allow-origin") == "https://tv.example.com"
        assert r.headers.get("access-control-allow-credentials") == "true"


async def test_default_origins_still_work_for_local_development():
    async with _client(_settings()) as c:
        r = await c.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_unlisted_origin_gets_no_permissive_cors_headers():
    cfg = _settings(cors_allow_origins="https://nexoraplay.net")
    async with _client(cfg) as c:
        r = await c.get("/health", headers={"Origin": "https://evil.example"})
        # Access-Control-Allow-Origin is the header that actually grants access:
        # without it the browser discards the response no matter what else is
        # present (Starlette emits Allow-Credentials unconditionally, which is
        # inert on its own).
        assert "access-control-allow-origin" not in r.headers

        # Preflight for the same unlisted origin must not be granted either.
        pre = await c.request(
            "OPTIONS",
            "/api/client/auth/login",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert pre.headers.get("access-control-allow-origin") != "https://evil.example"
        assert pre.headers.get("access-control-allow-origin") != "*"


async def test_never_wildcard_with_credentials():
    """`allow_origins=["*"]` + `allow_credentials=True` is invalid per the CORS
    spec and browsers reject it. Outside debug we send explicit origins with
    credentials; in debug we send the wildcard WITHOUT credentials."""
    async with _client(_settings(app_env="production")) as c:
        r = await c.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") != "*"
        assert r.headers.get("access-control-allow-credentials") == "true"

    async with _client(_settings(app_env="development", debug=True)) as c:
        r = await c.get("/health", headers={"Origin": "https://anything.example"})
        assert r.headers.get("access-control-allow-origin") == "*"
        assert "access-control-allow-credentials" not in r.headers
