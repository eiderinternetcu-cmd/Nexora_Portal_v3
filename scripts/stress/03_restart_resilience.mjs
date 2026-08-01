/**
 * Scenario 3 — Restart resilience, api and redis SEPARATELY.
 *
 * Redis is the interesting one: playback tokens (jti), entitlement grants and
 * the connection ZSET all live there and nowhere else. PostgreSQL keeps the
 * IPTV session rows, so after a Redis restart the DB and Redis disagree about
 * who is watching.
 *
 * Redis persistence in dev is `appendonly no` + `save 60 1`, i.e. an RDB
 * snapshot at most once a minute — so a restart can lose up to ~60s of writes
 * rather than everything. That makes the outcome timing-dependent, which is
 * itself worth knowing.
 *
 * SAFETY: restarting redis disrupts every other process sharing this stack.
 * The redis phase is therefore opt-in:
 *
 *   node scripts/stress/03_restart_resilience.mjs --api      (default, safe)
 *   node scripts/stress/03_restart_resilience.mjs --redis    (disruptive)
 *   node scripts/stress/03_restart_resilience.mjs --all
 *
 * Never pass --remove-orphans to compose in this stack.
 */
import { execFileSync } from 'node:child_process';
import {
  requireEnv, login, authorize, heartbeat, channels, req, zsetDump,
  API_CONTAINER, REDIS_CONTAINER, check, summary, header, sleep, BASE,
} from './lib.mjs';

requireEnv('NX_USER', 'NX_PASS');

const args = process.argv.slice(2);
const doApi = args.includes('--api') || args.includes('--all') || args.length === 0;
const doRedis = args.includes('--redis') || args.includes('--all');
const DEV = process.env.NX_DEV_A || 'probe-device-001';

const restart = (c) => {
  const t0 = Date.now();
  execFileSync('docker', ['restart', c], { encoding: 'utf8', timeout: 120000 });
  return Date.now() - t0;
};

async function waitHealthy(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await req('/health');
    if (r.status === 200) return Date.now() - (deadline - timeoutMs);
    await sleep(500);
  }
  return -1;
}

header('SCENARIO 3 — Restart resilience');
console.log(`base=${BASE} api=${API_CONTAINER} redis=${REDIS_CONTAINER}`);
console.log(`phases: api=${doApi} redis=${doRedis}`);

// ── Establish a playing client ────────────────────────────────────────────
const { token, subscriberId } = await login(DEV);
const keys = await channels(token, 5);
const CH = keys[0];
const before = await authorize(token, DEV, CH);
check('client is playing before the restart', before.status === 200, `http=${before.status}`);
const zBefore = zsetDump(subscriberId);
console.log(`  ZSET before: count=${zBefore.count} ttl=${zBefore.ttl}`);

if (doApi) {
  header('3a — restart the API container');
  const ms = restart(API_CONTAINER);
  console.log(`  docker restart returned in ${ms}ms`);
  const up = await waitHealthy();
  check('API becomes healthy again', up >= 0, up >= 0 ? `ready in ~${up}ms` : 'never became healthy');

  const zAfter = zsetDump(subscriberId);
  check('the connection ZSET survives an API restart (state is in Redis)',
    zAfter.count === zBefore.count, `before=${zBefore.count} after=${zAfter.count}`);

  // The access token is a JWT, so it should still be accepted.
  const reissue = await req(`/api/client/playback/${CH}?device_id=${encodeURIComponent(DEV)}`, { token });
  check('the client resumes without re-login (token reissue works)',
    reissue.status === 200, `http=${reissue.status} body=${JSON.stringify(reissue.body).slice(0, 200)}`);

  const hb = await heartbeat(token, DEV);
  check('heartbeat works after an API restart', hb.status === 200, `http=${hb.status}`);

  const auth = await authorize(token, DEV, CH);
  check('authorize works again on its own after an API restart',
    auth.status === 200, `http=${auth.status}`);
}

if (doRedis) {
  header('3b — restart Redis (DISRUPTIVE: shared by the whole stack)');
  const zPre = zsetDump(subscriberId);
  console.log(`  ZSET before redis restart: count=${zPre.count} members=${JSON.stringify(zPre.entries)}`);

  const ms = restart(REDIS_CONTAINER);
  console.log(`  docker restart returned in ${ms}ms`);

  // Give the API's connection pool a moment to notice and reconnect.
  let health = null;
  for (let i = 0; i < 40; i++) {
    health = await req('/health');
    if (health.status === 200 && health.body?.redis === 'ok') break;
    await sleep(500);
  }
  check('API reports redis healthy again (pool reconnects on its own)',
    health?.status === 200 && health?.body?.redis === 'ok',
    `health=${JSON.stringify(health?.body)}`);

  const zPost = zsetDump(subscriberId);
  console.log(`  ZSET after redis restart: count=${zPost.count} ttl=${zPost.ttl}`);
  console.log(`  (appendonly=no, save="60 1" → up to ~60s of writes can be lost)`);

  const hb = await heartbeat(token, DEV);
  check('heartbeat succeeds after a Redis restart', hb.status === 200,
    `http=${hb.status} body=${JSON.stringify(hb.body).slice(0, 200)}`);

  const auth = await authorize(token, DEV, CH);
  check('authorize recovers by itself after a Redis restart', auth.status === 200,
    `http=${auth.status} body=${JSON.stringify(auth.body).slice(0, 200)}`);

  const reissue = await req(`/api/client/playback/${CH}?device_id=${encodeURIComponent(DEV)}`, { token });
  check('token reissue recovers after a Redis restart', reissue.status === 200,
    `http=${reissue.status} body=${JSON.stringify(reissue.body).slice(0, 200)}`);
}

process.exit(summary('SCENARIO 3 — Restart resilience') ? 1 : 0);
