// ==UserScript==
// @name         拾存 · X 下载与归档
// @namespace    xarchive.personal
// @version      1.4.9
// @description  下载帖子、查看博主订阅与更新时间、立即更新，并打开自己的归档。
// @match        https://x.com/*
// @match        https://www.x.com/*
// @match        https://twitter.com/*
// @match        https://mobile.twitter.com/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @grant        GM_openInTab
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(() => {
  'use strict';
  if (window.top !== window.self) return;
  const reserved = new Set('home explore search settings i notifications messages compose intent login logout signup tos privacy jobs about download'.split(' '));
  const host = document.createElement('div');
  host.id = 'xarchive-dock';
  host.style.cssText = 'position:fixed;right:20px;bottom:158px;z-index:2147483646;--dock-size:55px;--dock-radius:16px;--surface:rgba(255,255,255,.95);--ink:#0f1419;--line:#9fb5c3;--dock-shadow:0 0 15px #65778633,0 0 3px 1px #65778626';
  const root = host.attachShadow({mode:'closed'});
  root.innerHTML = `
    <style>
      *{box-sizing:border-box}button,input,select{font:inherit}button{cursor:pointer;border:1px solid var(--line);border-radius:10px;padding:11px 13px;background:transparent;color:var(--ink)}button:hover{background:color-mix(in srgb,var(--ink) 7%,transparent)}button:disabled{opacity:.45;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--ink);outline-offset:3px}section{position:absolute;right:calc(var(--dock-size) + 12px);bottom:0;font:14px system-ui,"Microsoft YaHei",sans-serif;width:min(320px,calc(100vw - 24px));max-height:calc(100dvh - 32px);overflow:auto;background:var(--surface);backdrop-filter:blur(20px);color:var(--ink);border:1px solid var(--line);border-radius:20px;padding:18px;box-shadow:var(--dock-shadow)}header{display:flex;align-items:center;justify-content:space-between}h3{font-size:16px;margin:0}header button{border:0;border-radius:50%;font-size:21px;padding:0;width:30px;height:30px}p{line-height:1.7;margin:14px 0;overflow-wrap:anywhere}.actions{display:grid;gap:8px}.primary{background:var(--ink);color:var(--surface);border-color:var(--ink)}.primary:hover{background:var(--ink);filter:brightness(.9)}small{font-size:12px;opacity:.7}label{display:block;margin:16px 0 12px;font-size:13px}input,select{width:100%;padding:10px;border:1px solid var(--line);border-radius:9px;background:var(--surface);color:var(--ink);margin-top:7px}#toggle{display:grid;place-items:center;width:var(--dock-size);height:var(--dock-size);border-radius:var(--dock-radius);padding:0;background:var(--surface);color:var(--ink);border:var(--dock-border,1px solid var(--line));box-shadow:var(--dock-shadow);backdrop-filter:blur(16px)}#toggle:hover{filter:brightness(.94)}#toggle svg{width:var(--dock-icon,32px);height:var(--dock-icon,32px);fill:currentColor;pointer-events:none}#status{min-height:20px;font-size:12px}.foot{display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid var(--line);padding-top:10px;margin-top:10px}.foot button{font-size:12px;border:0;padding:5px}#save{width:100%;margin-top:12px}[hidden]{display:none!important}@media(max-width:600px){section{position:fixed;right:12px;bottom:calc(var(--dock-bottom,158px) + var(--dock-size) + 12px);max-height:calc(100dvh - var(--dock-bottom,158px) - var(--dock-size) - 24px)}}
      #post:not(.primary){background:color-mix(in srgb,var(--ink) 7%,var(--surface))}.primary:disabled{opacity:1}#toggle.active{background:var(--ink);color:var(--surface)}#status{white-space:pre-line}
      section{max-height:calc(100dvh - var(--dock-bottom,158px) - 12px);padding:17px 18px}header{gap:8px}.heading{display:flex;align-items:center;gap:9px}#connection{font-size:11px;white-space:nowrap}#connection:before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor;margin-right:5px}#connection[data-state="connected"]:before{background:#28a477}#subject{margin:14px 0 10px;font-weight:650;font-size:15px;line-height:1.5}.subscription{border:1px solid color-mix(in srgb,var(--line) 75%,transparent);border-radius:12px;padding:11px 12px;background:color-mix(in srgb,var(--ink) 3%,var(--surface));margin-bottom:13px}.subscription-top{display:flex;align-items:center;justify-content:space-between;gap:8px}#subscription-state{font-weight:650;font-size:13px}#subscription-state:before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--state-color,#8899a6);margin-right:7px}.subscription[data-state="subscribed"]{--state-color:#28a477}.subscription[data-state="paused"],.subscription[data-state="once"]{--state-color:#bd8a30}.subscription[data-state="error"]{--state-color:#c85745}#refresh-subscription{border:0;padding:3px 4px;font-size:12px;white-space:nowrap}.times{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:9px 0 0}.times dt{font-size:11px;opacity:.65;margin-bottom:3px}.times dd{margin:0;font-size:12px;overflow-wrap:anywhere}#subscription-note{font-size:11px;line-height:1.5;opacity:.7;margin:8px 0 0}.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}.row button{font-size:13px;padding-inline:7px}#post{font-weight:650}#profile{width:100%}.schedule-label{font-size:12px;margin:13px 0 8px}.schedule-hint{font-size:11px;line-height:1.5;display:block}#status{margin:10px 0 0}.foot{margin-top:8px;padding-top:8px}@media(max-width:600px){section{max-height:calc(100dvh - var(--dock-bottom,158px) - var(--dock-size) - 24px)}}
    </style>
    <button id="toggle" aria-label="打开拾存浮窗" aria-expanded="false" title="拾存 · 下载与归档"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 3h2v10.59l4.3-4.3 1.4 1.42L12 17.4l-6.7-6.69 1.4-1.42 4.3 4.3V3ZM4 18h2v3h12v-3h2v5H4v-5Z"/></svg></button>
    <section hidden aria-label="拾存下载浮窗">
      <header><div class="heading"><h3>拾存 · X 归档</h3><small id="connection">尚未连接</small></div><button id="close" aria-label="收起浮窗">×</button></header>
      <div id="actions-view">
        <p id="subject">打开博主主页，或将鼠标移到帖子上。</p>
        <div id="subscription" class="subscription" data-state="idle" aria-live="polite" aria-atomic="true">
          <div class="subscription-top"><span id="subscription-state">打开面板后查询</span><button id="refresh-subscription" aria-label="刷新当前博主订阅状态">刷新 ↻</button></div>
          <dl class="times"><div><dt>最近成功更新</dt><dd id="last-update">—</dd></div><div><dt>下次计划更新</dt><dd id="next-update">—</dd></div></dl>
          <p id="subscription-note" hidden></p>
        </div>
        <div class="actions">
          <button id="post">下载当前帖子</button>
          <div class="row"><button id="update" title="检查博主内容，不修改已有订阅周期；首次下载按历史范围策略处理。">立即更新博主</button><button id="archive">查看归档 ↗</button></div>
        </div>
        <label class="schedule-label">订阅更新频率 <select id="schedule" aria-label="更新频率"><option value="three_days">每三天一次</option><option value="weekly">每七天一次</option><option value="monthly">每月一次</option><option value="quarterly">每季度一次</option><option value="yearly">每年一次</option><option value="once">仅下载这一次</option></select></label>
        <button id="profile" title="立即提交首次历史下载，并按所选周期自动更新；历史范围确认请到网站处理。">下载博主并订阅</button>
        <small class="schedule-hint">自动更新在北京时间 08:00–18:00 随机开始，已开始的下载可继续完成。</small>
      </div>
      <form id="settings-view" hidden>
        <label>归档网站地址<input id="server" type="url" placeholder="https://your-archive.example" required></label>
        <label>连接密码<input id="credential" type="password" autocomplete="off" placeholder="填写你在网站设置的连接密码；留空验证已保存的连接"></label>
        <small>所有浏览器使用同一个连接密码；X Cookie 在网站设置中填写。内网可用私有 IP 的 HTTP 地址，外网使用 HTTPS；HTTP 不加密连接密码。</small>
        <button id="save" class="primary" type="submit">验证并保存</button>
      </form>
      <p id="status" role="status" aria-live="polite"></p>
      <div class="foot"><small>拾存 · 下载与归档</small><button id="settings">连接设置</button></div>
    </section>`;
  document.body.appendChild(host);
  const $ = id => root.getElementById(id);
  let base = String(GM_getValue('server','')), token = String(GM_getValue('token',''));
  let hovered = null, lastURL = location.href, busy = false, lastSubmission = null;
  let connectionState = base && token ? 'checking' : 'unconfigured', connectionGeneration = 0;
  let bloggerState = {key:'',state:'idle'};
  const cadence = {daily:'每天',three_days:'每三天',weekly:'每七天',monthly:'每月',quarterly:'每季度',yearly:'每年',once:'仅一次'};
  GM_setValue('activeTask',null);
  if (!GM_getValue('quarterlyDefaultV1',false)) {
    GM_setValue('schedule','quarterly');
    GM_setValue('quarterlyDefaultV1',true);
  }
  const savedSchedule = GM_getValue('schedule','quarterly');
  if (savedSchedule === 'daily') $('schedule').innerHTML += '<option value="daily">每天（旧计划）</option>';
  $('schedule').value = savedSchedule;

  function parse(path) {
    const p = path.split('/').filter(Boolean);
    if (!p.length || !/^[A-Za-z0-9_]{1,15}$/.test(p[0]) || reserved.has(p[0].toLowerCase())) return null;
    const post = p[1] === 'status' && /^\d{1,25}$/.test(p[2] || '') ? p[2] : null;
    if (p.length > 1 && !post && !['media','tweets','with_replies','highlights','articles'].includes(p[1])) return null;
    return {user:p[0].toLowerCase(), post};
  }
  function selected() {
    const current = parse(location.pathname);
    return current?.post ? current : hovered || current;
  }
  function alignDock() {
    const buttons = [...document.querySelectorAll('button[data-testid="GrokDrawerHeader"],button[data-testid="chat-drawer-main"]')]
      .map(button => ({button, rect:button.getBoundingClientRect()}))
      .filter(({rect}) => rect.width >= 36 && rect.width <= 96 && rect.height >= 36 && rect.height <= 96 && rect.top > 0 && rect.right <= innerWidth+1 && rect.bottom <= innerHeight+1)
      .sort((a,b) => a.rect.top - b.rect.top);
    if (!buttons.length) {
      const bottom = Math.min(158,Math.max(12,innerHeight-80));
      host.style.left = host.style.top = 'auto'; host.style.right = '20px'; host.style.bottom = bottom+'px';
      host.style.setProperty('--dock-bottom',bottom+'px');
      return;
    }
    const {button,rect} = buttons[0], style = getComputedStyle(button);
    const next = buttons.find(({rect:other}) => Math.abs(other.left-rect.left) < 2 && other.top >= rect.bottom);
    const gap = next ? next.rect.top-rect.bottom : 12;
    const top = Math.max(12,rect.top-rect.height-gap);
    host.style.left = rect.left+'px'; host.style.top = top+'px'; host.style.right = 'auto'; host.style.bottom = 'auto';
    for (const [name,value] of Object.entries({'--dock-size':rect.width+'px','--dock-radius':style.borderRadius,
      '--surface':style.backgroundColor,'--ink':style.color,'--line':style.borderColor,'--dock-border':style.border,
      '--dock-shadow':style.boxShadow,'--dock-icon':(button.querySelector('svg')?.getBoundingClientRect().width || 32)+'px',
      '--dock-bottom':Math.max(0,innerHeight-top-rect.height)+'px'})) host.style.setProperty(name,value);
  }
  function update() {
    alignDock();
    if (location.href !== lastURL) {lastURL = location.href; hovered = null;}
    const item = selected();
    const samePost = lastSubmission && lastSubmission.user === item?.user && lastSubmission.post_id === item?.post;
    const sameProfile = lastSubmission && lastSubmission.user === item?.user && !lastSubmission.post_id && lastSubmission.mode === 'subscribe';
    const sameUpdate = lastSubmission && lastSubmission.user === item?.user && lastSubmission.mode === 'update';
    $('subject').textContent = item ? '@' + item.user + (item.post ? ' · 当前选中帖子' : ' · 博主主页') : '打开博主主页，或将鼠标移到帖子上。';
    $('post').disabled = busy || connectionState === 'checking' || !item?.post;
    $('profile').disabled = busy || connectionState === 'checking' || !item?.user;
    $('update').disabled = busy || connectionState === 'checking' || !item?.user;
    const label = lastSubmission?.status === 'submitting' ? '正在提交…' : '已提交';
    $('post').textContent = samePost ? label : '下载当前帖子';
    $('profile').textContent = sameProfile ? label : ($('schedule').value === 'once' ? '下载博主历史内容' : '下载博主并订阅');
    $('update').textContent = sameUpdate ? label : '立即更新博主';
    $('post').classList.toggle('primary',!!samePost);
    $('profile').classList.toggle('primary',!!sameProfile);
    $('update').classList.toggle('primary',!!sameUpdate);
    $('toggle').classList.toggle('active',busy);
    $('toggle').setAttribute('aria-busy',String(busy));
    $('archive').disabled = !item?.user;
    $('connection').textContent = {checking:'正在验证连接',connected:'已连接',invalid:'连接验证失败',offline:'连接失败',unconfigured:'尚未连接'}[connectionState];
    $('connection').setAttribute('data-state',connectionState);
    renderBlogger(item);
    if (bloggerState.state === 'idle') refreshBlogger();
  }
  function expand() {const reopening=root.querySelector('section').hidden;root.querySelector('section').hidden = false; $('toggle').setAttribute('aria-expanded','true'); update();if(reopening&&bloggerState.state==='ready'&&Date.now()-bloggerState.checkedAt>60000)refreshBlogger(true);}
  function collapse() {root.querySelector('section').hidden = true; $('toggle').setAttribute('aria-expanded','false');}
  function showSettings() {
    $('actions-view').hidden = true; $('settings-view').hidden = false; expand();
    $('server').value = base; $('credential').value = ''; $('settings').textContent = '返回操作';
  }
  function showActions() {$('actions-view').hidden = false; $('settings-view').hidden = true; $('settings').textContent = '连接设置'; update();}
  function configured() {if (base && token && connectionState === 'connected') return true; showSettings(); $('status').textContent = '请填写归档网站地址和连接密码，并点击验证。'; return false;}
  function bloggerKey(item) {return connectionGeneration+':'+base+':'+(item?.user||'');}
  function updateTime(value) {return typeof value==='number'&&Number.isFinite(value)&&value>0?new Date(value*1000).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'暂无';}
  function renderBlogger(item) {
    const key=bloggerKey(item);
    if(bloggerState.key!==key)bloggerState={key,state:'idle'};
    let state=bloggerState.state,label=!item?.user?'请选择博主':connectionState!=='connected'?'连接后查询':'打开面板后查询',last='—',next='—',note='';
    if(state==='loading')label='正在查询订阅…';
    if(state==='error'){label='订阅查询失败';last=next='未获取';note=bloggerState.error;}
    if(state==='ready'){
      const data=bloggerState.data,sub=data.subscription;
      state=!sub?'unsubscribed':sub.schedule==='once'?'once':sub.enabled?'subscribed':'paused';
      label=!sub?'未订阅':state==='once'?'仅下载一次':state==='paused'?'订阅已暂停':'已订阅 · '+cadence[sub.schedule];
      last=updateTime(data.last_success_at);
      next=!sub?'未设计划':state==='once'?'不自动更新':state==='paused'?'已暂停':updateTime(sub.next_run);
      if(!sub&&!data.last_success_at)note='首次下载按历史范围处理；已有内容自动跳过。';
      const active=data.active_task;
      if(active)note={queued:'更新任务已在队列中。',running:'后台正在处理该博主。',retry:'后台等待重试，已保存内容会保留。',awaiting_confirmation:'历史范围待确认，请到网站选择。'}[active.status]||'';
    }
    $('subscription').setAttribute('data-state',state);
    $('subscription-state').textContent=label;$('last-update').textContent=last;$('next-update').textContent=next;
    $('subscription-note').textContent=note;$('subscription-note').hidden=!note;
    $('refresh-subscription').disabled=state==='loading'||connectionState!=='connected'||!item?.user;
  }
  async function refreshBlogger(force=false) {
    const item=selected();
    if(root.querySelector('section').hidden||!$('settings-view').hidden||connectionState!=='connected'||!item?.user)return;
    const key=bloggerKey(item);
    if(!force&&bloggerState.key===key&&bloggerState.state!=='idle')return;
    const lookup=bloggerState={key,state:'loading'},generation=connectionGeneration;
    renderBlogger(item);
    try{
      const data=await request('/scriptcat/bloggers/'+encodeURIComponent(item.user));
      if(bloggerState!==lookup||generation!==connectionGeneration||selected()?.user!==item.user)return;
      if(data.user!==item.user||!('subscription' in data)||(data.subscription!==null&&
          (typeof data.subscription.enabled!=='boolean'||!cadence[data.subscription.schedule])))throw Error('订阅查询响应异常，请刷新重试。');
      lookup.state='ready';lookup.data=data;lookup.checkedAt=Date.now();
    }catch(error){
      if(bloggerState!==lookup||generation!==connectionGeneration||selected()?.user!==item.user)return;
      lookup.state='error';lookup.error=error.status===404?'网站版本尚不支持订阅查询，请更新网站。':error.status===401?'连接已失效，请打开连接设置重新验证。':'暂时无法获取订阅状态，请点击刷新重试。';
      if(error.status===401)connectionState='invalid';
    }
    update();
  }
  function serverOrigin(value) {
    let url;
    try {url = new URL(value);} catch {throw Error('请填写归档网站的根地址：外网用 HTTPS，内网可用 HTTP 私有 IP。');}
    if (!['http:','https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw Error('请填写归档网站的根地址：外网用 HTTPS，内网可用 HTTP 私有 IP。');
    const ip = url.hostname.split('.').map(Number);
    const privateIPv4 = /^\d+\.\d+\.\d+\.\d+$/.test(url.hostname) && ip.every(octet => Number.isInteger(octet) && octet >= 0 && octet <= 255)
      && (ip[0] === 10 || ip[0] === 172 && ip[1] >= 16 && ip[1] <= 31 || ip[0] === 192 && ip[1] === 168);
    if (url.protocol === 'http:' && !privateIPv4 && !['localhost','127.0.0.1','[::1]'].includes(url.hostname)) throw Error('公网连接请使用 HTTPS；HTTP 仅支持内网私有 IPv4 或本机环回地址。');
    return url.origin;
  }

  $('toggle').onclick = () => root.querySelector('section').hidden ? (connectionState === 'connected' ? expand() : showSettings()) : collapse();
  $('close').onclick = collapse;
  $('settings').onclick = () => $('settings-view').hidden ? showSettings() : showActions();
  GM_registerMenuCommand('拾存：连接设置',showSettings);
  GM_registerMenuCommand('拾存：打开下载面板',expand);
  $('settings-view').onsubmit = async event => {
    event.preventDefault();
    ++connectionGeneration;
    $('save').disabled = true;
    try {
      const server = serverOrigin($('server').value.trim());
      const value = $('credential').value.trim();
      if ((!value && (!token || server !== base)) || value.length > 256) throw Error('请填写你在此网站设置的连接密码。');
      $('status').textContent = '正在验证连接密码…';
      await checkConnection({server,password:value || token});
      if(base !== server || value && value !== token) lastSubmission=null;
      base = server; token = value || token;
      GM_setValue('server',base); GM_setValue('token',token); $('credential').value = '';
      connectionState = 'connected'; showActions(); $('status').textContent = '连接验证成功，已保存。';
    } catch(error) {connectionState = error.status === 401 ? 'invalid' : 'offline'; $('status').textContent = error.message; update();}
    finally {$('save').disabled = false;}
  };

  function selectArticle(event) {
    const article = event.target.closest?.('article');
    if (!article) return;
    const links = [...article.querySelectorAll('a[href*="/status/"]')];
    const link = links.find(a => a.querySelector('time')) || links[0];
    if (!link) return;
    const url = new URL(link.href,location.href);
    if (!['x.com','twitter.com','www.x.com','mobile.twitter.com'].includes(url.hostname)) return;
    if (location.href !== lastURL) {lastURL=location.href; hovered=null;}
    hovered = parse(url.pathname); update();
  }
  document.addEventListener('pointerover',selectArticle);
  document.addEventListener('focusin',selectArticle);
  document.addEventListener('pointerdown',selectArticle);

  function request(path, method = 'GET', body, {server=base,password=token} = {}) {
    server = serverOrigin(server);
    const encoded = btoa(String.fromCharCode(...new TextEncoder().encode(password))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
    return new Promise((resolve,reject) => GM_xmlhttpRequest({
      method, url:server + '/api/v1' + path, anonymous:true,
      headers:{Authorization:'Bearer xarchive.' + encoded, ...(body ? {'Content-Type':'application/json'} : {})},
      data:body ? JSON.stringify(body) : undefined, timeout:15000,
      onload:response => {
        let data;
        try {data=JSON.parse(response.responseText);} catch {reject(Error('服务响应异常，请检查网站地址。'));return;}
        if (response.status < 200 || response.status >= 300) {
          const error = Error(response.status === 401 ? '连接密码不正确或已更改，请重新填写。' : data.error || '请求失败，请打开网站查看。');
          error.status = response.status; reject(error);
        }
        else resolve(data);
      },
      onerror:() => reject(Error('无法连接，请检查网站地址与 ScriptCat 站点访问权限。')),
      ontimeout:() => reject(Error('请求超时，请先在网站检查任务是否已接收。')),
    }));
  }
  async function checkConnection(credentials) {
    const data = await request('/connection/check','GET',undefined,credentials);
    if (data.ok !== true || !['password','legacy'].includes(data.method)) throw Error('连接验证响应异常，请检查网站地址。');
  }
  async function verifyConnection() {
    if (!base || !token) return;
    const generation = ++connectionGeneration;
    try {
      await checkConnection();
      if (generation !== connectionGeneration) return;
      connectionState = 'connected';
    } catch(error) {
      if (generation !== connectionGeneration) return;
      connectionState = error.status === 401 ? 'invalid' : 'offline'; $('status').textContent = error.message;
    }
    update();
  }
  async function submit(mode, post = false) {
    if (!configured() || busy) return;
    update(); const item = selected();
    if (!item?.user || (post && !item.post)) return;
    const schedule = $('schedule').value;
    const submission=lastSubmission={user:item.user,post_id:post?item.post:null,mode,status:'submitting'};
    busy=true; expand();showActions();$('status').textContent='正在提交…';
    try {
      GM_setValue('schedule',schedule);
      const url='https://x.com/'+item.user+(post?'/status/'+item.post:'/timeline');
      const data=await request('/tasks','POST',{url,mode,schedule:post?'once':schedule});
      if(lastSubmission !== submission)return;
      if(!data.task?.id)throw Error('提交结果异常，请打开下载任务核对。');
      submission.status='submitted';$('status').textContent=data.duplicate ? '已在队列中，后台会继续处理。' : '已提交'+(data.task.task_kind==='history'?'历史下载':'')+' @'+submission.user+'，后台会继续处理。';
      if(!post)refreshBlogger(true);
    } catch(error) {if(lastSubmission === submission){if(error.status === 401)connectionState='invalid';lastSubmission=null;$('status').textContent=error.message;}}
    finally {busy=false;update();}
  }
  $('post').onclick=()=>submit('download',true);
  $('profile').onclick=()=>submit('subscribe');
  $('update').onclick=()=>submit('update');
  $('refresh-subscription').onclick=()=>refreshBlogger(true);
  $('schedule').onchange=update;
  $('archive').onclick=()=>{if(!configured())return;update();const item=selected();if(item)GM_openInTab(base+'/?'+(item.post?'post='+encodeURIComponent(item.post):'user='+encodeURIComponent(item.user)),{active:true});};
  update(); const observer=setInterval(update,1000);
  verifyConnection();
  window.addEventListener('resize',update);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')collapse();});
  window.addEventListener('pagehide',()=>clearInterval(observer));
})();
