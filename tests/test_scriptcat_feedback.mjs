import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../xarchive/integration/xarchive.user.js',import.meta.url),'utf8');
function harness(resume=false,server='https://archive.test',schedule,migrated=false) {
  const nodes=new Map(), requests=[], timers=new Map(), intervals=[], values=new Map([['server',server],['token','test-only']]);
  if(resume)values.set('activeTask',{server,id:'job'});
  if(schedule!==undefined)values.set('schedule',schedule);
  if(migrated)values.set('quarterlyDefaultV1',true);
  let now=1_792_800_000_000, serial=0;
  const node=id=>{
    if(!nodes.has(id))nodes.set(id,{hidden:['section','settings-view','feedback'].includes(id),disabled:false,textContent:'',innerHTML:'',value:'',style:{setProperty(){}},
      classList:{values:new Set(),toggle(key,on){on?this.values.add(key):this.values.delete(key);},contains(key){return this.values.has(key);}},setAttribute(){}});
    return nodes.get(id);
  };
  const root={getElementById:node,querySelector:()=>node('section'),set innerHTML(value){}};
  const host={style:{setProperty(){}},attachShadow:()=>root};
  const window={addEventListener(){}};window.top=window.self=window;
  const context={window,document:{createElement:()=>host,body:{appendChild(){}},querySelectorAll:()=>[],addEventListener(){}},
    location:{href:'https://x.com/example/status/123',pathname:'/example/status/123'},innerWidth:1920,innerHeight:1080,
    URL,TextEncoder,btoa:value=>Buffer.from(value,'binary').toString('base64'),
    Date:class extends Date{static now(){return now;}},
    GM_getValue:(key,fallback)=>values.get(key)??fallback,GM_setValue:(key,value)=>values.set(key,value),GM_registerMenuCommand(){},GM_openInTab(){},
    GM_xmlhttpRequest:options=>requests.push(options),
    setInterval:fn=>intervals.push(fn),clearInterval(){},setTimeout:(fn,delay)=>{timers.set(++serial,{fn,delay});return serial;},clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(source,context);
  const reply=(index,data,status=200)=>requests[index].onload({status,responseText:JSON.stringify(data)});
  const flush=async()=>{for(let n=0;n<5;n++)await Promise.resolve();};
  return {node,requests,timers,intervals,values,reply,flush,advance:ms=>{now+=ms;intervals.forEach(fn=>fn());},runTimer:()=>{const [id,t]=timers.entries().next().value;timers.delete(id);t.fn();}};
}
const app=harness();
assert.equal(app.node('schedule').value,'quarterly','New script connections default to quarterly');
app.reply(0,{ok:true,method:'password'});await app.flush();
assert.equal(app.node('post').disabled,false);
const submit=app.node('post').onclick();
app.node('post').onclick();
assert.equal(app.requests.filter(r=>r.method==='POST').length,1,'Double click must submit once');
assert.equal(app.node('post').classList.contains('primary'),true,'Click becomes active before the network replies');
assert.equal(app.node('toggle').classList.contains('active'),true);
assert.equal(app.node('post').disabled,true);
assert.equal(app.node('feedback').hidden,false);
assert.match(app.node('status').textContent,/正在提交/);
app.advance(181_000);
assert.equal(app.node('elapsed').textContent,'已用时 3:01','A slow response still has live feedback');
const task={id:'job',user:'example',post_id:'123',created:1_792_800_000,status:'queued',progress:0,scanned_posts:0};
app.reply(1,{task});await submit;await app.flush();
assert.equal(app.requests[2].url,'https://archive.test/api/v1/tasks/job','Poll starts immediately after acceptance');
assert.match(app.node('status').textContent,/队列/);
app.reply(2,{task:{...task,status:'running',scanned_posts:3,progress:1}});await app.flush();
assert.match(app.node('status').textContent,/已读取 3.*已保存 1/);
assert.equal([...app.timers.values()][0].delay,1000);
app.runTimer();app.requests[3].onerror();await app.flush();
assert.match(app.node('status').textContent,/重新获取进度/);
assert.equal([...app.timers.values()][0].delay,3000);
app.runTimer();app.reply(4,{task:{...task,status:'succeeded',new_posts:1,new_media:1,finished:1_792_800_182}});await app.flush();
assert.match(app.node('status').textContent,/已完成/);
assert.equal(app.node('progress').hidden,true);
assert.equal(app.node('post').disabled,false);
assert.equal(app.values.get('activeTask'),null);
assert.equal(app.requests.filter(r=>r.method==='POST').length,1,'Reconnecting must never resubmit');
const resumed=harness(true);
resumed.reply(0,{ok:true,method:'password'});await resumed.flush();
assert.equal(resumed.requests[1].url,'https://archive.test/api/v1/tasks/job','Refresh resumes an existing task');
resumed.reply(1,{error:'任务不存在'},404);await resumed.flush();
assert.equal(resumed.values.get('activeTask'),null,'A removed task must not be resumed forever');
assert.equal(resumed.timers.size,0);
assert.equal(resumed.node('status').textContent,'任务不存在');
for(const server of ['http://archive.test','http://nas.example:8787','http://9.255.255.255','http://11.0.0.0',
  'http://172.15.255.255','http://172.32.0.0','http://192.167.255.255','http://192.169.0.0','http://169.254.1.1',
  'http://10.20.30.40.evil.test','http://172.16.0.1.evil.test','http://192.168.1.1.evil.test','http://localhost.evil.test',
  'http://10.0.0.1e1','http://172.16.0.1e1','http://192.168.0.1e1']){
  const legacy=harness(true,server);await legacy.flush();
  assert.equal(legacy.requests.length,0,'Public or disguised HTTP settings must never send stored credentials');
  assert.match(legacy.node('status').textContent,/HTTPS/);
  legacy.node('server').value=server;legacy.node('credential').value='test-replacement-only';
  await legacy.node('settings-view').onsubmit({preventDefault(){}});
  assert.equal(legacy.requests.length,0,'Saving public or disguised HTTP must fail before verification sends credentials');
  assert.match(legacy.node('status').textContent,/HTTPS/);
  assert.equal(legacy.values.get('server'),server,'Insecure old settings are not silently migrated');
  assert.equal(legacy.values.get('token'),'test-only','Rejected settings do not replace the stored credential');
  assert.equal(legacy.node('status').textContent.includes('test-replacement-only'),false);
}
for(const server of ['http://localhost:8787','http://127.0.0.1:8787','http://[::1]:8787',
  'http://10.0.0.0:8787','http://10.20.30.40:8787','http://10.255.255.255:8787',
  'http://172.16.0.0:8787','http://172.31.255.255:8787','http://192.168.0.0:8787','http://192.168.255.255:8787']){
  const local=harness(false,server);
  assert.equal(local.requests.length,1,'Loopback and private IPv4 HTTP must remain available');
  assert.equal(local.requests[0].url,server+'/api/v1/connection/check');
  local.reply(0,{ok:true,method:'password'});await local.flush();
  local.node('server').value=server;local.node('credential').value='';
  const save=local.node('settings-view').onsubmit({preventDefault(){}});
  local.reply(1,{ok:true,method:'password'});await save;
  assert.equal(local.values.get('server'),server);
}
const secure=harness();secure.reply(0,{ok:true,method:'password'});await secure.flush();
secure.node('server').value='https://secure.example';secure.node('credential').value='test-secure-only';
const secureSave=secure.node('settings-view').onsubmit({preventDefault(){}});
assert.equal(secure.requests[1].url,'https://secure.example/api/v1/connection/check');
secure.reply(1,{ok:true,method:'password'});await secureSave;
assert.equal(secure.values.get('server'),'https://secure.example');
for(const [schedule,migrated,expected] of [['monthly',false,'quarterly'],['daily',false,'quarterly'],[undefined,false,'quarterly'],
  ['weekly',true,'weekly'],['monthly',true,'monthly'],['yearly',true,'yearly'],['daily',true,'daily']]){
  const legacySchedule=harness(false,'https://archive.test',schedule,migrated);
  assert.equal(legacySchedule.node('schedule').value,expected,'The quarterly default migration runs once, then preserves manual choices');
  assert.equal(legacySchedule.values.get('quarterlyDefaultV1'),true);
  assert.equal(legacySchedule.values.get('schedule'),expected);
  assert.equal(legacySchedule.values.get('server'),'https://archive.test');
  assert.equal(legacySchedule.values.get('token'),'test-only','Changing the default must not touch connection credentials');
  if(expected==='daily')assert.match(legacySchedule.node('schedule').innerHTML,/每天（旧计划）/);
  legacySchedule.reply(0,{ok:true,method:'password'});await legacySchedule.flush();
  const submitted=legacySchedule.node('profile').onclick();
  assert.equal(JSON.parse(legacySchedule.requests[1].data).schedule,expected);
  legacySchedule.reply(1,{task:{id:'legacy-schedule',user:'example',status:'queued',created:Date.now()/1000}});await submitted;
}
console.log('ScriptCat feedback, polling, resume, HTTPS and private IPv4 HTTP boundary checks passed');
