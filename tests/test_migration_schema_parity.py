"""Migration ↔ ORM schema parity (roadmap P1.4).

WHY THIS FILE EXISTS
────────────────────
Every other test in this suite runs against a database built by
`tests/conftest.py::db_session`, which does:

    await conn.run_sync(Base.metadata.create_all)

i.e. the schema under test is generated **from the ORM models**. Production's
schema is generated from **Alembic migrations**. Nothing in the suite ever
compared the two, so *any* divergence between them was invisible no matter how
many tests were added — the suite could only ever confirm that the ORM agrees
with itself.

That blind spot shipped a blocker. `migrations/versions/005_plan_channels.py`
originally created `uq_plan_channels_plan_channel` with
`op.create_index(..., unique=True)` — a unique **INDEX** — while
`app/models/plan_channel.py` declares a `UniqueConstraint`. In Postgres those
are different objects: `INSERT ... ON CONFLICT ON CONSTRAINT <name>` (which
`app/services/plan_channel_service.py` uses for the plan→channel whitelist, the
entitlement gate) raises `UndefinedObject` against a bare index. Every write to
the whitelist would have 500'd on any migration-built database — production —
with 345 tests green. See `migrations/versions/008_fix_plan_channels_constraint.py`
and §1 of `docs/INFORME_SESION_2026-07-31.md`.

WHAT THIS FILE DOES
───────────────────
Creates a throwaway database, runs the real `alembic upgrade head` against it,
then compares the resulting schema to `Base.metadata` with `sqlalchemy.inspect`.
Unique CONSTRAINTS and INDEXES are compared as **separate categories**, because
conflating them is exactly the mistake that got through.

`alembic check` is also run (see `test_alembic_check_reports_no_new_drift`).
It is a genuinely useful second net — it covers server defaults, comments and
type changes with zero code on our side — so it is used rather than
reimplemented. It is not sufficient on its own here: it is a single pass/fail
whose failure text is an internal op list, and it cannot express "this drift is
known and owned by someone else". The explicit comparison below produces one
named divergence per problem, which is what makes a regression readable.

READ THIS BEFORE ADDING TO `KNOWN_DIVERGENCES`
──────────────────────────────────────────────
Every entry there is a hole this test is deliberately not guarding. They are
real bugs, recorded so this test can go green on the rest of the schema — not
absolved. `test_no_stale_known_divergences` fails when one is fixed, so the
list cannot rot into a permanent excuse.
"""
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from app.database import Base
import app.models  # noqa: F401  register every model on Base.metadata

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)

# ── What we deliberately do NOT compare, and why ─────────────────────────────
#
# Each exclusion below is a future hole, so each one is justified. The rule
# applied: ignore only what is *structurally guaranteed* to differ between a
# migrated database and `Base.metadata` — never anything that could hide a
# real divergence.

#: Alembic's own bookkeeping table. Created by Alembic, by design absent from
#: `Base.metadata`. Ignoring it is safe: no application code reads it, and its
#: shape is owned by Alembic, not by us.
IGNORED_TABLES = frozenset({"alembic_version"})

#: Postgres creates a backing INDEX for every UNIQUE CONSTRAINT, and SQLAlchemy
#: reflects both the constraint and its index. The index is an implementation
#: detail of the constraint, so counting it would report a phantom "extra index"
#: for every unique constraint in the schema — the sort of permanent false
#: positive that gets a test disabled.
#: The filter is precise, not a name heuristic: SQLAlchemy tags exactly these
#: with `duplicates_constraint`, so an index that merely *looks* like a
#: constraint's index (the 005 bug: a bare unique index squatting on the
#: constraint's name) is NOT filtered and still fails the comparison.
#: Primary-key indexes are likewise excluded by SQLAlchemy's own PG reflection
#: (`get_indexes` never returns them) and are covered by the primary-key test.
#
#: NOT ignored, on purpose: unique constraints vs indexes are compared as two
#: independent categories. That distinction is the whole point of this file.

