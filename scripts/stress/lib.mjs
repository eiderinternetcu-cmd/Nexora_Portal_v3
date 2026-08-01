/**
 * Shared helpers for the playback stress-test suite (P1.1 / Fase 4 · Bloque 3).
 *
 * No credentials are stored here. Every script reads them from the environment:
 *   NX_USER, NX_PASS            — test subscriber
 *   NX_ADMIN_USER, NX_ADMIN_PASS — admin (only needed for /api/admin/* probes)
 *   NX_BASE                     — API base URL      (default http://localhost:8000)
 *   NX_REDIS_CONTAINER          — redis container   (default nexora_redis)
 *   NX_API_CONTAINER            — api container     (default nexora_api)
 *
 * Requires Node >= 18 (native fetch). No npm dependencies on purpose: these
 * scripts must run on a bare checkout before every deploy.
 */
import { execFileSync } from 'node:child_process';

export const BASE = process.env.NX_BASE || 'http://localhost:8000';
export const REDIS_CONTAINER = process.env.NX_REDIS_CONTAINER || 'nexora_redis';
export const API_CONTAINER = process.env.NX_API_CONTAINER || 'nexora_api';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function requireEnv(...names) {
  const missing = names.filter((n) => !process.env[n]);
  if (missing.length) {
    console.error(`\nMissing env var(s): ${missing.join(', ')}`);
    console.error('Export them before running, e.g.:');
    console.error("  NX_USER=testuser1 NX_PASS='...' node scripts/stress/01_zapping.mjs\n");
    process.exit(2);
  }
}

/** HTTP helper returning {status, body, ms}. Never throws on non-2xx. */
export async function req(path, { method = 'GET', token = null, body = null, headers = {} } = {}) {
  const h = { ...headers };
  if (body !== null) h['Content-Type'] = 'application/json';
  if (token) h.Authorization = `Bearer ${token}`;
  const t0 = performance.now();
  let res, parsed;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: h,
      body: body === null ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    return { status: 0, body: { error: String(err) }, ms: performance.now() - t0 };
  }
  const text = await res.text();
  try { parsed = JSON.parse(text); } catch { parsed = text; }
  return { status: res.status, body: parsed, ms: performance.now() - t0 };
}

/** Subscriber login. Returns {token, refresh, subscriberId}. */
export async function login(deviceId, { user = process.env.NX_USER, pass = process.env.NX_PASS } = {}) {
  const r = await req('/api/client/auth/login', {
    method: 'POST',
    body: {
      username: user,
      password: pass,
      device_id: deviceId,
      device_type: 'web_player',
      model: 'stress',
      brand: 'Nexora',
      os_version: 'stress-suite',
    },
  });
  if (r.status !== 200) throw new Error(`login failed ${r.status}: ${JSON.stringify(r.body)}`);
  return { token: r.body.access_token, refresh: r.body.refresh_token, subscriberId: r.body.subscriber_id };
}

/** Admin login (for /api/admin/metrics and /api/admin/sessions/live). */
export async function adminLogin() {
  const r = await req('/api/v1/auth/login', {
    method: 'POST',
    body: { username: process.env.NX_ADMIN_USER, password: process.env.NX_ADMIN_PASS },
  });
  if (r.status !== 200) throw new Error(`admin login failed ${r.status}: ${JSON.stringify(r.body)}`);
  const b = r.body?.data || r.body;
  return b.access_token;
}

export const authorize = (token, deviceId, channelKey) =>
  req('/api/client/playback/authorize', {
    method: 'POST', token, body: { device_id: deviceId, channel_id: channelKey },
  });

export const heartbeat = (token, deviceId) =>
  req('/api/client/profile/devices/heartbeat', {
    method: 'POST', token, body: { device_id: deviceId, app_version: 'stress' },
  });

export const profile = (token) => req('/api/client/profile', { token });

export const listDevices = (token) => req('/api/client/profile/devices', { token });

export async function channels(token, limit = 10) {
  const r = await req(`/api/client/catalog/channels?limit=${limit}`, { token });
  const list = Array.isArray(r.body) ? r.body : (r.body?.data ?? []);
  return list.map((c) => c.channel_key);
}

export const adminMetrics = (t) => req('/api/admin/metrics', { token: t });
export const adminSessionsLive = (t) => req('/api/admin/sessions/live', { token: t });

// ── Redis introspection (via docker exec — read-only) ──────────────────────

export function redisCli(...args) {
  try {
    return execFileSync('docker', ['exec', REDIS_CONTAINER, 'redis-cli', ...args], {
      encoding: 'utf8', timeout: 15000,
    }).trim();
  } catch (err) {
    return `ERR ${err.message}`;
  }
}

export const zsetKey = (subId) => `nexora:active_conns:${subId}`;

/** Dump the connection ZSET as [{member, score, secondsLeft}], plus the key TTL. */
export function zsetDump(subId) {
  const key = zsetKey(subId);
  const raw = redisCli('ZRANGE', key, '0', '-1', 'WITHSCORES');
  const ttl = parseInt(redisCli('TTL', key), 10);
  const lines = raw ? raw.split(/\r?\n/).filter(Boolean) : [];
  const now = Date.now() / 1000;
  const entries = [];
  for (let i = 0; i < lines.length; i += 2) {
    const score = parseFloat(lines[i + 1]);
    entries.push({ member: lines[i], score, secondsLeft: +(score - now).toFixed(1) });
  }
  return { key, ttl, count: entries.length, entries };
}

/** ZCARD without the read-repair that count_active() performs. */
export const zsetRawCard = (subId) => parseInt(redisCli('ZCARD', zsetKey(subId)), 10) || 0;

export const zsetDel = (subId) => redisCli('DEL', zsetKey(subId));

// ── Result reporting ───────────────────────────────────────────────────────

const results = [];

export function check(name, passed, detail = '') {
  results.push({ name, passed, detail });
  console.log(`  [${passed ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
  return passed;
}

export function summary(title) {
  const pass = results.filter((r) => r.passed).length;
  const fail = results.length - pass;
  console.log(`\n=== ${title} — total ${results.length}, passed ${pass}, failed ${fail} ===`);
  if (fail) {
    console.log('Failures:');
    for (const r of results.filter((x) => !x.passed)) console.log(`  - ${r.name}: ${r.detail}`);
  }
  return fail;
}

export function header(title) {
  console.log(`\n${'='.repeat(72)}\n${title}\n${'='.repeat(72)}`);
}
