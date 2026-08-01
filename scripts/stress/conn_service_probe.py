"""
Direct stress of ConnectionService — no PostgreSQL, no HTTP.

WHY THIS EXISTS
───────────────
The end-to-end scripts (01..05*.mjs) drive the real client API. When the API is
down for an unrelated reason (e.g. an ORM↔migration drift 500ing every request
that loads a Subscriber), the *connection-limit mechanism itself* can still be
stressed: it lives entirely in Redis, in `app/services/connection_service.py`
and its `_OPEN_CONNECTION_LUA` script.

This probe exercises that real code — the real service class and the real Lua —
against the real Redis, using SYNTHETIC subscriber UUIDs so it never touches a
live subscriber's slots and never collides with other workers.

Run inside the api container (it has the deps and the app on PYTHONPATH):

    docker exec nexora_api python /app/scripts/stress/conn_service_probe.py

Exit code 0 = all checks passed, 1 = at least one failed.
"""
import asyncio
import os
import sys
import time
import uuid

sys.path.insert(0, "/app")

import redis.asyncio as aioredis  # noqa: E402

from app.services.connection_service import ConnectionService  # noqa: E402
from app.redis_client import key_active_connections  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return passed


def section(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


async def main():
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    svc = ConnectionService(r)
    print(f"redis={REDIS_URL}  heartbeat_ttl={svc.ttl}s")

    # ── A. Zapping: same device, many opens → must stay at 1 slot ───────────
    section("A — Zapping (same device, 20 rapid opens) → slot must not grow")
    sub = str(uuid.uuid4())
    dev = str(uuid.uuid4())
    key = key_active_connections(sub)
    try:
        t0 = time.perf_counter()
        oks = [await svc.open_connection(sub, dev, 3) for _ in range(20)]
        dt = (time.perf_counter() - t0) * 1000
        card = await r.zcard(key)
        check("20 opens of the SAME device all succeed", all(oks), f"{sum(oks)}/20")
        check("ZSET holds exactly 1 slot after 20 zaps", card == 1, f"zcard={card}")
        print(f"  20 opens in {dt:.1f}ms ({dt/20:.2f}ms each)")
    finally:
        await r.delete(key)

    # ── B. Zapping with a NEW device id per channel (worst case) ────────────
    section("B — Zapping where each channel opens with a NEW device id")
    sub = str(uuid.uuid4())
    key = key_active_connections(sub)
    try:
        statuses = []
        for i in range(6):
            ok = await svc.open_connection(sub, str(uuid.uuid4()), 3)
            statuses.append(ok)
        card = await r.zcard(key)
        check(
            "a client that rotates device_id per zap is capped at max_connections",
            statuses[:3] == [True, True, True] and statuses[3:] == [False, False, False],
            f"opens={statuses} zcard={card}",
        )
        print("  NOTE: slots 4-6 are refused (409) even though ONE viewer is watching.")
        print("        Correctness of the cap is fine; this documents the blast radius")
        print("        if any client ever rotates its device_id.")
    finally:
        await r.delete(key)

    # ── C. TRUE concurrency: N simultaneous opens of DISTINCT devices ───────
    section("C — Concurrency: 12 simultaneous opens, max_connections=3")
    for attempt in range(1, 6):
        sub = str(uuid.uuid4())
        key = key_active_connections(sub)
        try:
            devs = [str(uuid.uuid4()) for _ in range(12)]
            res = await asyncio.gather(*(svc.open_connection(sub, d, 3) for d in devs))
            granted = sum(1 for x in res if x)
            card = await r.zcard(key)
            ok = granted == 3 and card == 3
            check(
                f"round {attempt}: exactly 3 of 12 concurrent opens granted",
                ok,
                f"granted={granted} zcard={card}",
            )
        finally:
            await r.delete(key)

    # ── D. Heartbeat vs the limit ──────────────────────────────────────────
    section("D — Does heartbeat (extend_connection) respect max_connections?")
    sub = str(uuid.uuid4())
    key = key_active_connections(sub)
    try:
        d1, d2, d3, d4 = (str(uuid.uuid4()) for _ in range(4))
        for d in (d1, d2, d3):
            await svc.open_connection(sub, d, 3)
        refused = await svc.open_connection(sub, d4, 3)
        check("4th device is refused by open_connection", refused is False, f"opened={refused}")

        # Now the same 4th device just sends a heartbeat instead.
        await svc.extend_connection(sub, d4)
        card = await r.zcard(key)
        count = await svc.count_active(sub)
        bypassed = card > 3
        check(
            "heartbeat does NOT let a refused device take a slot",
            not bypassed,
            f"zcard after heartbeat={card} (limit=3), count_active={count}",
        )
        if bypassed:
            print("  >>> LIMIT BYPASS: extend_connection() does a bare ZADD with no")
            print("  >>> max_connections check, so any device that can reach the")
            print("  >>> heartbeat endpoint inserts itself into the ZSET.")
    finally:
        await r.delete(key)

    # ── D2. The full chain: heartbeat first, then authorize ────────────────
    section("D2 — Chain: does a heartbeat-seeded device then WIN an authorize?")
    sub = str(uuid.uuid4())
    key = key_active_connections(sub)
    try:
        d1, d2, d3, d4 = (str(uuid.uuid4()) for _ in range(4))
        for d in (d1, d2, d3):
            await svc.open_connection(sub, d, 3)

        # Baseline: without the heartbeat trick, d4 is refused.
        check("baseline — 4th device refused", await svc.open_connection(sub, d4, 3) is False)

        # Exploit: heartbeat inserts d4 into the ZSET, and _OPEN_CONNECTION_LUA
        # only evaluates the limit when ZSCORE(member) is nil. d4 is no longer
        # nil, so the limit check is skipped entirely on the next open.
        await svc.extend_connection(sub, d4)
        opened = await svc.open_connection(sub, d4, 3)
        card = await r.zcard(key)
        check(
            "after a heartbeat, the SAME refused device must still be refused",
            opened is False,
            f"open_connection returned {opened}, zcard={card} (limit=3)",
        )
        if opened:
            print("  >>> FULL BYPASS CONFIRMED: heartbeat → authorize now returns a")
            print("  >>> playback token. max_connections is not enforced for any")
            print("  >>> device that beats before it authorizes.")

        # How far does it scale?
        extra = [str(uuid.uuid4()) for _ in range(6)]
        for d in extra:
            await svc.extend_connection(sub, d)
            await svc.open_connection(sub, d, 3)
        final = await r.zcard(key)
        check("ZSET stays within max_connections under repeated abuse", final <= 3,
              f"zcard={final} with max_connections=3")
    finally:
        await r.delete(key)

    # ── E. Expiry frees the slot (heartbeat timeout) ────────────────────────
    section("E — Expired entries free the slot (short TTL simulation)")
    sub = str(uuid.uuid4())
    key = key_active_connections(sub)
    try:
        d1, d2, d3, d4 = (str(uuid.uuid4()) for _ in range(4))
        now = time.time()
        # Seed 3 slots that expire in 2s, mimicking a client that stopped beating.
        for d in (d1, d2, d3):
            await r.zadd(key, {d: now + 2})
        check("seeded 3 slots", await r.zcard(key) == 3)
        refused = await svc.open_connection(sub, d4, 3)
        check("while they are alive, a 4th device is refused", refused is False)
        await asyncio.sleep(2.5)
        raw = await r.zcard(key)  # no cleanup yet — stale entries still physically there
        opened = await svc.open_connection(sub, d4, 3)
        card = await r.zcard(key)
        check("after expiry a new device gets a slot", opened is True, f"opened={opened}")
        check("expired members are purged by the open path", card == 1,
              f"raw_zcard_before_open={raw} zcard_after={card}")
        print("  NOTE: raw ZCARD before the next operation was "
              f"{raw} — expired members linger in Redis until some operation")
        print("        touches the key. Nothing expires them proactively.")
    finally:
        await r.delete(key)

    # ── F. Key TTL hygiene ─────────────────────────────────────────────────
    section("F — ZSET key TTL hygiene")
    sub = str(uuid.uuid4())
    key = key_active_connections(sub)
    try:
        d = str(uuid.uuid4())
        await svc.open_connection(sub, d, 3)
        ttl_open = await r.ttl(key)
        check("open_connection sets a key TTL", ttl_open > 0, f"ttl={ttl_open}s")
        await svc.close_connection(sub, d)
        card = await r.zcard(key)
        ttl_after = await r.ttl(key)
        check("close_connection removes the member", card == 0, f"zcard={card}")
        print(f"  key TTL after closing the last member: {ttl_after}s "
              f"({'key still present but empty' if ttl_after > 0 else 'key gone'})")
    finally:
        await r.delete(key)

    # ── G. Scale: does the cap survive real pressure? ──────────────────────
    section("G — Scale: 50 subscribers x 20 concurrent opens, max_connections=3")
    print(f"  client pool max_connections={r.connection_pool.max_connections} "
          "(app/redis_client.py sets none, so this is redis-py's default)")
    subs = [str(uuid.uuid4()) for _ in range(50)]
    keys = [key_active_connections(s) for s in subs]
    errors = []
    try:
        async def guarded(s):
            try:
                return await svc.open_connection(s, str(uuid.uuid4()), 3)
            except Exception as exc:  # pool exhaustion shows up here
                errors.append(type(exc).__name__)
                return None

        tasks = [guarded(s) for s in subs for _ in range(20)]
        t0 = time.perf_counter()
        res = await asyncio.gather(*tasks)
        dt = time.perf_counter() - t0
        granted = sum(1 for x in res if x)
        cards = [await r.zcard(k) for k in keys]
        over = [c for c in cards if c > 3]
        check("no subscriber exceeded max_connections under load",
              not over, f"offenders={len(over)} cards_max={max(cards)}")
        check("exactly 3 grants per subscriber (150 of 1000)", granted == 150,
              f"granted={granted}/1000")
        check("no connection-pool errors at 1000 in-flight ops", not errors,
              f"{len(errors)} errors: {sorted(set(errors))}")
        print(f"  1000 open_connection calls in {dt*1000:.0f}ms "
              f"({1000/dt:.0f} ops/s)")
    finally:
        for k in keys:
            await r.delete(k)

    # ── H. Where exactly does the pool give out? ───────────────────────────
    section("H — Redis connection-pool ceiling (app uses the library default)")
    sub = str(uuid.uuid4())
    key = key_active_connections(sub)
    try:
        for level in (50, 100, 150, 300):
            errs = []

            async def one():
                try:
                    await svc.open_connection(sub, str(uuid.uuid4()), 10**9)
                except Exception as exc:
                    errs.append(type(exc).__name__)

            t0 = time.perf_counter()
            await asyncio.gather(*(one() for _ in range(level)))
            dt = (time.perf_counter() - t0) * 1000
            print(f"  concurrency={level:>4}  errors={len(errs):>4} "
                  f"{sorted(set(errs)) if errs else ''}  {dt:.0f}ms")
            await r.delete(key)
        print("  NOTE: every request that touches Redis borrows from this pool.")
        print("        Beyond the ceiling the call raises and the endpoint 500s.")
    finally:
        await r.delete(key)

    await r.aclose()

    passed = sum(1 for _, p, _ in RESULTS if p)
    failed = len(RESULTS) - passed
    print(f"\n=== ConnectionService probe — total {len(RESULTS)}, "
          f"passed {passed}, failed {failed} ===")
    for n, p, d in RESULTS:
        if not p:
            print(f"  FAIL: {n} — {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
