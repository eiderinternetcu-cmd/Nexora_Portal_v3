"""Migration 011 — audit_logs partitioning, and the guarantee it must not break.

WHY THIS FILE NEEDS ITS OWN DATABASE
────────────────────────────────────
Every other test runs against `tests/conftest.py::db_session`, which builds the
schema with `Base.metadata.create_all()`. That produces a plain, unpartitioned
`audit_logs` and none of the `audit_part` machinery — partitioning is physical
storage management and is deliberately not ORM-mapped. So none of this is
observable there. These tests build a real migrated database instead, the same
way `tests/test_migration_schema_parity.py` does.

`alembic upgrade 011`, not `upgrade head`: several lanes currently branch off 009,
so the revision graph has multiple heads and `head` is ambiguous. Naming the
revision also makes this file test the migration it is about, rather than whatever
happens to be newest.

THE POINT OF THE FILE
─────────────────────
Migration 007 made the audit trail append-only. Partitioning is the step most
likely to quietly undo that, because a trigger on a parent table is not inherited
the way one might assume, and a table that has stopped rejecting UPDATEs looks
exactly like one that never had to. So the central assertions are behavioural, not
structural: UPDATE and DELETE are attempted for real — through the parent and
straight at each individual partition — and must be refused.

A TRAP THIS FILE IS BUILT TO AVOID
──────────────────────────────────
`DELETE FROM <empty partition>` succeeds with "DELETE 0". A BEFORE **ROW** trigger
never fires when there are no rows, so a deletion test against an empty partition
passes whether or not the protection exists — the test would be green precisely in
the case where the guarantee had been destroyed. Found while prototyping this
migration. Every rejection test below therefore asserts the target partition is
non-empty FIRST (`_assert_populated`), and the fixture plants a row in each
partition it exercises.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.database import Base
import app.models  # noqa: F401  register every model on Base.metadata

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)

REVISION = "011"
PARTITION_SCHEMA = "audit_part"
TRIGGER = "audit_logs_no_update_delete"


# ── An ephemeral, migration-built database ───────────────────────────────────

def _admin_engine(url):
    return create_engine(
        url.set(drivername="postgresql+psycopg", database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=sa.pool.NullPool,
    )


@pytest.fixture(scope="module")
def partitioned_engine():
    """A throwaway database at revision 011."""
    url = make_url(TEST_DATABASE_URL)
    db_name = f"nexora_auditpart_{uuid.uuid4().hex[:12]}"

    admin = _admin_engine(url)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    try:
        # Subprocess, for the reasons test_migration_schema_parity.py documents:
        # migrations/env.py reads an @lru_cache'd settings object and calls
        # asyncio.run(), neither of which survives being driven in-process from
        # pytest. POSTGRES_* outranks .env in pydantic-settings.
        env = dict(os.environ)
        env.update(
            POSTGRES_HOST=url.host,
            POSTGRES_PORT=str(url.port),
            POSTGRES_USER=url.username,
            POSTGRES_PASSWORD=url.password,
            POSTGRES_DB=db_name,
        )
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", REVISION],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            f"`alembic upgrade {REVISION}` failed on a fresh database.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

        engine = create_engine(
            url.set(drivername="postgresql+psycopg", database=db_name),
            poolclass=sa.pool.NullPool,
        )
        try:
            yield engine
        finally:
            engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


@pytest.fixture
def conn(partitioned_engine):
    """A connection in a transaction that is ALWAYS rolled back.

    Postgres DDL is transactional, so tests can create, attach, detach and drop
    partitions freely and leave the database exactly as they found it. That is
    what lets one expensive migrated database serve every test here without the
    tests becoming order-dependent.
    """
    with partitioned_engine.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _partitions(conn) -> dict[str, str]:
    """{partition name → its bound expression}, straight from the catalog."""
    rows = conn.execute(text(
        "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) "
        "FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid "
        "WHERE i.inhparent = 'public.audit_logs'::regclass"
    )).all()
    return {name: bound for name, bound in rows}


def _insert(conn, action: str, created_at_sql: str) -> str:
    """Insert one audit row and report which partition it landed in."""
    return conn.execute(text(
        "INSERT INTO audit_logs (id, action, created_at) "
        f"VALUES (gen_random_uuid(), :action, {created_at_sql}) "
        "RETURNING tableoid::regclass::text"
    ), {"action": action}).scalar()


def _count(conn, relation: str) -> int:
    return conn.execute(text(f"SELECT count(*) FROM {relation}")).scalar()


def _assert_populated(conn, relation: str) -> None:
    """Guard against the false green described in this module's docstring.

    A BEFORE ROW trigger cannot fire over zero rows, so a rejection test on an
    empty relation proves nothing at all.
    """
    assert _count(conn, relation) > 0, (
        f"{relation} is empty, so an UPDATE/DELETE against it would be a no-op "
        f"and would 'pass' even with the immutability trigger gone. The test "
        f"needs a row here to prove anything."
    )


def _expect_rejected(conn, statement: str) -> str:
    """Run `statement`, require Postgres to refuse it, return its actual message.

    Wrapped in a SAVEPOINT: the failure aborts the current (sub)transaction, and
    without one the outer transaction would be unusable for the rest of the test.
    """
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(statement))
    except DBAPIError as exc:
        savepoint.rollback()
        return str(exc.orig).strip()
    savepoint.rollback()
    pytest.fail(
        f"AUDIT TRAIL IS NO LONGER APPEND-ONLY.\n"
        f"  {statement}\n"
        f"was accepted. Migration 007 must reject every UPDATE and DELETE on "
        f"audit_logs, on every partition. Migration 011 has broken it."
    )


@pytest.fixture
def populated(conn):
    """One audit row in each of the three kinds of partition that exist.

    Returns {kind → partition name}. Which partition a timestamp routes to is
    read back from `tableoid` rather than assumed, so the fixture also proves the
    routing itself works:

      history — 'now', which falls inside the pre-011 partition (it runs to the
                start of next month)
      monthly — two months out, a partition `ensure_partitions()` pre-created
      default — far beyond any monthly partition; the safety net of §6
    """
    landed = {
        "history": _insert(conn, "part.history", "now()"),
        "monthly": _insert(conn, "part.monthly", "now() + interval '2 months'"),
        "default": _insert(conn, "part.default", "now() + interval '100 years'"),
    }
    # Strip the schema qualification psql-style regclass may add.
    landed = {k: v.split(".")[-1].strip('"') for k, v in landed.items()}
    assert len(set(landed.values())) == 3, (
        f"expected three distinct partitions, rows landed in {landed}"
    )
    return landed


# ── Structure ────────────────────────────────────────────────────────────────

def test_audit_logs_is_partitioned_by_created_at(conn):
    relkind = conn.execute(text(
        "SELECT relkind FROM pg_class "
        "WHERE relname = 'audit_logs' AND relnamespace = 'public'::regnamespace"
    )).scalar()
    assert relkind == "p", f"audit_logs is not a partitioned table (relkind={relkind!r})"

    strategy, key = conn.execute(text(
        "SELECT p.partstrat, pg_get_partkeydef('public.audit_logs'::regclass) "
        "FROM pg_partitioned_table p WHERE p.partrelid = 'public.audit_logs'::regclass"
    )).one()
    assert strategy == "r", "audit_logs must be RANGE-partitioned"
    assert "created_at" in key


def test_a_default_partition_exists(conn):
    """The safety net. Without it, a month nobody created is a login outage."""
    bounds = _partitions(conn)
    assert "DEFAULT" in bounds.values(), (
        f"no DEFAULT partition: an INSERT for an uncovered month would raise "
        f"'no partition of relation found for row' on the authentication path. "
        f"Partitions: {bounds}"
    )


def test_future_partitions_were_pre_created(conn):
    """Nobody should have to remember next month."""
    created = conn.execute(text(
        "SELECT unnest(audit_part.ensure_partitions())"
    )).scalars().all()
    # Idempotent: a second call creates nothing new and must not error.
    again = conn.execute(text(
        "SELECT unnest(audit_part.ensure_partitions())"
    )).scalars().all()
    assert created == again

    months = [p for p in _partitions(conn) if p[len("audit_logs_"):][:2] == "20"]
    assert len(months) >= 3, f"expected several months pre-created, got {months}"


def test_partitions_are_invisible_to_public_schema_reflection(partitioned_engine):
    """Partitions must not leak into `public`.

    SQLAlchemy reflects relkind 'r' and 'p' alike, so partitions left in `public`
    would show up in `Inspector.get_table_names()` and
    tests/test_migration_schema_parity.py would report a brand-new phantom
    `table.extra_in_db:audit_logs_YYYY_MM` divergence every single month.
    """
    tables = set(inspect(partitioned_engine).get_table_names())
    strays = {t for t in tables if t.startswith("audit_logs_")}
    assert not strays, (
        f"partition tables are visible in the `public` schema: {sorted(strays)}. "
        f"They belong in `{PARTITION_SCHEMA}`."
    )
    assert "audit_logs" in tables


# ── Immutability: the acceptance criterion ───────────────────────────────────

def test_every_partition_carries_the_immutability_trigger(conn):
    """Structural sweep: no partition may be left unprotected.

    The behavioural tests below exercise the three kinds of partition that
    actually exist; this one holds for every partition regardless.
    """
    protected = set(conn.execute(text(
        "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE NOT t.tgisinternal AND t.tgname = :trg"
    ), {"trg": TRIGGER}).scalars().all())

    missing = set(_partitions(conn)) - protected
    assert not missing, (
        f"these partitions have no {TRIGGER} trigger and accept UPDATE/DELETE "
        f"freely: {sorted(missing)}"
    )
    assert "audit_logs" in protected, "the parent itself lost the trigger"


@pytest.mark.parametrize("kind", ["history", "monthly", "default"])
def test_update_is_rejected_on_each_partition(conn, populated, kind):
    """Straight at the partition, bypassing the parent entirely."""
    partition = f"{PARTITION_SCHEMA}.{populated[kind]}"
    _assert_populated(conn, partition)

    message = _expect_rejected(conn, f"UPDATE {partition} SET action = 'tampered'")

    assert "append-only" in message, message
    assert _count(conn, partition) > 0
    assert _count(conn, f"{partition} WHERE action = 'tampered'") == 0


@pytest.mark.parametrize("kind", ["history", "monthly", "default"])
def test_delete_is_rejected_on_each_partition(conn, populated, kind):
    partition = f"{PARTITION_SCHEMA}.{populated[kind]}"
    _assert_populated(conn, partition)
    before = _count(conn, partition)

    message = _expect_rejected(conn, f"DELETE FROM {partition}")

    assert "append-only" in message, message
    assert _count(conn, partition) == before, "rows disappeared despite the refusal"


def test_update_and_delete_are_rejected_through_the_parent(conn, populated):
    _assert_populated(conn, "public.audit_logs")
    before = _count(conn, "public.audit_logs")

    assert "append-only" in _expect_rejected(
        conn, "UPDATE audit_logs SET action = 'tampered'")
    assert "append-only" in _expect_rejected(
        conn, "DELETE FROM audit_logs")

    assert _count(conn, "public.audit_logs") == before


def test_a_partition_created_after_the_migration_is_protected_too(conn):
    """The guarantee has to cover partitions that do not exist yet.

    This is the case a per-partition trigger list would silently get wrong: the
    trigger is defined on the parent, so Postgres clones it onto every partition
    created from here to eternity, with nothing to maintain.
    """
    conn.execute(text(
        "CREATE TABLE audit_part.audit_logs_test_future PARTITION OF public.audit_logs "
        "FOR VALUES FROM ('2090-01-01 00:00:00+00') TO ('2090-02-01 00:00:00+00')"
    ))
    landed = _insert(conn, "far.future", "TIMESTAMPTZ '2090-01-15 00:00:00+00'")
    assert landed.endswith("audit_logs_test_future")

    _assert_populated(conn, "audit_part.audit_logs_test_future")
    assert "append-only" in _expect_rejected(
        conn, "DELETE FROM audit_part.audit_logs_test_future")


def test_the_trigger_cannot_be_removed_from_a_single_partition(conn):
    """Cloned triggers are all-or-nothing, which is stronger than before 011.

    Postgres refuses to drop a cloned trigger from one partition, so the
    protection cannot be quietly lifted off a single month.
    """
    partition = next(
        p for p, bound in _partitions(conn).items() if bound != "DEFAULT"
    )
    message = _expect_rejected(
        conn, f"DROP TRIGGER {TRIGGER} ON {PARTITION_SCHEMA}.{partition}"
    )
    assert "requires it" in message, message


def test_detaching_lifts_protection_and_reattaching_restores_it(conn, populated):
    """Pins the one real escape hatch, and the fact that it closes again.

    DETACH removes the cloned trigger — the detached table accepts DELETE
    immediately, and no `DISABLE TRIGGER` is involved (attempting one fails: there
    is no trigger left to disable). That makes DETACH itself the moment 007's
    guarantee is lifted, which is why the DEFAULT-consolidation runbook in
    docs/AUDIT_PARTITIONING.md insists on one announced transaction.

    Asserted here so the runbook cannot drift from what Postgres actually does,
    and — the part that matters for safety — so that a future version which fails
    to restore protection on re-attach is caught.
    """
    default = f"{PARTITION_SCHEMA}.{populated['default']}"
    _assert_populated(conn, default)

    conn.execute(text(f"ALTER TABLE public.audit_logs DETACH PARTITION {default}"))
    assert conn.execute(text(
        # cast(...) rather than `:rel::regclass`: text() will not bind a
        # parameter that is immediately followed by `::`.
        "SELECT count(*) FROM pg_trigger "
        "WHERE tgrelid = cast(:rel AS regclass) AND NOT tgisinternal AND tgname = :trg"
    ), {"rel": default, "trg": TRIGGER}).scalar() == 0, (
        "the cloned trigger survived DETACH — the runbook in "
        "docs/AUDIT_PARTITIONING.md describes the opposite and would be wrong"
    )
    conn.execute(text(f"DELETE FROM {default}"))  # permitted while detached

    conn.execute(text(
        f"ALTER TABLE public.audit_logs ATTACH PARTITION {default} DEFAULT"))
    _insert(conn, "after.reattach", "now() + interval '100 years'")
    _assert_populated(conn, default)
    assert "append-only" in _expect_rejected(conn, f"DELETE FROM {default}"), (
        "re-attaching did not restore the append-only protection"
    )


# ── Reads and writes behave as before ────────────────────────────────────────

def test_insert_and_query_work_exactly_as_before(conn):
    before = _count(conn, "audit_logs")

    landed = _insert(conn, "auth.login", "now()")
    assert landed  # routed somewhere

    assert _count(conn, "audit_logs") == before + 1
    row = conn.execute(text(
        "SELECT action FROM audit_logs WHERE action = 'auth.login' LIMIT 1"
    )).scalar()
    assert row == "auth.login", "a row written through the parent is not readable there"


def test_an_uncovered_month_lands_in_default_instead_of_failing_the_login(conn):
    """The forgotten-maintenance scenario, which must never break authentication.

    Audit rows are written on the login path. On a partitioned table an INSERT
    matching no partition raises 'no partition of relation ... found for row',
    which would turn a missed maintenance job into a total login outage. The
    DEFAULT partition absorbs it instead — and the row stays immutable there.
    """
    landed = _insert(conn, "auth.login", "now() + interval '100 years'")
    assert landed.endswith("audit_logs_default"), (
        f"a row for an uncovered month landed in {landed!r} rather than the "
        f"DEFAULT partition"
    )
    assert _count(conn, "audit_logs WHERE action = 'auth.login'") == 1


# ── Retention ────────────────────────────────────────────────────────────────

def _age_the_history_partition(conn, upper: str) -> None:
    """Re-bound the (empty) pre-011 partition so its range lies in the past.

    Retention can only be tested against partitions old enough to have aged out,
    and on a freshly migrated database the pre-011 partition covers all of the
    past — leaving no room to create one. It is empty here, so it can be detached
    and re-attached with a historical upper bound, which reproduces the exact
    shape a real database reaches years later. Rolled back with the test.
    """
    conn.execute(text(
        "ALTER TABLE public.audit_logs DETACH PARTITION audit_part.audit_logs_legacy"))
    conn.execute(text(
        "ALTER TABLE public.audit_logs ATTACH PARTITION audit_part.audit_logs_legacy "
        f"FOR VALUES FROM (MINVALUE) TO ('{upper}')"))


def test_retention_ships_disabled_and_with_a_conservative_window(conn):
    enabled, months, premake = conn.execute(text(
        "SELECT enabled, retain_months, premake_months FROM audit_part.retention_policy"
    )).one()
    assert enabled is False, (
        "retention must ship OFF: audit data must never start disappearing "
        "because someone deployed a migration"
    )
    assert months == 84, f"expected a conservative 7-year default, got {months}"
    assert premake >= 1


def test_retention_disabled_drops_nothing_even_when_asked_directly(conn):
    _age_the_history_partition(conn, "2021-01-01 00:00:00+00")
    conn.execute(text(
        "CREATE TABLE audit_part.audit_logs_2021_02 PARTITION OF public.audit_logs "
        "FOR VALUES FROM ('2021-02-01 00:00:00+00') TO ('2021-03-01 00:00:00+00')"))
    conn.execute(text("UPDATE audit_part.retention_policy SET retain_months = 12"))

    # enabled is still false — and the caller explicitly asks for a real run.
    rows = conn.execute(text(
        "SELECT partition_name, dropped FROM audit_part.apply_retention(false)"
    )).all()

    assert all(dropped is False for _, dropped in rows), (
        f"retention deleted partitions while disabled: {rows}"
    )
    assert "audit_logs_2021_02" in _partitions(conn)


def test_retention_drops_aged_partitions_and_keeps_recent_ones(conn):
    _age_the_history_partition(conn, "2021-01-01 00:00:00+00")
    for name, lo, hi in (
        ("audit_logs_2021_02", "2021-02-01", "2021-03-01"),
        ("audit_logs_2021_03", "2021-03-01", "2021-04-01"),
    ):
        conn.execute(text(
            f"CREATE TABLE audit_part.{name} PARTITION OF public.audit_logs "
            f"FOR VALUES FROM ('{lo} 00:00:00+00') TO ('{hi} 00:00:00+00')"))
        conn.execute(text(
            "INSERT INTO audit_logs (id, action, created_at) "
            f"VALUES (gen_random_uuid(), 'aged', TIMESTAMPTZ '{lo} 12:00:00+00')"))

    recent = _insert(conn, "recent", "now()")
    conn.execute(text(
        "UPDATE audit_part.retention_policy SET enabled = true, retain_months = 12"))

    # Dry run first: it must report the same partitions without removing them.
    preview = {r[0] for r in conn.execute(text(
        "SELECT partition_name FROM audit_part.apply_retention(true)")).all()}
    assert preview == {"audit_logs_2021_02", "audit_logs_2021_03"}, preview
    assert preview <= set(_partitions(conn)), "the dry run deleted something"

    dropped = {r[0] for r in conn.execute(text(
        "SELECT partition_name FROM audit_part.apply_retention(false)")).all()}
    assert dropped == preview

    surviving = _partitions(conn)
    assert "audit_logs_2021_02" not in surviving
    assert "audit_logs_2021_03" not in surviving
    assert recent.split(".")[-1] in surviving, "retention removed a current partition"
    assert _count(conn, "audit_logs WHERE action = 'aged'") == 0
    assert _count(conn, "audit_logs WHERE action = 'recent'") == 1


def test_retention_will_not_drop_the_unbounded_history_partition_by_default(conn):
    """Dropping it discards an unknown span of history in one go, not one month.

    So a routine run leaves it alone even once it has aged out; removing it takes
    a second, explicit decision.
    """
    _age_the_history_partition(conn, "2021-01-01 00:00:00+00")
    conn.execute(text(
        "UPDATE audit_part.retention_policy SET enabled = true, retain_months = 12"))

    reported = {r[0] for r in conn.execute(text(
        "SELECT partition_name FROM audit_part.apply_retention(false)")).all()}
    assert "audit_logs_legacy" not in reported
    assert "audit_logs_legacy" in _partitions(conn)

    # ...and goes only when asked for by name.
    conn.execute(text("SELECT audit_part.apply_retention(false, true)"))
    assert "audit_logs_legacy" not in _partitions(conn)


def test_retention_never_touches_the_default_partition(conn):
    """Its bounds are unknown, so its age is unknowable — it must be skipped.

    Anything whose upper bound cannot be read from the catalog is skipped for the
    same reason: the safe direction to fail.
    """
    _age_the_history_partition(conn, "2021-01-01 00:00:00+00")
    conn.execute(text(
        "UPDATE audit_part.retention_policy SET enabled = true, retain_months = 12"))
    conn.execute(text("SELECT audit_part.apply_retention(false, true)"))

    assert "audit_logs_default" in _partitions(conn)


# ── The service wrappers a scheduler would call ──────────────────────────────

@pytest.mark.asyncio
async def test_maintenance_methods_are_a_no_op_on_an_orm_built_database(db_session):
    """They must stay callable where the `audit_part` machinery does not exist.

    `tests/conftest.py` builds every other test database with
    `Base.metadata.create_all()`, which yields a plain, unpartitioned table and no
    `audit_part` schema. Without the probe in `_partitioning_installed()` these
    would raise UndefinedFunction, so a scheduler wired to them would look broken
    across the whole suite while being perfectly healthy in production.
    """
    from app.services.audit_service import AuditService

    service = AuditService(db_session)
    assert await service.ensure_partitions() == []
    assert await service.apply_retention() == []
    assert await service.apply_retention(dry_run=False) == []

    # ...and the ordinary write path is untouched by any of it.
    await service.log(action="auth.login")
    await db_session.commit()
    assert len(await service.list(action="auth.login")) == 1


# ── The ORM still describes the migrated table ───────────────────────────────

def test_orm_and_migrated_schema_agree_on_audit_logs(partitioned_engine):
    """Focused parity check for the table this migration reshapes.

    tests/test_migration_schema_parity.py covers the whole schema, but it runs
    `alembic upgrade head` and cannot run at all while several lanes have 009 as
    their parent. Partitioning forces PRIMARY KEY (id) → (id, created_at), which
    is exactly the kind of ORM/migration divergence that file exists to catch, so
    it is pinned here too rather than left unguarded until the branches merge.
    """
    insp = inspect(partitioned_engine)
    orm = Base.metadata.tables["audit_logs"]

    db_pk = tuple(insp.get_pk_constraint("audit_logs")["constrained_columns"])
    orm_pk = tuple(c.name for c in orm.primary_key.columns)
    assert db_pk == orm_pk == ("id", "created_at"), (
        f"primary key mismatch — ORM {orm_pk}, migrated database {db_pk}. "
        f"Postgres requires the partition key in every unique constraint, so "
        f"app/models/audit.py must declare created_at as part of the key."
    )

    db_cols = {c["name"]: c for c in insp.get_columns("audit_logs")}
    assert set(db_cols) == {c.name for c in orm.columns}
    assert isinstance(db_cols["user_agent"]["type"], sa.Text), (
        "migration 009's varchar(255) → TEXT fix must survive 011 — the new "
        "parent is built with LIKE precisely so it cannot drift"
    )

    db_ix = {ix["name"] for ix in insp.get_indexes("audit_logs")}
    orm_ix = {ix.name for ix in orm.indexes}
    assert orm_ix <= db_ix, f"indexes lost in the conversion: {sorted(orm_ix - db_ix)}"

    fks = insp.get_foreign_keys("audit_logs")
    assert any(
        fk["constrained_columns"] == ["actor_id"]
        and fk["referred_table"] == "users"
        and (fk.get("options") or {}).get("ondelete", "").upper() == "SET NULL"
        for fk in fks
    ), f"the actor_id → users foreign key did not survive partitioning: {fks}"
