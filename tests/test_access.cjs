const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const source = file => fs.readFileSync(path.join(root, file), 'utf8');
const state = require('../miniprogram/services/meeting-state');

function web(fetch) {
  const elements = new Map();
  const element = () => ({ value:'', hidden:true, disabled:false, options:[], children:[], dataset:{},
    addEventListener(){}, focus(){}, setAttribute(){}, removeAttribute(){},
    replaceChildren(...x){this.children=x;}, append(...x){this.children.push(...x);},
    classList:{contains:()=>false, toggle(){}, add(){}, remove(){}}, querySelector(){return element();} });
  const document = { querySelector(key){if(!elements.has(key)) elements.set(key,element());return elements.get(key);},createElement:element };
  const c=vm.createContext({ document, fetch:(url,...args)=> url==='/api/languages' ? new Promise(()=>{}) : fetch(url,...args),
    window:{setTimeout:fn=>fn(),localStorage:{getItem:()=> 'client-test-one'}}, URLSearchParams });
  vm.runInContext(source('app.js'),c);
  vm.runInContext('var audioStarts=0; beginAudioCheck=async()=>{audioStarts++}',c);
  return {c,e:elements,run:code=>vm.runInContext(code,c)};
}
const response = (data, status=200)=>({ok:status>=200&&status<300,status,json:async()=>data,text:async()=>JSON.stringify(data)});

test('Web landing is public; cancel preserves pair and does not start audio', async()=>{
  let calls=0;
  const {c,e,run}=web(async()=>{calls++;return response({auth_required:true,authenticated:false});});
  assert.equal(calls,0);
  e.get('#language-first').value='yue';e.get('#language-second').value='zh';
  await c.initializeAccess();
  assert.equal(e.get('#access-gate').hidden,false);
  assert.equal(run('audioStarts'),0);
  c.cancelAccess();
  assert.equal(e.get('#access-gate').hidden,true);
  assert.equal(e.get('#language-first').value,'yue');
  assert.equal(run('audioStarts'),0);
});

test('Web wrong code stays in dialog; valid code continues once',async()=>{
  let valid=false;
  const {c,e,run}=web(async url=>url.endsWith('/status')?response({auth_required:true,authenticated:false}):response(valid?{authenticated:true}:{error:'Incorrect'},valid?200:401));
  await c.initializeAccess();
  e.get('#invite-code').value='bad';
  await c.submitInvitation({preventDefault(){}});
  assert.equal(run('audioStarts'),0);
  assert.equal(e.get('#access-error').textContent,'Incorrect');
  valid=true;
  await c.submitInvitation({preventDefault(){}});
  assert.equal(run('audioStarts'),1);
  assert.equal(e.get('#access-gate').hidden,true);
});

test('Web valid session bypasses dialog; cancellation ignores late verification',async()=>{
  const good=web(async()=>response({auth_required:true,authenticated:true}));
  await good.c.initializeAccess();assert.equal(good.run('audioStarts'),1);
  let finish;
  const pending=web(async url=>url.endsWith('/status')?response({auth_required:true,authenticated:false}):new Promise(resolve=>{finish=resolve;}));
  await pending.c.initializeAccess();
  const attempt=pending.c.submitInvitation({preventDefault(){}});
  pending.c.cancelAccess();finish(response({authenticated:true}));await attempt;
  assert.equal(pending.run('audioStarts'),0);
});

test('Web failed save survives login and retries original payload without audio',async()=>{
  let fail=true;const bodies=[];
  const {c,run}=web(async(url,opts)=>{
    if(url.endsWith('/status'))return response({auth_required:true,authenticated:false});
    if(url.endsWith('/invite'))return response({authenticated:true});
    bodies.push(JSON.parse(opts.body));return fail?response({error:'expired'},401):response({filename:'saved.md'});
  });
  run('pendingTranscript={entries:[{text:"hello"}],ended_at:"fixed"}');
  await c.saveMeetingTranscript();assert.ok(run('pendingTranscript'));
  await c.initializeAccess('save');fail=false;
  await c.submitInvitation({preventDefault(){}});
  assert.equal(run('pendingTranscript'),null);assert.equal(run('audioStarts'),0);
  assert.deepEqual(bodies[0],bodies[1]);
});

