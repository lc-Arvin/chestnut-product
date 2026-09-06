const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const languages = require('../miniprogram/utils/languages');
const state = require('../miniprogram/services/meeting-state');
const root = path.join(__dirname, '..');

function loadPage(file) {
  let page;
  const context = vm.createContext({
    Page: value => { page = value; },
    require: name => {
      if (name.endsWith('/languages')) return languages;
      if (name.endsWith('/meeting-state')) return state;
      if (name.endsWith('/layout')) return { safeTopPadding: () => 50 };
      if (name.endsWith('/time')) return { formatTime: () => '00:00:00' };
      return {};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context);
  page.data = { ...page.data };
  page.setData = data => Object.assign(page.data, data);
  page.languagePair = ['ja', 'fr'];
  page.entrySequence = 0;
  return page;
}

test('mini program defaults, explicit choice survives start, completed meeting resets', () => {
  state.reset();
  assert.deepEqual(state.state.languagePair, ['zh', 'en']);
  state.state.languagePair = ['ja', 'fr'];
  state.start();
  assert.deepEqual(state.state.languagePair, ['ja', 'fr']);
  state.addEntry({ language: 'fr', text: 'Bonjour' });
  assert.equal(state.state.entries[0].language, 'fr');
  state.reset();
  assert.deepEqual(state.state.languagePair, ['zh', 'en']);
});

test('mini program routes both directions and ignores unrelated originals', () => {
  state.reset();
  const page = loadPage('miniprogram/pages/live/live.js');
  page.handleRealtimeEvent({ type: 'conversation.item.input_audio_transcription.completed', language: 'ja', transcript: 'こんにちは' });
  page.handleRealtimeEvent({ type: 'response.text.done', translation_target: 'fr', text: 'Bonjour' });
  page.handleRealtimeEvent({ type: 'conversation.item.input_audio_transcription.completed', language: 'fr', transcript: 'Merci' });
  page.handleRealtimeEvent({ type: 'response.text.done', translation_target: 'ja', text: 'ありがとう' });
  page.handleRealtimeEvent({ type: 'conversation.item.input_audio_transcription.completed', language: 'de', transcript: 'Danke' });
  assert.equal(page.data.chineseEntries.length, 2);
  assert.equal(page.data.englishEntries.length, 2);
  assert.deepEqual(state.state.entries.map(x => x.language), ['ja', 'fr', 'fr', 'ja']);
});

test('mini program reconnect keeps pair for local and cloud connections', async () => {
  for (const cloud of [false, true]) {
    const urls = [];
    const socketTask = { onOpen() {}, onMessage() {}, onError() {}, onClose() {}, close() {} };
    const context = vm.createContext({
      module: { exports: {} }, setTimeout, clearTimeout,
      require: name => name.endsWith('meeting-state') ? state : {
        isCloudEnabled: () => cloud, websocketUrl: () => 'ws://localhost/ws', CLOUD_SERVICE: 'test',
      },
      wx: {
        connectSocket: ({ url }) => { urls.push(url); return socketTask; },
        cloud: { connectContainer: ({ path }) => { urls.push(path); return Promise.resolve({ socketTask }); } },
      },
    });
    vm.runInContext(fs.readFileSync(path.join(root, 'miniprogram/services/meeting-socket.js'), 'utf8'), context);
    state.state.languagePair = ['ja', 'fr'];
    const socket = new context.module.exports();
    socket.connect();
    await Promise.resolve();
    state.state.languagePair = ['zh', 'en'];
    socket.connect();
    await Promise.resolve();
    assert.equal(urls.length, 2);
    for (const url of urls) assert.equal(new URL(url, 'http://localhost').searchParams.get('languages'), 'ja,fr');
    socket.close();
  }
});

function webContext() {
  const elements = new Map();
  function element() {
    return {
      textContent: '', children: [], dataset: {}, options: [], value: '',
      addEventListener() {}, removeAttribute() {},
      append(...nodes) { this.children.push(...nodes); },
      replaceChildren(...nodes) { this.children = nodes; },
      classList: { contains: () => false, toggle() {}, add() {}, remove() {} },
      querySelector() { return element(); },
    };
  }
  const context = vm.createContext({
    document: {
      querySelector(key) {
        if (!elements.has(key)) elements.set(key, element());
        return elements.get(key);
      },
      createElement: element, createTextNode: text => ({ textContent: text }),
    },
    fetch: () => new Promise(() => {}),
    window: {}, URLSearchParams,
  });
  context.document.querySelector('#language-first').value = 'zh';
  context.document.querySelector('#language-second').value = 'en';
  vm.runInContext(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), context);
  return { context, elements };
}

test('web keeps Chinese/English default and routes arbitrary pair without relabeling', () => {
  const { context, elements } = webContext();
  assert.equal(vm.runInContext('activeLanguagePair.join(",")', context), 'zh,en');
  vm.runInContext('activeLanguagePair = ["ja", "fr"]', context);
  for (const event of [
    { type: 'conversation.item.input_audio_transcription.completed', language: 'ja', transcript: 'こんにちは' },
    { type: 'response.text.done', translation_target: 'fr', text: 'Bonjour' },
    { type: 'conversation.item.input_audio_transcription.completed', language: 'fr', transcript: 'Merci' },
    { type: 'response.text.done', translation_target: 'ja', text: 'ありがとう' },
    { type: 'conversation.item.input_audio_transcription.completed', language: 'de', transcript: 'Danke' },
  ]) context.handleRealtimeEvent(event);
  assert.equal(elements.get('#chinese-history').children.length, 2);
  assert.equal(elements.get('#english-history').children.length, 2);
  assert.equal(vm.runInContext('meetingRecords.map(x => x.language).join(",")', context), 'ja,fr,fr,ja');
  vm.runInContext('setCurrentCaption("未完", "ja", "original"); capturePendingCaption(chineseCurrent, activeLanguagePair[0])', context);
  assert.equal(vm.runInContext('meetingRecords.at(-1).language', context), 'ja');
});

test('native mini-program catalog stays in sync with server JSON', () => {
  assert.deepEqual(languages.labels, require('../shared/languages.json'));
});

test('Cantonese and Chinese stay in separate web columns and pending records', () => {
  const { context, elements } = webContext();
  vm.runInContext('activeLanguagePair = ["yue", "zh"]', context);
  context.handleRealtimeEvent({ type: 'conversation.item.input_audio_transcription.completed', language: 'yue', transcript: '我哋開會' });
  context.handleRealtimeEvent({ type: 'response.text.done', translation_target: 'zh', text: '我们开会' });
  assert.equal(elements.get('#chinese-history').children.length, 1);
  assert.equal(elements.get('#english-history').children.length, 1);
  assert.equal(vm.runInContext('meetingRecords.map(x => x.language).join(",")', context), 'yue,zh');
  vm.runInContext('setCurrentCaption("未完", "yue", "original"); capturePendingCaption(chineseCurrent, activeLanguagePair[0])', context);
  assert.equal(vm.runInContext('meetingRecords.at(-1).language', context), 'yue');
});

test('mini program supports Cantonese pair in either order without losing source identity', () => {
  for (const pair of [['yue', 'zh'], ['zh', 'yue']]) {
    state.reset();
    state.state.languagePair = pair;
    const page = loadPage('miniprogram/pages/live/live.js');
    page.languagePair = pair;
    page.handleRealtimeEvent({ type: 'conversation.item.input_audio_transcription.completed', language: 'yue', transcript: '我哋開會' });
    page.handleRealtimeEvent({ type: 'response.text.done', translation_target: 'zh', text: '我们开会' });
    assert.equal(page.data.chineseEntries.length, 1);
    assert.equal(page.data.englishEntries.length, 1);
    assert.deepEqual(state.state.entries.map(x => x.language), ['yue', 'zh']);
    assert.equal(state.state.entries[0].text, '我哋開會');
  }
});
