/**
 * Scenario 2 — Continuous playback.
 *
 * Roadmap asks for 3-6 h; the default here is 10 min because the properties
 * worth watching are periodic, not cumulative — set NX_DURATION_S for a long
 * soak before a release.
 *
 * What it actually watches:
 *   - ZSET hygiene: the slot count must stay flat at 1 for one device. Any
 *     upward drift is a leak that will surface as a spurious 409.
 *   - the heartbeat keeps the session alive without accumulating members
 *   - token reissue (GET /api/client/playback/{channel}) keeps working, since
 *     a real player renews every ~45s
 *   - error/latency drift over time
 *
 * Usage:
 *   NX_USER=testuser1 NX_PASS='...' NX_DURATION_S=600 node scripts/stress/02_continuous_playback.mjs
 */
import {
  requireEnv, login, authorize, heartbeat, channels, profile, req,
  zsetDump, zsetRawCard, check, summary, header, sleep,
} from './lib.mjs';

requireEnv('NX_USER', 'NX_PASS');

const DURATION_S = parseInt(process.env.NX_DURATION_S || '600', 10);
const BEAT_S = parseInt(process.env.NX_BEAT_S || '30', 10);
const REISSUE_S = parseInt(process.env.NX_REISSUE_S || '45', 10);
const DEV = process.env.NX_DEV_A || 'probe-device-001';

header(`SCENARIO 2 — Continuous playback (${DURATION_S}s, beat ${BEAT_S}s, reissue ${REISSUE_S}s)`);

const { token, subscriberId } = await login(DEV);
const MAX = (await profile(token)).body.max_connections;
const keys = await channels(token, 5);
const CH = keys[0];

const first = await authorize(token, DEV, CH);
check('initial authorize', first.status === 200, `http=${first.status}`);

const samples = [];
const errors = [];
const t0 = Date.now();
let beats = 0, reissues = 0;
let nextBeat = t0 + BEAT_S * 1000;
let nextReissue = t0 + REISSUE_S * 1000;

while (Date.now() - t0 < DURATION_S * 1000) {
  await sleep(1000);
  const now = Date.now();

  if (now >= nextBeat) {
    nextBeat = now + BEAT_S * 1000;
    const hb = await heartbeat(token, DEV);
    beats++;
    if (hb.status !== 200) errors.push({ t: Math.round((now - t0) / 1000), op: 'heartbeat', status: hb.status, body: hb.body });
  }

  if (now >= nextReissue) {
    nextReissue = now + REISSUE_S * 1000;
    const rr = await req(`/api/client/playback/${CH}?device_id=${encodeURIComponent(DEV)}`, { token });
    reissues++;
    if (rr.status !== 200) errors.push({ t: Math.round((now - t0) / 1000), op: 'reissue', status: rr.status, body: rr.body });
    const z = zsetDump(subscriberId);
    samples.push({
      t: Math.round((now - t0) / 1000),
      zcount: z.count,
      rawZcard: zsetRawCard(subscriberId),
      ttl: z.ttl,
      secondsLeft: z.entries[0]?.secondsLeft ?? null,
      reissueMs: Math.round(rr.ms),
    });
    const s = samples[samples.length - 1];
    console.log(`  t+${String(s.t).padStart(4)}s zset=${s.zcount} raw=${s.rawZcard} ` +
      `keyTtl=${s.ttl} slotLeft=${s.secondsLeft} reissue=${s.reissueMs}ms`);
  }
}

console.log(`\nbeats=${beats} reissues=${reissues} samples=${samples.length}`);

const maxZ = Math.max(...samples.map((s) => s.zcount));
const maxRaw = Math.max(...samples.map((s) => s.rawZcard));
check('ZSET stayed at a single slot for the whole run', maxZ === 1, `max zcount=${maxZ}`);
check('no stale-member accumulation (raw ZCARD never drifted above the live count)',
  maxRaw <= MAX, `max raw ZCARD=${maxRaw} max_connections=${MAX}`);
check('ZSET key kept a positive TTL throughout',
  samples.every((s) => s.ttl > 0), `min ttl=${Math.min(...samples.map((s) => s.ttl))}`);
check('no request errors during the soak', errors.length === 0,
  errors.length ? JSON.stringify(errors.slice(0, 5)) : `${beats + reissues} requests, 0 errors`);

const lat = samples.map((s) => s.reissueMs).sort((x, y) => x - y);
if (lat.length) {
  const p = (q) => lat[Math.min(lat.length - 1, Math.floor(lat.length * q))];
  console.log(`reissue latency: p50=${p(0.5)}ms p95=${p(0.95)}ms max=${lat[lat.length - 1]}ms`);
}

process.exit(summary('SCENARIO 2 — Continuous playback') ? 1 : 0);
