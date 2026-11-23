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
  const filters = ['all','active','confirmation','attention','done'].map(filter=>element({filter}));
  const bulkButtons = ['delete','retry','cancel'].map(operation=>element({operation}));
  const sandbox = {console,URLSearchParams,Map,Set,Date,Number,String,Object,Math,AbortController,
    document:{hidden:false,getElementById:node,addEventListener(type,listener){listeners.set(type,listener);},querySelector(){return null;},querySelectorAll(selector){return selector==='[data-action="task-layout"]'?layouts:selector==='[data-action="task-filter"]'?filters:selector==='[data-action="task-bulk"]'?bulkButtons:[];}},
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
      if(path==='/stats')return {posts:0,media:0,bloggers:0,active_tasks:1,featured_bloggers:[]};
      if(path==='/bloggers')return {items:[]};
      if(path.startsWith('/tasks?')){
        const params=new URLSearchParams(path.split('?')[1]),query=(params.get('q')||'').toLowerCase();
        const matches=fixture.filter(row=>(row.user+' '+(row.post_id||'')+' '+row.id).includes(query));
        const groups={all:matches,active:matches.filter(row=>['queued','running','retry'].includes(row.status)),confirmation:matches.filter(row=>row.status==='awaiting_confirmation'),attention:matches.filter(row=>['failed','retry','awaiting_confirmation'].includes(row.status)),done:matches.filter(row=>row.status==='succeeded')};
        const items=groups[params.get('filter')||'all'];
        return {items,total:items.length,counts:Object.fromEntries(Object.entries(groups).map(([key,rows])=>[key,rows.length]))};
      }
      return {};};`,context);
  return {run:code=>vm.runInContext(code,context),node,values,calls,intervals,context,listeners,timers,bulkButtons,
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
assert.doesNotMatch(markup,/id="task-summary"/,'Task counts belong in their filter buttons');

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
      if(path==='/stats')return {posts:0,media:0,bloggers:0,active_tasks:0,featured_bloggers:[]};
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

const pausedPlan=harness();
pausedPlan.run(`rows=new Map([['example',{user:'example',schedule:'monthly',enabled:false}]]);route=async()=>{};`);
await pausedPlan.run(`action({dataset:{action:'toggle-plan',user:'example'}})`);
assert.ok(pausedPlan.calls.some(call=>call.path==='/subscriptions/example'),'Resuming a plan must update it');
assert.deepEqual(JSON.parse(JSON.stringify(pausedPlan.calls.find(call=>call.path==='/subscriptions/example').body)),{schedule:'monthly',enabled:true},'Resuming a plan must preserve its frequency');
pausedPlan.calls.length=0;
pausedPlan.run(`rows.get('example').enabled=false`);
await pausedPlan.listeners.get('change')({target:{id:'',dataset:{plan:'example'},value:'weekly',disabled:false,matches:()=>true}});
assert.equal(pausedPlan.calls.find(call=>call.path==='/subscriptions/example').body.enabled,false,'Changing a paused plan frequency must keep it paused');
pausedPlan.calls.length=0;
pausedPlan.run(`rows.get('example').enabled=true;rows.get('example').schedule='monthly'`);
await pausedPlan.run(`action({dataset:{action:'toggle-plan',user:'example'}})`);
assert.equal(pausedPlan.calls.find(call=>call.path==='/subscriptions/example').body.enabled,false,'Pausing must disable the recurring plan');
assert.equal(pausedPlan.calls.find(call=>call.path==='/subscriptions/example').body.schedule,'monthly');
pausedPlan.calls.length=0;
pausedPlan.run(`rows.get('example').schedule='once';rows.get('example').enabled=false`);
await pausedPlan.listeners.get('change')({target:{id:'',dataset:{plan:'example'},value:'weekly',disabled:false,matches:()=>true}});
assert.equal(pausedPlan.calls.find(call=>call.path==='/subscriptions/example').body.enabled,true,'Choosing a recurring frequency for a once plan enables it');
pausedPlan.context.closeMedia=()=>{};
pausedPlan.run(`rows.get('example').schedule='monthly';rows.get('example').enabled=false`);
await pausedPlan.run(`action({dataset:{action:'plan-user',user:'example'}})`);
pausedPlan.node('edit-schedule').value='weekly';pausedPlan.calls.length=0;
await pausedPlan.run('editSave()');
assert.equal(pausedPlan.calls.find(call=>call.path==='/subscriptions/example').body.enabled,false,'Editing frequency through blogger search must keep a paused plan paused');
pausedPlan.run(`api=async()=>({items:[{user:'example',display_name:'Example',schedule:'monthly',enabled:0,next_run:0}]})`);
assert.match(await pausedPlan.run('renderSubscriptions()'),/data-action="toggle-plan"[^>]*>恢复/,'Paused recurring plans expose resume');

const planRace=harness();
planRace.run(`rows=new Map([['example',{user:'example',schedule:'monthly',enabled:true}]]);route=async()=>{};
  api=(path,method,body)=>{calls.push({path,method,body});return new Promise(resolve=>finishPlan=resolve);};`);
const planToggle={dataset:{action:'toggle-plan',user:'example'},disabled:false};
const planSelect={id:'',dataset:{plan:'example'},value:'monthly',disabled:false,matches:()=>true};
const planRow={querySelectorAll:()=>[planToggle,planSelect]};
planToggle.closest=planSelect.closest=()=>planRow;planRace.context.planToggle=planToggle;
const pendingPlan=planRace.run('action(planToggle)');
assert.equal(planToggle.disabled,true,'Pause must lock both plan controls');
assert.equal(planSelect.disabled,true,'Pause must lock the frequency control');
planSelect.value='weekly';await planRace.listeners.get('change')({target:planSelect});
assert.equal(planRace.calls.length,1,'A frequency change cannot race a pending pause');
planRace.context.closeMedia=()=>{};
await assert.rejects(planRace.run(`action({dataset:{action:'plan-user',user:'example'}})`),/正在保存/,'Blogger plan edits must also wait for the same plan');
assert.equal(planRace.calls.length,1);
planRace.run('finishPlan({})');await pendingPlan;
assert.equal(planToggle.disabled,false);assert.equal(planSelect.disabled,false);
planRace.calls.length=0;planSelect.value='weekly';
const pendingFrequency=planRace.listeners.get('change')({target:planSelect});
assert.equal(planToggle.disabled,true);assert.equal(planSelect.disabled,true);
await planRace.run('action(planToggle)');
assert.equal(planRace.calls.length,1,'Pause cannot race a pending frequency change');
planRace.run('finishPlan({})');await pendingFrequency;
assert.equal(planToggle.disabled,false);assert.equal(planSelect.disabled,false);

const failedPlan=harness();
failedPlan.run(`rows=new Map([['example',{user:'example',schedule:'monthly',enabled:true}]]);route=async()=>{};api=async()=>{throw Error('offline')};`);
const failedToggle={dataset:{action:'toggle-plan',user:'example'},disabled:false};
const failedSelect={id:'',dataset:{plan:'example'},value:'weekly',disabled:false,matches:()=>true};
const failedRow={querySelectorAll:()=>[failedToggle,failedSelect]};failedToggle.closest=failedSelect.closest=()=>failedRow;
await failedPlan.listeners.get('change')({target:failedSelect});
assert.equal(failedSelect.value,'monthly','A failed frequency update restores the saved value');
assert.equal(failedSelect.disabled,false);assert.equal(failedToggle.disabled,false);
failedPlan.context.failedToggle=failedToggle;
await assert.rejects(failedPlan.run('action(failedToggle)'),/offline/);
assert.equal(failedSelect.disabled,false);assert.equal(failedToggle.disabled,false);
failedSelect.value='weekly';
failedPlan.run(`api=async()=>({});route=async()=>{throw Error('refresh failed')}`);
await failedPlan.listeners.get('change')({target:failedSelect});
assert.equal(failedSelect.value,'weekly','A saved frequency is not rolled back when only refresh fails');
assert.equal(failedSelect.disabled,false);assert.equal(failedToggle.disabled,false);
await assert.rejects(failedPlan.run('action(failedToggle)'),/refresh failed/);
assert.equal(failedPlan.run(`planMutationBusy.has('example')`),false,'A failed refresh releases the plan lock');
assert.equal(failedSelect.disabled,false);assert.equal(failedToggle.disabled,false);
const redrawnPlan=harness();
redrawnPlan.run(`planMutationBusy.add('example');api=async()=>({items:[{user:'example',display_name:'Example',schedule:'monthly',enabled:1,next_run:0}]})`);
const redrawn=await redrawnPlan.run('renderSubscriptions()');
assert.match(redrawn,/data-plan="example"[^>]*disabled/,'A redrawn plan keeps its frequency locked');
assert.match(redrawn,/data-action="toggle-plan"[^>]*disabled/,'A redrawn plan keeps pause locked');

const pendingSearch=harness();
await pendingSearch.run(`taskQuery='cheng';route()`);
assert.equal(pendingSearch.bulkButtons[0].disabled,false);
pendingSearch.calls.length=0;
pendingSearch.listeners.get('input')({target:{id:'task-search',value:''}});
assert.equal(pendingSearch.bulkButtons[0].disabled,true,'Search changes must disable visible batch controls immediately');
await pendingSearch.run(`action({dataset:{action:'task-bulk',operation:'delete'}})`);
assert.equal(pendingSearch.calls.some(call=>call.path==='/tasks/bulk'),false,'Changing search must block bulk operations until the new results load');
await pendingSearch.runTimer(200);
assert.equal(pendingSearch.bulkButtons[0].disabled,false);
await pendingSearch.run(`action({dataset:{action:'task-bulk',operation:'delete'}})`);
assert.equal(pendingSearch.calls.find(call=>call.path==='/tasks/bulk').body.q,'','Bulk actions resume with the displayed search after it loads');
const lateSearch=harness();
lateSearch.run(`api=(path,method,body,signal)=>new Promise(resolve=>pendingResult={resolve,signal});oldRoute=route();`);
lateSearch.listeners.get('input')({target:{id:'task-search',value:'new'}});
assert.equal(lateSearch.run('pendingResult.signal.aborted'),true,'Typing cancels an older task request');
lateSearch.run(`pendingResult.resolve({items:fixture,total:3,counts:{all:3,active:1,confirmation:0,attention:1,done:1}})`);
await lateSearch.run('oldRoute');
assert.equal(lateSearch.bulkButtons[0].disabled,true,'An old response cannot re-enable bulk controls for a new search');
const failedBatchSearch=harness();await failedBatchSearch.run('route()');
failedBatchSearch.run(`api=(path)=>path==='/tasks/bulk'?new Promise((resolve,reject)=>failBatch=reject):Promise.resolve({});`);
const batchButton=failedBatchSearch.bulkButtons[0];batchButton.dataset.action='task-bulk';
batchButton.closest=selector=>selector==='[data-action]'?batchButton:null;
const failingClick=failedBatchSearch.listeners.get('click')({target:batchButton});
failedBatchSearch.listeners.get('input')({target:{id:'task-search',value:'new'}});
failedBatchSearch.run(`failBatch(Error('offline'))`);await failingClick;
assert.equal(batchButton.disabled,true,'A failed batch must not re-enable controls while new search results are pending');

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
assert.equal(submit.body.mode,'update','Immediate updates must not return the waiting history task');

app.run(`taskItems=[{id:'single-post',user:'example',post_id:'123',url:'https://x.com/example/status/123',status:'succeeded'}]`);
assert.match(app.run(`taskActions(taskItems[0])`),/data-action="download-task"/);
app.calls.length=0;
await app.run(`action({dataset:{action:'download-task',id:'single-post'}})`);
assert.equal(app.calls.find(call=>call.method==='POST').body.url,'https://x.com/example/status/123','Checking one post must keep the original target');
assert.equal(app.calls.find(call=>call.method==='POST').body.mode,'download','Checking a saved task preserves the existing download intake');

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

const diagnostics=harness();
const diagnosticTask={...fixture[1],user:'example',post_id:'123',url:'javascript:alert(1)'};
diagnostics.context.diagnosticTask=diagnosticTask;
for(const render of ['taskCard','taskDetails']){
  const html=diagnostics.run(`${render}(diagnosticTask)`);
  assert.match(html,/href="https:\/\/x\.com\/example\/status\/123"/);
  assert.match(html,/target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(html,/href="javascript:/);
}
diagnostics.run(`diagnosticTask.user='evil.test/path';diagnosticTask.post_id='bad'`);
assert.doesNotMatch(diagnostics.run('taskCard(diagnosticTask)'),/href="https:\/\/x\.com/,'Invalid task identities must not create an X link');
diagnostics.run(`taskItems=[{user:'nasa',id:'one',status:'failed'},{user:'nasa_news',id:'two',status:'failed'}];taskFilter='all';taskQuery='@nasa'`);
assert.equal(diagnostics.run('filteredTasks().length'),1,'An @handle task filter must match the exact author');
diagnostics.run(`taskCooldownUntil=Date.now()/1000+3600;later=Date.now()/1000+7200`);
assert.match(diagnostics.run(`taskResult({status:'queued',next_run:0})`),/整个队列/);
assert.equal(diagnostics.run(`taskResult({status:'retry',next_run:later}).includes(date(later))`),true,'A later task retry must not be shown before its own next_run');

const unified=harness();
unified.run(`summary={user:'example',display_name:'Example Summary',post_count:12,media_count:20,favorite_count:3,subscribed:true,enabled:true,schedule:'quarterly',next_run:1792890000,last_success_at:1792700000,last_post_at:'2026-10-23 12:00:00',active_task:null};
  normalApi=api;api=(path,method='GET',body)=>{if(path.startsWith('/posts?')){calls.push({path,method,body});return Promise.resolve({items:[],total:0,snapshot:1,bloggers:[summary],blogger_total:1});}return normalApi(path,method,body);};`);
for(const view of ['archive','favorites','bloggers','subscriptions','tasks','settings']){
  unified.run(`state={view:'${view}',order:'timeline'};location.search='?view=${view}'`);unified.node('search').value='@example';
  await unified.node('search-form').onsubmit({preventDefault(){}});
  assert.equal(new URLSearchParams(unified.context.location.search).get('view'),null,'Every global search must open the shared archive results');
  assert.equal(new URLSearchParams(unified.context.location.search).get('q'),'@example');
  assert.match(unified.node('content').innerHTML,/Example Summary/);
  assert.match(unified.node('content').innerHTML,/最近成功更新/);
  assert.match(unified.node('content').innerHTML,/内容收录到/);
  assert.match(unified.node('content').innerHTML,/每季度一次/);
  assert.match(unified.node('content').innerHTML,/data-action="plan-user"/);
}
const searchRequests=unified.calls.filter(call=>call.path.startsWith('/posts?')).length;
await unified.run(`action({dataset:{action:'download-user',user:'example'}})`);
assert.equal(unified.calls.filter(call=>call.path.startsWith('/posts?')).length,searchRequests+1,'An update must refresh the matching author task status');
await unified.run(`action({dataset:{action:'user-tasks',user:'example'}})`);
assert.equal(unified.run('taskQuery'),'@example');
assert.match(unified.calls.findLast(call=>call.path.startsWith('/tasks?')).path,/q=%40example/);
unified.run(`summary.last_success_at=null;state={view:'archive',q:'example',offset:0}`);
assert.match(await unified.run('renderArchive()'),/暂无记录/);
unified.run(`summary.subscribed=false;summary.enabled=false;summary.schedule='quarterly'`);
const pausedSummary=unified.run('searchBloggerCard(summary)');
assert.match(pausedSummary,/每季度一次/);
assert.match(pausedSummary,/已暂停/);
assert.doesNotMatch(pausedSummary,/未设计划/,'A paused plan still has its saved frequency');
unified.run(`summary.schedule='once'`);
const onceSummary=unified.run('searchBloggerCard(summary)');
assert.match(onceSummary,/仅下载这一次/);
assert.match(onceSummary,/不自动更新/);
assert.doesNotMatch(onceSummary,/未设计划/,'A once plan is present even though it is not automatically enabled');
const draftSearch=harness();await draftSearch.run('route()');draftSearch.node('search').value='@draft';
await draftSearch.intervals[0]();
assert.equal(draftSearch.node('search').value,'@draft','Background task refresh must not erase an unsubmitted global search');

const overview=harness();
overview.run(`state={view:'archive',order:'timeline'};
  const stubApi=api;api=(path)=>path==='/stats'
    ?stubApi(path).then(data=>({...data,featured_bloggers:[{user:'nasa',display_name:'NASA'}]}))
    :stubApi(path);`);
await overview.intervals[0]();
assert.deepEqual(overview.calls.map(call=>call.path),['/stats'],'Periodic overview must not request full blogger summaries');
assert.match(overview.node('side-bloggers').innerHTML,/NASA/);
assert.equal(overview.node('queue-badge').textContent,1);

const history=harness();
history.run(`pendingTask={...fixture[1],id:'history-id',status:'awaiting_confirmation',task_kind:'history',total_posts:null,media_posts:1001,media_complete:false};taskItems=[pendingTask];taskFilter='all';taskQuery='';`);
const historyCard=history.run('taskCard(pendingTask)');
assert.match(historyCard,/待确认/);
assert.match(historyCard,/总帖数未知/);
assert.match(historyCard,/至少 1001 条含媒体帖子/);
assert.match(historyCard,/选择范围/);
assert.doesNotMatch(historyCard,/data-choice=/,'History choices belong in the task dialog');
const historyDetails=history.run('taskDetails(pendingTask)');
for(const choice of ['all','recent300','skip'])assert.match(historyDetails,new RegExp('data-choice="'+choice+'"'));
assert.doesNotMatch(history.run('taskActions(pendingTask)'),/data-action="delete"/,'Pending history must be cancelled before deleting');
history.run(`pendingTask.media_complete=true;pendingTask.total_posts=2048;`);
assert.match(history.run('taskCard(pendingTask)'),/X 显示 2048 条帖子/);
assert.match(history.run('taskCard(pendingTask)'),/已发现 1001 条含媒体帖子/);
assert.doesNotMatch(history.run('taskCard(pendingTask)'),/至少 1001/,'An exhausted media scan has an exact count');
history.run(`taskFilter='confirmation'`);
assert.equal(history.run('filteredTasks().length'),1);
await history.run(`action({dataset:{action:'confirm-history',id:'history-id',choice:'recent300'}})`);
const confirmed=history.calls.find(call=>call.path==='/tasks/history-id/confirm');
assert.equal(confirmed.method,'POST');assert.equal(confirmed.body.choice,'recent300');
assert.match(history.run(`taskCard({...fixture[0],task_kind:'incremental'})`),/增量更新/);
assert.match(history.run(`taskCard({...fixture[0],task_kind:'history',history_choice:'recent300'})`),/最近 300 条/);
assert.doesNotMatch(history.run(`taskActions({...fixture[1],status:'cancelled',history_choice:'skip'})`),/data-action="retry"/,'Skipped history must not silently resume through retry');
assert.match(history.run(`taskResult({...fixture[0],progress:12})`),/已处理 12 条帖子/,'Progress counts posts, not media files');
history.run('taskCooldownUntil=0');
assert.match(history.run(`taskResult({status:'retry',cooldown_scope:'task',cooldown_reason:'查询额度等待',next_run:Date.now()/1000+1800})`),/仅此任务/);
console.log('Task cards, history confirmation, count bounds, global author search, X diagnostics, queue cooldown, bulk actions and race-safe navigation passed.');
