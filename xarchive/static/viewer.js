'use strict';
const mediaViewer=document.createElement('dialog');
mediaViewer.className='media-viewer';
mediaViewer.setAttribute('aria-label','媒体查看器');
mediaViewer.innerHTML='<button class="viewer-close" aria-label="关闭看图">×</button><span class="viewer-count" aria-live="polite"></span><a class="viewer-download" download aria-label="下载原文件">下载</a><button class="viewer-prev" aria-label="上一张">‹</button><div class="viewer-stage"></div><button class="viewer-next" aria-label="下一张">›</button>';
document.body.append(mediaViewer);
const viewerStage=mediaViewer.querySelector('.viewer-stage');
let viewerItems=[],viewerIndex=0,viewerTouch=null;
function renderMedia(){
  viewerStage.querySelector('video')?.pause();
  const item=viewerItems[viewerIndex],media=document.createElement(item.kind==='video'?'video':'img');
  media.src=item.url;
  if(item.kind==='video'){media.controls=true;media.playsInline=true;media.preload='metadata';}
  else media.alt=`图片 ${viewerIndex+1}，共 ${viewerItems.length} 张`;
  viewerStage.replaceChildren(media);
  mediaViewer.querySelector('.viewer-count').textContent=`${viewerIndex+1} / ${viewerItems.length}`;
  const previous=mediaViewer.querySelector('.viewer-prev'),next=mediaViewer.querySelector('.viewer-next');
  previous.hidden=next.hidden=viewerItems.length<2;
  previous.disabled=viewerIndex===0;next.disabled=viewerIndex===viewerItems.length-1;
  mediaViewer.querySelector('.viewer-download').href=item.url+'?download=true';
}
function moveMedia(delta){const index=viewerIndex+delta;if(index>=0&&index<viewerItems.length){viewerIndex=index;renderMedia();}}
function openMedia(items,index=0){
  if(!items.length)return;
  viewerItems=items;viewerIndex=Math.max(0,Math.min(index,items.length-1));
  renderMedia();if(!mediaViewer.open)mediaViewer.showModal();
}
function closeMedia(){mediaViewer.close();}
mediaViewer.querySelector('.viewer-close').onclick=closeMedia;
mediaViewer.querySelector('.viewer-prev').onclick=()=>moveMedia(-1);
mediaViewer.querySelector('.viewer-next').onclick=()=>moveMedia(1);
document.addEventListener('keydown',event=>{
  if(!mediaViewer.open||event.target.tagName==='VIDEO')return;
  if(event.key==='ArrowLeft'||event.key==='ArrowRight'){event.preventDefault();moveMedia(event.key==='ArrowLeft'?-1:1);}
});
viewerStage.onclick=event=>{if(event.target===viewerStage)closeMedia();};
viewerStage.addEventListener('pointerdown',event=>{viewerTouch=event.pointerType==='touch'&&event.target.tagName!=='VIDEO'?{x:event.clientX,y:event.clientY}:null;});
viewerStage.addEventListener('pointerup',event=>{
  if(!viewerTouch)return;
  const dx=event.clientX-viewerTouch.x,dy=event.clientY-viewerTouch.y;viewerTouch=null;
  if(Math.abs(dx)>50&&Math.abs(dy)<Math.abs(dx)/2)moveMedia(dx<0?1:-1);
});
mediaViewer.addEventListener('close',()=>{viewerStage.querySelector('video')?.pause();viewerStage.replaceChildren();viewerItems=[];viewerTouch=null;});
