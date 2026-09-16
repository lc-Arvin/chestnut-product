const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../miniprogram/config/environment.js'), 'utf8');

function environment(version, code = source) {
  const context = { module: { exports: {} }, wx: {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: version } }),
    getStorageSync: () => { throw Error('Legacy host storage must not be read'); },
  }};
  vm.runInNewContext(code, context);
  return context.module.exports;
}

test('developer, trial and release builds all use the production cloud service', () => {
  for (const version of ['develop', 'trial', 'release', undefined]) {
    const config = environment(version);
    assert.equal(config.cloudConfig().env, 'chestnut-prod-d6ggcq8yzf8d2e322');
    assert.equal(config.CLOUD_SERVICE, 'chestnut-api');
    for (const name of ['getServerHost', 'setServerHost', 'apiUrl', 'websocketUrl', 'TRANSPORT_MODE']) {
      assert.equal(config[name], undefined);
    }
  }
});

test('missing cloud routing configuration fails instead of falling back locally', () => {
  for (const key of ['CLOUD_ENV_ID', 'CLOUD_SERVICE']) {
    const config = environment('develop', source.replace(new RegExp(`const ${key} = "[^"]*";`), `const ${key} = "";`));
    assert.throws(() => config.cloudConfig(), /云服务尚未配置/);
  }
});
