# Playback stress tests (roadmap P1.1)

Reusable scenarios for the concurrent-connection and playback path. Written to
be run **before every deploy**, not once.

## Requirements

- Node >= 18 (native `fetch`; no npm install needed) for the `*.mjs` scenarios
- `docker` on PATH — the scripts read the connection ZSET straight from Redis
- The local stack up: api `:8000`, postgres `:5433`, redis `:6380`

## Credentials

Never hard-coded and never written to a file. Export them per run:

```bash
export NX_USER=testuser1
export NX_PASS='<the test subscriber password>'
export NX_ADMIN_USER=admin          # only for the /api/admin/* probes
export NX_ADMIN_PASS='<admin password>'
```

Optional overrides: `NX_BASE` (default `http://localhost:8000`),
`NX_REDIS_CONTAINER` (`nexora_redis`), `NX_API_CONTAINER` (`nexora_api`),
`NX_DEVICES`, `NX_DEV_A`, `NX_DEV_B`.

The test subscriber has a finite `max_devices`, so the scripts **reuse
already-registered device ids** instead of creating new ones on each run.

## Scenarios

| Script | What it stresses |
|---|---|
| `01_zapping.mjs [zaps] [intervalMs]` | 5 channels in 30 s on one device. Hunts zombie slots and false 409s. |
| `02_continuous_playback.mjs` | Soak with heartbeat + token reissue. Watches ZSET hygiene and latency drift. `NX_DURATION_S=600` by default. |
| `03_restart_resilience.mjs --api\|--redis\|--all` | Restarts a container and checks the client recovers unaided. **`--redis` is disruptive** — it is shared by the whole stack — so it is opt-in. |
| `04_concurrent_devices.mjs [rounds]` | N devices authorizing *simultaneously* (`Promise.all`). Asserts exactly `max_connections` win. Also runs the heartbeat-then-authorize bypass check. |
| `05_heartbeat_timeout.mjs --fast\|--real` | 180 s without a beat must release the slot. `--fast` asserts the semantics in seconds; `--real` waits the true TTL. |
| `conn_service_probe.py` | Direct stress of `ConnectionService` + its Lua script against Redis. **No PostgreSQL, no HTTP** — so it still runs when the API is down. |

Every script exits `0` when all its checks pass, `1` otherwise, and prints one
`[PASS]`/`[FAIL]` line per assertion.

## Running

```bash
node scripts/stress/01_zapping.mjs
node scripts/stress/04_concurrent_devices.mjs 5
node scripts/stress/05_heartbeat_timeout.mjs --fast
node scripts/stress/03_restart_resilience.mjs --api

# service-level probe (runs inside the api container)
docker exec nexora_api python /app/scripts/stress/conn_service_probe.py
```

## Notes

- `conn_service_probe.py` uses **synthetic subscriber UUIDs**, so it never
  touches a real subscriber's slots and is safe to run alongside other work.
- The `*.mjs` scripts do delete the *test subscriber's* ZSET key between rounds
  to isolate measurements. Point them at a throwaway subscriber if that matters.
- Never pass `--remove-orphans` to compose in this stack.

Findings and measured numbers: `docs/STRESS_TESTS_PLAYBACK.md`.
