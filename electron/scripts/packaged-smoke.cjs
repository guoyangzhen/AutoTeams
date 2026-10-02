const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const resources = process.argv[2];
const main = path.join(resources, 'app.asar', 'dist-electron', 'main');
const { startDesktopServer } = require(path.join(main, 'loopback-server.js'));
const { parseUpstreamUrl } = require(path.join(main, 'upstream.js'));
const rootDir = path.join(resources, 'frontend');
function get(origin, target, html = false) {
  return new Promise((resolve, reject) => {
    const req = http.get(origin + target, { headers: { 'Sec-Fetch-Site': 'none', Accept: html ? 'text/html' : '*/*' } }, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks) }));
      res.on('error', reject);
    });
    req.setTimeout(5000, () => req.destroy(new Error('smoke timeout')));
    req.on('error', reject);
  });
}
(async () => {
  const server = await startDesktopServer({ rootDir, api: parseUpstreamUrl('http://127.0.0.1:9','api'), collab: parseUpstreamUrl('http://127.0.0.1:9','collab'), runtimeConfig: () => ({apiBaseUrl:'/api/v1'}), port:0 });
  try {
    const index = await get(server.origin, '/', true);
    assert.equal(index.status, 200);
    assert.deepEqual(index.body, fs.readFileSync(path.join(rootDir,'index.html')));
    const refs = [...index.body.toString().matchAll(/(?:src|href)="(\/[^\"]+)"/g)].map(m => m[1]);
    assert.ok(refs.length > 0);
    for (const ref of refs) {
      const response = await get(server.origin, ref);
      assert.equal(response.status,200,ref);
      assert.deepEqual(response.body,fs.readFileSync(path.join(rootDir,ref.slice(1))));
    }
    assert.deepEqual((await get(server.origin,'/enterprise/settings/model',true)).body,index.body);
    assert.equal((await get(server.origin,'/api/v1/unavailable')).status,502);
    console.log(JSON.stringify({electron:process.versions.electron,node:process.versions.node,assets:refs.length,packagedAsar:true,entry:true,deepLink:true,upstreamFailure:true}));
  } finally { await server.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
