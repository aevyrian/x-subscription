'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths={home:'M3 10.5 12 3l9 7.5V21h-6v-7H9v7H3z',bookmark:'M6 3h12v18l-6-4-6 4z',users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 4a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',calendar:'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2M8 14h3M8 18h6',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',settings:'m9 3-.6 2.3-2 .9-2.1-.7-2 3.5 1.7 1.6v2.8l-1.7 1.6 2 3.5 2.1-.7 2 .9L9 21h6l.6-2.3 2-.9 2.1.7 2-3.5-1.7-1.6v-2.8l1.7-1.6-2-3.5-2.1.7-2-.9L15 3z M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0',plus:'M12 5v14M5 12h14',close:'m6 6 12 12M6 18 18 6',search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',refresh:'M21 5v5h-5M3 19v-5h5M5 8a8 8 0 0 1 13-3l3 5M3 14l3 5a8 8 0 0 0 13-3',logout:'M9 4H4v16h5M10 12h11m-4-4 4 4-4 4',more:'M5 12h.01M12 12h.01M19 12h.01',comment:'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l1.8-4A8.5 8.5 0 1 1 21 11.5z',external:'M14 3h7v7M21 3 10 14M10 3H3v18h18v-7',check:'m5 12 4 4L20 5',chevron:'m9 5 7 7-7 7',image:'M3 3h18v18H3zM3 17l6-7 4 4 3-3 5 6M7 7h.01'};
function icon(name){return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.more}"/></svg>`;}
paths.star='m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z';
document.querySelectorAll('[data-icon]').forEach(n=>n.innerHTML=icon(n.dataset.icon));
function avatar(user,name=''){return `<span class="avatar"><img src="/api/v1/avatars/${encodeURIComponent(user)}" alt="${esc(name||user)} 的头像" loading="lazy" decoding="async"></span>`;}
function xLink(user,post){
  const identifier=post==null?'':String(post);
  if(typeof user!=='string'||!/^[A-Za-z0-9_]{1,15}$/.test(user)||identifier&&!/^[0-9]{1,25}$/.test(identifier))return '';
  const url='https://x.com/'+user+(identifier?'/status/'+identifier:'');
  return `<a class="x-link" href="${url}" target="_blank" rel="noopener noreferrer">${identifier?'X 原帖':'X 博主'} ${icon('external')}</a>`;
}
let feedSeed=Math.random().toString(36).slice(2),feedSnapshot=0;
const feedOrders={random:'随机浏览',timeline:'时间：新到旧',oldest:'时间：旧到新'};
const labels={archive:'首页',favorites:'书签',bloggers:'博主',subscriptions:'计划',tasks:'下载',settings:'设置'};
const cadence={three_days:'每三天一次',weekly:'每七天一次',monthly:'每月一次',quarterly:'每季度一次',yearly:'每年一次',once:'仅下载这一次'};
const statuses={queued:'排队中',running:'正在下载',retry:'等待重试',succeeded:'已完成',failed:'失败',cancelled:'已取消'};
let state={},settings={},posts=new Map(),rows=new Map(),editSave,setupRequired=false,toastTimer,renderSequence=0,total=0,routeController;
let taskItems=[],taskFilter='all',taskQuery='',selectedTask='',taskLoadError='',taskCounts={all:0,active:0,attention:0,done:0},taskMutationBusy=false,taskSearchTimer,taskCooldownUntil=0;
const date=v=>v?new Date(typeof v==='number'?v*1000:v.replace(' ','T')).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
const scheduleOptions=(value='quarterly')=>(value==='daily'?'<option value="daily" selected>每天（旧计划）</option>':'')+Object.entries(cadence).map(([k,v])=>`<option value="${k}" ${value===k?'selected':''}>${v}</option>`).join('');
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500);}
function handleError(error){if(error.name!=='AbortError')toast(error.message||'暂时无法连接服务');}
async function api(path,method='GET',body,signal){
  const response=await fetch('/api/v1'+path,{method,credentials:'same-origin',signal,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){if(response.status===401&&!path.startsWith('/auth'))showAuth(false);throw Error(data.error||'请求未成功');}
  return data;
}
function empty(title,text,add=false){return `<div class="empty"><h2>${esc(title)}</h2><p>${esc(text)}</p>${add?'<button class="primary" data-action="add">添加内容</button>':''}</div>`;}
function showAuth(setup){setupRequired=setup;$('shell').hidden=true;$('auth-screen').hidden=false;$('auth-title').textContent=setup?'从这里开始':'欢迎回来';$('auth-description').textContent=setup?'设置一个网站密码，打开你的私人归档。':'打开你的私人归档。';$('auth-submit').textContent=setup?'设置密码并进入':'登录';$('password').autocomplete=setup?'new-password':'current-password';}
async function enter(){settings=await api('/settings');$('auth-screen').hidden=true;$('shell').hidden=false;$('password').value='';await route();await overview();}
async function overview(){
  const [data,people]=await Promise.all([api('/stats'),api('/bloggers')]);
  $('overview').textContent=`${data.posts.toLocaleString()} 篇帖子 · ${['archive','favorites'].includes(state.view)?feedOrders[state.order]:data.media.toLocaleString()+' 份媒体'}`;
  $('queue-badge').textContent=data.active_tasks;$('queue-badge').hidden=!data.active_tasks;
  $('side-summary').textContent=`${data.bloggers.toLocaleString()} 位博主 · ${data.media.toLocaleString()} 份图片与视频`;
  const selected=people.items.filter(b=>b.featured);
  $('side-bloggers').innerHTML=selected.slice(0,6).map(b=>`<button class="side-person" data-action="user" data-user="${esc(b.user)}">${avatar(b.user,b.display_name)}<span class="side-person-info"><strong>${esc(b.display_name||b.user)}</strong><small>@${esc(b.user)}</small></span>${icon('chevron')}</button>`).join('')||'<p class="muted">在博主页点亮星标，让喜欢的博主留在这里。</p>';
}
function readRoute(){const p=new URLSearchParams(location.search),view=p.get('view')||'archive';return {view:labels[view]?view:'archive',user:p.get('user')||'',post:p.get('post')||'',q:p.get('q')||'',tag:p.get('tag')||'',kind:p.get('kind')||'',section:p.get('section')||'account',featured:p.get('featured')==='1',order:Object.hasOwn(feedOrders,p.get('order'))?p.get('order'):'random',plan_order:['featured','next','name'].includes(p.get('plan_order'))?p.get('plan_order'):'featured',offset:Math.max(0,Number(p.get('offset'))||0)};}
function navigate(view,values={}){const p=new URLSearchParams();if(view!=='archive')p.set('view',view);for(const[k,v]of Object.entries(values))if(v)p.set(k,String(v));history.pushState(null,'','/'+(p.size?'?'+p:''));const loading=route().catch(handleError);window.scrollTo(0,0);return loading;}
function filters(){return {q:$('search').value.trim(),kind:state.kind,user:state.user,post:state.post,tag:state.tag,order:state.order};}
async function route(){
  const next=readRoute(),changingView=next.view!==state.view||next.section!==state.section,queryChanged=next.q!==state.q;
  if(next.view!=='tasks')clearTimeout(taskSearchTimer);
  routeController?.abort();routeController=new AbortController();const signal=routeController.signal;
  state=next;const sequence=++renderSequence,archive=['archive','favorites'].includes(state.view);
  if(changingView)$('content').innerHTML='<p class="section-note" role="status">正在读取'+labels[state.view]+'…</p>';
  $('shell').classList.toggle('archive-view',archive);$('content').classList.toggle('archive-feed',archive);$('shell').classList.toggle('settings-view',state.view==='settings');$('page-title').textContent=labels[state.view];
  $('shell').classList.toggle('tasks-view',state.view==='tasks');$('task-controls').hidden=state.view!=='tasks';
  if(state.view!=='tasks')$('task-dialog').close();
  document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav===state.view));
  $('archive-filters').hidden=!archive;$('pagination').hidden=true;$('filter-context').hidden=true;
  $('feed-order').value=state.order;$('shuffle-feed').hidden=state.order!=='random';
  if(changingView||queryChanged){$('search').value=state.q;$('right-search').value=state.q;}document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',b.dataset.kind===state.kind));
  const clear=state.user||state.post||state.tag||state.q||state.kind;$('clear-filters').hidden=!clear;
  const context=[state.user&&'@'+state.user,state.post&&'单条帖子',state.tag&&'#'+state.tag,state.q&&'搜索：'+state.q].filter(Boolean).join(' · ');$('filter-context').hidden=!archive||!context;$('filter-context').textContent=context;
  let html;
  try{if(archive)html=await renderArchive(sequence,signal);else if(state.view==='tasks')html=await renderTasks(sequence,signal);else if(state.view==='bloggers')html=await renderBloggers(sequence,signal);else if(state.view==='subscriptions')html=await renderSubscriptions(sequence,signal);else html=await renderSettings(sequence,signal);}
  catch(error){if(sequence!==renderSequence||error.name==='AbortError')return;$('content').innerHTML=empty('暂时无法读取'+labels[state.view],error.message||'请稍后刷新重试。');throw error;}
  if(sequence!==renderSequence)return;
  $('content').innerHTML=html;
  if(archive)pagination(total,30);if(state.view==='tasks')pagination(total,50);if(state.view==='settings')bindSettings();
}
function pagination(count,size){$('pagination').hidden=count<=size&&!state.offset;$('previous').disabled=!state.offset;$('next').disabled=state.offset+size>=count;$('page-number').textContent=`第 ${Math.floor(state.offset/size)+1} 页`;}
function mediaHTML(m){return m.kind==='video'?`<video src="${esc(m.url)}" controls preload="metadata" playsinline aria-label="归档视频"></video>`:`<img src="${esc(m.url)}" alt="归档图片" loading="lazy" decoding="async">`;}
function tagsHTML(p){return p.tags?`<div class="tag-list">${p.tags.split(',').filter(Boolean).map(tag=>`<button class="tag" data-action="tag" data-tag="${esc(tag)}">#${esc(tag)}</button>`).join('')}</div>`:'';}
function postCard(p){
  const media=p.media.slice(0,4),count=media.length;
  return `<article class="post-card"><div class="post-author"><a class="post-avatar" href="${esc(p.url)}" target="_blank" rel="noopener noreferrer" aria-label="在 X 打开这条帖子">${avatar(p.user,p.display_name)}</a><div class="post-identity"><button class="author-name" data-action="user" data-user="${esc(p.user)}">${esc(p.display_name||p.user)}</button><span class="author-meta">@${esc(p.user)} · ${esc(date(p.posted_at||p.added))}</span></div><button class="post-menu icon-button" data-action="share" data-id="${p.id}" aria-label="分享帖子">${icon('more')}</button></div><p class="post-text">${esc(p.content)}</p>${count?`<div class="media-grid count-${count}">${media.map((m,i)=>m.kind==='video'?`<div class="media-tile">${mediaHTML(m)}<button class="expand-video" data-action="detail" data-id="${p.id}" data-index="${i}" aria-label="全屏查看视频">${icon('external')}</button></div>`:`<button class="media-tile" data-action="detail" data-id="${p.id}" data-index="${i}" aria-label="查看图片 ${i+1}">${mediaHTML(m)}${i===3&&p.media.length>4?`<span class="media-overflow">＋${p.media.length-4}</span>`:''}</button>`).join('')}</div>`:''}${tagsHTML(p)}${p.note?`<p class="post-note">${esc(p.note)}</p>`:''}<div class="post-footer"><button data-action="edit-post" data-id="${p.id}" aria-label="编辑标签和备注">${icon('comment')}<span>备注</span></button><button class="${p.favorite?'selected':''}" data-action="favorite" data-id="${p.id}" aria-label="${p.favorite?'取消收藏':'收藏'}" aria-pressed="${!!p.favorite}">${icon('bookmark')}<span>${p.favorite?'已收藏':'收藏'}</span></button><button data-action="detail" data-id="${p.id}" ${!p.media.length?'disabled':''}>${icon('image')}<span>${p.media.length} 份媒体</span></button><a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer" aria-label="打开原帖">${icon('external')}</a></div></article>`;
}
function searchBloggerCard(b){
  const hasPlan=!!b.schedule;
  const schedule=hasPlan?(b.schedule==='daily'?'每天（旧计划）':cadence[b.schedule]||'未设频率'):'未设计划';
  const next=!hasPlan?'未设计划':b.schedule==='once'?'不自动更新':!b.enabled?'已暂停':date(b.next_run);
  return `<article class="person-row search-blogger">${avatar(b.user,b.display_name)}<div class="person-info"><div class="person-header"><div><h3>${esc(b.display_name||b.user)}</h3><span class="muted small">@${esc(b.user)}</span></div>${xLink(b.user)}</div><p class="search-blogger-counts">${b.post_count||0} 篇归档 · ${b.media_count||0} 份媒体 · ${b.favorite_count||0} 篇书签</p><dl class="search-blogger-facts"><dt>更新频率</dt><dd>${esc(schedule)}</dd><dt>最近完整更新</dt><dd>${b.last_success_at?date(b.last_success_at):'暂无记录'}</dd><dt>下次计划</dt><dd>${esc(next)}</dd><dt>内容收录到</dt><dd>${b.last_post_at?date(b.last_post_at):'暂无记录'}</dd></dl>${b.active_task?`<p class="search-blogger-status">当前任务：${esc(statuses[b.active_task.status]||b.active_task.status)}${b.active_task.error?' · '+esc(b.active_task.error):''}</p>`:''}${b.note?`<p>${esc(b.note)}</p>`:''}<div class="actions"><button data-action="user" data-user="${esc(b.user)}">归档</button><button class="quiet" data-action="user-favorites" data-user="${esc(b.user)}">书签</button><button class="quiet" data-action="plan-user" data-user="${esc(b.user)}">计划</button><button class="quiet" data-action="user-tasks" data-user="${esc(b.user)}">任务</button><button class="primary" data-action="download-user" data-user="${esc(b.user)}">立即更新</button></div></div></article>`;
}
async function renderArchive(sequence=renderSequence,signal){
  const p=new URLSearchParams({...state,favorite:state.view==='favorites',limit:30,seed:feedSeed,snapshot:feedSnapshot});p.delete('view');p.delete('section');
  const data=await api('/posts?'+p,'GET',undefined,signal);if(sequence!==renderSequence)return '';
  feedSnapshot=data.snapshot;posts=new Map(data.items.map(p=>[p.id,p]));total=data.total;
  const bloggers=data.bloggers||[];rows=new Map(bloggers.map(b=>[b.user,b]));
  const authors=bloggers.length?`<section class="search-bloggers" aria-label="匹配博主"><h2>匹配博主 <span class="muted small">${data.blogger_total??bloggers.length} 位${data.blogger_total>bloggers.length?'，显示前 '+bloggers.length+' 位':''}</span></h2>${bloggers.map(searchBloggerCard).join('')}</section>`:'';
  if(!data.items.length)return authors+(authors?'<p class="section-note">没有匹配的已归档帖子。</p>':empty(state.view==='favorites'?'你的书签，从这里开始':'这里还没有帖子',state.q||state.user||state.tag||state.post?'没有找到符合条件的内容，试试清除筛选。':'添加一位博主，或保存一条喜欢的帖子。',true));
  return authors+data.items.map(postCard).join('');
}
const taskActive=r=>['queued','running','retry'].includes(r.status);
function filteredTasks(){const query=taskQuery.trim().toLowerCase(),exact=/^@[a-z0-9_]{1,15}$/.test(query);return taskItems.filter(r=>(taskFilter==='all'||taskFilter==='active'&&taskActive(r)||taskFilter==='attention'&&['failed','retry'].includes(r.status)||taskFilter==='done'&&r.status==='succeeded')&&(exact?r.user.toLowerCase()===query.slice(1):`${r.user} ${r.post_id||''} ${r.id}`.toLowerCase().includes(query)));}
function selectedTaskId(items,id){return items.some(r=>r.id===id)?id:items[0]?.id||'';}
function taskResult(r){
  const next=Math.max(r.next_run||0,taskCooldownUntil);
  if(['queued','retry'].includes(r.status)&&taskCooldownUntil>Date.now()/1000)return 'X 账号限流，整个队列等待至 '+date(next)+'；任务已保留。';
  return r.status==='running'?(r.scanned_posts?`已读取 ${r.scanned_posts} 篇帖子，保存 ${r.progress} 份媒体。`:'正在连接 X，读取帖子…'):r.status==='succeeded'?(r.new_posts||r.new_media?`本次新增 ${r.new_posts} 篇帖子、${r.new_media} 份媒体。`:'检查完成，没有新增内容。'):r.status==='queued'?(next>Date.now()/1000?'已入队，计划于 '+date(next)+' 开始。':'等待前面的任务完成。'):r.status==='retry'?'将于 '+date(next)+' 再试一次。':r.status==='cancelled'?'任务已取消，已保存的内容会保留。':'本次下载未完成，可以重试。';
}
function taskBadge(r){return `<span class="badge ${esc(r.status)}">${esc(statuses[r.status]||r.status)}</span>`;}
function taskActions(r){
  const active=taskActive(r),id=esc(r.id);
  return `<button data-action="user" data-user="${esc(r.user)}">查看归档</button>`
    +(['failed','cancelled','retry'].includes(r.status)?`<button class="task-retry" data-action="retry" data-id="${id}">${icon('refresh')}重试</button>`:'')
    +(active||r.status==='failed'?`<button class="quiet" data-action="cancel" data-id="${id}">取消</button>`:'')
    +(r.status==='succeeded'?`<button class="quiet" data-action="download-task" data-id="${id}">再次检查</button>`:'')
    +(!active?`<button class="quiet danger" data-action="delete" data-id="${id}">删除任务</button>`:'');
}
function taskDetails(r){return `<div class="task-detail-identity">${avatar(r.user)}<div><h3>@${esc(r.user)}</h3>${xLink(r.user,r.post_id)}<span class="muted small">${r.post_id?'单条帖子':'博主 · 全部可读取的帖子'}</span></div></div><div class="task-detail-result">${taskBadge(r)}<p>${esc(taskResult(r))}</p>${r.error?`<p class="error">${esc(r.error)}</p>`:''}</div><dl class="task-detail-fields"><dt>添加时间</dt><dd>${date(r.created)}</dd><dt>开始时间</dt><dd>${r.started?date(r.started):'尚未开始'}</dd><dt>尝试次数</dt><dd>${r.attempts} 次</dd>${r.status==='retry'?`<dt>下次重试</dt><dd>${date(r.next_run)}</dd>`:''}${r.post_id?`<dt>帖子 ID</dt><dd>${esc(r.post_id)}</dd>`:''}<dt>已有归档</dt><dd>${r.archive_posts} 篇帖子</dd><dt>任务编号</dt><dd class="task-id">${esc(r.id)}</dd></dl><div class="actions">${taskActions(r)}</div><p class="task-detail-note">已保存的文件会自动跳过；取消本次任务不会移除更新计划。</p>`;}
function taskCard(r){return `<article class="task-card ${esc(r.status)}"><span class="avatar task-icon">${icon('download')}</span><div class="task-info"><div class="task-header"><div class="task-author"><strong>@${esc(r.user)}</strong>${xLink(r.user,r.post_id)}</div>${taskBadge(r)}</div><div class="task-meta">${r.post_id?'单条帖子':'全部帖子'} · ${date(r.created)}${r.attempts>1?' · 第 '+r.attempts+' 次尝试':''}</div><p class="task-result">${esc(taskResult(r))}</p>${r.error?`<p class="error">${esc(r.error)}</p>`:''}<div class="task-card-bottom"><span class="task-meta">已有 ${r.archive_posts} 篇归档</span><div class="actions"><button class="text-button" data-action="task-select" data-id="${esc(r.id)}">查看详情</button>${taskActions(r)}</div></div></div></article>`;}
function currentTaskPanel(){const r=taskItems.find(r=>r.status==='running');if(!r)return `<aside class="task-current-panel"><h2>本页正在保存</h2><p class="muted small">本页没有正在下载的任务。</p><p class="task-detail-note">单条帖子保存原文和媒体；博主任务保存全部可读取的内容。</p></aside>`;return `<aside class="task-current-panel"><h2>本页正在保存</h2><div class="task-detail-identity">${avatar(r.user)}<div><h3>@${esc(r.user)}</h3><span class="muted small">全部可读取的内容</span></div></div><div class="task-progress-counts"><div><strong>${r.scanned_posts||0}</strong><span>已读取帖子</span></div><div><strong>${r.progress||0}</strong><span>已保存媒体</span></div></div><p class="task-detail-note">长任务会持续更新归档，已保存的文件自动跳过。</p><button class="text-button" data-action="user" data-user="${esc(r.user)}">查看此博主归档</button></aside>`;}
function updateTaskControls(){
  const items=filteredTasks();selectedTask=selectedTaskId(items,selectedTask);$('page-title').textContent='下载任务';
  const counts=taskCounts,labels={all:'全部',active:'进行中',attention:'需关注',done:'已完成'};
  document.querySelectorAll('[data-action="task-filter"]').forEach(b=>{b.classList.toggle('active',b.dataset.filter===taskFilter);b.setAttribute('aria-pressed',String(b.dataset.filter===taskFilter));b.innerHTML=`${labels[b.dataset.filter]} <span>${counts[b.dataset.filter]}</span>`;});
  $('task-summary').innerHTML=Object.entries(counts).map(([key,count])=>`<div><strong>${taskLoadError?'—':count}</strong><span>${labels[key]}</span></div>`).join('');
  document.querySelectorAll('[data-action="task-bulk"]').forEach(button=>button.disabled=taskMutationBusy||!total||!!taskLoadError);
  $('task-cooldown').hidden=taskCooldownUntil<=Date.now()/1000;
  $('task-cooldown').textContent='X 账号限流，整个队列等待至 '+date(taskCooldownUntil)+'。立即更新只会入队，不能跳过等待。';
  if($('task-search').value!==taskQuery)$('task-search').value=taskQuery;
  $('task-filter-summary').textContent=taskLoadError?'任务读取失败，请重新加载。':`匹配 ${total} 项，本页显示 ${items.length} 项。批量操作覆盖当前筛选的全部匹配任务；删除只移除任务记录，活动任务请先取消。`;
}
function taskPage(){
  if(taskLoadError)return `<div class="task-message" role="alert"><h2>暂时无法读取任务</h2><p class="error">${esc(taskLoadError)}</p><button data-action="task-refresh">重新加载</button></div>`;
  if(!taskItems.length&&taskFilter==='all'&&!taskQuery)return empty('把喜欢的内容留下来','添加一条帖子或博主，这里会显示下载进度。',true);
  const items=filteredTasks();
  const blank=`<div class="task-message"><h2>没有匹配任务</h2><p class="muted">换一个名称，或清除筛选后查看其他任务。</p><button data-action="task-clear">清除筛选</button></div>`;
  return `<div class="task-layout-content"><section>${items.length?items.map(taskCard).join(''):blank}</section>${currentTaskPanel()}</div>`;
}
function renderTaskContent(){if(state.view!=='tasks')return;updateTaskControls();$('content').innerHTML=taskPage();}
async function renderTasks(sequence,signal){
  const offset=state.offset;if(!taskItems.length)$('content').innerHTML='<p class="section-note" role="status">正在读取下载任务…</p>';
  const params=new URLSearchParams({filter:taskFilter,q:taskQuery.trim(),limit:50,offset});
  try{const data=await api('/tasks?'+params,'GET',undefined,signal);if(sequence!==renderSequence)return '';taskItems=data.items;taskCounts=data.counts;taskCooldownUntil=data.cooldown_until||0;taskLoadError='';total=data.total;rows=new Map(taskItems.map(r=>[r.id,r]));}
  catch(error){if(sequence!==renderSequence)return '';taskItems=[];selectedTask='';taskLoadError=error.message||'请求未成功';total=0;}
  updateTaskControls();return taskPage();
}
async function renderBloggers(sequence=renderSequence,signal){
  const data=await api('/bloggers'+(state.featured?'?featured=true':''),'GET',undefined,signal);if(sequence!==renderSequence)return '';rows=new Map(data.items.map(b=>[b.user,b]));
  const tabs=`<div class="feed-tabs"><button data-action="all-bloggers" class="${!state.featured?'active':''}">全部博主</button><button data-action="pick-featured" class="${state.featured?'active':''}">精选博主</button></div>`;
  if(!data.items.length)return tabs+empty(state.featured?'把喜欢的博主放在前面':'你的博主',state.featured?'在全部博主中点亮星标，下次就能快速找到。':'添加博主后会保存全部内容。',!state.featured);
  return tabs+data.items.map(b=>`<article class="person-row">${avatar(b.user,b.display_name)}<div class="person-info"><div class="person-header"><h3>${esc(b.display_name||b.user)}</h3><button class="star-button icon-button ${b.featured?'selected':''}" data-action="feature-blogger" data-user="${esc(b.user)}" aria-label="${b.featured?'取消精选':'设为精选'} ${esc(b.user)}" aria-pressed="${!!b.featured}">${icon('star')}</button></div><span class="muted small">@${esc(b.user)} · ${b.post_count} 篇帖子</span>${b.note?`<p>${esc(b.note)}</p>`:''}<div class="actions"><button data-action="user" data-user="${esc(b.user)}">查看帖子</button><button class="quiet" data-action="download-user" data-user="${esc(b.user)}">立即更新</button><button class="quiet" data-action="plan-user" data-user="${esc(b.user)}">${b.schedule?(b.schedule==='daily'?'每天（旧计划）':cadence[b.schedule]):'设置更新'}</button><button class="quiet" data-action="edit-blogger" data-user="${esc(b.user)}">编辑</button></div></div></article>`).join('');
}
async function renderSubscriptions(sequence=renderSequence,signal){
  const data=await api('/subscriptions','GET',undefined,signal);if(sequence!==renderSequence)return '';rows=new Map(data.items.map(s=>[s.user,s]));
  if(!data.items.length)return empty('按照你的节奏','默认每季度更新一次，也可以选择三天、七天、一个月、一年或仅下载一次。',true);
  const selected=data.items.filter(s=>!state.featured||s.featured).sort((a,b)=>(state.plan_order==='featured'?b.featured-a.featured:0)||(state.plan_order==='name'?a.display_name.localeCompare(b.display_name,'zh-CN'):Number(!a.enabled)-Number(!b.enabled)||a.next_run-b.next_run)||a.user.localeCompare(b.user));
  const toolbar=`<div class="plan-toolbar"><label class="check-label"><input id="plan-featured" type="checkbox" ${state.featured?'checked':''}>只看星标 <span class="muted">${selected.length}</span></label><select id="plan-order" class="inline-select" aria-label="计划排序"><option value="featured" ${state.plan_order==='featured'?'selected':''}>星标优先</option><option value="next" ${state.plan_order==='next'?'selected':''}>下次更新时间</option><option value="name" ${state.plan_order==='name'?'selected':''}>博主名称</option></select></div>`;
  const plans=selected.map(s=>`<article class="plan-row" data-plan-user="${esc(s.user)}"><div class="plan-avatar">${avatar(s.user,s.display_name)}<button class="star-button icon-button ${s.featured?'selected':''}" data-action="feature-blogger" data-user="${esc(s.user)}" aria-label="${s.featured?'取消星标':'设为星标'} ${esc(s.user)}" aria-pressed="${!!s.featured}">${icon('star')}</button></div><div class="plan-info"><div class="plan-header"><div><h3>${esc(s.display_name)}</h3><small class="muted">@${esc(s.user)}</small></div><select class="inline-select" data-plan="${esc(s.user)}" aria-label="${esc(s.user)} 更新频率">${scheduleOptions(s.schedule)}</select></div><p>${s.schedule==='once'?'不再自动更新':s.enabled?'下次更新：'+date(s.next_run):'自动更新已暂停'}</p><div class="actions"><button data-action="download-user" data-user="${esc(s.user)}">立即更新</button><button class="quiet" data-action="user" data-user="${esc(s.user)}">查看帖子</button><button class="quiet danger" data-action="delete-sub" data-user="${esc(s.user)}">移除计划</button></div></div></article>`).join('');
  return toolbar+(plans||empty('还没有星标计划','点亮博主头像旁的星号，就可以在这里单独查看。'))+'<p class="section-note">本次升级将已有计划分散到未来三个月；新计划按所选周期在北京时间 08:00–18:00 随机开始；已开始的下载可继续完成。手动立即更新不受此时间段限制。星标与博主精选共用；「仅下载这一次」会停止后续自动更新。</p>';
}
async function renderSettings(sequence=renderSequence,signal){
  const data=await api('/settings','GET',undefined,signal);if(sequence!==renderSequence)return '';settings=data;const sections={account:'你的账号',updates:'下载与更新',connections:'连接与扩展',about:'关于拾存'},section=sections[state.section]?state.section:'account';let body='';
  if(section==='account')body=`<div class="settings-heading"><h2>X 登录状态</h2><p>使用 X 的登录 Cookie 访问你有权限查看的帖子。</p></div><div class="settings-body"><div class="setting-status">${icon(settings.cookie_configured?'check':'close')}${settings.cookie_configured?'已保存 X Cookie':'尚未连接 X'}</div><form id="settings-form"><label>X Cookie<textarea id="cookies" autocomplete="off" spellcheck="false" placeholder="auth_token=…; ct0=…\n或粘贴 cookies.txt 的完整内容"></textarea></label><p class="muted">已保存的 Cookie 不会回显。留空可保留当前登录信息。</p><label class="check-label"><input id="clear-cookies" type="checkbox">清除当前 Cookie</label><button class="primary" type="submit">保存</button></form></div>`;
  if(section==='updates')body=`<div class="settings-heading"><h2>下载与更新</h2><p>保存完整内容，只选择适合你的更新节奏。</p></div><div class="settings-row"><span>下载范围</span><span class="muted">全部可读取的内容</span></div><div class="settings-body"><form id="settings-form"><label>新添加博主的默认更新频率<select id="default-schedule">${scheduleOptions(settings.default_schedule)}</select></label><p class="muted">一个月、一季度和一年按日历计算。本次升级将已有计划分散到未来三个月；新计划按所选周期在北京时间 08:00–18:00 随机开始；已开始的下载可继续完成。</p><label>下载代理（HTTP / HTTPS）<input id="proxy" type="password" maxlength="2048" autocomplete="off" placeholder="http://代理地址:端口"></label><p class="muted">${settings.proxy_configured?'已配置代理，留空保留；地址和凭据不会回显。':'未配置代理，留空直接连接。'}</p><label class="check-label"><input id="clear-proxy" type="checkbox">清除当前代理</label><button class="primary" type="submit">保存</button></form></div>`;
  if(section==='connections'){
    const clients=await api('/clients','GET',undefined,signal);if(sequence!==renderSequence)return '';
    body=`<div class="settings-heading"><h2>连接与扩展</h2><p>在 X 页面直接保存帖子、添加博主或打开归档。</p></div><div class="settings-body"><div class="setting-status">${icon(settings.connection_password_configured?'check':'close')}${settings.connection_password_configured?'已设置连接密码':'请先设置连接密码'}</div><form id="connection-password-form"><label>${settings.connection_password_configured?'修改连接密码':'连接密码'}<input id="connection-password" type="password" minlength="8" maxlength="256" autocomplete="new-password" placeholder="由你设置，至少 8 个字符" required></label><label>再输入一次<input id="connection-password-confirm" type="password" minlength="8" maxlength="256" autocomplete="new-password" required></label><p class="muted">所有浏览器使用你设置的同一个连接密码。修改后，需要在脚本里重新填写。X Cookie 始终保留在服务端。</p><button class="primary" type="submit">保存连接密码</button></form><div class="connection"><h3>在其他浏览器使用</h3><p><a href="/integrations/xarchive.user.js" target="_blank" rel="noopener">安装 / 更新脚本猫脚本 ↗</a></p><label>归档网站地址<input value="${esc(location.origin)}" readonly aria-label="归档网站地址"></label><p class="muted">安装后刷新 X 页面，点击 Grok 上方的下载图标。在「连接设置」填写本站地址和你设置的连接密码，点击「验证并保存」；验证通过才会显示已连接。</p></div>${clients.items.length?`<details class="connection"><summary>旧版连接管理（${clients.items.length}）</summary><p class="muted">已配置的旧连接继续可用，不需要重新安装。</p>${clients.items.map(c=>`<div class="client-row"><div>${esc(c.name)}<small>${date(c.created)}</small></div><button class="danger" data-action="revoke-client" data-id="${c.id}">撤销</button></div>`).join('')}</details>`:''}</div>`;
  }
  if(section==='about')body=`<div class="settings-heading"><h2>拾存</h2><p>喜欢的，留下来。</p></div><div class="settings-row"><span>下载引擎</span><span class="muted">gallery-dl ${esc(settings.engine_version)}</span></div><div class="settings-body"><p>帖子、图片和视频保存在自己的存储中；收藏、标签和备注也会一直保留。</p><p class="muted">数据位置</p><div class="data-path">${esc(settings.data_dir)}</div><p class="muted">${settings.legacy_attached?'旧媒体通过只读卷接入。备份时同时保留旧媒体目录和新版数据目录；计划与登录设置保存在新版数据中。':'备份整个数据目录可同时保留归档、计划和登录设置。'}</p></div>`;
  return `<div class="settings-layout"><nav class="settings-nav" aria-label="设置分类">${Object.entries(sections).map(([key,title])=>`<button class="${section===key?'active':''}" data-settings="${key}">${title}${icon('chevron')}</button>`).join('')}</nav><section class="settings-panel">${body}</section></div>`;
}
function bindSettings(){
  const form=$('settings-form');if(form)form.onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{await api('/settings','PUT',{cookies:$('cookies')?.value||null,clear_cookies:$('clear-cookies')?.checked||false,default_schedule:$('default-schedule')?.value||settings.default_schedule,x_proxy:$('proxy')?.value||null,clear_proxy:$('clear-proxy')?.checked||false});if($('cookies'))$('cookies').value='';if($('proxy'))$('proxy').value='';toast('设置已保存');await route();}catch(error){handleError(error);}finally{b.disabled=false;}};
  const connectionForm=$('connection-password-form');if(connectionForm)connectionForm.onsubmit=async event=>{event.preventDefault();const b=event.submitter,password=$('connection-password'),confirm=$('connection-password-confirm');if(password.value!==confirm.value){toast('两次输入的连接密码不一致');confirm.focus();return;}b.disabled=true;try{await api('/connection/password','PUT',{password:password.value});password.value=confirm.value='';toast('连接密码已保存，在其他浏览器填写同一个密码即可');await route();}catch(error){handleError(error);}finally{b.disabled=false;}};
}
function showDetail(id,index=0){const p=posts.get(id);if(p)openMedia(p.media,index);}
function showEdit(title,fields,save){closeMedia();$('edit-title').textContent=title;$('edit-fields').innerHTML=fields;editSave=save;$('edit-dialog').showModal();}
async function copyLink(value){
  try{await navigator.clipboard.writeText(value);toast('链接已复制');return;}catch{}
  const input=document.createElement('textarea');input.value=value;$('share-dialog').append(input);input.select();
  const copied=document.execCommand('copy');input.remove();toast(copied?'链接已复制':'请选中显示的链接，按 Ctrl+C 复制');
}
async function showShare(id){
  const p=posts.get(id);if(!p)return;
  const share=await api('/posts/'+id+'/share');
  $('share-content').innerHTML=`<p class="muted">${esc(p.display_name||p.user)} 的帖子</p><button class="share-option" data-action="share-archive" data-id="${id}" ${!share.enabled?'disabled':''}>${icon('external')}<span>${share.active?'复制外网分享链接':'分享本站帖子'}<small>生成独立页面，接收方无需登录</small></span></button><button class="share-option" data-action="share-original" data-id="${id}">${icon('external')}<span>复制 X 原帖链接</span></button>${share.active?`<label class="share-link-label">外网分享链接<input id="public-share-link" readonly value="${esc(share.url)}" aria-label="外网分享链接"></label><button class="text-button danger" data-action="stop-sharing" data-id="${id}">停止本站分享</button>`:!share.enabled?'<p class="muted small">外网分享入口尚未连接。</p>':''}${p.media.length?`<div class="share-files"><h3>保存媒体文件</h3><p class="muted small">下载原文件后可发送到其他应用。</p>${p.media.map((m,i)=>`<a class="share-file" href="${esc(m.url)}?download=true" download>${icon('download')}${m.kind==='video'?'视频':'图片'} ${i+1}</a>`).join('')}</div>`:''}`;
  if(!$('share-dialog').open)$('share-dialog').showModal();
  if($('public-share-link'))$('public-share-link').onclick=event=>event.target.select();
}
function showAdd(){const schedule=settings.default_schedule||'quarterly';$('intake-schedule').innerHTML=scheduleOptions(schedule);$('intake-schedule').value=schedule;updateIntake();$('add-dialog').showModal();}
function updateIntake(){const post=/\/status\/\d+/.test($('intake-url').value);$('intake-schedule-label').hidden=post;$('intake-hint').textContent=post?'保存这条帖子的原文和全部媒体。':'首次保存全部可读取的内容，已有文件自动跳过。本次升级将已有计划分散到未来三个月；新计划按所选周期在北京时间 08:00–18:00 随机开始。';}
async function action(button){
  const{action:a,id,user,tag}=button.dataset;
  if(a==='task-filter'){if(!['all','active','attention','done'].includes(button.dataset.filter))return;taskFilter=button.dataset.filter;return navigate('tasks');}
  if(a==='task-clear'){taskFilter='all';taskQuery='';return navigate('tasks');}
  if(a==='task-refresh')return route();
  if(a==='task-select'){const task=taskItems.find(r=>r.id===id);if(!task)return;if(!filteredTasks().some(r=>r.id===id)){taskFilter='all';taskQuery='';}selectedTask=id;renderTaskContent();$('task-dialog-content').innerHTML=taskDetails(task);$('task-dialog').showModal();return;}
  if(a==='add')return showAdd();
  if(a==='share')return showShare(id);
  if(a==='share-original')return copyLink(posts.get(id).url);
  if(a==='share-archive'){const share=await api('/posts/'+id+'/share','POST');await showShare(id);await copyLink(share.url);return;}
  if(a==='stop-sharing'){await api('/posts/'+id+'/share','DELETE');await showShare(id);toast('分享已停止，原链接已失效');return;}
  if(a==='pick-featured')return navigate('bloggers',{featured:1});
  if(a==='all-bloggers')return navigate('bloggers');
  if(a==='feature-blogger'){const b=rows.get(user);await api('/bloggers/'+user,'PATCH',{featured:!b.featured});await route();await overview();return;}
  if(a==='user'){closeMedia();$('task-dialog').close();return navigate('archive',{user});}
  if(a==='user-favorites')return navigate('favorites',{user});
  if(a==='user-tasks'){taskQuery='@'+user;taskFilter='all';return navigate('tasks');}
  if(a==='tag'){closeMedia();return navigate('archive',{tag});}
  if(a==='detail')return showDetail(id,Number(button.dataset.index)||0);
  if(a==='favorite'){const p=posts.get(id);await api('/posts/'+id,'PATCH',{favorite:!p.favorite});await route();return;}
  if(['cancel','retry','delete','task-bulk'].includes(a)){
    if(taskMutationBusy)return;
    const operation=a==='task-bulk'?button.dataset.operation:a;
    if(!['delete','retry','cancel'].includes(operation))return;
    taskMutationBusy=true;if(state.view==='tasks')updateTaskControls();
    try{
      const result=await api(a==='task-bulk'?'/tasks/bulk':'/tasks/'+id+'/'+operation,'POST',a==='task-bulk'?{action:operation,filter:taskFilter,q:taskQuery.trim()}:undefined);
      $('task-dialog').close();
      if(state.view==='tasks')await navigate('tasks');
      await overview();
      const label={delete:'删除',retry:'重试',cancel:'取消'}[operation];
      toast(a==='task-bulk'?`已${label} ${result.affected} 项，跳过 ${result.skipped} 项。`:`已${label}任务。`);
    }finally{taskMutationBusy=false;if(state.view==='tasks')updateTaskControls();}
    return;
  }
  if(a==='download-user'||a==='download-task'){const url=a==='download-user'?'@'+user:taskItems.find(r=>r.id===id)?.url;if(!url)throw Error('任务已变化，请刷新后重试。');await api('/tasks','POST',{url,mode:'download'});$('task-dialog').close();toast('已加入下载队列');await overview();if(['tasks','archive','favorites'].includes(state.view))await route();return;}
  if(a==='plan-user'){const b=rows.get(user);showEdit('@'+user+' 的计划',`<label>什么时候更新<select id="edit-schedule">${scheduleOptions(b.schedule||settings.default_schedule)}</select></label><p class="muted small">下载全部内容，已有文件自动跳过。</p>`,()=>b.schedule?api('/subscriptions/'+user,'PATCH',{schedule:$('edit-schedule').value}):api('/tasks','POST',{url:'@'+user,schedule:$('edit-schedule').value}));return;}
  if(a==='delete-sub'){if(confirm('移除此计划？已保存的内容会保留。')){await api('/subscriptions/'+user,'DELETE');await route();}return;}
  if(a==='revoke-client'){if(confirm('撤销此连接凭据？')){await api('/clients/'+id,'DELETE');await route();}return;}
  if(a==='edit-post'){const p=posts.get(id);showEdit('编辑帖子',`<label>标签，用逗号分隔<input id="edit-tags" maxlength="500" value="${esc(p.tags)}"></label><label>备注<textarea id="edit-note" maxlength="4000">${esc(p.note)}</textarea></label>`,()=>api('/posts/'+id,'PATCH',{tags:$('edit-tags').value,note:$('edit-note').value}));return;}
  if(a==='edit-blogger'){const b=rows.get(user);showEdit('编辑博主',`<label>显示名称<input id="edit-name" maxlength="100" value="${esc(b.display_name)}"></label><label>备注<textarea id="edit-note" maxlength="2000">${esc(b.note)}</textarea></label>`,()=>api('/bloggers/'+user,'PATCH',{display_name:$('edit-name').value,note:$('edit-note').value}));}
}
document.addEventListener('click',async event=>{
  const nav=event.target.closest('[data-nav]'),setting=event.target.closest('[data-settings]'),kind=event.target.closest('[data-kind]'),close=event.target.closest('[data-close]'),button=event.target.closest('[data-action]');
  if(nav)return navigate(nav.dataset.nav);if(setting)return navigate('settings',{section:setting.dataset.settings});if(kind)return navigate(state.view,{...filters(),kind:kind.dataset.kind});if(close){$(close.dataset.close).close();return;}if(!button)return;
  button.disabled=true;try{await action(button);}catch(error){handleError(error);}finally{button.disabled=false;}
});
document.addEventListener('input',event=>{if(event.target.id==='task-search'){taskQuery=event.target.value;clearTimeout(taskSearchTimer);taskSearchTimer=setTimeout(()=>navigate('tasks'),200);}});
document.addEventListener('change',async event=>{if(['plan-featured','plan-order'].includes(event.target.id))return navigate('subscriptions',{featured:$('plan-featured').checked?1:0,plan_order:$('plan-order').value});if(!event.target.matches('[data-plan]'))return;const select=event.target;select.disabled=true;try{await api('/subscriptions/'+select.dataset.plan,'PATCH',{schedule:select.value});toast('计划已保存');await route();}catch(error){handleError(error);}finally{select.disabled=false;}});
$('auth-form').onsubmit=async event=>{event.preventDefault();$('auth-submit').disabled=true;$('auth-error').textContent='';try{await api('/auth/'+(setupRequired?'setup':'login'),'POST',{password:$('password').value});await enter();}catch(error){$('auth-error').textContent=error.message;}finally{$('auth-submit').disabled=false;}};
$('intake-form').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{const post=/\/status\/\d+/.test($('intake-url').value);const data=await api('/tasks','POST',{url:$('intake-url').value,mode:post?'download':'subscribe',schedule:post?'once':$('intake-schedule').value});$('add-dialog').close();toast(data.duplicate?'已有任务，未重复添加':'已开始保存');navigate('tasks');await overview();}catch(error){handleError(error);}finally{b.disabled=false;}};
$('intake-url').oninput=updateIntake;
$('edit-form').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{await editSave();$('edit-dialog').close();toast('已保存');await route();await overview();}catch(error){handleError(error);}finally{b.disabled=false;}};
$('search-form').onsubmit=event=>{event.preventDefault();feedSnapshot=0;return navigate('archive',{q:$('search').value.trim(),order:state.order});};
$('right-search-form').onsubmit=event=>{event.preventDefault();feedSnapshot=0;return navigate('archive',{q:$('right-search').value.trim(),order:state.order});};
$('clear-filters').onclick=()=>navigate(state.view,{order:state.order});$('add-task').onclick=showAdd;$('sidebar-add').onclick=showAdd;
function shuffleFeed(){feedSeed=Math.random().toString(36).slice(2);feedSnapshot=0;navigate(state.view,{...filters(),offset:0});}
$('shuffle-feed').onclick=shuffleFeed;
$('feed-order').onchange=()=>{feedSnapshot=0;navigate(state.view,{...filters(),order:$('feed-order').value});overview().catch(handleError);};
$('refresh').onclick=()=>{if(['archive','favorites'].includes(state.view))shuffleFeed();else route().catch(handleError);overview().catch(handleError);};
$('logout').onclick=async()=>{try{await api('/auth/logout','POST');showAuth(false);}catch(error){handleError(error);}};
$('previous').onclick=()=>navigate(state.view,{...(state.view==='tasks'?{}:filters()),offset:Math.max(0,state.offset-(state.view==='tasks'?50:30))});
$('next').onclick=()=>navigate(state.view,{...(state.view==='tasks'?{}:filters()),offset:state.offset+(state.view==='tasks'?50:30)});
window.addEventListener('popstate',()=>route().catch(handleError));
// A tall collection rail follows the scroll until either edge reaches the viewport.
const sidebars=[...document.querySelectorAll('.right-sidebar')].map(element=>({element,top:null}));
let sidebarScrollY=window.scrollY,sidebarFrame=0;
function positionSidebars(){
  sidebarFrame=0;
  const y=Math.max(0,window.scrollY),delta=y-sidebarScrollY;
  sidebarScrollY=y;
  for(const sidebar of sidebars){
    const maximum=document.querySelector('.masthead').offsetHeight+24;
    const minimum=Math.min(maximum,window.innerHeight-sidebar.element.offsetHeight-24);
    sidebar.top=Math.max(minimum,Math.min(maximum,(sidebar.top??maximum)-delta));
    sidebar.element.style.setProperty('--sidebar-top',`${sidebar.top}px`);
  }
}
function scheduleSidebars(){if(!sidebarFrame)sidebarFrame=requestAnimationFrame(positionSidebars);}
const sidebarResize=new ResizeObserver(scheduleSidebars);
sidebars.forEach(({element})=>sidebarResize.observe(element));
window.addEventListener('scroll',scheduleSidebars,{passive:true});
window.addEventListener('resize',scheduleSidebars);
setInterval(async()=>{if($('shell').hidden||document.hidden)return;try{await overview();if(state.view==='tasks'&&!document.querySelector('dialog[open]'))await route();}catch{}},5000);
(async()=>{try{const auth=await api('/auth');if(auth.logged_in)await enter();else showAuth(auth.setup_required);}catch(error){showAuth(false);$('auth-error').textContent=error.message;}})();
