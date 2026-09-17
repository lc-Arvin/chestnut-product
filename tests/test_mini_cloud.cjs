const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const read = name => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function sharePage(scene) {
  let page;
  const calls = [];
  vm.runInNewContext(read('miniprogram/pages/setup/setup.js'), {
    Page: value => { page = value; },
    wx: { getLaunchOptionsSync: () => ({ scene }), showShareMenu: options => calls.push(options.menus) },
    require: name => {
      if (name.endsWith('/languages')) return { codes: ['zh', 'en'], labels: { zh: '中文', en: 'English' } };
      if (name.endsWith('/layout')) return { safeTopPadding: () => 0 };
      // Preview must not even load the recorder module, which creates its manager eagerly.
      if (name.endsWith('/recorder')) throw Error('Preview initialized recorder');
      if (name.endsWith('/access')) return { recordVisit() { throw Error('Preview called cloud'); } };
      return {};
    },
  });
  return { page, calls };
}

test('timeline preview opens without cloud, recorder or share-menu side effects', () => {
  const { page, calls } = sharePage(1154);
  assert.equal(page.data.timelinePreview, true);
  page.onLoad();
  page.onShow();
  assert.equal(calls.length, 0);
  assert.equal(page.data.inviteVisible, false);
});

test('normal and full-app timeline entry enable both menus and drop inbound share parameters', () => {
  const appPages = JSON.parse(read('miniprogram/app.json')).pages;
  for (const scene of [1001, 1155]) {
    const { page, calls } = sharePage(scene);
    assert.equal(page.data.timelinePreview, false);
    page.onLoad();
    assert.equal(JSON.stringify(calls), '[["shareAppMessage","shareTimeline"]]');
    page.options = { invite: 'private-code', token: 'private-token' };
    const friend = page.onShareAppMessage(), timeline = page.onShareTimeline();
    assert.ok(appPages.includes(friend.path.slice(1)));
    assert.equal(timeline.query, '');
    assert.equal(timeline.path, undefined);
    for (const card of [friend, timeline]) {
      assert.doesNotMatch(JSON.stringify(card), /private-/);
      assert.ok(fs.existsSync(path.join(__dirname, '..', 'miniprogram', card.imageUrl)));
    }
  }
});

test('app skips authenticated cloud initialization only in timeline single-page mode', () => {
  for (const scene of [1001, 1154, 1155]) {
    let app, initializations = 0, resets = 0;
    vm.runInNewContext(read('miniprogram/app.js'), {
      App: value => { app = value; },
      wx: { cloud: { init: () => initializations++ } },
      require: name => name.endsWith('/meeting-state') ? { reset: () => resets++ } : { cloudConfig: () => ({ env: 'test' }) },
    });
    app.onLaunch({ scene });
    assert.equal(initializations, scene === 1154 ? 0 : 1);
    assert.equal(resets, 1);
  }
});

function accessHarness() {
  const storage = new Map(), requests = [];
  const environment = {
    CLOUD_ENV_ID: 'test-env', CLOUD_SERVICE: 'api',
    cloudConfig: () => ({ env: environment.CLOUD_ENV_ID }),
  };
  let handler = async request => ({ statusCode: 200, data: request.path === '/api/auth/trial'
    ? { access_token: 'trial', authenticated: true, access_mode: 'trial', trial: { meeting_id: 'trial-id' } }
    : { access_token: 'signed', authenticated: true, access_mode: 'invitation', invitation_expires_at: null } });
  const wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
    cloud: { callContainer: request => { requests.push(request); return handler(request); } },
    request: () => { throw new Error('Direct HTTP must never be used'); },
  };
  const context = { module: { exports: {} }, wx, require: () => environment };
  vm.runInNewContext(read('miniprogram/services/access.js'), context);
  return { access: context.module.exports, environment, wx, requests, storage,
    handle: fn => { handler = fn; } };
}

test('credentials and cached trials are isolated by cloud environment and service', async () => {
  const h = accessHarness();
  await h.access.startTrial();
  assert.equal(h.access.trialInfo().meeting_id, 'trial-id');
  h.environment.CLOUD_SERVICE = 'another-api';
  assert.equal(h.access.token(), '');
  assert.equal(h.access.trialInfo(), null);
  h.environment.CLOUD_SERVICE = 'api';
  assert.equal(h.access.token(), 'trial');
  h.environment.CLOUD_ENV_ID = 'other-env';
  assert.equal(h.access.token(), '');
  h.environment.CLOUD_ENV_ID = 'test-env';
  assert.equal(h.access.token(), 'trial');
});

