"""audit_logs: monthly RANGE partitioning + a retention policy that is OFF by default.

Migration 007 made the admin audit trail append-only with a BEFORE UPDATE OR DELETE
row trigger. That trigger is the constraint this migration is written around: the
table it protects can only ever grow, so the only way to bound it is to drop whole
partitions (DDL, which does not fire row triggers) — never to edit rows.

────────────────────────────────────────────────────────────────────────────────
1. WHY ATTACH-IN-PLACE AND NOT COPY-AND-SWAP
────────────────────────────────────────────────────────────────────────────────
Postgres cannot turn an existing table into a partitioned one with an ALTER.
There are two ways round it:

  (a) COPY-AND-SWAP — create a new partitioned table, INSERT ... SELECT every row
      across, drop the original, rename. Every audit row is physically rewritten.
      It needs double the storage, it takes as long as the table is big, and the
      window between "copied" and "swapped" is one where rows can be lost or
      double-counted if anything writes concurrently.

  (b) ATTACH-IN-PLACE — rename the existing table aside and ATTACH it, unchanged,
      as the first partition of a new parent. Not one row is read or rewritten;
      the heap file that holds the real audit trail is the same file afterwards.

This migration uses (b). The deciding argument is the one that governs this table:
in production `audit_logs` holds the real audit trail, and losing it would be
worse than not partitioning at all. (b) never copies the data, so there is no
copy to get wrong — and because Postgres DDL is transactional, a failure at any
point rolls the whole thing back to exactly the table we started with.

  WRITE-BLOCKING WINDOW — STATE THIS IN THE CHANGE REQUEST.
  The conversion holds an ACCESS EXCLUSIVE lock on `audit_logs` for the duration
  of the migration, so *writes to audit_logs block* while it runs. Audit rows are
  written on the authentication path, so for that window logins block too rather
  than fail. The cost is dominated by ONE index build: the parent's primary key is
  (id, created_at) — see §3 — and no such index exists on the old table, so
  Postgres builds it over the existing rows while ATTACHing. Everything else
  (rename, schema move, catalog surgery) is O(1). The five `ix_audit_logs_*`
  indexes are NOT rebuilt: they are created on the parent first, so ATTACH matches
  the identical ones already on the old table and simply adopts them.
  Budget the window as "one btree build over audit_logs" and run it off-peak.

────────────────────────────────────────────────────────────────────────────────
2. WHY THE PARTITIONS LIVE IN THEIR OWN SCHEMA (audit_part)
────────────────────────────────────────────────────────────────────────────────
Partitions are tables. Left in `public` they would be returned by
`Inspector.get_table_names()` — SQLAlchemy reflects relkind 'r' and 'p' alike —
and `tests/test_migration_schema_parity.py` compares exactly that set against
`Base.metadata`. Every monthly partition would surface as a phantom
`table.extra_in_db:audit_logs_2026_09` divergence, and the list would grow by one
every month: a test that fails a bit more each month is a test that gets muted.

Putting them in `audit_part` is not a trick to dodge that test. Partitions, the
retention policy and the maintenance functions are *physical storage management*.
They are deliberately not ORM-mapped, because the application must keep addressing
`public.audit_logs` and nothing else. The schema boundary states that: `public` is
the contract the ORM owns, `audit_part` is machinery the DBA owns. `psql \\dt public.*`
stays readable as a side benefit.

────────────────────────────────────────────────────────────────────────────────
3. THE PRIMARY KEY HAS TO CHANGE, AND WHAT THAT COSTS
────────────────────────────────────────────────────────────────────────────────
Postgres requires every UNIQUE/PRIMARY KEY on a partitioned table to contain all
partition-key columns. Partitioning by `created_at` therefore forces
PRIMARY KEY (id) → PRIMARY KEY (id, created_at). `app/models/audit.py` is updated
to match, so the ORM and the migrated schema still agree.

What is lost: `id` alone is no longer *enforced* unique across the whole table —
Postgres cannot enforce a global unique index that omits the partition key. In
practice this is close to free: `id` is an application-generated UUIDv4, and the
pair (id, created_at) is still unique. It is recorded here rather than glossed
over, because "the primary key changed" is exactly the sort of thing that should
never be discovered by accident.

────────────────────────────────────────────────────────────────────────────────
4. IMMUTABILITY MUST SURVIVE — THIS IS THE ACCEPTANCE CRITERION
────────────────────────────────────────────────────────────────────────────────
This is the point where the work could silently destroy the guarantee 007 built.
A trigger on a parent table is NOT inherited the way one might assume, and the
ordering below is load-bearing:

  * Postgres 13+ clones a FOR EACH ROW trigger defined on a partitioned table onto
    every partition — those attached later and those created later included. So
    the trigger is created ON THE PARENT, *before* any partition exists. Every
    partition this migration makes, and every partition the maintenance functions
    will ever make, inherits it automatically. There is no list to keep updated.

  * The old table already carries 007's trigger, and ATTACH refuses to clone a
    trigger onto a child that already has one of that name. It is therefore
    dropped from the old table immediately before the ATTACH, which puts it
    straight back as a clone. The gap is inside this migration's transaction,
    under ACCESS EXCLUSIVE, so nothing can write during it.

  * Cloned triggers are not detachable piecemeal: `DROP TRIGGER
    audit_logs_no_update_delete ON audit_part.audit_logs_2026_09` is refused by
    Postgres ("... because trigger ... on table audit_logs requires it"). Removing
    the protection from one partition is impossible; it is all or nothing, at the
    parent. That is strictly stronger than the pre-partitioning arrangement.

  * Unchanged from before: a table owner can still `ALTER TABLE ... DISABLE
    TRIGGER`. That was equally true of the single table 007 protected, so
    partitioning takes nothing away — it remains a deliberate, privileged act.

`tests/test_audit_partitioning.py` proves the result against a migrated database:
UPDATE and DELETE are rejected through the parent AND straight at each individual
partition, on rows that actually exist.

────────────────────────────────────────────────────────────────────────────────
5. RETENTION VS IMMUTABILITY — AND WHY IT SHIPS DISABLED
────────────────────────────────────────────────────────────────────────────────
Dropping a whole partition is DDL: it fires no row trigger, so it does not fight
007. That is legitimate and it is categorically different from editing history —
"we do not keep records older than N" is a policy that can be stated, published
and audited; "this row was changed" is tampering. The distinction only holds while
the policy is *explicit*, because an audit trail that silently forgets is an audit
trail nobody can rely on.

So the defaults are deliberately inert:

    enabled        = false   -- NOTHING is ever dropped until someone turns it on
    retain_months  = 84      -- 7 years, conservative; long enough for the usual
                                commercial/fiscal record-keeping horizons
    premake_months = 3

`audit_part.apply_retention()` also defaults to `p_dry_run => true`, so the
careless call reports what it *would* drop and removes nothing. Two independent
gates — a stored `enabled` flag and an explicit argument — have to be opened
before a single partition disappears.

One further guard: the partition holding everything from before this migration is
unbounded below (FROM MINVALUE), so dropping it would delete an unknown span of
history in one go rather than one month. `apply_retention` skips any
unbounded-below partition unless called with `p_include_unbounded => true`.

WHAT IS KEPT, AND FOR HOW LONG — the operator-facing summary:
  * Nothing is deleted at all unless `audit_part.retention_policy.enabled` is set
    to true by a human.
  * Once enabled, a monthly partition is dropped only when its whole range is
    older than `retain_months` (default 84 = 7 years). Retention is therefore
    whole months, and the effective guarantee is "at least retain_months".
  * The pre-migration history partition is never dropped by a routine run.
  * Read `SELECT * FROM audit_part.apply_retention()` (dry run) to see the exact
    list before enabling anything.

────────────────────────────────────────────────────────────────────────────────
6. THE DAY NOBODY CREATED NEXT MONTH'S PARTITION
────────────────────────────────────────────────────────────────────────────────
Audit rows are INSERTed on the authentication path. On a partitioned table an
INSERT whose key matches no partition fails with "no partition of relation
... found for row" — which would turn a forgotten maintenance job into a total
login outage. That is not an acceptable failure mode for a logging concern.

Two independent defences:

  (a) A DEFAULT partition, `audit_part.audit_logs_default`. Any row that matches
      no monthly partition lands there instead of raising. Logins keep working,
      the row is still written, still queryable through `audit_logs`, and still
      immutable — the DEFAULT partition inherits the trigger like any other.
  (b) `audit_part.ensure_partitions()` pre-creates the current month plus
      `premake_months` ahead (default 3), and this migration calls it once. So
      the machinery has to be neglected for four months before (a) is even
      reached.

`AuditService.ensure_audit_partitions()` exposes (b) so a scheduler can call it;
see `docs/AUDIT_PARTITIONING.md` for wiring it up.

The one consequence worth knowing: once rows for month M have landed in the
DEFAULT partition, creating M's own partition fails (SQLSTATE 23514 — Postgres
will not let a new partition steal rows out of the default). `ensure_partition`
catches that, emits a WARNING and carries on instead of aborting the whole
maintenance run. The rows are safe and readable where they are; consolidating them
is a manual, deliberate operation, precisely because moving rows out of the
default partition means deleting them from it, which 007 forbids by design.
The runbook is in `docs/AUDIT_PARTITIONING.md`.

Revision ID: 011
Revises: 009
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "audit_part"
_TABLE = "audit_logs"
_LEGACY = "audit_logs_legacy"
_DEFAULT_PART = "audit_logs_default"
_TRIGGER = "audit_logs_no_update_delete"
_FUNCTION = "nexora_audit_logs_immutable"

#: Conservative on purpose — see §5. Long enough for the usual commercial and
#: fiscal record-keeping horizons, and inert until `enabled` is flipped.
_RETAIN_MONTHS = 84
_PREMAKE_MONTHS = 3

_INDEXES = (
    ("ix_audit_logs_actor_id", "actor_id"),
    ("ix_audit_logs_action", "action"),
    ("ix_audit_logs_target_type", "target_type"),
    ("ix_audit_logs_target_id", "target_id"),
    ("ix_audit_logs_created_at", "created_at"),
)


def _relkind(conn, schema: str, name: str) -> str | None:
    """'r' ordinary table, 'p' partitioned table, None if absent."""
    return conn.exec_driver_sql(
        "SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %(s)s AND c.relname = %(t)s",
        {"s": schema, "t": name},
    ).scalar()


def _sql(conn, statement: str) -> None:
    """Run DDL through `op.execute()` — NOT `exec_driver_sql()`.

    The plpgsql below is full of `format()` specifiers (`%I`, `%L`) and LIKE
    patterns (`'%FROM (MINVALUE)%'`). `exec_driver_sql` hands psycopg an empty
    parameter tuple, which switches on client-side interpolation, and psycopg then
    rejects the lot with "only '%s', '%b', '%t' are allowed as placeholders, got
    '%I'". `op.execute()` goes through `sqlalchemy.text()` with no bind parameters,
    so psycopg is called with no params at all and never scans for placeholders —
    which is why the `%` in migration 007's RAISE has always worked.

    The other half of that trade: `text()` DOES treat `:word` as a bind parameter.
    Hence `audit_part.bound_literal()` formats dates as 'YYYY-MM-DD' and appends a
    literal ' 00:00:00+00' — a `to_char(..., 'HH24:MI:SS')` here would have its
    `:MI` and `:SS` silently eaten as bind parameters. `::type` casts and plpgsql's
    `:=` are both safe (text() requires a word character after the colon).

    `conn` stays in the signature so the catalog probes and the DDL read as one
    sequence against one connection.
    """
    op.execute(statement)


# ── The maintenance machinery (§5, §6) ──────────────────────────────────────
#
# Kept as SQL functions rather than Python so that a DBA, a cron job, pg_cron or
# the application can all drive it through one implementation. Deliberately not
# ORM-mapped: this is storage management, not domain data.

_FN_MONTH_START = """
CREATE OR REPLACE FUNCTION audit_part.month_start(p_ts timestamptz)
RETURNS timestamptz
LANGUAGE sql IMMUTABLE AS $fn$
    -- Always UTC. Month boundaries computed in the session's TimeZone would put
    -- the partition edges in a different place depending on who ran the job.
    SELECT date_trunc('month', p_ts AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
$fn$;
"""

_FN_BOUND_LITERAL = """
CREATE OR REPLACE FUNCTION audit_part.bound_literal(p_ts timestamptz)
RETURNS text
LANGUAGE sql IMMUTABLE AS $fn$
    -- An explicit +00 offset, so the stored bound means the same instant no
    -- matter what TimeZone the session that runs the DDL happens to have.
    SELECT to_char(p_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD') || ' 00:00:00+00';
$fn$;
"""

_FN_ENSURE_PARTITION = """
CREATE OR REPLACE FUNCTION audit_part.ensure_partition(p_month timestamptz)
RETURNS text
LANGUAGE plpgsql AS $fn$
DECLARE
    v_from timestamptz := audit_part.month_start(p_month);
    v_to   timestamptz := audit_part.month_start(p_month) + interval '1 month';
    v_name text := 'audit_logs_' || to_char(v_from AT TIME ZONE 'UTC', 'YYYY_MM');
BEGIN
    IF to_regclass('audit_part.' || quote_ident(v_name)) IS NOT NULL THEN
        RETURN v_name;
    END IF;

    EXECUTE format(
        'CREATE TABLE audit_part.%I PARTITION OF public.audit_logs '
        'FOR VALUES FROM (%L) TO (%L)',
        v_name, audit_part.bound_literal(v_from), audit_part.bound_literal(v_to)
    );
    RETURN v_name;
EXCEPTION
    WHEN duplicate_table THEN
        -- Another maintenance run got there first. Not an error.
        RETURN v_name;
    WHEN invalid_object_definition THEN
        -- SQLSTATE 42P17: "would overlap partition ...". The month is already
        -- covered by some other partition, so there is nothing to create and
        -- nothing is wrong. This is the NORMAL case for the first run after the
        -- migration: the pre-011 history partition runs to the start of NEXT
        -- month, so the CURRENT month already lives there. Postgres itself is the
        -- authority on coverage, so the overlap is detected by attempting the
        -- CREATE rather than by reasoning about bounds beforehand -- which also
        -- makes it race-free and correct for hand-made partitions.
        RAISE NOTICE 'audit_part: % already covered by an existing partition', v_name;
        RETURN NULL;
    WHEN check_violation THEN
        -- SQLSTATE 23514: rows for this month are already sitting in the DEFAULT
        -- partition, and Postgres will not let a new partition take them out of
        -- it. WARN and carry on -- the rows are safe and readable where they are,
        -- and failing here would abort an entire maintenance run (and, if this is
        -- ever called inline, the request that triggered it) over a condition
        -- that costs nothing to leave alone. See docs/AUDIT_PARTITIONING.md.
        RAISE WARNING
            'audit_part: cannot create % — rows for that month are already in %.%; '
            'left in place, see docs/AUDIT_PARTITIONING.md',
            v_name, 'audit_part', 'audit_logs_default';
        RETURN NULL;
END;
$fn$;
"""

_FN_ENSURE_PARTITIONS = """
CREATE OR REPLACE FUNCTION audit_part.ensure_partitions(p_premake int DEFAULT NULL)
RETURNS text[]
LANGUAGE plpgsql AS $fn$
DECLARE
    v_premake int := COALESCE(
        p_premake, (SELECT premake_months FROM audit_part.retention_policy), 3
    );
    v_out  text[] := '{}';
    v_name text;
    i      int;
BEGIN
    -- Current month through current+premake, so the schedule can be missed
    -- several times over before the DEFAULT partition is needed at all.
    FOR i IN 0 .. v_premake LOOP
        v_name := audit_part.ensure_partition(now() + (i || ' months')::interval);
        IF v_name IS NOT NULL THEN
            v_out := v_out || v_name;
        END IF;
    END LOOP;
    RETURN v_out;
END;
$fn$;
"""

_FN_APPLY_RETENTION = """
CREATE OR REPLACE FUNCTION audit_part.apply_retention(
    p_dry_run           boolean DEFAULT true,
    p_include_unbounded boolean DEFAULT false
)
RETURNS TABLE (partition_name text, upper_bound timestamptz, dropped boolean)
LANGUAGE plpgsql AS $fn$
DECLARE
    v_enabled boolean;
    v_months  int;
    v_cutoff  timestamptz;
    r         record;
BEGIN
    SELECT enabled, retain_months INTO v_enabled, v_months
    FROM audit_part.retention_policy;

    IF NOT FOUND THEN
        RAISE WARNING 'audit_part: no retention policy row; nothing done';
        RETURN;
    END IF;

    -- Gate 1: the stored flag. A caller that passes p_dry_run => false while
    -- retention is disabled gets a report, not a deletion.
    IF NOT v_enabled AND NOT p_dry_run THEN
        RAISE NOTICE
            'audit_part: retention is DISABLED '
            '(audit_part.retention_policy.enabled = false); reporting only';
        p_dry_run := true;
    END IF;

    v_cutoff := audit_part.month_start(now()) - (v_months || ' months')::interval;

    FOR r IN
        SELECT
            c.relname::text AS name,
            -- The upper bound straight from the catalog rather than parsed out of
            -- the partition NAME: the catalog cannot drift from what the partition
            -- actually holds. DEFAULT partitions and anything this pattern does not
            -- understand yield NULL, and NULL is skipped -- so an unrecognised
            -- partition is never dropped. The safe direction to fail.
            (regexp_match(
                pg_get_expr(c.relpartbound, c.oid), 'TO \\(''([^'']+)''\\)'
            ))[1]::timestamptz AS upper_b,
            pg_get_expr(c.relpartbound, c.oid) LIKE '%FROM (MINVALUE)%' AS unbounded
        FROM pg_class c
        JOIN pg_inherits i ON i.inhrelid = c.oid
        WHERE i.inhparent = 'public.audit_logs'::regclass
        ORDER BY 1
    LOOP
        CONTINUE WHEN r.upper_b IS NULL;          -- DEFAULT / unparseable
        CONTINUE WHEN r.upper_b > v_cutoff;       -- still inside the window
        CONTINUE WHEN r.unbounded AND NOT p_include_unbounded;

        partition_name := r.name;
        upper_bound    := r.upper_b;
        dropped        := NOT p_dry_run;

        IF NOT p_dry_run THEN
            -- DDL. Fires no row trigger, so this does not fight migration 007:
            -- the whole record is discarded under a stated policy, no row is
            -- ever rewritten.
            EXECUTE format('DROP TABLE audit_part.%I', r.name);
            RAISE NOTICE 'audit_part: dropped partition % (upto %)', r.name, r.upper_b;
        END IF;

        RETURN NEXT;
    END LOOP;
END;
$fn$;
"""

_FN_MAINTAIN = """
CREATE OR REPLACE FUNCTION audit_part.maintain()
RETURNS void
LANGUAGE plpgsql AS $fn$
BEGIN
    -- One call for a scheduler to make. Creating partitions first means a run
    -- that trips over retention has still kept the write path healthy.
    PERFORM audit_part.ensure_partitions();
    PERFORM audit_part.apply_retention(p_dry_run => false);
END;
$fn$;
"""

_FUNCTIONS = (
    _FN_MONTH_START,
    _FN_BOUND_LITERAL,
    _FN_ENSURE_PARTITION,
    _FN_ENSURE_PARTITIONS,
    _FN_APPLY_RETENTION,
    _FN_MAINTAIN,
)


def upgrade() -> None:
    conn = op.get_bind()

    if _relkind(conn, "public", _TABLE) == "p":
        # Already partitioned. Idempotent in the same spirit as 008/009/010:
        # never assume history, ask the catalog.
        return

    _sql(conn, f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    # Where history stops and the monthly partitions start: the first day of the
    # month after BOTH `now()` and the newest row already in the table.
    #
    #   * The database clock, never Python's — these bounds and the `now()` the
    #     maintenance functions use must come from one clock, or a row can fall in
    #     the gap between them. (The two really do differ here: the container runs
    #     UTC while the host is an hour behind, which is exactly how a
    #     Python-computed bound would land in the wrong month.)
    #
    #   * GREATEST(..., max(created_at)) because ATTACH refuses a partition whose
    #     bound does not cover every row present, and audit_logs is not guaranteed
    #     free of future-dated rows — one clock-skewed writer is enough. Without
    #     this the migration aborts on a Postgres range error at deploy time.
    #     GREATEST ignores NULL, so an empty table falls back to `now()`.
    #
    # Read before the table is renamed, while it is still public.audit_logs.
    cutover = conn.exec_driver_sql(
        "SELECT to_char("
        "  GREATEST("
        "    date_trunc('month', now() AT TIME ZONE 'UTC'),"
        "    (SELECT date_trunc('month', max(created_at) AT TIME ZONE 'UTC')"
        "       FROM public.audit_logs)"
        "  ) + interval '1 month',"
        "  'YYYY-MM-DD') || ' 00:00:00+00'"
    ).scalar()

    # ── Move the real table aside, untouched ─────────────────────────────────
    # ATTACH will not clone the parent trigger onto a child that already has one
    # of that name (§4). Dropped here, restored as a clone by the ATTACH below;
    # the gap is inside this transaction, under ACCESS EXCLUSIVE.
    _sql(conn, f"DROP TRIGGER {_TRIGGER} ON public.{_TABLE}")
    _sql(conn, f"ALTER TABLE public.{_TABLE} RENAME TO {_LEGACY}")
    _sql(conn, f"ALTER TABLE public.{_LEGACY} SET SCHEMA {_SCHEMA}")

    # Postgres rejects "multiple primary keys" when attaching a child that has
    # its own PK, and the parent's must be (id, created_at) — see §3.
    _sql(conn, f"ALTER TABLE {_SCHEMA}.{_LEGACY} DROP CONSTRAINT audit_logs_pkey")
    _sql(conn, f"ALTER TABLE {_SCHEMA}.{_LEGACY} ALTER COLUMN id SET NOT NULL")

    # ── The new parent ───────────────────────────────────────────────────────
    # LIKE, not a hand-written column list: the parent then cannot drift from
    # whatever the real table actually is. Transcribing ten columns by hand is
    # how audit_logs.user_agent ended up varchar(255) in 001 while the ORM said
    # Text — the divergence migration 009 had to repair.
    _sql(
        conn,
        f"CREATE TABLE public.{_TABLE} "
        f"(LIKE {_SCHEMA}.{_LEGACY} INCLUDING DEFAULTS INCLUDING CONSTRAINTS) "
        f"PARTITION BY RANGE (created_at)",
    )
    _sql(
        conn,
        f"ALTER TABLE public.{_TABLE} "
        f"ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id, created_at)",
    )
    _sql(
        conn,
        f"ALTER TABLE public.{_TABLE} "
        f"ADD CONSTRAINT audit_logs_actor_id_fkey FOREIGN KEY (actor_id) "
        f"REFERENCES public.users(id) ON DELETE SET NULL",
    )

    # Immutability goes on the parent BEFORE any partition exists, so every
    # partition — now and forever — inherits it (§4).
    _sql(
        conn,
        f"CREATE TRIGGER {_TRIGGER} "
        f"BEFORE UPDATE OR DELETE ON public.{_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()",
    )

    # Before the ATTACH, so Postgres matches these against the identical indexes
    # already on the old table and adopts them instead of rebuilding.
    for index_name, column in _INDEXES:
        _sql(conn, f"CREATE INDEX {index_name} ON public.{_TABLE} ({column})")

    # ── Adopt the existing trail as the first partition. No rows are copied. ──
    _sql(
        conn,
        f"ALTER TABLE public.{_TABLE} ATTACH PARTITION {_SCHEMA}.{_LEGACY} "
        f"FOR VALUES FROM (MINVALUE) TO ('{cutover}')",
    )

    # The safety net (§6a): an INSERT matching no monthly partition lands here
    # rather than raising and taking the login path down with it.
    _sql(
        conn,
        f"CREATE TABLE {_SCHEMA}.{_DEFAULT_PART} "
        f"PARTITION OF public.{_TABLE} DEFAULT",
    )

    # ── Policy + machinery ───────────────────────────────────────────────────
    _sql(
        conn,
        f"""
        CREATE TABLE {_SCHEMA}.retention_policy (
            id             boolean PRIMARY KEY DEFAULT true CHECK (id),
            enabled        boolean NOT NULL DEFAULT false,
            retain_months  int     NOT NULL DEFAULT {_RETAIN_MONTHS}
                                   CHECK (retain_months >= 1),
            premake_months int     NOT NULL DEFAULT {_PREMAKE_MONTHS}
                                   CHECK (premake_months >= 1),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            updated_by     text
        )
        """,
    )
    # `id boolean PRIMARY KEY DEFAULT true CHECK (id)` permits exactly one row:
    # the policy is a singleton, and making that a constraint rather than a
    # convention means a second, contradictory policy row cannot be inserted.
    _sql(conn, f"INSERT INTO {_SCHEMA}.retention_policy (id) VALUES (true)")

    _sql(
        conn,
        f"COMMENT ON TABLE {_SCHEMA}.retention_policy IS "
        f"'Audit retention. enabled=false ships OFF: nothing is ever dropped "
        f"until a human sets it true. retain_months={_RETAIN_MONTHS} (7 years). "
        f"Preview with: SELECT * FROM {_SCHEMA}.apply_retention();'",
    )

    for fn in _FUNCTIONS:
        _sql(conn, fn)

    # Pre-create the current month and the next few (§6b).
    _sql(conn, f"SELECT {_SCHEMA}.ensure_partitions()")


def downgrade() -> None:
    """Collapse back to one ordinary table — WITHOUT dropping a single audit row.

    The naive downgrade (drop the partitioned table, rename the legacy partition
    back) would silently discard every row written after this migration ran. On a
    table whose entire purpose is to be a complete record, that is not a rollback,
    it is data loss — the same judgement migration 009's downgrade makes when it
    refuses to truncate over-long values.

    So the rows are moved back first. INSERT is permitted by 007's trigger, and
    dropping the emptied partitions afterwards is DDL, so nothing here needs the
    immutability guarantee weakened even for an instant.
    """
    conn = op.get_bind()

    if _relkind(conn, "public", _TABLE) != "p":
        return  # not partitioned; nothing to undo

    # Detach the original table so that dropping the parent cannot take it along.
    _sql(
        conn,
        f"ALTER TABLE public.{_TABLE} DETACH PARTITION {_SCHEMA}.{_LEGACY}",
    )

    # Everything still in the remaining partitions goes back into it. Reading
    # through the parent no longer sees the detached table, so this cannot
    # duplicate the rows it already holds.
    _sql(
        conn,
        f"INSERT INTO {_SCHEMA}.{_LEGACY} SELECT * FROM public.{_TABLE}",
    )

    # Safe now: every row has a home outside this tree.
    _sql(conn, f"DROP TABLE public.{_TABLE}")

    _sql(conn, f"ALTER TABLE {_SCHEMA}.{_LEGACY} SET SCHEMA public")
    _sql(conn, f"ALTER TABLE public.{_LEGACY} RENAME TO {_TABLE}")

    # Restore the pre-011 shape: PRIMARY KEY (id), and 007's trigger.
    #
    # ATTACH built a (id, created_at) unique index on this table to satisfy the
    # parent's primary key, and DETACH leaves it behind as a CONSTRAINT — not a
    # bare index, so `DROP INDEX` is refused ("you can drop constraint ...
    # instead"). It has to go before the old PRIMARY KEY (id) can be added back,
    # or Postgres rejects the second primary key.
    _sql(
        conn,
        f"ALTER TABLE public.{_TABLE} DROP CONSTRAINT IF EXISTS {_LEGACY}_pkey",
    )
    _sql(
        conn,
        f"ALTER TABLE public.{_TABLE} ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id)",
    )
    _sql(conn, f"DROP TRIGGER IF EXISTS {_TRIGGER} ON public.{_TABLE}")
    _sql(
        conn,
        f"CREATE TRIGGER {_TRIGGER} "
        f"BEFORE UPDATE OR DELETE ON public.{_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()",
    )

    _sql(conn, f"DROP SCHEMA {_SCHEMA} CASCADE")
