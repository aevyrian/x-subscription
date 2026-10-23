'use strict';
const sharedRoot=document.getElementById('shared-post');
const escapeShare=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
(async()=>{
  try{
    const response=await fetch(location.pathname.replace(/\/$/,'')+'/data');
    const post=await response.json();if(!response.ok)throw Error(post.error||'分享不存在或已停止');
    document.title=(post.display_name||post.user)+' 的帖子 · 拾存';
    sharedRoot.innerHTML=`<div class="shared-author"><span class="shared-avatar">${escapeShare((post.display_name||post.user).slice(0,1))}</span><div><h1>${escapeShare(post.display_name||post.user)}</h1><p>@${escapeShare(post.user)} · ${escapeShare(post.posted_at)}</p></div><a href="${escapeShare(post.url)}" target="_blank" rel="noopener noreferrer">X 原帖 ↗</a></div><p class="shared-text">${escapeShare(post.content)}</p><div class="shared-media">${post.media.map((m,i)=>`<section>${m.kind==='video'?`<video src="${escapeShare(m.url)}" controls playsinline preload="metadata"></video>`:`<button class="shared-image" data-index="${i}" aria-label="查看图片 ${i+1}"><img src="${escapeShare(m.url)}" alt="图片 ${i+1}" loading="lazy"></button>`}<div class="shared-actions"><button data-index="${i}">放大查看</button><a href="${escapeShare(m.url)}?download=true" download>下载原文件</a></div></section>`).join('')}</div>`;
    sharedRoot.addEventListener('click',event=>{const button=event.target.closest('[data-index]');if(button)openMedia(post.media,Number(button.dataset.index));});
  }catch(error){sharedRoot.textContent=error.message||'暂时无法打开分享。';}
})();
