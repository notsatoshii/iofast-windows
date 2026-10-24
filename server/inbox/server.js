// iofast phone inbox: the phone posts captured links, the PC app pulls and marks them done.
// Served at https://agentos.rest/iofast-inbox/* (Caddy strips the prefix). No dependencies.
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PORT = Number(process.env.PORT || 4795);
const DATA = path.join(process.env.DATA_DIR || '/data', 'inbox.json');
const PHONE_KEY = process.env.PHONE_KEY || '';
const PC_KEY = process.env.PC_KEY || '';
const DUP_WINDOW_MS = 10 * 60 * 1000;
const KEEP_DONE_MS = 7 * 24 * 3600 * 1000;
const MAX_PENDING = 500;
// Phone self-update: phone.json ({versionCode, versionName, sha256, size}) + phone.apk, both phone-key only.
const APP_DIR = process.env.APP_DIR || '/app-dl';

if (PHONE_KEY.length < 32 || PC_KEY.length < 32) {
  console.error('PHONE_KEY and PC_KEY must be set (32+ chars)');
  process.exit(1);
}

let links = [];
try { links = JSON.parse(fs.readFileSync(DATA, 'utf8')).links || []; } catch { links = []; }

function save() {
  const now = Date.now();
  links = links.filter(l => !l.done_at || now - Date.parse(l.done_at) < KEEP_DONE_MS);
  const tmp = DATA + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify({ links }));
  fs.renameSync(tmp, DATA);
}

function keyOk(req, key) {
  const m = /^Bearer (.+)$/.exec(req.headers.authorization || '');
  if (!m) return false;
  const a = Buffer.from(m[1]);
  const b = Buffer.from(key);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function send(res, code, body) {
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(body));
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', c => {
      size += c.length;
      if (size > 8192) { reject(new Error('too large')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
      catch { reject(new Error('bad json')); }
    });
    req.on('error', reject);
  });
}

function validUrl(u) {
  if (typeof u !== 'string' || u.length > 2048) return false;
  try { const p = new URL(u); return p.protocol === 'http:' || p.protocol === 'https:'; }
  catch { return false; }
}

const pub = l => ({ id: l.id, url: l.url, source: l.source, captured_at: l.captured_at });

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const p = url.pathname.replace(/\/+$/, '') || '/';
  try {
    if (req.method === 'GET' && p === '/health') return send(res, 200, { ok: true });

    if (req.method === 'POST' && p === '/links') {
      if (!keyOk(req, PHONE_KEY)) return send(res, 401, { error: 'bad key' });
      const body = await readJson(req);
      if (!validUrl(body.url)) return send(res, 400, { error: 'url must be http(s), max 2048 chars' });
      const now = Date.now();
      const dup = links.find(l => l.url === body.url && now - Date.parse(l.received_at) < DUP_WINDOW_MS);
      if (dup) return send(res, 200, { id: dup.id, duplicate: true });
      if (links.filter(l => !l.done_at).length >= MAX_PENDING) return send(res, 507, { error: 'inbox full' });
      const link = {
        id: crypto.randomUUID(),
        url: body.url,
        source: String(body.source || '').slice(0, 200),
        captured_at: String(body.captured_at || new Date(now).toISOString()).slice(0, 40),
        received_at: new Date(now).toISOString(),
      };
      links.push(link);
      save();
      return send(res, 201, { id: link.id });
    }

    if (req.method === 'GET' && p === '/links') {
      if (!keyOk(req, PC_KEY)) return send(res, 401, { error: 'bad key' });
      const pending = links.filter(l => !l.done_at)
        .sort((a, b) => a.received_at.localeCompare(b.received_at));
      return send(res, 200, { links: pending.map(pub) });
    }

    if (req.method === 'GET' && (p === '/app/phone.json' || p === '/app/phone.apk')) {
      if (!keyOk(req, PHONE_KEY)) return send(res, 401, { error: 'bad key' });
      const file = path.join(APP_DIR, p === '/app/phone.json' ? 'phone.json' : 'phone.apk');
      let st;
      try { st = fs.statSync(file); } catch { return send(res, 404, { error: 'no release' }); }
      res.writeHead(200, {
        'Content-Type': p.endsWith('.json') ? 'application/json' : 'application/vnd.android.package-archive',
        'Content-Length': st.size,
        'Cache-Control': 'no-store',
      });
      return fs.createReadStream(file).pipe(res);
    }

    // Phone asks how its capture went: {done, ok, filed, title}.
    const st = /^\/links\/([0-9a-f-]{36})$/.exec(p);
    if (req.method === 'GET' && st) {
      if (!keyOk(req, PHONE_KEY)) return send(res, 401, { error: 'bad key' });
      const link = links.find(l => l.id === st[1]);
      if (!link) return send(res, 404, { error: 'not found' });
      return send(res, 200, {
        id: link.id,
        done: Boolean(link.done_at),
        ok: link.ok === true,
        filed: link.filed === true,
        title: link.title || '',
      });
    }

    const m = /^\/links\/([0-9a-f-]{36})\/done$/.exec(p);
    if (req.method === 'POST' && m) {
      if (!keyOk(req, PC_KEY)) return send(res, 401, { error: 'bad key' });
      const body = await readJson(req);
      const link = links.find(l => l.id === m[1]);
      if (!link) return send(res, 404, { error: 'not found' });
      link.done_at = new Date().toISOString();
      link.ok = body.ok === true;
      link.note = String(body.note || '').slice(0, 500);
      if (typeof body.title === 'string') link.title = body.title.slice(0, 200);
      // Older PC apps send no "filed": a successful grab was filed then.
      link.filed = typeof body.filed === 'boolean' ? body.filed : link.ok;
      save();
      return send(res, 200, { ok: true });
    }

    return send(res, 404, { error: 'not found' });
  } catch (e) {
    return send(res, 400, { error: e.message });
  }
});

server.listen(PORT, '0.0.0.0', () => console.log(`iofast inbox on ${PORT}`));
