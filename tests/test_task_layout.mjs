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
  const nodes = new Map(), values = new Map([['shicun.downloadLayout',saved]]), calls = [], intervals=[];
  const element = (dataset={}) => ({dataset,hidden:false,value:'',open:false,textContent:'',innerHTML:'',
    classList:{items:new Set(),toggle(name,on){if(on)this.items.add(name);else this.items.delete(name);},contains(name){return this.items.has(name);}},
    setAttribute(name,value){this[name]=value;},showModal(){this.open=true;},close(){this.open=false;}});
  const node = id => {if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const layouts = ['a','b'].map(layout=>element({layout}));
  const filters = ['all','active','attention','done'].map(filter=>element({filter}));
  const sandbox = {console,URLSearchParams,Map,Set,Date,Number,String,Object,Math,
    document:{hidden:false,getElementById:node,addEventListener(){},querySelector(){return null;},querySelectorAll(selector){return selector==='[data-action="task-layout"]'?layouts:selector==='[data-action="task-filter"]'?filters:[];}},
    localStorage:{getItem(key){if(denied)throw Error('unavailable');return values.get(key);},setItem(key,value){if(denied)throw Error('unavailable');values.set(key,value);}},
    location:{search:'?view=tasks'},history:{pushState(){}},window:{scrollY:0,innerHeight:1000,addEventListener(){},scrollTo(){}},
    ResizeObserver:class{observe(){}},requestAnimationFrame(){},matchMedia:()=>({matches:false}),
    setInterval(fn){intervals.push(fn);},setTimeout(){},clearTimeout(){},confirm:()=>true,
    fetch:()=>new Promise(()=>{})};
  const context=vm.createContext(sandbox);
  vm.runInContext(source,context);
  context.fixture=fixture;context.calls=calls;
  vm.runInContext(`state={view:'tasks',offset:0};taskItems=fixture;
    api=async(path,method='GET',body)=>{calls.push({path,method,body});
      if(path==='/stats')return {posts:0,media:0,bloggers:0,active_tasks:1};
      if(path==='/bloggers')return {items:[]};
      if(path.startsWith('/tasks?'))return {items:fixture};
      return {};};`,context);
  return {run:code=>vm.runInContext(code,context),node,values,calls,intervals,context};
}

for(const [saved,expected] of [[undefined,'a'],['a','a'],['b','b'],['invalid','a']])
  assert.equal(harness(saved).run('taskLayout'),expected);
const blocked=harness('b',true);
assert.equal(blocked.run('taskLayout'),'a');
await blocked.run(`action({dataset:{action:'task-layout',layout:'b'}})`);
assert.equal(blocked.run('taskLayout'),'b');
assert.match(blocked.node('toast').textContent,/无法保存/);
assert.equal(blocked.calls.length,0);

const app=harness();
await app.run(`action({dataset:{action:'task-layout',layout:'b'}})`);
assert.equal(app.values.get('shicun.downloadLayout'),'b');
assert.equal(app.calls.length,0,'Changing layout must never submit or alter a task');
assert.equal(harness(app.values.get('shicun.downloadLayout')).run('taskLayout'),'b');
await app.run(`action({dataset:{action:'task-filter',filter:'attention'}})`);
assert.equal(app.run('selectedTask'),'failed-id');
assert.match(app.node('content').innerHTML,/@chengstudio/);
app.run(`taskFilter='all';taskQuery='spacex';renderTaskContent()`);
assert.equal(app.run('selectedTask'),'done-id');
app.run(`taskQuery='missing';renderTaskContent()`);
assert.equal(app.run('selectedTask'),'');
assert.match(app.node('content').innerHTML,/本页没有匹配任务/);

app.run(`taskQuery='cheng';taskFilter='all';selectedTask='failed-id';renderTaskContent()`);
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
app.context.confirm=()=>false;
await app.run(`action({dataset:{action:'cancel',id:'running-id'}})`);
assert.equal(app.calls.length,0,'Declining cancellation must keep the queue unchanged');
app.context.confirm=()=>true;
await app.run(`action({dataset:{action:'cancel',id:'running-id'}})`);
assert.equal(app.calls.find(call=>call.method==='POST').path,'/tasks/running-id/cancel');
app.calls.length=0;
await app.run(`action({dataset:{action:'download-user',user:'spacex'}})`);
const submit=app.calls.find(call=>call.method==='POST');
assert.equal(submit.path,'/tasks');
assert.equal(submit.body.url,'@spacex');
assert.equal(submit.body.mode,'download');

console.log('X task layouts: persistence, storage failure, filtering, refresh, mobile detail and shared API actions passed.');