function miniSetup(access, save=async()=>{}) {
  state.reset();let page;const visits=[];let mic=0;
  const c=vm.createContext({Page:value=>page=value,wx:{getDeviceInfo:()=>({platform:'devtools'}),navigateTo:o=>visits.push(o.url),showToast(){},showModal(){}},
    require:name=> {
      if(name.endsWith('/access'))return access;
      if(name.endsWith('/meeting-api'))return {saveMeeting:save};
      if(name.endsWith('/meeting-state'))return state;
      if(name.endsWith('/languages'))return require('../miniprogram/utils/languages');
      if(name.endsWith('/environment'))return {isCloudEnabled:()=>true,getServerHost:()=> 'localhost'};
      if(name.endsWith('/recorder'))return {stop(){},start(){mic++;}};
      if(name.endsWith('/layout'))return {safeTopPadding:()=>50};
      throw Error(name);
    }});
  vm.runInContext(source('miniprogram/pages/setup/setup.js'),c);page.setData=data=>Object.assign(page.data,data);
  return {page,visits,mic:()=>mic};
}
test('Mini homepage is public; start shows invite; cancel keeps language choice',async()=>{
  let checks=0;const {page,visits,mic}=miniSetup({authorized:async()=>{checks++;return false;}});
  page.onShow();assert.equal(checks,0);
  state.state.languagePair=['yue','zh'];await page.startMeeting();
  assert.equal(page.data.inviteVisible,true);assert.equal(visits.length,0);assert.equal(mic(),0);
  page.cancelInvite();assert.deepEqual(state.state.languagePair,['yue','zh']);
  assert.equal(page.data.inviteVisible,false);
  await page.startMeeting();await page.verified();assert.deepEqual(visits,['/pages/audio-check/audio-check']);
});
test('Mini valid credentials continue; failed save blocks overwrite and can retry',async()=>{
  let fail=true;const {page,visits}=miniSetup({authorized:async()=>true},async()=>{if(fail)throw Error('offline');});
  await page.startMeeting();assert.equal(visits.length,1);
  state.state.pendingPayload={entries:[{text:'keep'}]};
  await page.startMeeting();assert.equal(visits.length,1);
  await page.runAuthorized('save');assert.ok(state.state.pendingPayload);
  fail=false;await page.runAuthorized('save');assert.equal(state.state.pendingPayload,null);
});
test('Mini invite cancel ignores late verification response',async()=>{
  let component,finish;const events=[];
  const c=vm.createContext({Component:x=>component=x,require:()=>({login:()=>new Promise(resolve=>finish=resolve)})});
  vm.runInContext(source('miniprogram/components/invite-dialog/invite-dialog.js'),c);
  const dialog={...component.methods,data:{code:'good',busy:false,error:''},setData(x){Object.assign(this.data,x);},triggerEvent:x=>events.push(x)};
  const p=dialog.submit();dialog.cancel();finish();await p;assert.deepEqual(events,['cancel']);
});
test('Mini cloud socket authenticates with first message and stops reconnect on denial',async()=>{
  let open,message,close;const sent=[];let timers=0;
  const task={send:x=>sent.push(JSON.parse(x.data)),onOpen:fn=>open=fn,onMessage:fn=>message=fn,onClose:fn=>close=fn,onError(){},close(){}};
  const c=vm.createContext({module:{exports:{}},setTimeout:()=>{timers++;},clearTimeout(){},
    wx:{cloud:{connectContainer:async()=>({socketTask:task})}},
    require:name=>name.endsWith('/access')?{token:()=> 'signed-credential'}:name.endsWith('meeting-state')?state:{isCloudEnabled:()=>true,CLOUD_SERVICE:'api'}});
  vm.runInContext(source('miniprogram/services/meeting-socket.js'),c);
  const socket=new c.module.exports();socket.connect();await Promise.resolve();open();
  assert.deepEqual(sent,[{type:'auth.authenticate',token:'signed-credential'}]);
  message({data:JSON.stringify({type:'access.denied'})});close();assert.equal(timers,0);
});

