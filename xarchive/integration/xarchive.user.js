// ==UserScript==
// @name         拾存 · X 下载与归档
// @namespace    xarchive.personal
// @version      1.4.1
// @description  下载帖子、推送博主、订阅更新，并打开自己的归档。
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
    </style>
    <button id="toggle" aria-label="打开拾存浮窗" aria-expanded="false" title="拾存 · 下载与归档"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 3h2v10.59l4.3-4.3 1.4 1.42L12 17.4l-6.7-6.69 1.4-1.42 4.3 4.3V3ZM4 18h2v3h12v-3h2v5H4v-5Z"/></svg></button>
    <section hidden aria-label="拾存下载浮窗">
      <header><h3>拾存 · X 归档</h3><button id="close" aria-label="收起浮窗">×</button></header>
      <div id="actions-view">
        <p id="subject">打开博主主页，或将鼠标移到帖子上。</p>
        <div class="actions">
          <button id="post" class="primary">下载当前帖子</button>
          <button id="profile" title="保存此博主的全部可读取内容">添加博主订阅</button>
          <button id="archive">打开对应归档 ↗</button>
        </div>
        <label>什么时候更新 <select id="schedule" aria-label="更新频率"><option value="monthly">每月一次</option><option value="weekly">每周一次</option><option value="daily">每天一次</option><option value="once">仅下载这一次</option></select></label>
      </div>
      <form id="settings-view" hidden>
        <label>归档网站地址<input id="server" type="url" placeholder="https://your-archive.example" required></label>
        <label>连接密码<input id="credential" type="password" autocomplete="off" placeholder="填写你在网站设置的连接密码；留空验证已保存的连接"></label>
        <small>所有浏览器使用同一个连接密码；X Cookie 在网站设置中填写。</small>
        <button id="save" class="primary" type="submit">验证并保存</button>
      </form>
      <p id="status" role="status"></p>
      <div class="foot"><small id="connection">尚未连接</small><button id="settings">连接设置</button></div>
    </section>`;
  document.body.appendChild(host);
  const $ = id => root.getElementById(id);
  let base = String(GM_getValue('server','')), token = String(GM_getValue('token',''));
  let hovered = null, lastURL = location.href, timer = null, pollingGeneration = 0, busy = false;
  let connectionState = base && token ? 'checking' : 'unconfigured', connectionGeneration = 0;
  $('schedule').value = GM_getValue('schedule','monthly');

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
    $('subject').textContent = item ? '@' + item.user + (item.post ? ' · 当前选中帖子' : ' · 博主主页') : '打开博主主页，或将鼠标移到帖子上。';
    $('post').disabled = busy || connectionState === 'checking' || !item?.post;
    $('profile').disabled = busy || connectionState === 'checking' || !item?.user;
    $('profile').textContent = $('schedule').value === 'once' ? '下载博主全部内容' : '添加博主订阅';
    $('archive').disabled = !item?.user;
    $('connection').textContent = {checking:'正在验证连接',connected:'已连接',invalid:'连接验证失败',offline:'连接失败',unconfigured:'尚未连接'}[connectionState];
  }
  function expand() {root.querySelector('section').hidden = false; $('toggle').setAttribute('aria-expanded','true'); update();}
  function collapse() {root.querySelector('section').hidden = true; $('toggle').setAttribute('aria-expanded','false');}
  function showSettings() {
    expand(); $('actions-view').hidden = true; $('settings-view').hidden = false;
    $('server').value = base; $('credential').value = ''; $('settings').textContent = '返回操作';
  }
  function showActions() {$('actions-view').hidden = false; $('settings-view').hidden = true; $('settings').textContent = '连接设置'; update();}
  function configured() {if (base && token && connectionState === 'connected') return true; showSettings(); $('status').textContent = '请填写归档网站地址和连接密码，并点击验证。'; return false;}

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
      const url = new URL($('server').value.trim());
      if (!['http:','https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw Error('请填写归档网站的根地址。');
      const value = $('credential').value.trim();
      if ((!value && (!token || url.origin !== base)) || value.length > 256) throw Error('请填写你在此网站设置的连接密码。');
      $('status').textContent = '正在验证连接密码…';
      await checkConnection({server:url.origin,password:value || token});
      base = url.origin; token = value || token;
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
  function poll(identifier) {
    clearTimeout(timer);
    const generation = ++pollingGeneration;
    let checks = 0;
    const check = async () => {
      if (generation !== pollingGeneration) return;
      try {
        const {task} = await request('/tasks/' + encodeURIComponent(identifier));
        if (generation !== pollingGeneration) return;
        const names = {queued:'排队中',running:'正在下载，已保存 ' + task.progress + ' 份媒体',retry:'等待自动重试：' + (task.error || ''),failed:'下载失败：' + (task.error || '请在网站查看'),cancelled:'任务已取消',succeeded:'已完成，新增 ' + task.new_posts + ' 篇帖子 / ' + task.new_media + ' 份媒体'};
        $('status').textContent = names[task.status] || task.status;
        if (['succeeded','failed','cancelled'].includes(task.status)) return;
        if (++checks >= 200) {$('status').textContent = '任务仍在处理中，请打开网站查看后续状态。';return;}
        timer = setTimeout(check,3000);
      } catch(error) {if(error.status === 401)connectionState='invalid';$('status').textContent = error.message;update();}
    };
    timer = setTimeout(check,1500);
  }
  async function submit(mode, post = false) {
    if (!configured() || busy) return;
    update(); const item = selected();
    if (!item?.user || (post && !item.post)) return;
    const schedule = $('schedule').value;
    busy=true; update(); $('status').textContent='正在提交…';
    ++pollingGeneration; clearTimeout(timer);
    try {
      GM_setValue('schedule',schedule);
      const url='https://x.com/'+item.user+(post?'/status/'+item.post:'/timeline');
      const data=await request('/tasks','POST',{url,mode,schedule:post?'once':schedule});
      $('status').textContent=data.duplicate?'该对象已有活动任务，未重复创建。':'已接收，已加入下载队列。';
      if(data.task)poll(data.task.id);
    } catch(error) {if(error.status === 401)connectionState='invalid';$('status').textContent=error.message;}
    finally {busy=false;update();}
  }
  $('post').onclick=()=>submit('download',true);
  $('profile').onclick=()=>submit('subscribe');
  $('schedule').onchange=update;
  $('archive').onclick=()=>{if(!configured())return;update();const item=selected();if(item)GM_openInTab(base+'/?'+(item.post?'post='+encodeURIComponent(item.post):'user='+encodeURIComponent(item.user)),{active:true});};
  update(); const observer=setInterval(update,1000);
  verifyConnection();
  window.addEventListener('resize',update);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')collapse();});
  window.addEventListener('pagehide',()=>{clearInterval(observer);clearTimeout(timer);++pollingGeneration;});
})();
