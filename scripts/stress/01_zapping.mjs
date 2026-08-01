/**
 * Scenario 1 — Rapid zapping.
 *
 * Roadmap: "5 canales en 30 s". What this actually hunts for:
 *   a) zombie sessions — ZSET slots that stay occupied after switching channel
 *   b) false 409s — "max connections reached" while the user watches ONE channel
 *
 * The ZSET member is the DEVICE uuid, not the channel, so a correct
 * implementation keeps count == 1 no matter how many channels are zapped.
 * Any growth is a leak. We also verify a SECOND device can still open a slot
 * after the zapping burst (i.e. zapping did not eat the plan's other slots).
 *
 * Usage:
 *   NX_USER=testuser1 NX_PASS='...' node scripts/stress/01_zapping.mjs [zaps] [intervalMs]
 */
import {
  requireEnv, login, authorize, channels, zsetDump, profile,
  check, summary, header, sleep,
} from './lib.mjs';

requireEnv('NX_USER', 'NX_PASS');

const ZAPS = parseInt(process.argv[2] || '5', 10);
const INTERVAL = parseInt(process.argv[3] || '6000', 10); // 5 zaps x 6s = 30s
const DEV_A = process.env.NX_DEV_A || 'probe-device-001';
const DEV_B = process.env.NX_DEV_B || 'tc-test-001';

header(`SCENARIO 1 — Rapid zapping (${ZAPS} channels, ${INTERVAL}ms apart)`);

const { token, subscriberId } = await login(DEV_A);
const prof = (await profile(token)).body;
const MAX = prof.max_connections;
console.log(`subscriber=${subscriberId}  max_connections=${MAX}`);

const keys = await channels(token, 12);
if (keys.length < ZAPS) {
  console.error(`Only ${keys.length} channels in catalog, need ${ZAPS}`);
  process.exit(2);
}

// Clean slate: wait out any slot this device already holds? No — we do NOT
// flush Redis (other workers share it). We just record the baseline.
const baseline = zsetDump(subscriberId);
console.log(`baseline ZSET count=${baseline.count} ttl=${baseline.ttl}`);

const observed = [];
const t0 = Date.now();

for (let i = 0; i < ZAPS; i++) {
  const ch = keys[i % keys.length];
  const r = await authorize(token, DEV_A, ch);
  const z = zsetDump(subscriberId);
  observed.push({ i: i + 1, ch, status: r.status, ms: Math.round(r.ms), zcount: z.count, ttl: z.ttl });
  console.log(
    `  zap ${i + 1}/${ZAPS} ch=${ch.padEnd(10)} http=${r.status} ${String(Math.round(r.ms)).padStart(4)}ms ` +
    `zset=${z.count} ttl=${z.ttl}` +
    (r.status !== 200 ? `  body=${JSON.stringify(r.body).slice(0, 160)}` : '')
  );
  if (i < ZAPS - 1) await sleep(INTERVAL);
}

const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
console.log(`\nelapsed ${elapsed}s`);

// ── Assertions ────────────────────────────────────────────────────────────
const failed = observed.filter((o) => o.status !== 200);
check('all zaps authorized (no false 409)', failed.length === 0,
  failed.length ? `failures: ${JSON.stringify(failed)}` : `${ZAPS}/${ZAPS} returned 200`);

const finalZ = zsetDump(subscriberId);
const devEntries = finalZ.count;
check('zapping did not grow the ZSET beyond 1 slot for this device',
  devEntries <= Math.max(1, baseline.count),
  `baseline=${baseline.count} final=${devEntries} members=${JSON.stringify(finalZ.entries)}`);

check('ZSET key carries a TTL (no immortal key)', finalZ.ttl > 0,
  `ttl=${finalZ.ttl}`);

// A second device must still be able to open a slot after the burst.
const { token: tokenB } = await login(DEV_B);
const rb = await authorize(tokenB, DEV_B, keys[0]);
const zAfterB = zsetDump(subscriberId);
check('a second device can still open a slot after zapping', rb.status === 200,
  `http=${rb.status} zset=${zAfterB.count}/${MAX} body=${JSON.stringify(rb.body).slice(0, 200)}`);

console.log(`\nfinal ZSET: ${JSON.stringify(zAfterB, null, 1)}`);
process.exit(summary('SCENARIO 1 — Rapid zapping') ? 1 : 0);
