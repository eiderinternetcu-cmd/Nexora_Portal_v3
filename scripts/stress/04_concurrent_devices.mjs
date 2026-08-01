/**
 * Scenario 4 — Three devices of the same subscriber, TRULY concurrent.
 *
 * The bug this hunts is a race, so the requests must leave the client at the
 * same time. Sequential authorizes can never expose it: every real
 * check-then-act window is microseconds wide. We fire N authorizes with
 * Promise.all (one in-flight socket each) and assert that EXACTLY
 * max_connections succeed — not "about" that many.
 *
 * It also runs the heartbeat-then-authorize chain, which is where the limit
 * actually leaks (see conn_service_probe.py section D2).
 *
 * Usage:
 *   NX_USER=testuser1 NX_PASS='...' node scripts/stress/04_concurrent_devices.mjs [rounds]
 */
import {
  requireEnv, login, authorize, heartbeat, channels, profile,
  zsetDump, zsetDel, check, summary, header,
} from './lib.mjs';

requireEnv('NX_USER', 'NX_PASS');
const ROUNDS = parseInt(process.argv[2] || '5', 10);

// Devices must already be registered to the subscriber (max_devices is finite,
// so the suite reuses existing rows rather than creating new ones each run).
const DEVICES = (process.env.NX_DEVICES ||
  'probe-device-001,tc-test-001,test-device-001,diag-testuser1-01')
  .split(',').map((s) => s.trim()).filter(Boolean);

header(`SCENARIO 4 — Concurrent device limit (${DEVICES.length} devices, ${ROUNDS} rounds)`);

const sessions = [];
for (const d of DEVICES) {
  const s = await login(d);
  sessions.push({ deviceId: d, ...s });
}
const subscriberId = sessions[0].subscriberId;
const MAX = (await profile(sessions[0].token)).body.max_connections;
const keys = await channels(sessions[0].token, 5);
console.log(`subscriber=${subscriberId} max_connections=${MAX} devices=${DEVICES.length}`);

if (DEVICES.length <= MAX) {
  console.warn(`WARNING: ${DEVICES.length} devices <= max_connections ${MAX}; ` +
    'the limit can never trip. Add more device ids via NX_DEVICES.');
}

// ── Round loop: all devices authorize simultaneously ──────────────────────
for (let round = 1; round <= ROUNDS; round++) {
  zsetDel(subscriberId); // isolate the round; this key belongs to the test subscriber
  const t0 = performance.now();
  const res = await Promise.all(
    sessions.map((s) => authorize(s.token, s.deviceId, keys[0]))
  );
  const wall = performance.now() - t0;

  const ok = res.filter((r) => r.status === 200).length;
  const conflict = res.filter((r) => r.status === 409).length;
  const other = res.filter((r) => r.status !== 200 && r.status !== 409);
  const z = zsetDump(subscriberId);

  const expected = Math.min(MAX, DEVICES.length);
  check(
    `round ${round}: exactly ${expected} of ${DEVICES.length} concurrent authorizes granted`,
    ok === expected && conflict === DEVICES.length - expected && other.length === 0,
    `200=${ok} 409=${conflict} other=${JSON.stringify(other.map((o) => o.status))} ` +
    `zset=${z.count} wall=${Math.round(wall)}ms`
  );
  check(`round ${round}: ZSET never exceeds max_connections`, z.count <= MAX,
    `zset=${z.count} max=${MAX}`);
}

// ── The leak: heartbeat first, then authorize ─────────────────────────────
if (DEVICES.length > MAX) {
  header('SCENARIO 4b — heartbeat-then-authorize (limit bypass check)');
  zsetDel(subscriberId);

  // Fill every slot with the first MAX devices.
  for (let i = 0; i < MAX; i++) {
    await authorize(sessions[i].token, sessions[i].deviceId, keys[0]);
  }
  const victim = sessions[MAX]; // one device beyond the limit

  const refused = await authorize(victim.token, victim.deviceId, keys[0]);
  check('baseline: the extra device is refused with 409', refused.status === 409,
    `http=${refused.status}`);

  const hb = await heartbeat(victim.token, victim.deviceId);
  const zAfterHb = zsetDump(subscriberId);
  check('heartbeat by a device with NO slot must not add it to the ZSET',
    zAfterHb.count <= MAX,
    `http=${hb.status} zset=${zAfterHb.count} max=${MAX} ` +
    `active_connections_reported=${hb.body?.active_connections}`);

  const after = await authorize(victim.token, victim.deviceId, keys[0]);
  const zFinal = zsetDump(subscriberId);
  check('after heartbeat, the extra device must STILL be refused',
    after.status === 409,
    `http=${after.status} zset=${zFinal.count} max=${MAX} ` +
    `-> a 200 here means max_connections is bypassable`);
}

zsetDel(subscriberId);
process.exit(summary('SCENARIO 4 — Concurrent devices') ? 1 : 0);
