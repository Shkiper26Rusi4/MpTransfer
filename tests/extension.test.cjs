// Extension API contract tests with a stub Chrome. No real browser/site involved.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('extension/background.js','utf8');
function harness(tabs=[]) {
  const sent=[];const callbacks={};let connects=0;
  const chrome={
    runtime:{id:'unit-test-extension',getManifest:()=>({version:'0.1.0'}),
      connectNative:()=>{connects++;return {postMessage:m=>sent.push(m),onMessage:{addListener:f=>callbacks.native=f},onDisconnect:{addListener:f=>callbacks.disconnect=f}};},
      onMessage:{addListener:f=>callbacks.popup=f},onStartup:{addListener:()=>{}},onInstalled:{addListener:()=>{}}},
    tabs:{query:async()=>tabs,sendMessage:async()=>({path:'/observed-page',inventoryComplete:false})},
    scripting:{executeScript:async()=>[]},
    alarms:{create:()=>{},onAlarm:{addListener:f=>callbacks.alarm=f}}
  };
  vm.runInNewContext(source,{chrome,URL,Date});
  return {sent,callbacks,chrome,connects:()=>connects};
}
test('handshake and tabs command preserve tab ID without URL queries',async()=>{
  const h=harness([{id:4,title:'Авито',url:'https://www.avito.ru/profile?private=x',active:true}]);
  assert.equal(h.sent[0].event,'hello');
  await h.callbacks.native({id:'q',command:'tabs',expires:Date.now()/1000+10});
  const reply=h.sent.at(-1);
  assert.equal(reply.result.tabs[0].url,'https://www.avito.ru/profile');
  assert.equal(reply.result.tabs[0].id,4);
});
test('content script returns observed page',async()=>{
  const h=harness([{id:4,url:'https://www.avito.ru/profile',active:true}]);
  await h.callbacks.native({id:'q',command:'diagnose',expires:Date.now()/1000+10});
  assert.equal(h.sent.at(-1).result.path,'/observed-page');
  assert.equal(h.sent.at(-1).result.inventoryComplete,false);
});
test('no Avito tab reports exact reason',async()=>{
  const h=harness();
  await h.callbacks.native({id:'q',command:'diagnose',expires:Date.now()/1000+10});
  assert.match(h.sent.at(-1).error,/AVITO_TAB_NOT_FOUND/);
});
test('publishing and arbitrary commands are rejected',async()=>{
  const h=harness();
  await h.callbacks.native({id:'q',command:'publish',expires:Date.now()/1000+10});
  assert.match(h.sent.at(-1).error,/UNVERIFIED_SITE_ADAPTER/);
});
test('disconnect permits reconnect',()=>{
  const h=harness();h.callbacks.disconnect();h.callbacks.alarm({name:'reconnect'});
  assert.equal(h.connects(),2);
});
test('expired commands never execute',async()=>{
  const h=harness();
  await h.callbacks.native({id:'q',command:'tabs',expires:1});
  assert.match(h.sent.at(-1).error,/просрочена/);
});