#: Server defaults are not compared. This is the one exclusion that is about
#: our code rather than about Postgres: the ORM sets most defaults in **Python**
#: (`default=lambda: datetime.now(timezone.utc)`, `default=True`), which emits
#: no DDL default at all, while the migrations set `server_default=sa.text("NOW()")`
#: / `"true"` so that direct SQL inserts behave. Both are intentional and they
#: can never agree, so comparing them would be pure noise.
#: Cost of this hole: a *changed* server default (e.g. NOW() → a fixed date) is
#: not caught here. `alembic check` does compare server defaults and is run in
#: `test_alembic_check_reports_no_new_drift`, which covers this gap.

#: Constraint NAMES are only compared where the ORM states one explicitly.
#: `ForeignKey(...)` and `unique=True` produce unnamed constraints in the ORM,
#: which Postgres then names by its own convention (`plan_channels_plan_id_fkey`).
#: There is no divergence to report there. Where the ORM *does* name a
#: constraint the name is load-bearing — `ON CONFLICT ON CONSTRAINT
#: uq_plan_channels_plan_channel` names it in SQL — so named constraints are
#: compared by name and a rename fails the test.

_PG = postgresql.dialect()


# ── Known divergences ────────────────────────────────────────────────────────
#
# Real ORM↔migration bugs found while building this test. They are NOT fixed
# here: `app/` and `migrations/` are owned by other workers this session, and
# fixing a schema bug needs a migration decision (edit-in-place only helps new
# databases; production is already past these revisions — that is precisely the
# lesson of 008). Reported instead.
#
# Keys are the stable divergence identifiers produced by `_diff_*` below.
KNOWN_DIVERGENCES: dict[str, str] = {
    # SAME BUG CLASS AS 005. app/models/plan.py:15 declares
    # `String(128), unique=True` → an (unnamed) UNIQUE CONSTRAINT, while
    # migrations/versions/001_initial_schema.py:77 creates
    # `op.create_index("ix_plans_name", "plans", ["name"], unique=True)` → a
    # unique INDEX. Uniqueness IS enforced either way, so no data corruption;
    # it becomes a 500 the day any code does `ON CONFLICT ON CONSTRAINT` on
    # plans.name, exactly as plan_channel_service.py does for plan_channels.
    "unique_constraint.missing_in_db:plans:(name)":
        "plans.name: ORM UniqueConstraint vs migration unique INDEX ix_plans_name "
        "(001_initial_schema.py:77). Same class as the 005 bug. Owner: migrations/.",
    "index.extra_in_db:plans:ix_plans_name":
        "Other half of the same plans.name divergence: the unique index the "
        "migration creates has no counterpart in the ORM.",

    # RESOLVED — audit_logs.user_agent (ORM TEXT vs migration VARCHAR(255),
    # 001_initial_schema.py:138) was the live login 500 this test surfaced. Fixed
    # by migrations/versions/009_audit_logs_user_agent_text.py, which alters the
    # column to TEXT. The application-side cap now lives beside the model
    # (app/models/audit.py: AUDIT_USER_AGENT_MAX_LEN), is applied centrally in
    # AuditService.log, and _assert_fits raises at import if it ever exceeds the
    # column again. See tests/test_audit_user_agent_length.py, which reshapes the
    # column back to varchar(255) to reproduce the failure and then applies 009's
    # DDL. Deleted rather than kept: a stale entry silently stops guarding the
    # object, which is what test_no_stale_known_divergences exists to prevent.

    # app/models/subscription.py:23 declares `starts_at ... index=True`; no
    # migration ever creates ix_subscriptions_starts_at. Performance-only
    # (no correctness impact), but it means an EXPLAIN measured against a
    # test database does not describe production.
    "index.missing_in_db:subscriptions:ix_subscriptions_starts_at":
        "subscriptions.starts_at: ORM index=True, never created by any migration "
        "(001_initial_schema.py:87). Performance-only. Owner: migrations/.",
}

#: Operation signatures `alembic check` reports for the same four divergences
#: above. Kept separate from KNOWN_DIVERGENCES because alembic phrases them in
#: its own vocabulary; both lists describe the same four bugs.
KNOWN_ALEMBIC_CHECK_OPS = (
    "modify_type",       # audit_logs.user_agent VARCHAR(255) → Text
    "remove_index",      # ix_plans_name
    "add_constraint",    # plans.name UniqueConstraint
    "add_index",         # ix_subscriptions_starts_at
)


# ── Ephemeral database ───────────────────────────────────────────────────────

