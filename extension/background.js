let port;
let lastError = 'Приложение ещё не подключено';
let busy = false;
let connected = false;

async function tabsList() {
  const tabs = await chrome.tabs.query({url:['https://avito.ru/*','https://*.avito.ru/*']});
  return tabs.map(t => ({id:t.id,title:t.title,url:new URL(t.url).origin+new URL(t.url).pathname,active:t.active}));
}
async function inspect(payload) {
  const tabs = await tabsList();
  const active = tabs.filter(t => t.active);
  let tab;
  if (payload.tabId !== undefined) tab = tabs.find(t => t.id === payload.tabId);
  else if (tabs.length === 1) tab = tabs[0];
  else if (active.length === 1) tab = active[0];
  if (!tabs.length) throw new Error('AVITO_TAB_NOT_FOUND: откройте «Мои объявления» в этом профиле Chrome');
  if (!tab) throw new Error('AVITO_TAB_AMBIGUOUS: сделайте нужную вкладку Авито активной');
  try {
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']});
    return await chrome.tabs.sendMessage(tab.id,{channel:'marketplace-transfer-v1',action:(payload.operation || 'inspect'),payload});
  } catch (error) { throw new Error('CONTENT_SCRIPT_FAILED: '+error.message); }
}
async function command(message) {
  if (message.command === 'tabs') return {tabs:await tabsList(),extensionId:chrome.runtime.id};
  if (['diagnose','inspect'].includes(message.command)) return inspect(message.payload || {});
  if (message.command === 'ping') return {version:chrome.runtime.getManifest().version,extensionId:chrome.runtime.id};
  throw new Error('UNVERIFIED_SITE_ADAPTER: команда недоступна до проверки интерфейса Авито');
}
function connect() {
  if (port) return;
  const current = chrome.runtime.connectNative('com.marketplace.transfer');
  port = current;
  connected = false;
  lastError = 'Проверяется связь с Python…';
  current.onDisconnect.addListener(() => {
    const cause = chrome.runtime.lastError?.message || 'Native Host закрыл соединение';
    if (port === current) { port=undefined;connected=false;lastError=cause; }
  });
  current.onMessage.addListener(async message => {
    connected=true;lastError='Chrome ↔ Native Host ↔ Python: подключено';
    if (message.command === 'heartbeat') return;
    if (busy) { current.postMessage({id:message.id,error:'Расширение занято'});return; }
    if (message.expires*1000 < Date.now()) { current.postMessage({id:message.id,error:'Команда просрочена'});return; }
    busy=true;
    try { current.postMessage({id:message.id,result:await command(message)}); }
    catch(error) { current.postMessage({id:message.id,error:error.message}); }
    finally { busy=false; }
  });
  current.postMessage({event:'hello',extensionId:chrome.runtime.id,version:chrome.runtime.getManifest().version});
}
chrome.runtime.onMessage.addListener((message,sender,respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.action === 'connect') connect();
  if (['status','connect'].includes(message.action)) respond({connected,status:lastError,extensionId:chrome.runtime.id});
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'reconnect') connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create('reconnect',{periodInMinutes:1});connect(); });
chrome.alarms.create('reconnect',{periodInMinutes:1});
connect();
