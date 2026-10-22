// ==UserScript==
// @name         X归档站 · 下载与推送
// @namespace    https://github.com/aevyrian/x-subscription
// @version      0.1.0
// @description  在 X 页面推送博主、下载帖子，并打开自己的归档网站。
// @match        https://x.com/*
// @match        https://twitter.com/*
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
  const reserved = new Set(['home','explore','search','settings','i','notifications','messages','compose','intent']);
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:20px;bottom:24px;z-index:2147483646';
  const root = host.attachShadow({mode:'closed'});
  root.innerHTML = `<style>*{box-sizing:border-box}button,input{font:inherit}section{font:14px system-ui,sans-serif;color:#193147;width:268px;background:#fff;border:1px solid #dce6ee;border-radius:16px;box-shadow:0 8px 32px #102a4429;padding:16px}header{display:flex;justify-content:space-between;align-items:center}h3{margin:0;font-size:15px}button{cursor:pointer;border:0;border-radius:9px;padding:10px 12px;background:#1689d2;color:#fff}button:disabled{opacity:.55;cursor:wait}.secondary{background:#edf3f8;color:#36516b}.actions{display:grid;gap:8px;margin:12px 0}p{line-height:1.5;margin:10px 0;color:#60778a}#status{min-height:20px;overflow-wrap:anywhere}#toggle{width:44px;height:44px;border-radius:50%;font-size:18px;box-shadow:0 3px 14px #163c5a22}input{width:70px;padding:5px;border:1px solid #ccd9e5;border-radius:6px}small{color:#6c8193}[hidden]{display:none!important}</style><button id="toggle" title="X归档站">↓</button><section hidden><header><h3>X归档站</h3><button id="close" class="secondary" aria-label="收起">×</button></header><p id="who">请打开博主主页或帖子</p><div class="actions"><button id="post">下载当前帖子</button><button id="profile">下载这个博主</button><button id="subscribe" class="secondary">订阅这个博主</button><button id="open" class="secondary">打开归档</button></div><small>博主首次范围 <input id="limit" type="number" min="1" max="1500" value="20"> 条</small><p id="status" role="status"></p><button id="settings" class="secondary">连接设置</button></section>`;
  document.body.append(host);
  const $ = id => root.getElementById(id);
  let base = GM_getValue('server',''), token = GM_getValue('token','');
  let current = null, hoveredPost = null, lastURL = '', polling = null, archive = '/';
  function configured(){if(base&&token)return true;$('status').textContent='请先打开连接设置。';return false}
  function settings(){const value=prompt('填写归档网站地址，例如 https://archive.example.com',base);if(value===null)return;try{const u=new URL(value);if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.pathname!=='/'||u.search||u.hash)throw Error();base=u.origin}catch{$('status').textContent='请输入网站根地址，仅支持 HTTP / HTTPS。';return}const secret=prompt('填写网站「下载与连接」中生成的连接凭据');if(secret===null)return;token=secret.trim();GM_setValue('server',base);GM_setValue('token',token);$('status').textContent='设置已保存。凭据可在网站撤销。'}
  GM_registerMenuCommand('设置 X归档站连接',settings);$('settings').onclick=settings;
  $('toggle').onclick=()=>{root.querySelector('section').hidden=false;$('toggle').hidden=true;update()};
  $('close').onclick=()=>{root.querySelector('section').hidden=true;$('toggle').hidden=false};
  function parse(path){const p=path.split('/').filter(Boolean);if(!p.length||!/^\w{1,15}$/.test(p[0])||reserved.has(p[0].toLowerCase()))return null;return {user:p[0].toLowerCase(),tid:p[1]==='status'&&/^\d+$/.test(p[2]||'')?p[2]:null}}
  function update(){if(location.href!==lastURL){lastURL=location.href;hoveredPost=null}current=parse(location.pathname);const selected=current?.tid?current:hoveredPost;const user=current?.user||selected?.user;$('who').textContent=user?'@'+user:'请打开博主主页，或将鼠标移到帖子';$('post').disabled=!selected?.tid;$('profile').disabled=!user;$('subscribe').disabled=!user;archive=selected?.tid?'/#tweet='+selected.tid:'/?user='+encodeURIComponent(user||'')}
  document.addEventListener('pointerover',event=>{const article=event.target.closest?.('article');if(!article)return;const links=[...article.querySelectorAll('a[href*="/status/"]')];const link=links.find(a=>a.querySelector('time'))||links[0];if(link){const u=new URL(link.href);if(['x.com','twitter.com'].includes(u.hostname)){hoveredPost=parse(u.pathname);update()}}});
  function request(path,method='GET',body){return new Promise((resolve,reject)=>GM_xmlhttpRequest({method,url:base+path,anonymous:true,headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},data:body?JSON.stringify(body):undefined,timeout:15000,onload:r=>{let data;try{data=JSON.parse(r.responseText)}catch{reject(Error('服务返回异常，请检查网站地址'));return}if(r.status<200||r.status>=300)reject(Error(data.error||'请求失败'));else resolve(data)},onerror:()=>reject(Error('无法连接。请检查地址及脚本的站点访问权限。')),ontimeout:()=>reject(Error('连接超时，请检查网站任务后再重试'))}))}
  async function submit(mode,post){if(!configured())return;update();const item=post?(current?.tid?current:hoveredPost):{user:current?.user||hoveredPost?.user};if(!item?.user||(post&&!item.tid))return;const limit=Number($('limit').value);if(!Number.isInteger(limit)||limit<1||limit>1500){$('status').textContent='范围需要 1–1500 条';return}const url='https://x.com/'+item.user+(post?'/status/'+item.tid:'/media');$('status').textContent='正在发送…';try{const response=await request('/api/v1/tasks','POST',{url,mode,limit});$('status').textContent=response.duplicate?'已经添加，未重复创建任务。':'已接收，等待下载。';if(response.task){archive=response.task.archive_path;poll(response.task.job_id)}}catch(error){$('status').textContent=error.message}}
  function poll(id){clearInterval(polling);let count=0;polling=setInterval(async()=>{if(++count>120){clearInterval(polling);return}try{const {task}=await request('/api/v1/tasks/'+id);const names={queued:'等待下载',running:'正在下载',done:'下载完成，可打开归档',failed:'下载失败，请在网站查看',cancelled:'已取消'};$('status').textContent=names[task.status]||task.status;if(['done','failed','cancelled'].includes(task.status))clearInterval(polling)}catch(error){$('status').textContent=error.message;clearInterval(polling)}},5000)}
  $('post').onclick=()=>submit('download',true);$('profile').onclick=()=>submit('download',false);$('subscribe').onclick=()=>submit('subscribe',false);$('open').onclick=()=>{if(configured())GM_openInTab(base+archive,{active:true})};
  update();setInterval(update,1000);window.addEventListener('pagehide',()=>clearInterval(polling));
})();