def _admin_engine(url):
    """Engine on the `postgres` maintenance DB — CREATE/DROP DATABASE cannot
    run inside a transaction, hence AUTOCOMMIT."""
    return create_engine(
        url.set(drivername="postgresql+psycopg", database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=sa.pool.NullPool,
    )


@pytest.fixture(scope="module")
def migrated_database():
    """A throwaway database built by `alembic upgrade head`, and its inspector.

    Created and destroyed here so the test never depends on anyone having
    prepared a database first. The name carries a random suffix and its own
    prefix so it can never collide with `nexora` or the per-lane `nexora_test*`
    databases even if several of these run at once.
    """
    url = make_url(TEST_DATABASE_URL)
    # Distinct prefix: must not collide with `nexora` or `nexora_test*`.
    db_name = f"nexora_schemaparity_{uuid.uuid4().hex[:12]}"

    admin = _admin_engine(url)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    try:
        # Alembic runs in a SUBPROCESS on purpose:
        #   - migrations/env.py reads the URL from app.config.get_settings(),
        #     which is @lru_cache'd at import time — it cannot be redirected
        #     in-process without poisoning the cache for every other test.
        #   - env.py's online path calls asyncio.run(), which explodes inside
        #     pytest-asyncio's already-running loop.
        #   - it exercises the literal command an operator runs on deploy.
        # POSTGRES_* env vars outrank .env in pydantic-settings, which is how
        # the subprocess gets pointed at the ephemeral database.
        env = dict(os.environ)
        env.update(
            POSTGRES_HOST=url.host,
            POSTGRES_PORT=str(url.port),
            POSTGRES_USER=url.username,
            POSTGRES_PASSWORD=url.password,
            POSTGRES_DB=db_name,
        )
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            "`alembic upgrade head` failed on a fresh database — the migrations "
            "cannot build the schema at all.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

        engine = create_engine(
            url.set(drivername="postgresql+psycopg", database=db_name),
            poolclass=sa.pool.NullPool,
        )
        try:
            yield _Migrated(engine=engine, inspector=inspect(engine), env=env)
        finally:
            engine.dispose()
    finally:
        with admin.connect() as conn:
            # A leftover connection would make DROP DATABASE hang.
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": db_name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        admin.dispose()


class _Migrated:
    def __init__(self, engine, inspector, env):
        self.engine = engine
        self.inspector = inspector
        self.env = env


# ── Snapshot helpers ─────────────────────────────────────────────────────────

def _type_str(type_) -> str:
    """Compile a type with the postgresql dialect.

    `str(type_)` is NOT usable here: it renders both `DateTime(timezone=True)`
    and `DateTime()` as "TIMESTAMP", which would silently pass a
    timestamptz→timestamp divergence — a real class of production bug.
    """
    try:
        return type_.compile(dialect=_PG).upper()
    except Exception:  # pragma: no cover - types PG cannot render
        return str(type_).upper()


def _orm_tables() -> dict[str, sa.Table]:
    return {n: t for n, t in Base.metadata.tables.items() if n not in IGNORED_TABLES}


def _db_tables(insp) -> set[str]:
    return set(insp.get_table_names()) - set(IGNORED_TABLES)


def _fk_key(cols, ref_table, ref_cols, ondelete):
    """FKs are compared by shape, not by name (see the naming note above).
    Postgres reports no ondelete for its default, so both sides normalise to
    NO ACTION — otherwise every plain FK would report a phantom difference."""
    return (
        tuple(cols),
        ref_table,
        tuple(ref_cols),
        (ondelete or "NO ACTION").upper(),
    )


def _db_indexes(insp, table: str) -> dict:
    """Reflected indexes MINUS those that merely back a unique constraint.

    `duplicates_constraint` is set by SQLAlchemy only when the index is the one
    Postgres built for a real constraint. A bare unique index that happens to
    share a constraint's name — the 005 bug — has no such tag and survives this
    filter, which is what lets the comparison see it.
    """
    return {
        ix["name"]: (tuple(sorted(ix["column_names"])), bool(ix["unique"]))
        for ix in insp.get_indexes(table)
        if not ix.get("duplicates_constraint")
    }


def _orm_indexes(table: sa.Table) -> dict:
    return {
        ix.name: (tuple(sorted(c.name for c in ix.columns)), bool(ix.unique))
        for ix in table.indexes
    }


# ── The comparison ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def divergences(migrated_database) -> dict[str, str]:
    """{stable divergence key → human-readable description}.

    One dict for the whole schema; each test below asserts on its own slice so
    a failure names one category, not a wall of text.
    """
    insp = migrated_database.inspector
    orm = _orm_tables()
    found: dict[str, str] = {}

    db_names = _db_tables(insp)
    for name in sorted(set(orm) - db_names):
        found[f"table.missing_in_db:{name}"] = (
            f"table {name!r} is declared in the ORM but no migration creates it"
        )
    for name in sorted(db_names - set(orm)):
        found[f"table.extra_in_db:{name}"] = (
            f"table {name!r} is created by a migration but absent from the ORM"
        )

    for name in sorted(set(orm) & db_names):
        table = orm[name]
        _diff_columns(found, insp, name, table)
        _diff_primary_key(found, insp, name, table)
        _diff_foreign_keys(found, insp, name, table)
        _diff_unique_constraints(found, insp, name, table)
        _diff_indexes(found, insp, name, table)

    return found


def _diff_columns(found, insp, name, table):
    db_cols = {c["name"]: c for c in insp.get_columns(name)}
    orm_cols = {c.name: c for c in table.columns}

    for col in sorted(set(orm_cols) - set(db_cols)):
        found[f"column.missing_in_db:{name}:{col}"] = (
            f"{name}.{col} is declared in the ORM but no migration creates it"
        )
    for col in sorted(set(db_cols) - set(orm_cols)):
        found[f"column.extra_in_db:{name}:{col}"] = (
            f"{name}.{col} is created by a migration but absent from the ORM"
        )

    for col in sorted(set(orm_cols) & set(db_cols)):
        orm_type = _type_str(orm_cols[col].type)
        db_type = _type_str(db_cols[col]["type"])
        if orm_type != db_type:
            found[f"column.type_mismatch:{name}:{col}"] = (
                f"{name}.{col} type differs — ORM says {orm_type}, "
                f"the migrated database has {db_type}"
            )
        if bool(orm_cols[col].nullable) != bool(db_cols[col]["nullable"]):
            found[f"column.nullable_mismatch:{name}:{col}"] = (
                f"{name}.{col} nullability differs — ORM says "
                f"nullable={orm_cols[col].nullable}, the migrated database has "
                f"nullable={db_cols[col]['nullable']}"
            )


def _diff_primary_key(found, insp, name, table):
    orm_pk = tuple(c.name for c in table.primary_key.columns)
    db_pk = tuple(insp.get_pk_constraint(name).get("constrained_columns") or ())
    if orm_pk != db_pk:
        found[f"primary_key.mismatch:{name}"] = (
            f"{name} primary key differs — ORM says {orm_pk}, "
            f"the migrated database has {db_pk}"
        )


def _diff_foreign_keys(found, insp, name, table):
    orm_fks = {
        _fk_key(
            [e.parent.name for e in fk.elements],
            fk.referred_table.name,
            [e.column.name for e in fk.elements],
            fk.ondelete,
        )
        for fk in table.foreign_key_constraints
    }
    db_fks = {
        _fk_key(
            fk["constrained_columns"],
            fk["referred_table"],
            fk["referred_columns"],
            (fk.get("options") or {}).get("ondelete"),
        )
        for fk in insp.get_foreign_keys(name)
    }
    for key in sorted(orm_fks - db_fks, key=str):
        found[f"foreign_key.missing_in_db:{name}:{key[0]}->{key[1]}"] = (
            f"{name}{list(key[0])} → {key[1]}{list(key[2])} ON DELETE {key[3]} "
            f"is declared in the ORM but the migrated database has no matching "
            f"foreign key (ondelete differences count: they change what a "
            f"parent DELETE does to child rows)"
        )
    for key in sorted(db_fks - orm_fks, key=str):
        found[f"foreign_key.extra_in_db:{name}:{key[0]}->{key[1]}"] = (
            f"{name}{list(key[0])} → {key[1]}{list(key[2])} ON DELETE {key[3]} "
            f"exists in the migrated database but not in the ORM"
        )


def _diff_unique_constraints(found, insp, name, table):
    """Unique CONSTRAINTS only — a unique index is not one of these.

    This is the check that would have caught the 005 bug: with
    `op.create_index(..., unique=True)` the migrated database has no unique
    constraint at all, so the ORM's `uq_plan_channels_plan_channel` shows up as
    missing here while the stray index shows up in `_diff_indexes`.
    """
    orm_uqs = {
        tuple(sorted(c.name for c in con.columns)): con.name
        for con in table.constraints
        if isinstance(con, sa.UniqueConstraint)
    }
    db_uqs = {
        tuple(sorted(u["column_names"])): u["name"]
        for u in insp.get_unique_constraints(name)
    }

    for cols in sorted(set(orm_uqs) - set(db_uqs)):
        found[f"unique_constraint.missing_in_db:{name}:({','.join(cols)})"] = (
            f"{name}{list(cols)} is a UNIQUE CONSTRAINT in the ORM "
            f"(name={orm_uqs[cols]!r}) but the migrated database has no unique "
            f"constraint on those columns. A unique INDEX is NOT a substitute: "
            f"`INSERT ... ON CONFLICT ON CONSTRAINT` fails with UndefinedObject "
            f"against an index (this is the 005/plan_channels bug)"
        )
    for cols in sorted(set(db_uqs) - set(orm_uqs)):
        found[f"unique_constraint.extra_in_db:{name}:({','.join(cols)})"] = (
            f"{name}{list(cols)} has a UNIQUE CONSTRAINT in the migrated "
            f"database (name={db_uqs[cols]!r}) that the ORM does not declare"
        )

    # Names matter only where the ORM commits to one — SQL can reference it.
    for cols in sorted(set(orm_uqs) & set(db_uqs)):
        orm_name, db_name = orm_uqs[cols], db_uqs[cols]
        if orm_name is not None and orm_name != db_name:
            found[f"unique_constraint.name_mismatch:{name}:({','.join(cols)})"] = (
                f"{name}{list(cols)} unique constraint is named {orm_name!r} in "
                f"the ORM but {db_name!r} in the migrated database; SQL that "
                f"names the constraint would break"
            )


def _diff_indexes(found, insp, name, table):
    orm_ix = _orm_indexes(table)
    db_ix = _db_indexes(insp, name)

    for ix in sorted(set(orm_ix) - set(db_ix)):
        found[f"index.missing_in_db:{name}:{ix}"] = (
            f"index {ix!r} on {name}{list(orm_ix[ix][0])} is declared in the ORM "
            f"but no migration creates it"
        )
    for ix in sorted(set(db_ix) - set(orm_ix)):
        cols, unique = db_ix[ix]
        found[f"index.extra_in_db:{name}:{ix}"] = (
            f"index {ix!r} on {name}{list(cols)} (unique={unique}) exists in the "
            f"migrated database but not in the ORM"
            + (
                " — a UNIQUE INDEX with no matching ORM index is the signature "
                "of a unique constraint that a migration created with "
                "create_index(unique=True) instead of create_unique_constraint"
                if unique else ""
            )
        )
    for ix in sorted(set(orm_ix) & set(db_ix)):
        if orm_ix[ix] != db_ix[ix]:
            found[f"index.definition_mismatch:{name}:{ix}"] = (
                f"index {ix!r} on {name} differs — ORM says "
                f"columns={list(orm_ix[ix][0])} unique={orm_ix[ix][1]}, the "
                f"migrated database has columns={list(db_ix[ix][0])} "
                f"unique={db_ix[ix][1]}"
            )


# ── Assertions ───────────────────────────────────────────────────────────────

def _assert_clean(divergences: dict[str, str], prefix: str, category: str):
    new = {
        k: v for k, v in divergences.items()
        if k.startswith(prefix) and k not in KNOWN_DIVERGENCES
    }
    assert not new, (
        f"\n\n{category} drift between `alembic upgrade head` and Base.metadata "
        f"({len(new)} new):\n\n"
        + "\n".join(f"  • [{k}]\n      {v}" for k, v in sorted(new.items()))
        + "\n\nThe migrated schema is what production runs; Base.metadata is what "
          "every other test runs against. They must agree.\n"
          "Fix the migration (or the model). Only add a key to "
          "KNOWN_DIVERGENCES if the bug is real, owned elsewhere, and recorded.\n"
    )


def test_migrated_schema_has_the_same_tables_as_the_orm(divergences):
    _assert_clean(divergences, "table.", "Table")


def test_migrated_columns_match_orm_types_and_nullability(divergences):
    _assert_clean(divergences, "column.", "Column")


def test_migrated_primary_keys_match_the_orm(divergences):
    _assert_clean(divergences, "primary_key.", "Primary key")


def test_migrated_foreign_keys_match_the_orm_including_ondelete(divergences):
    _assert_clean(divergences, "foreign_key.", "Foreign key")


def test_migrated_unique_constraints_match_the_orm(divergences):
    """The check that would have caught the plan_channels blocker."""
    _assert_clean(divergences, "unique_constraint.", "Unique constraint")


def test_migrated_indexes_match_the_orm(divergences):
    _assert_clean(divergences, "index.", "Index")


def test_plan_channels_unique_is_a_constraint_not_a_bare_index(migrated_database):
    """Regression pin for the exact 005 bug, asserted against Postgres catalogs.

    The category tests above would already fail if this regressed, but they
    speak in terms of "some unique constraint is missing". This one names the
    object the production code depends on: plan_channel_service.py issues
    `INSERT ... ON CONFLICT ON CONSTRAINT uq_plan_channels_plan_channel`, and
    `pg_constraint` is precisely what Postgres consults to resolve that clause.
    A unique index of the same name does not appear there.
    """
    with migrated_database.engine.connect() as conn:
        contype = conn.execute(
            text(
                "SELECT contype FROM pg_constraint "
                "WHERE conname = 'uq_plan_channels_plan_channel' "
                "AND conrelid = 'plan_channels'::regclass"
            )
        ).scalar()

    assert contype == "u", (
        "uq_plan_channels_plan_channel is not a UNIQUE CONSTRAINT in a database "
        f"built by `alembic upgrade head` (pg_constraint.contype={contype!r}; "
        "expected 'u'). app/services/plan_channel_service.py upserts with "
        "`INSERT ... ON CONFLICT ON CONSTRAINT uq_plan_channels_plan_channel`, "
        "which Postgres resolves through pg_constraint — a unique INDEX of the "
        "same name is invisible there and every write to the plan→channel "
        "whitelist 500s with UndefinedObject. See migration 008."
    )


def test_no_stale_known_divergences(divergences):
    """KNOWN_DIVERGENCES must not outlive the bugs it excuses.

    Without this, a fixed bug leaves behind a silent exemption that would mask
    the same bug when it comes back.
    """
    stale = sorted(set(KNOWN_DIVERGENCES) - set(divergences))
    assert not stale, (
        "These KNOWN_DIVERGENCES entries no longer correspond to a real "
        "divergence — the underlying bug appears to be fixed. Delete them, or "
        "this test stops guarding those objects:\n"
        + "\n".join(f"  • {k}" for k in stale)
    )


def test_alembic_check_reports_no_new_drift(migrated_database):
    """`alembic check` as a second net, over the migrated database.

    Autogenerate compares things this file deliberately skips (server defaults,
    comments) for free, so it is used rather than reimplemented. It is kept as a
    supplement, not the primary check, because its output is an internal
    operation list — good for "something changed", poor at saying what broke.

    It exits non-zero today because of the four divergences in
    KNOWN_DIVERGENCES, so the assertion is on the *kinds* of operation
    reported: a new kind means new drift. Weaker than the comparisons above by
    design — those are the ones with teeth.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=REPO_ROOT, env=migrated_database.env, capture_output=True, text=True,
    )
    if proc.returncode == 0:
        # Every known divergence got fixed; test_no_stale_known_divergences
        # will be the one demanding the list be cleaned up.
        return

    report = proc.stdout + proc.stderr
    ops = set(re.findall(r"'(\w+)'\s*,", report)) | set(
        re.findall(r"\(\s*'(\w+)'", report)
    )
    unexpected = {
        op for op in ops
        if op.endswith(("_index", "_constraint", "_table", "_column", "_type"))
        or op in ("modify_type", "modify_nullable", "modify_default")
    } - set(KNOWN_ALEMBIC_CHECK_OPS)

    assert not unexpected, (
        "`alembic check` reports operation kinds beyond the known divergences: "
        f"{sorted(unexpected)}\n\nFull report:\n{report}"
    )
