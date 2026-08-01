/**
 * Scenario 5 — Heartbeat timeout releases the slot.
 *
 * Contract: HEARTBEAT_TTL_SECONDS (180s) without a beat must free the
 * connection. The interesting part is not the arithmetic, it is whether the
 * slot is released *in practice*: entries are only purged when some other
 * operation touches the key (ZREMRANGEBYSCORE runs inside the open path and
 * inside count_active), so nothing reclaims a slot proactively.
 *
 * Two modes:
 *   --real   wait the full TTL through the HTTP API (default; ~4 min)
 *   --fast   seed the ZSET with a short expiry to assert the release semantics
 *            in seconds (used in pre-deploy runs)
 *
 * Usage:
 *   NX_USER=testuser1 NX_PASS='...' node scripts/stress/05_heartbeat_timeout.mjs --fast
 *   NX_USER=testuser1 NX_PASS='...' node scripts/stress/05_heartbeat_timeout.mjs --real
 */
import {
  requireEnv, login, authorize, heartbeat, channels, profile, zsetDump, zsetDel,
  zsetRawCard, redisCli, zsetKey, check, summary, header, sleep,
} from './lib.mjs';

requireEnv('NX_USER', 'NX_PASS');
const MODE = process.argv.includes('--real') ? 'real' : 'fast';
const TTL = parseInt(process.env.NX_HEARTBEAT_TTL || '180', 10);
const DEV_A = process.env.NX_DEV_A || 'probe-device-001';
const DEV_B = process.env.NX_DEV_B || 'tc-test-001';

header(`SCENARIO 5 — Heartbeat timeout (${MODE} mode, TTL=${TTL}s)`);

const a = await login(DEV_A);
const b = await login(DEV_B);
const subscriberId = a.subscriberId;
const MAX = (await profile(a.token)).body.max_connections;
const keys = await channels(a.token, 5);
console.log(`subscriber=${subscriberId} max_connections=${MAX}`);

zsetDel(subscriberId);

if (MODE === 'fast') {
  // Fill every slot with entries that expire in 3s, then prove the slot frees.
  const now = Date.now() / 1000;
  const fake = [];
  for (let i = 0; i < MAX; i++) fake.push(`ffffffff-0000-0000-0000-${String(i).padStart(12, '0')}`);
  for (const m of fake) redisCli('ZADD', zsetKey(subscriberId), String(now + 3), m);
  check('slots pre-filled to the limit', zsetRawCard(subscriberId) === MAX,
    `zcard=${zsetRawCard(subscriberId)}`);

  const refused = await authorize(a.token, DEV_A, keys[0]);
  check('while slots are held, a new device is refused', refused.status === 409,
    `http=${refused.status}`);

  await sleep(4000);
  const stale = zsetRawCard(subscriberId);
  const granted = await authorize(a.token, DEV_A, keys[0]);
  const z = zsetDump(subscriberId);
  check('after the entries expire, a new device is admitted', granted.status === 200,
    `http=${granted.status} zset_after=${z.count}`);
  check('expired members are purged from the ZSET', z.count === 1,
    `raw_zcard_before=${stale} zset_after=${z.count} members=${JSON.stringify(z.entries)}`);
  console.log(`  NOTE: raw ZCARD stayed at ${stale} after expiry until an operation ` +
    'touched the key — nothing reclaims slots proactively.');
} else {
  // Real mode: open a slot, stop beating, watch it die.
  const r = await authorize(a.token, DEV_A, keys[0]);
  check('device A opened a slot', r.status === 200, `http=${r.status}`);
  const z0 = zsetDump(subscriberId);
  console.log(`  slot opened, expires in ${z0.entries[0]?.secondsLeft}s`);

  // Beat twice to prove the slot is kept alive, then go silent.
  for (let i = 0; i < 2; i++) {
    await sleep(20000);
    const hb = await heartbeat(a.token, DEV_A);
    const z = zsetDump(subscriberId);
    console.log(`  beat ${i + 1}: http=${hb.status} secondsLeft=${z.entries[0]?.secondsLeft}`);
  }
  const zBeat = zsetDump(subscriberId);
  check('heartbeat keeps the slot alive without duplicating it', zBeat.count === 1,
    `zset=${zBeat.count}`);
  check('heartbeat pushes the expiry back to ~TTL',
    zBeat.entries[0] && zBeat.entries[0].secondsLeft > TTL - 15,
    `secondsLeft=${zBeat.entries[0]?.secondsLeft} ttl=${TTL}`);

  console.log(`  going silent for ${TTL + 15}s...`);
  const deadline = Date.now() + (TTL + 15) * 1000;
  while (Date.now() < deadline) {
    await sleep(30000);
    const z = zsetDump(subscriberId);
    console.log(`    t+${Math.round((TTL + 15) - (deadline - Date.now()) / 1000)}s ` +
      `rawZcard=${zsetRawCard(subscriberId)} secondsLeft=${z.entries[0]?.secondsLeft ?? 'n/a'}`);
  }

  const rawStale = zsetRawCard(subscriberId);
  const rb = await authorize(b.token, DEV_B, keys[0]);
  const zEnd = zsetDump(subscriberId);
  check('after TTL with no heartbeat, the slot is released to another device',
    rb.status === 200, `http=${rb.status} zset=${zEnd.count}`);
  console.log(`  raw ZCARD just before the reclaiming call: ${rawStale}`);
}

zsetDel(subscriberId);
process.exit(summary('SCENARIO 5 — Heartbeat timeout') ? 1 : 0);