test('legacy local address and credentials cannot affect cloud routing', async () => {
  const h = accessHarness();
  h.storage.set('chestnut_server_host', '192.168.1.99');
  h.storage.set('chestnut_access:v2:local:http://192.168.1.99:8080/', 'local-token');
  assert.equal(h.access.token(), '');
  await h.access.status();
  assert.equal(h.requests[0].config.env, 'test-env');
  assert.equal(h.requests[0].header['X-WX-SERVICE'], 'api');
  assert.equal(h.requests[0].header.Authorization, undefined);
});

test('a late unauthorized response cannot clear a newer login', async () => {
  const h = accessHarness(); await h.access.login('123456');
  const pending = deferred(); h.handle(() => pending.promise);
  const oldRequest = h.access.request('/api/meetings', 'POST', {});
  h.handle(async () => ({ statusCode: 200, data: { access_token: 'new-token', authenticated: true } }));
  await h.access.login('234567');
  pending.resolve({ statusCode: 401, data: { error: 'expired' } });
  await assert.rejects(oldRequest, /expired/);
  assert.equal(h.access.token(), 'new-token');
});

test('an old status response does not overwrite a newer successful login', async () => {
  const h = accessHarness(), pending = deferred(); h.handle(() => pending.promise);
  const checking = h.access.status();
  h.handle(async () => ({ statusCode: 200, data: { access_token: 'new', authenticated: true } }));
  await h.access.login('123456');
  pending.resolve({ statusCode: 200, data: { authenticated: false } });
  assert.equal((await checking).authenticated, true);
});

test('an old authenticated response cannot restore access after logout', async () => {
  const h = accessHarness(); await h.access.login('123456');
  const pending = deferred(); h.handle(() => pending.promise);
  const checking = h.access.status(); h.access.clear();
  pending.resolve({ statusCode: 200, data: { authenticated: true } });
  await assert.rejects(checking, /登录状态已变化/);
  assert.equal(h.access.currentStatus(), null);
});

test('switching service during login does not store the response in the new service', async () => {
  const h = accessHarness(), pending = deferred(); h.handle(() => pending.promise);
  const signingIn = h.access.login('123456'); h.environment.CLOUD_SERVICE = 'other';
  pending.resolve({ statusCode: 200, data: { access_token: 'wrong-service' } });
  await assert.rejects(signingIn, /登录状态已变化/);
  assert.equal(h.access.token(), '');
});

test('cloud requests carry authentication, service routing and an explicit timeout', async () => {
  const h = accessHarness(); await h.access.login('123456');
  await h.access.request('/api/meetings', 'POST', { entries: [] });
  const request = h.requests[1];
  assert.equal(request.header.Authorization, 'Bearer signed');
  assert.equal(request.header['X-WX-SERVICE'], 'api');
  assert.equal(request.config.env, 'test-env');
  assert.equal(request.timeout, 15000);
});

test('gateway HTML and SDK timeout failures produce readable errors', async () => {
  const h = accessHarness();
  h.handle(async () => ({ statusCode: 200, data: '<html>wrong service</html>' }));
  await assert.rejects(h.access.request('/api/auth/status'), /云服务响应异常/);
  h.handle(async () => ({ statusCode: 502, data: '<html>bad gateway</html>' }));
  await assert.rejects(h.access.request('/api/meetings'), /502/);
  h.handle(() => { throw { errMsg: 'callContainer:fail timeout' }; });
  await assert.rejects(h.access.request('/api/meetings'), /响应超时/);
  delete h.wx.cloud;
  await assert.rejects(h.access.request('/api/meetings'), /升级微信/);
});

test('unsupported cloud sockets stop safely instead of throwing or retrying forever', () => {
  const context = { module: { exports: {} }, wx: { cloud: {} }, clearTimeout,
    require: name => name.endsWith('meeting-state') ? { state: { languagePair: ['zh', 'en'] } }
      : name.endsWith('/access') ? {} : { isCloudEnabled: () => true } };
  vm.runInNewContext(read('miniprogram/services/meeting-socket.js'), context);
  const socket = new context.module.exports(), events = [];
  socket.subscribe('event', event => events.push(event));
  socket.connect();
  assert.equal(socket.intentionalClose, true);
  assert.equal(events[0].retryable, false);
  assert.match(events[0].error.message, /升级微信/);
});

test('result page describes MySQL and COS as cloud storage, and local files as local service', () => {
  for (const [storage, expected] of [['mysql', '云端'], ['cos', '云端'], ['local', '本地服务']]) {
    let page;
    vm.runInNewContext(read('miniprogram/pages/meeting-result/meeting-result.js'), {
      Page: value => { page = value; }, require: name => name.endsWith('/layout') ? { safeTopPadding: () => 0 }
        : { state: { lastResult: { saved: true, storage, filename: 'test.md' } } },
    });
    page.setData = data => Object.assign(page.data, data); page.onLoad();
    assert.ok(page.data.message.includes(expected));
  }
});
