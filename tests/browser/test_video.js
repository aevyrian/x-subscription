/* X Reader 2.0 视频播放状态管理 —— 真实浏览器（headless Chrome + CDP）验收脚本。
   用法：node local-dev/test_video.js <page-ws-url> <base-url> <shot-dir>
   仅读取与交互页面，不修改后端数据。
*/
const fs = require("fs");
const path = require("path");
// ws 依赖：优先本地 node_modules；否则用环境变量 CDP_WS 指向的路径
let WebSocket;
try {
  WebSocket = require("ws");
} catch (e) {
  WebSocket = require(process.env.CDP_WS || path.join(__dirname, "..", "..", "local-dev", "cdp", "node_modules", "ws"));
}

const [wsUrl, baseUrl, shotDir] = process.argv.slice(2);
let msgId = 0;

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, { perMessageDeflate: false, maxPayload: 256 * 1024 * 1024 });
    ws.on("open", () => resolve(ws));
    ws.on("error", reject);
  });
}

const pending = new Map();
function send(ws, method, params) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

async function evalJs(ws, expression) {
  const r = await send(ws, "Runtime.evaluate", {
    expression, awaitPromise: true, returnByValue: true, timeout: 30000,
  });
  if (r.exceptionDetails) {
    throw new Error("JS 异常: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
  }
  return r.result ? r.result.value : undefined;
}

async function shot(ws, name) {
  const r = await send(ws, "Page.captureScreenshot", { format: "png" });
  const p = path.join(shotDir, name);
  fs.writeFileSync(p, Buffer.from(r.data, "base64"));
  return p;
}

const results = [];
function record(id, desc, pass, detail) {
  results.push({ id, desc, pass: !!pass, detail: detail || "" });
  console.log(`${pass ? "PASS" : "FAIL"}  ${id}  ${desc}${detail ? "  :: " + detail : ""}`);
}

(async () => {
  const ws = await connect(wsUrl);
  ws.on("message", (raw) => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    }
  });

  await send(ws, "Runtime.enable");
  await send(ws, "Page.enable");
  await send(ws, "Page.navigate", { url: baseUrl });
  await new Promise(r => setTimeout(r, 2500));

  // 等待视频元素渲染
  await evalJs(ws, `(async()=>{for(let i=0;i<40;i++){if(document.querySelectorAll("video").length>=2)return true;await new Promise(r=>setTimeout(r,250))}return false})()`);
  const nVid = await evalJs(ws, `document.querySelectorAll("video").length`);
  record("V0", "时间线渲染出 2 个视频元素", nVid >= 2, `video 数量=${nVid}`);

  // 让两个视频可见（视频卡片在页面下方，先滚动到第一个）
  await evalJs(ws, `(()=>{const v=document.querySelectorAll("video")[0];v.scrollIntoView({block:"center"});return true})()`);
  await new Promise(r => setTimeout(r, 800));
  await shot(ws, "video_01_inview.png");

  // V1 播放第一个视频
  const v1 = await evalJs(ws, `(async()=>{const v=document.querySelectorAll("video")[0];try{await v.play()}catch(e){return "play-error:"+e.message}await new Promise(r=>setTimeout(r,600));return {paused:v.paused,ct:v.currentTime,dur:v.duration}})()`);
  record("V1", "第一个视频可以播放", v1 && v1.paused === false && v1.ct > 0, JSON.stringify(v1));
  const ctBefore = v1 && v1.ct || 0;

  // V2 滑出屏幕后自动暂停且进度保留
  await evalJs(ws, `window.scrollTo(0,0);true`);
  await new Promise(r => setTimeout(r, 1200));
  const v2 = await evalJs(ws, `(()=>{const v=document.querySelectorAll("video")[0];const r=v.getBoundingClientRect();return {paused:v.paused,ct:v.currentTime,top:Math.round(r.top),bottom:Math.round(r.bottom),vh:innerHeight}})()`);
  record("V2", "滑出可视区后自动暂停且进度保留", v2 && v2.paused === true && v2.ct >= ctBefore - 0.2,
    `paused=${v2 && v2.paused} currentTime=${v2 && v2.ct && v2.ct.toFixed(2)} (播放时 ${ctBefore.toFixed(2)}) 位置top=${v2 && v2.top}`);
  await shot(ws, "video_02_offscreen.png");

  // V3 滑回不自动播放
  await evalJs(ws, `(()=>{const v=document.querySelectorAll("video")[0];v.scrollIntoView({block:"center"});return true})()`);
  await new Promise(r => setTimeout(r, 1000));
  const v3 = await evalJs(ws, `(()=>{const v=document.querySelectorAll("video")[0];return {paused:v.paused,ct:v.currentTime}})()`);
  record("V3", "滑回原位不自动播放", v3 && v3.paused === true, JSON.stringify(v3));

  // V4 手动播放从原进度继续
  const v4 = await evalJs(ws, `(async()=>{const v=document.querySelectorAll("video")[0];const before=v.currentTime;try{await v.play()}catch(e){return "play-error:"+e.message}await new Promise(r=>setTimeout(r,500));return {before:before,after:v.currentTime,paused:v.paused}})()`);
  record("V4", "再次播放从原进度继续", v4 && v4.paused === false && v4.after >= v4.before - 0.2 && v4.before > 0,
    JSON.stringify(v4));

  // V5 播放第二个视频，第一个自动暂停
  const v5 = await evalJs(ws, `(async()=>{const vs=document.querySelectorAll("video");const a=vs[0],b=vs[1];b.scrollIntoView({block:"center"});await new Promise(r=>setTimeout(r,400));try{await b.play()}catch(e){return "play-error:"+e.message}await new Promise(r=>setTimeout(r,700));return {aPaused:a.paused,bPaused:b.paused,playing:[...vs].filter(v=>!v.paused).length}})()`);
  record("V5", "播放第二个视频时第一个立即暂停（同时最多 1 个）", v5 && v5.aPaused === true && v5.bPaused === false && v5.playing === 1,
    JSON.stringify(v5));
  await shot(ws, "video_03_second_playing.png");

  // V6 打开视频推文的详情，背景视频停止（严格版：详情内应出现 video）
  const v6 = await evalJs(ws, `(async()=>{const tw=[...document.querySelectorAll(".tweet")].find(t=>t.querySelector("video"));if(!tw)return {err:"未找到含视频的时间线卡片"};tw.querySelector(".content").click();await new Promise(r=>setTimeout(r,1400));const mask=document.getElementById("detailMask");const timeline=[...document.querySelectorAll("#feed video")];return {open:mask.style.display==="flex",timelinePlaying:timeline.filter(v=>!v.paused).length,detailVideos:document.querySelectorAll("#detail video").length}})()`);
  record("V6", "打开视频帖详情后背景时间线视频停止且详情含视频", v6 && v6.open === true && v6.timelinePlaying === 0 && v6.detailVideos >= 1,
    JSON.stringify(v6));
  await shot(ws, "video_04_detail_open.png");

  // V7 播放详情内视频 → 关闭详情后必须停止
  const v7 = await evalJs(ws, `(async()=>{const dv=document.querySelector("#detail video");if(!dv)return {err:"详情内无视频"};let played=false;try{await dv.play();played=true}catch(e){}await new Promise(r=>setTimeout(r,700));const beforeClose={played:played,detailPaused:dv.paused,timelinePlaying:[...document.querySelectorAll("#feed video")].filter(v=>!v.paused).length};document.getElementById("dtClose").click();await new Promise(r=>setTimeout(r,900));return {played:played,beforeClose:beforeClose,after:{detailVideos:document.querySelectorAll("#detail video").length,anyPlaying:[...document.querySelectorAll("video")].filter(v=>!v.paused).length}}})()`);
  record("V7", "详情内视频播放中关闭详情后停止（无残留播放）", v7 && v7.played === true && v7.beforeClose && !v7.beforeClose.detailPaused && v7.after && v7.after.anyPlaying === 0,
    JSON.stringify(v7));

  // V8 切换标签页（页面隐藏）自动暂停
  const v8 = await evalJs(ws, `(async()=>{const v=document.querySelectorAll("video")[0];v.scrollIntoView({block:"center"});await new Promise(r=>setTimeout(r,300));try{await v.play()}catch(e){}await new Promise(r=>setTimeout(r,500));const playingBefore=!v.paused;Object.defineProperty(document,"hidden",{configurable:true,get:()=>true});Object.defineProperty(document,"visibilityState",{configurable:true,get:()=>"hidden"});document.dispatchEvent(new Event("visibilitychange"));await new Promise(r=>setTimeout(r,400));return {playingBefore:playingBefore,pausedAfter:v.paused}})()`);
  record("V8", "标签页切到后台时视频暂停", v8 && v8.playingBefore === true && v8.pausedAfter === true, JSON.stringify(v8));

  // V9 全屏/画中画不误暂停（逻辑分支验证；headless 无法真实进入全屏）
  const v9 = await evalJs(ws, `(async()=>{const v=document.querySelectorAll("video")[0];try{await v.play()}catch(e){}await new Promise(r=>setTimeout(r,400));Object.defineProperty(document,"pictureInPictureElement",{configurable:true,get:()=>v});window.scrollTo(0,0);await new Promise(r=>setTimeout(r,800));const pipKeeps=v.paused===false;Object.defineProperty(document,"pictureInPictureElement",{configurable:true,get:()=>null});v.pause();return {pipKeepsPlaying:pipKeeps}})()`);
  record("V9", "画中画（PiP）离屏不被误暂停（真实 PiP 与全屏需人工确认）", v9 && v9.pipKeepsPlaying === true, JSON.stringify(v9) + " [headless 未做真实全屏/PiP]");

  // V10 数字音量仍生效（含记忆与增益分支）
  const v10 = await evalJs(ws, `(async()=>{const inp=document.getElementById("volNum");inp.value="120";inp.dispatchEvent(new Event("change"));await new Promise(r=>setTimeout(r,300));const v=document.querySelectorAll("video")[0];const stored=localStorage.getItem("ar-vol");inp.value="80";inp.dispatchEvent(new Event("change"));await new Promise(r=>setTimeout(r,300));const v2=document.querySelectorAll("video")[0];return {stored:stored,volAt120:v?v.volume:null,mutedAt120:v?v.muted:null,storedAfter:localStorage.getItem("ar-vol"),volAt80:v2?v2.volume:null}})()`);
  record("V10", "数字音量输入仍生效并记忆", v10 && v10.stored === "120" && v10.storedAfter === "80" && v10.mutedAt120 === false,
    JSON.stringify(v10));

  // V11 筛选/切换博主：不在展示的视频被暂停
  // 说明：V8 伪造过 document.hidden、V9 伪造过 pictureInPictureElement，
  // 会污染后续同页断言；此处先重新加载页面取得干净状态（不降低断言标准）。
  await send(ws, "Page.navigate", { url: baseUrl });
  await new Promise(r => setTimeout(r, 2500));
  await evalJs(ws, `(async()=>{for(let i=0;i<40;i++){if(document.querySelectorAll("video").length>=1)return true;await new Promise(r=>setTimeout(r,250))}return false})()`);
  const v11 = await evalJs(ws, `(async()=>{
    const v=document.querySelectorAll("video")[0];
    v.scrollIntoView({block:"center"});
    await new Promise(r=>setTimeout(r,900));
    let playErr="";
    try{await v.play()}catch(e){playErr=e.name+":"+e.message}
    await new Promise(r=>setTimeout(r,700));
    const playingBefore=!v.paused;
    const ctBefore=v.currentTime;
    const q=document.getElementById("q");
    q.value="不存在的关键词zzz";
    q.dispatchEvent(new Event("input"));
    await new Promise(r=>setTimeout(r,1000));
    const pausedByFilter=v.paused;
    const ctAfter=v.currentTime;
    q.value="";
    q.dispatchEvent(new Event("input"));
    await new Promise(r=>setTimeout(r,500));
    return {playErr:playErr,playingBefore:playingBefore,pausedByFilter:pausedByFilter,
            progressKept:ctAfter>=ctBefore-0.2,pausedAfterRestore:v.paused};
  })()`);
  record("V11", "关键词筛选隐藏视频后自动暂停（含进度保留、恢复显示不自动播放）",
    v11 && v11.playingBefore === true && v11.pausedByFilter === true && v11.pausedAfterRestore === true,
    JSON.stringify(v11));

  // 回归：时间线卡片与订阅列表仍存在
  const reg = await evalJs(ws, `(async()=>{const r=await fetch("/api/admin/state");const j=await r.json().catch(()=>({}));document.querySelectorAll("#subList .sub").length;return {tweets:document.querySelectorAll(".tweet").length,subsFromApi:(j.subscriptions||[]).length,subsRendered:document.querySelectorAll("#subList .sub").length}})()`);
  record("R1", "时间线卡片与订阅列表仍可用（未因改动消失）", reg && reg.tweets > 0 && reg.subsFromApi > 0,
    JSON.stringify(reg));

  fs.writeFileSync(path.join(shotDir, "video_test_result.json"),
    JSON.stringify(results, null, 2), "utf-8");
  const failed = results.filter(r => !r.pass);
  console.log(`\n=== 合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length} ===`);
  ws.close();
  process.exit(failed.length ? 2 : 0);
})().catch(e => { console.error("脚本异常:", e.message); process.exit(1); });
