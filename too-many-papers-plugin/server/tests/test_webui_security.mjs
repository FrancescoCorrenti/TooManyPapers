// The web UI has no password: check that only its own page on this machine gets in.
// Run: node --test server/tests/test_webui_security.mjs
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const SERVER = fileURLToPath(new URL('../../webui/paper-library-server.js', import.meta.url));
const PORT = 38737;

function call(url, { method = 'GET', headers = {}, address = '127.0.0.1', body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: address, port: PORT, path: url, method, headers: { host: `localhost:${PORT}`, ...headers } },
      res => {
        let data = '';
        res.on('data', d => { data += d; });
        res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, data }));
      });
    req.on('error', reject);
    req.end(body);
  });
}

test('web UI only serves its own page and library PDFs', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tmp-webui-'));
  const data = path.join(root, 'data');
  fs.mkdirSync(path.join(data, 'pdfs'), { recursive: true });
  fs.writeFileSync(path.join(data, 'pdfs', 'P001.pdf'), 'inside');
  fs.writeFileSync(path.join(root, 'secret.pdf'), 'outside');
  fs.writeFileSync(path.join(root, 'secret.txt'), 'outside');

  const server = spawn(process.execPath, [SERVER], {
    env: { ...process.env, PORT: String(PORT), TOO_MANY_PAPERS_DATA_DIR: data },
  });
  t.after(() => { server.kill(); fs.rmSync(root, { recursive: true, force: true }); });
  await new Promise((resolve, reject) => {
    server.stdout.on('data', resolve);
    server.on('exit', code => reject(new Error(`server exited with ${code}`)));
  });

  const pdf = file => call('/pdf/' + encodeURIComponent(file));
  assert.equal((await pdf('pdfs/P001.pdf')).data, 'inside');
  assert.equal((await pdf('P001.pdf')).data, 'inside', 'bare file name still resolves');
  for (const file of ['../secret.pdf', '../secret.txt', 'pdfs/../../secret.pdf', path.join(root, 'secret.pdf'), '_papers.json']) {
    assert.equal((await pdf(file)).status, 404, file);
  }

  const own = await call('/api/papers', { headers: { origin: `http://localhost:${PORT}` } });
  assert.equal(own.status, 200);
  assert.equal(own.headers['access-control-allow-origin'], undefined);

  assert.equal((await call('/', { headers: { host: `evil.example:${PORT}` } })).status, 403);
  for (const origin of ['https://evil.example', 'null']) {
    const res = await call('/api/hide', { method: 'POST', headers: { origin, 'content-type': 'text/plain' }, body: '{"id":"P001"}' });
    assert.equal(res.status, 403, origin);
  }

  for (const address of Object.values(os.networkInterfaces()).flat().filter(a => a.family === 'IPv4' && !a.internal).map(a => a.address)) {
    await assert.rejects(call('/', { address, headers: {} }), `reachable on ${address}`);
  }
});
