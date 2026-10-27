import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../xarchive/integration/xarchive.user.js',import.meta.url),'utf8');
function harness(resume=false,server='https://archive.test',schedule,migrated=false) {
  const nodes=new Map(), requests=[], timers=new Map(), intervals=[], renderedIds=new Set(), opened=[], listeners=new Map(), values=new Map([['server',server],['token','test-only']]);
  if(resume)values.set('activeTask',{server,id:'job'});
  if(schedule!==undefined)values.set('schedule',schedule);
  if(migrated)values.set('quarterlyDefaultV1',true);
  let now=1_792_800_000_000, serial=0;
  const node=id=>{
    if(!nodes.has(id))nodes.set(id,{hidden:['section','settings-view','feedback'].includes(id),disabled:false,textContent:'',innerHTML:'',value:'',style:{setProperty(){}},attributes:new Map(),
      classList:{values:new Set(),toggle(key,on){on?this.values.add(key):this.values.delete(key);},contains(key){return this.values.has(key);}},setAttribute(key,value){this.attributes.set(key,value);}});
    return nodes.get(id);
  };
  const root={getElementById:node,querySelector:()=>node('section'),set innerHTML(value){for(const [,id] of value.matchAll(/\bid="([^"]+)"/g))renderedIds.add(id);}};
  const host={style:{setProperty(){}},attachShadow:()=>root};
  const window={addEventListener(){}};window.top=window.self=window;
  const context={window,document:{createElement:()=>host,body:{appendChild(){}},querySelectorAll:()=>[],addEventListener:(name,handler)=>listeners.set(name,handler)},
    location:{href:'https://x.com/example/status/123',pathname:'/example/status/123'},innerWidth:1920,innerHeight:1080,
    URL,TextEncoder,btoa:value=>Buffer.from(value,'binary').toString('base64'),
    Date:class extends Date{static now(){return now;}},
    GM_getValue:(key,fallback)=>values.get(key)??fallback,GM_setValue:(key,value)=>values.set(key,value),GM_registerMenuCommand(){},GM_openInTab:(...args)=>opened.push(args),
    GM_xmlhttpRequest:options=>requests.push(options),
    setInterval:fn=>intervals.push(fn),clearInterval(){},setTimeout:(fn,delay)=>{timers.set(++serial,{fn,delay});return serial;},clearTimeout:id=>timers.delete(id)};
  vm.runInNewContext(source,context);
  const reply=(index,data,status=200)=>requests[index].onload({status,responseText:JSON.stringify(data)});
  const flush=async()=>{for(let n=0;n<5;n++)await Promise.resolve();};
  const navigate=path=>{context.location.pathname=path;context.location.href='https://x.com'+path;intervals.forEach(fn=>fn());};
  const hover=url=>listeners.get('pointerover')({target:{closest:()=>({querySelectorAll:()=>[{href:url,querySelector:()=>true}]})}});
  return {node,requests,timers,intervals,renderedIds,opened,values,reply,flush,navigate,hover,advance:ms=>{now+=ms;intervals.forEach(fn=>fn());}};
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
assert.equal(app.node('post').textContent,'正在提交…');
assert.match(app.node('status').textContent,/正在提交/);
for(const id of ['feedback','task-subject','progress','elapsed','tasks'])assert.equal(app.renderedIds.has(id),false,'Submission must not render a background task card');
app.advance(181_000);
assert.match(app.node('status').textContent,/正在提交/,'A pending response keeps only submission feedback');
const task={id:'job',user:'example',post_id:'123',created:1_792_800_000,status:'queued',progress:0,scanned_posts:0};
app.reply(1,{task});await submit;await app.flush();
assert.match(app.node('status').textContent,/已提交.*后台/);
assert.equal(app.node('post').disabled,false);
assert.equal(app.node('profile').disabled,false,'An accepted post must not block another submission');
assert.equal(app.node('post').textContent,'已提交');
assert.equal(app.node('toggle').classList.contains('active'),false,'The dock must stop showing busy after acceptance');
assert.equal(app.node('toggle').attributes.get('aria-busy'),'false');
app.advance(60_000);
assert.equal(app.requests.length,2,'Acceptance must not fetch or poll task progress');
assert.equal(app.timers.size,0);
const duplicate=app.node('post').onclick();
app.reply(2,{task,duplicate:true});await duplicate;
assert.match(app.node('status').textContent,/已在队列/);
app.navigate('/other/status/456');
assert.equal(app.node('post').textContent,'下载当前帖子');
const next=app.node('post').onclick();
app.navigate('/third/status/789');
assert.equal(app.node('post').disabled,true,'Only the in-flight submit prevents concurrent clicks');
app.node('profile').onclick();
assert.equal(app.requests.length,4);
app.reply(3,{task:{...task,user:'other',post_id:'456'}});await next;
assert.equal(app.node('post').disabled,false);
assert.equal(app.node('post').classList.contains('primary'),false,'A slow reply must not select a different post');
const third=app.node('post').onclick();
assert.equal(JSON.parse(app.requests[4].data).url,'https://x.com/third/status/789');
assert.equal(JSON.parse(app.requests[4].data).schedule,'once');
app.reply(4,{task:{...task,user:'third',post_id:'789'}});await third;
app.node('archive').onclick();
assert.equal(app.opened[0][0],'https://archive.test/?post=789');
app.navigate('/home');app.hover('https://x.com/hovered/status/987');
assert.match(app.node('subject').textContent,/@hovered/);
const hovered=app.node('post').onclick();
assert.equal(JSON.parse(app.requests[5].data).url,'https://x.com/hovered/status/987');
app.reply(5,{task:{...task,user:'hovered',post_id:'987'}});await hovered;
const resumed=harness(true);
resumed.reply(0,{ok:true,method:'password'});await resumed.flush();
resumed.advance(60_000);
assert.equal(resumed.requests.length,1,'Old saved tasks must not resume task requests');
assert.equal(resumed.values.get('activeTask'),null,'The old task-tracking entry must be cleared');
assert.equal(resumed.timers.size,0);
assert.equal(resumed.node('post').disabled,false);
for(const failure of ['network','timeout','unauthorized','malformed']){
  const failed=harness();failed.reply(0,{ok:true,method:'password'});await failed.flush();
  const pending=failed.node('post').onclick();
  if(failure==='network')failed.requests[1].onerror();
  if(failure==='timeout')failed.requests[1].ontimeout();
  if(failure==='unauthorized')failed.reply(1,{error:'denied'},401);
  if(failure==='malformed')failed.reply(1,{ok:true});
  await pending;failed.advance(60_000);
  assert.equal(failed.node('post').disabled,false,'Failure must release the in-flight submit lock');
  assert.equal(failed.node('post').classList.contains('primary'),false);
  assert.equal(failed.requests.length,2,'An uncertain result must never automatically resubmit or poll');
  assert.equal(failed.timers.size,0);
  assert.match(failed.node('status').textContent,{network:/无法连接/,timeout:/超时.*网站.*检查/,unauthorized:/连接密码/,malformed:/核对/}[failure]);
  if(failure==='unauthorized'){
    assert.equal(failed.node('connection').textContent,'连接验证失败');
    await failed.node('post').onclick();
    assert.equal(failed.node('settings-view').hidden,false);
    assert.equal(failed.requests.length,2,'A rejected credential must be verified before another submission');
  }
}
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
const oldSubmission=secure.node('post').onclick();
secure.node('server').value='https://secure.example';secure.node('credential').value='test-secure-only';
const secureSave=secure.node('settings-view').onsubmit({preventDefault(){}});
assert.equal(secure.requests[2].url,'https://secure.example/api/v1/connection/check');
secure.reply(2,{ok:true,method:'password'});await secureSave;
assert.equal(secure.values.get('server'),'https://secure.example');
assert.equal(secure.node('post').textContent,'下载当前帖子','Changing servers clears the last submission');
secure.reply(1,{task});await oldSubmission;
assert.match(secure.node('status').textContent,/连接验证成功/,'An old server response must not overwrite the new connection feedback');
const newSubmission=secure.node('post').onclick();
assert.equal(secure.requests[3].url,'https://secure.example/api/v1/tasks');
secure.reply(3,{task});await newSubmission;
const renewed=harness();renewed.reply(0,{ok:true,method:'password'});await renewed.flush();
const staleSubmission=renewed.node('post').onclick();
renewed.node('server').value='https://archive.test';renewed.node('credential').value='test-renewed-only';
const renewedSave=renewed.node('settings-view').onsubmit({preventDefault(){}});
renewed.reply(2,{ok:true,method:'password'});await renewedSave;
renewed.reply(1,{error:'旧密码已失效'},401);await staleSubmission;
assert.equal(renewed.node('connection').textContent,'已连接','An old credential response must not invalidate a newly verified credential on the same server');
assert.match(renewed.node('status').textContent,/连接验证成功/);
assert.equal(renewed.node('post').textContent,'下载当前帖子','Changing credentials clears the last submission');
assert.equal(renewed.values.get('token'),'test-renewed-only');
const renewedSubmission=renewed.node('post').onclick();
assert.equal(renewed.requests.length,4,'The new credential must be ready to submit after the old response');
renewed.reply(3,{task});await renewedSubmission;
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
  legacySchedule.navigate('/example');
  assert.equal(legacySchedule.node('profile').textContent,expected==='once'?'下载博主历史内容':'下载博主并订阅');
  const submitted=legacySchedule.node('profile').onclick();
  assert.equal(JSON.parse(legacySchedule.requests[1].data).schedule,expected);
  assert.equal(JSON.parse(legacySchedule.requests[1].data).mode,'subscribe','Subscribe immediately queues the first history download');
  assert.equal(JSON.parse(legacySchedule.requests[1].data).url,'https://x.com/example/timeline');
  legacySchedule.reply(1,{task:{id:'legacy-schedule',user:'example',status:'queued',created:Date.now()/1000}});await submitted;
  assert.equal(legacySchedule.node('profile').disabled,false);
  assert.equal(legacySchedule.requests.length,2);
}
for(const status of ['queued','running','awaiting_confirmation']){
  const subscription=harness(false,'https://archive.test','once',true);
  subscription.reply(0,{ok:true,method:'password'});await subscription.flush();subscription.navigate('/example');
  assert.equal(subscription.node('profile').textContent,'下载博主历史内容');
  const pending=subscription.node('profile').onclick();
  assert.equal(JSON.parse(subscription.requests[1].data).schedule,'once');
  subscription.reply(1,{task:{...task,post_id:null,status}});await pending;subscription.advance(60_000);
  assert.match(subscription.node('status').textContent,/已提交.*后台/);
  assert.equal(subscription.node('profile').disabled,false,'Background history status must not lock submission');
  assert.equal(subscription.requests.length,2,'History confirmation remains owned by the website');
  assert.equal(subscription.timers.size,0);
}
console.log('ScriptCat submit-only feedback, lock release, fast post switching, failures, no polling/resume, schedules and connection security checks passed');