test('Mini HTTP authorization persists credential and attaches it to save requests',async()=>{
  const storage=new Map();const requests=[];let status=200;
  const c=vm.createContext({module:{exports:{}},wx:{
    getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    cloud:{callContainer:async req=>{requests.push(req);return {statusCode:status,data:req.path.endsWith('/invite')?{access_token:'signed',authenticated:true}:{error:'expired'}};}}
  },require:()=>({isCloudEnabled:()=>true,CLOUD_ENV_ID:'env',CLOUD_SERVICE:'api',cloudConfig:()=>({env:'env'})})});
  vm.runInContext(source('miniprogram/services/access.js'),c);
  const access=c.module.exports;
  await access.login('invite');assert.equal(access.token(),'signed');
  await access.request('/api/meetings','POST',{entries:[]});
  assert.equal(requests[1].header.Authorization,'Bearer signed');
  status=401;await assert.rejects(access.request('/api/meetings','POST',{}));
  assert.equal(access.token(),'');
});

test('Mini direct audio-page entry checks access before microphone subscription',async()=>{
  let page;let subscriptions=0;let redirects=0;
  const c=vm.createContext({Page:x=>page=x,wx:{redirectTo:()=>redirects++},
    require:name=>name.endsWith('/access')?{authorized:async()=>false}:name.endsWith('/layout')?{safeTopPadding:()=>0}:name.endsWith('/recorder')?{subscribe:()=>subscriptions++}:{} });
  vm.runInContext(source('miniprogram/pages/audio-check/audio-check.js'),c);
  await page.onLoad();assert.equal(subscriptions,0);assert.equal(redirects,1);
});

test('Mini invitation errors are English and keep the dialog retryable', async()=>{
  let component;let failure;let calls=0;const events=[];
  const c=vm.createContext({Component:x=>component=x,require:()=>({login:async()=>{calls++;if(failure)throw failure;}})});
  vm.runInContext(source('miniprogram/components/invite-dialog/invite-dialog.js'),c);
  const dialog={...component.methods,data:{code:'  ',busy:false,error:''},setData(x){Object.assign(this.data,x);},triggerEvent:x=>events.push(x)};
  await dialog.submit();assert.equal(calls,0);assert.equal(dialog.data.error,'Enter your invitation code.');
  for (const [error,expected] of [
    [{status:401,message:'邀请码错误'},'Invalid invitation code. Please try again.'],
    [{status:429},'Too many attempts. Please wait and try again.'],
    [{errMsg:'request:fail'},'Unable to verify your code. Please try again.'],
    [{status:500,message:'内部错误'},'Unable to verify your code. Please try again.'],
  ]) {
    dialog.input({detail:{value:'try-again'}});assert.equal(dialog.data.error,'');
    failure=error;await dialog.submit();assert.equal(dialog.data.error,expected);
    assert.equal(dialog.data.busy,false);assert.deepEqual(events,[]);
  }
  failure=null;await dialog.submit();assert.deepEqual(events,['verified']);
});

test('Mini invitation submits once while verification is pending',async()=>{
  let component,finish;let calls=0;
  const c=vm.createContext({Component:x=>component=x,require:()=>({login:()=>{calls++;return new Promise(resolve=>finish=resolve);}})});
  vm.runInContext(source('miniprogram/components/invite-dialog/invite-dialog.js'),c);
  const dialog={...component.methods,data:{code:'good',busy:false,error:''},setData(x){Object.assign(this.data,x);},triggerEvent(){}};
  const pending=dialog.submit();await dialog.submit();assert.equal(calls,1);
  finish();await pending;assert.equal(dialog.data.busy,false);
});
