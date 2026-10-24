import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../xarchive/static/app.js', import.meta.url), 'utf8');
const fixture = [
  {id:'running-id',user:'nasa',status:'running',scanned_posts:128,progress:39},
  {id:'failed-id',user:'chengstudio',status:'failed',error:'连接超时'},
  {id:'done-id',user:'spacex',status:'succeeded',new_posts:5,new_media:10}
].map(task => ({created:1792756800,attempts:1,archive_posts:20,...task}));

function harness(saved, denied=false) {
  const nodes = new Map(), values = new Map([['shicun.downloadLayout',saved]]), calls = [], intervals=[],listeners=new Map(),timers=new Map();let serial=0;
  const element = (dataset={}) => ({dataset,hidden:false,value:'',open:false,textContent:'',innerHTML:'',
    classList:{items:new Set(),toggle(name,on){if(on)this.items.add(name);else this.items.delete(name);},contains(name){return this.items.has(name);}},
    setAttribute(name,value){this[name]=value;},showModal(){this.open=true;},close(){this.open=false;}});
  const node = id => {if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const layouts = ['a','b'].map(layout=>element({layout}));
  const filters = ['all','active','attention','done'].map(filter=>element({filter}));
  const sandbox = {console,URLSearchParams,Map,Set,Date,Number,String,Object,Math,AbortController,
    document:{hidden:false,getElementById:node,addEventListener(type,listener){listeners.set(type,listener);},querySelector(){return null;},querySelectorAll(selector){return selector==='[data-action="task-layout"]'?layouts:selector==='[data-action="task-filter"]'?filters:[];}},
    localStorage:{getItem(key){if(denied)throw Error('unavailable');return values.get(key);},setItem(key,value){if(denied)throw Error('unavailable');values.set(key,value);}},
    location:{search:'?view=tasks'},history:{pushState(state,title,url){sandbox.location.search=url.includes('?')?url.slice(url.indexOf('?')):'';}},window:{scrollY:0,innerHeight:1000,addEventListener(){},scrollTo(){}},
    ResizeObserver:class{observe(){}},requestAnimationFrame(){},matchMedia:()=>({matches:false}),
    setInterval(fn){intervals.push(fn);},setTimeout(fn,delay){timers.set(++serial,{fn,delay});return serial;},clearTimeout(id){timers.delete(id);},confirm:()=>true,
    fetch:()=>new Promise(()=>{})};
  const context=vm.createContext(sandbox);
  vm.runInContext(source,context);
  context.fixture=fixture;context.calls=calls;
  vm.runInContext(`state={view:'tasks',offset:0};taskItems=fixture;
    api=async(path,method='GET',body)=>{calls.push({path,method,body});
      if(path==='/stats')return {posts:0,media:0,bloggers:0,active_tasks:1};
      if(path==='/bloggers')return {items:[]};
      if(path.startsWith('/tasks?')){
        const params=new URLSearchParams(path.split('?')[1]),query=(params.get('q')||'').toLowerCase();
        const matches=fixture.filter(row=>(row.user+' '+(row.post_id||'')+' '+row.id).includes(query));
        const groups={all:matches,active:matches.filter(row=>['queued','running','retry'].includes(row.status)),attention:matches.filter(row=>['failed','retry'].includes(row.status)),done:matches.filter(row=>row.status==='succeeded')};
        const items=groups[params.get('filter')||'all'];
        return {items,total:items.length,counts:Object.fromEntries(Object.entries(groups).map(([key,rows])=>[key,rows.length]))};
      }
      return {};};`,context);
  return {run:code=>vm.runInContext(code,context),node,values,calls,intervals,context,listeners,timers,
    runTimer(delay){const entry=[...timers].find(([id,timer])=>timer.delay===delay);if(entry){timers.delete(entry[0]);return entry[1].fn();}}};
}

// A slow previous request must not leave the old page visible or overwrite the new page's data.
const navigation=harness();
navigation.run(`state={view:'archive'};$('content').innerHTML='<video src="old.mp4"></video>';
  pending=[];api=(path,method,body,signal)=>new Promise(resolve=>pending.push({path,signal,resolve}));
  location.search='?view=settings';firstRoute=route();`);
assert.doesNotMatch(navigation.node('content').innerHTML,/<video/,'Navigation must remove the old media immediately');
assert.match(navigation.node('content').innerHTML,/role="status"/);
navigation.run(`location.search='?view=tasks';secondRoute=route();`);
assert.equal(navigation.run('pending[0].signal.aborted'),true,'The previous page request should be cancelled');
navigation.run(`pending[1].resolve({items:fixture,total:3,counts:{all:3,active:1,attention:1,done:1}});`);
await navigation.run('secondRoute');
navigation.run(`pending[0].resolve({cookie_configured:true,default_schedule:'weekly'});`);
await navigation.run('firstRoute');
assert.match(navigation.node('content').innerHTML,/@nasa/);
assert.equal(navigation.run('settings.cookie_configured'),undefined,'An obsolete settings response must not replace the current settings');

navigation.run(`location.search='';archiveRoute=route();location.search='?view=bloggers';bloggerRoute=route();
  pending[3].resolve({items:[{user:'current',display_name:'Current',post_count:1}]});`);
await navigation.run('bloggerRoute');
navigation.run(`pending[2].resolve({items:[{id:'old',media:[]}],total:999,snapshot:999});`);
await navigation.run('archiveRoute');
assert.equal(navigation.run('posts.has("old")'),false,'An obsolete archive response must not replace the active post map');
assert.equal(navigation.run('rows.has("current")'),true);
assert.notEqual(navigation.run('total'),999);
navigation.run(`api=async()=>{throw Error('暂时离线')};location.search='?view=settings';failedRoute=route().catch(error=>error);`);
await navigation.run('failedRoute');
assert.match(navigation.node('content').innerHTML,/暂时无法读取设置/,'A failed page request must not remain a loading screen');
assert.match(navigation.node('content').innerHTML,/暂时离线/);

for(const saved of [undefined,'a','b','invalid']){
  const fixed=harness(saved);fixed.run('renderTaskContent()');
  assert.match(fixed.node('content').innerHTML,/class="task-card/);
  assert.doesNotMatch(fixed.node('content').innerHTML,/class="task-queue/,'Old saved B must still display the original cards');
}
const blocked=harness('b',true);blocked.run('renderTaskContent()');
assert.match(blocked.node('content').innerHTML,/class="task-card/);
const markup=fs.readFileSync(new URL('../xarchive/static/index.html',import.meta.url),'utf8');
assert.doesNotMatch(markup,/data-action="task-layout"/,'The layout selector is no longer part of the page');

const app=harness();
await app.run(`action({dataset:{action:'task-filter',filter:'attention'}})`);
assert.equal(app.run('selectedTask'),'failed-id');
assert.match(app.node('content').innerHTML,/@chengstudio/);
await app.run(`taskFilter='all';taskQuery='spacex';route()`);
assert.equal(app.run('selectedTask'),'done-id');
await app.run(`taskQuery='missing';route()`);
assert.equal(app.run('selectedTask'),'');
assert.match(app.node('content').innerHTML,/没有匹配任务/);

await app.run(`taskQuery='cheng';taskFilter='all';selectedTask='failed-id';route()`);
await app.intervals[0]();
assert.equal(app.run('taskQuery'),'cheng');
assert.equal(app.run('selectedTask'),'failed-id');
assert.equal(app.node('task-search').value,'cheng');
app.context.matchMedia=()=>({matches:true});
await app.run(`action({dataset:{action:'task-select',id:'failed-id'}})`);
assert.equal(app.node('task-dialog').open,true);
assert.match(app.node('task-dialog-content').innerHTML,/@chengstudio/);
app.calls.length=0;
await app.run(`action({dataset:{action:'retry',id:'failed-id'}})`);
assert.equal(app.calls.filter(call=>call.method==='POST').length,1);
assert.equal(app.calls.find(call=>call.method==='POST').path,'/tasks/failed-id/retry');
assert.equal(app.node('task-dialog').open,false);
app.calls.length=0;
let confirmations=0;app.context.confirm=()=>{confirmations++;return true;};
await app.run(`action({dataset:{action:'cancel',id:'running-id'}})`);
assert.equal(app.calls.find(call=>call.method==='POST').path,'/tasks/running-id/cancel');
assert.equal(confirmations,0,'Task actions must not ask for a second confirmation');
assert.match(app.run(`taskActions(fixture[1])`),/data-action="delete"/);
assert.match(app.run(`taskActions(fixture[1])`),/data-action="cancel"/);
assert.doesNotMatch(app.run(`taskActions(fixture[0])`),/data-action="delete"/,'Active records must be cancelled before deleting');
app.calls.length=0;
await app.run(`action({dataset:{action:'delete',id:'failed-id'}})`);
assert.equal(app.calls.find(call=>call.method==='POST').path,'/tasks/failed-id/delete');

for(const operation of ['delete','retry','cancel']){
  const bulk=harness();bulk.context.operation=operation;
  bulk.run(`taskFilter='attention';taskQuery='cheng';taskItems=[fixture[1]];
    api=async(path,method='GET',body)=>{calls.push({path,method,body});
      if(path==='/tasks/bulk')return {affected:120,skipped:3,matched:123};
      if(path.startsWith('/tasks?'))return {items:[fixture[1]],total:123,counts:{all:123,active:3,attention:123,done:0}};
      if(path==='/stats')return {posts:0,media:0,bloggers:0,active_tasks:0};
      if(path==='/bloggers')return {items:[]};return {};};
    confirm=()=>{throw Error('Bulk actions must not ask for confirmation');};`);
  await bulk.run(`action({dataset:{action:'task-bulk',operation}})`);
  const calls=bulk.calls.filter(call=>call.method==='POST');
  assert.equal(calls.length,1,'One batch request must cover the complete filter, not loop over the loaded page');
  assert.equal(calls[0].path,'/tasks/bulk');
  assert.equal(JSON.stringify(calls[0].body),JSON.stringify({action:operation,filter:'attention',q:'cheng'}));
  assert.match(bulk.node('toast').textContent,/120/);
  assert.match(bulk.node('toast').textContent,/跳过 3/);
  assert.equal(bulk.run('total'),123);
  assert.match(bulk.calls.find(call=>call.path.startsWith('/tasks?')).path,/filter=attention/);
}
const pendingBatch=harness();
pendingBatch.run(`normalApi=api;api=(path,method,body)=>{if(path==='/tasks/bulk'){calls.push({path,method,body});return new Promise(resolve=>finishBatch=resolve);}return normalApi(path,method,body);};`);
const firstBatch=pendingBatch.run(`action({dataset:{action:'task-bulk',operation:'cancel'}})`);
await pendingBatch.run(`action({dataset:{action:'task-bulk',operation:'delete'}})`);
assert.equal(pendingBatch.calls.filter(call=>call.method==='POST').length,1,'An in-flight batch must block a second task mutation');
pendingBatch.run(`finishBatch({affected:1,skipped:0,matched:1})`);await firstBatch;
pendingBatch.run(`api=(path,method,body)=>path==='/tasks/bulk'?Promise.reject(Error('fixture offline')):normalApi(path,method,body);`);
await assert.rejects(pendingBatch.run(`action({dataset:{action:'task-bulk',operation:'cancel'}})`),/fixture offline/);
assert.equal(pendingBatch.run('taskMutationBusy'),false,'A failed batch must release the action controls');

assert.match(app.run('scheduleOptions()'),/value="quarterly" selected/);
assert.doesNotMatch(app.run('scheduleOptions()'),/value="daily"/);
assert.match(app.run(`scheduleOptions('daily')`),/每天（旧计划）/);
for(const value of ['three_days','weekly','monthly','quarterly','yearly','once'])assert.match(app.run('scheduleOptions()'),new RegExp('value="'+value+'"'));

const search=harness();
search.listeners.get('input')({target:{id:'task-search',value:'spacex'}});
assert.equal(search.calls.length,0,'Search should wait briefly before querying the server');
await search.runTimer(200);
assert.match(search.calls.find(call=>call.path.startsWith('/tasks?')).path,/q=spacex/);
assert.equal(search.run('selectedTask'),'done-id');
search.listeners.get('input')({target:{id:'task-search',value:'nasa'}});
await search.run(`location.search='?view=settings';route()`);
assert.equal([...search.timers.values()].some(timer=>timer.delay===200),false,'Leaving tasks must cancel a pending search');

const intake=harness();intake.node('intake-url').value='https://x.com/example/status/123';intake.node('intake-schedule').value='quarterly';
await intake.node('intake-form').onsubmit({preventDefault(){},submitter:{disabled:false}});
assert.equal(intake.calls.find(call=>call.method==='POST').body.schedule,'once','A single post never starts an automatic schedule');
app.calls.length=0;
await app.run(`action({dataset:{action:'download-user',user:'spacex'}})`);
const submit=app.calls.find(call=>call.method==='POST');
assert.equal(submit.path,'/tasks');
assert.equal(submit.body.url,'@spacex');
assert.equal(submit.body.mode,'download');

app.run(`taskItems=[{id:'single-post',user:'example',post_id:'123',url:'https://x.com/example/status/123',status:'succeeded'}]`);
assert.match(app.run(`taskActions(taskItems[0])`),/data-action="download-task"/);
app.calls.length=0;
await app.run(`action({dataset:{action:'download-task',id:'single-post'}})`);
assert.equal(app.calls.find(call=>call.method==='POST').body.url,'https://x.com/example/status/123','Checking one post must keep the original target');

app.run(`state={view:'settings',section:'updates'};api=async()=>({proxy_configured:true,default_schedule:'monthly'})`);
assert.match(await app.run('renderSettings()'),/id="proxy"/);
app.run(`api=async(path,method,body)=>{calls.push({path,method,body});return {proxy_configured:true}};route=async()=>{}`);
app.node('proxy').value='http://proxy.example:7890';
app.node('clear-proxy').checked=false;
app.node('default-schedule').value='monthly';
app.run('bindSettings()');app.calls.length=0;
await app.node('settings-form').onsubmit({preventDefault(){},submitter:{disabled:false}});
assert.equal(app.calls.find(call=>call.method==='PUT').body.x_proxy,'http://proxy.example:7890');
app.node('proxy').value='';app.node('clear-proxy').checked=true;app.calls.length=0;
await app.node('settings-form').onsubmit({preventDefault(){},submitter:{disabled:false}});
assert.equal(app.calls.find(call=>call.method==='PUT').body.clear_proxy,true);

console.log('X fixed task cards, filtering, refresh, detail, shared actions and immediate race-safe navigation passed.');
