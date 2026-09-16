const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../miniprogram/services/meeting-socket.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(failure) {
  const requests = [], timers = new Map(), states = [], events = [], logs = [], handlers = {};
  let nextTimer = 0;
  const task = { send() {}, close() {}, onOpen: fn => handlers.open = fn,
    onMessage: fn => handlers.message = fn, onClose: fn => handlers.close = fn, onError: fn => handlers.error = fn };
  const context = { module: { exports: {} }, console: { warn: (...args) => logs.push(args.join(' ')) },
    setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer; }, clearTimeout: id => timers.delete(id),
    require: name => name.endsWith('meeting-state') ? { state: { languagePair: ['zh', 'en'], startedAt: 'meeting-one' } }
      : name.endsWith('/access') ? { token: () => 'secret-token' }
      : { CLOUD_ENV_ID: 'production', CLOUD_SERVICE: 'api', cloudConfig: () => ({ env: 'production' }) },
    wx: { cloud: { connectContainer: options => {
      requests.push(options);
      return failure ? Promise.reject(failure) : Promise.resolve({ socketTask: task });
    } } },
  };
  vm.runInNewContext(source, context);
  const socket = new context.module.exports();
  socket.subscribe('state', event => states.push(event));
  socket.subscribe('event', event => events.push(event));
  return { socket, requests, states, events, logs, handlers, timers, task };
}

test('socket explicitly selects the same cloud environment and never places credentials in URL', async () => {
  const h = harness(); h.socket.connect(); await flush();
  assert.equal(h.requests[0].config.env, 'production');
  assert.equal(h.requests[0].service, 'api');
  assert.equal(h.requests[0].timeout, 20000);
  assert.ok(!h.requests[0].path.includes('secret-token'));
  h.socket.close();
});

test('handshake permission failures stop immediately and keep a useful error code', async () => {
  const h = harness({ errMsg: 'connectSocket:fail Unexpected server response: 403' });
  h.socket.connect(); await flush();
  assert.equal(h.timers.size, 0);
  assert.equal(h.events.at(-1).retryable, false);
  assert.match(h.events.at(-1).error.message, /403/);
  assert.equal(h.socket.intentionalClose, true);
});

test('transient failures retain their cause, stop after five retries and permit manual restart', async () => {
  const h = harness({ errMsg: 'connectContainer:fail timeout', errCode: -1 });
  h.socket.connect(); await flush();
  assert.match(h.states.at(-1).message, /超时.*自动重连/);
  for (let i = 0; i < 5; i++) {
    assert.equal(h.timers.size, 1);
    const [id, fn] = h.timers.entries().next().value; h.timers.delete(id); fn(); await flush();
  }
  assert.equal(h.requests.length, 6);
  assert.equal(h.timers.size, 0);
  assert.match(h.events.at(-1).error.message, /暂停自动重试/);
  h.socket.connect(); await flush();
  assert.equal(h.timers.size, 1);
  h.socket.close();
});

test('rate limiting and policy closes do not enter a reconnect loop', async () => {
  for (const policy of [true, false]) {
    const h = harness(); h.socket.connect(); await flush();
    if (policy) h.handlers.close({ code: 1008 });
    else h.handlers.message({ data: JSON.stringify({ type: 'connection.rate_limited' }) });
    assert.equal(h.timers.size, 0);
    assert.equal(h.events.at(-1).retryable, false);
  }
});

test('late failures from a replaced connection cannot overwrite current state', async () => {
  const h = harness(); h.socket.connect(); await flush();
  const stale = { ...h.handlers };
  h.socket.connect(); await flush();
  const stateCount = h.states.length;
  stale.error({ errMsg: 'timeout' }); stale.close({ code: 1006 });
  assert.equal(h.states.length, stateCount);
  assert.equal(h.timers.size, 0);
  h.socket.close();
});

test('diagnostics contain phase and code but no SDK credential or signed URL', async () => {
  const h = harness({ errMsg: 'connectContainer:fail timeout https://example.com/?token=secret-token', errCode: -1 });
  h.socket.connect(); await flush();
  const log = h.logs.join('\n');
  assert.match(log, /cloud_connect/);
  assert.match(log, /-1/);
  assert.doesNotMatch(log, /secret-token|example.com/);
  h.socket.close();
});

test('authentication send failures schedule recovery before audio can start', async () => {
  const h = harness();
  h.task.send = ({ fail }) => fail({ errMsg: 'socket send failed' });
  h.socket.connect(); await flush(); h.handlers.open();
  assert.equal(h.timers.size, 1);
  assert.equal(h.states.at(-1).state, 'reconnecting');
  assert.match(h.states.find(item => item.state === 'reconnecting').message, /认证消息发送失败/);
  h.socket.close();
});
