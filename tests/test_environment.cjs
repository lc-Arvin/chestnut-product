const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../miniprogram/config/environment.js'), 'utf8');

function environment(version, mode = 'auto') {
  const context = { module: { exports: {} }, wx: {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: version } }),
    getStorageSync: () => '',
  }};
  vm.runInNewContext(source.replace('const TRANSPORT_MODE = "auto";', `const TRANSPORT_MODE = "${mode}";`), context);
  return context.module.exports;
}
test('auto keeps developer builds local and routes trial/release to cloud', () => {
  assert.equal(environment('develop').isCloudEnabled(), false);
  assert.equal(environment('trial').isCloudEnabled(), true);
  assert.equal(environment('release').isCloudEnabled(), true);
});
test('explicit mode supports cloud debugging and local override', () => {
  assert.equal(environment('develop', 'cloud').isCloudEnabled(), true);
  assert.equal(environment('release', 'local').isCloudEnabled(), false);
  assert.equal(environment('develop').websocketUrl(), 'ws://127.0.0.1:8080/ws');
});
