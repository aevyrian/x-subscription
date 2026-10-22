/* X Reader V2 最终验收：管理按钮位置 + 视频与音轨真实性 + 前端回归。
   用法：node tests/browser/test_final.js <page-ws-url> <base-url> <shot-dir>
   仅与本地模拟环境交互，不触碰生产数据。
*/
const fs = require("fs");
const path = require("path");
let WebSocket;
try { WebSocket = require("ws"); } catch (e) {
  WebSocket = require(process.env.CDP_WS || path.join(__dirname, "..", "..", "local-dev", "cdp", "node_modules", "ws"));
}
const [wsUrl, baseUrl, shotDir] = process.argv.slice(2);
let msgId = 0;
const pending = new Map();
const results = [];

function send(ws, method, params) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}
async function evalJs(ws, expression) {
  const r = await send(ws, "Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, timeout: 40000 });
  if (r.exceptionDetails) throw new Error("JS异常: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
  return r.result ? r.result.value : undefined;
}
async function shot(ws, name) {
  const r = await send(ws, "Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(shotDir, name), Buffer.from(r.data, "base64"));
}
function record(id, desc, pass, detail) {
  results.push({ id, desc, pass: !!pass, detail: detail || "" });
  console.log(`${pass ? "PASS" : "FAIL"}  ${id}  ${desc}${detail ? "  :: " + detail : ""}`);
}
async function waitVideos(ws, n) {
  return evalJs(ws, `(async()=>{for(let i=0;i<48;i++){if(document.querySelectorAll("video").length>=${n})return true;await new Promise(r=>setTimeout(r,250))}return false})()`);
}

(async () => {
  const ws = new WebSocket(wsUrl, { maxPayload: 256 * 1024 * 1024 });
  await new Promise((res, rej) => { ws.on("open", res); ws.on("error", rej); });
  ws.on("message", raw => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    }
  });
  await send(ws, "Runtime.enable");
  await send(ws, "Page.enable");
  await send(ws, "Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send(ws, "Page.navigate", { url: baseUrl });
  await new Promise(r => setTimeout(r, 2200));
  await waitVideos(ws, 2);

  /* ---------- A. 管理按钮 ---------- */
  const a = await evalJs(ws, `(()=>{
    const b=document.getElementById("adminBtn");
    if(!b)return {err:"未找到 adminBtn"};
    const cs=getComputedStyle(b);
    const inNav=!!b.closest("nav.nav");
    const nav=document.querySelector("nav.nav");
    const ncs=getComputedStyle(nav);
    const navRect=nav.getBoundingClientRect();
    const bRect=b.getBoundingClientRect();
    const list=document.getElementById("navUsers");
    const foot=b.closest(".nav-foot");
    return {
      count:document.querySelectorAll("#adminBtn").length,
      inNav:inNav, position:cs.position, fixed:cs.position==="fixed",
      navFlexDir:ncs.flexDirection,
      scrollContainer:!!document.querySelector(".nav-scroll"),
      scrollOverflow:document.querySelector(".nav-scroll")?getComputedStyle(document.querySelector(".nav-scroll")).overflowY:null,
      inFoot:!!foot,
      bottomDist:Math.round(navRect.bottom-bRect.bottom),
      ariaLabel:b.getAttribute("aria-label"), title:b.getAttribute("title"),
      visibleInViewport:bRect.top>=0&&bRect.bottom<=innerHeight
    };
  })()`);
  record("A1", "管理按钮位于左侧导航底部且非 fixed 定位",
    a && a.count === 1 && a.inNav && a.fixed === false && a.inFoot && a.bottomDist >= 0 && a.bottomDist < 80,
    JSON.stringify(a));
  record("A2", "导航为 Flex 纵向布局，博主列表独立滚动",
    a && a.navFlexDir === "column" && a.scrollContainer && a.scrollOverflow === "auto",
    `flexDirection=${a && a.navFlexDir} scrollOverflow=${a && a.scrollOverflow}`);
  record("A3", "管理按钮保留无障碍名称", a && a.ariaLabel === "管理设置" && a.title === "管理设置",
    `aria-label=${a && a.ariaLabel} title=${a && a.title}`);

  // 列表很长时按钮不被挤出屏幕
  const a4 = await evalJs(ws, `(async()=>{
    const list=document.getElementById("navUsers");
    const origin=list.innerHTML;
    let html="";
    for(let i=0;i<40;i++){html+='<div class="nav-item" data-user="x'+i+'">'+document.querySelector(".nav-item").innerHTML+'<span class="cnt">'+(i+1)+'</span></div>'}
    list.innerHTML=html;
    await new Promise(r=>setTimeout(r,300));
    const b=document.getElementById("adminBtn");
    const r=b.getBoundingClientRect();
    const out={buttonTop:Math.round(r.top),vh:innerHeight,inViewport:r.top>=0&&r.bottom<=innerHeight+1,scrollable:document.querySelector(".nav-scroll").scrollHeight>document.querySelector(".nav-scroll").clientHeight};
    list.innerHTML=origin;
    await new Promise(r=>setTimeout(r,200));
    return out;
  })()`);
  record("A4", "博主列表超长时管理按钮仍可见（不被挤出屏幕）",
    a4 && a4.inViewport === true && a4.scrollable === true, JSON.stringify(a4));

  // 点击打开 + 关闭 + 认证
  const a5 = await evalJs(ws, `(async()=>{
    document.getElementById("adminBtn").click();
    await new Promise(r=>setTimeout(r,500));
    const opened=document.getElementById("drawer").classList.contains("open");
    const state=await fetch("/api/admin/state").then(r=>r.json()).catch(()=>({}));
    const unauth=await fetch("/api/admin/subscriptions",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}).then(r=>r.status).catch(()=>0);
    document.getElementById("drawerClose").click();
    await new Promise(r=>setTimeout(r,400));
    const closed=!document.getElementById("drawer").classList.contains("open");
    return {opened:opened,closed:closed,subs:(state.subscriptions||[]).length,version:state.version,unauthStatus:unauth};
  })()`);
  record("A5", "点击打开管理后台、关闭正常、订阅与版本接口可用",
    a5 && a5.opened === true && a5.closed === true && a5.subs > 0, JSON.stringify(a5));
  record("A6", "无凭证的管理写操作被拒绝（认证仍生效）",
    a5 && a5.unauthStatus === 403, `status=${a5 && a5.unauthStatus}`);
  await shot(ws, "final_01_nav_admin.png");

  // 移动端：仅图标但保留无障碍名称
  await send(ws, "Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await new Promise(r => setTimeout(r, 800));
  const a7 = await evalJs(ws, `(()=>{
    const b=document.getElementById("adminBtn");
    const span=b.querySelector("span");
    const cs=span?getComputedStyle(span):null;
    const r=b.getBoundingClientRect();
    return {spanHidden:cs?cs.display==="none":null,aria:b.getAttribute("aria-label"),visible:r.width>0&&r.height>0};
  })()`);
  record("A7", "移动端只显示齿轮图标且保留无障碍名称",
    a7 && a7.spanHidden === true && a7.visible === true && a7.aria === "管理设置", JSON.stringify(a7));
  await shot(ws, "final_02_mobile_nav.png");
  await send(ws, "Emulation.clearDeviceMetricsOverride");
  await new Promise(r => setTimeout(r, 500));

  /* ---------- V. 视频与音轨 ---------- */
  await send(ws, "Page.navigate", { url: baseUrl });
  await new Promise(r => setTimeout(r, 2200));
  await waitVideos(ws, 2);

  const hasAudio = await evalJs(ws, `(async()=>{
    const v=document.querySelectorAll("video")[0];
    v.scrollIntoView({block:"center"});
    await new Promise(r=>setTimeout(r,600));
    await v.play().catch(()=>{});
    await new Promise(r=>setTimeout(r,1500));
    return {decoded:v.webkitAudioDecodedByteCount||0,hasAudioTrack:(v.audioTracks?v.audioTracks.length:null),dur:v.duration,ct:v.currentTime};
  })()`);
  record("V1", "测试视频真实带音轨（Chrome 已解码音频字节）",
    hasAudio && hasAudio.decoded > 0, JSON.stringify(hasAudio));

  // 滑出屏幕 → 暂停 + 音频解码停止（声音停止的客观证据）
  const v2 = await evalJs(ws, `(async()=>{
    const v=document.querySelectorAll("video")[0];
    const c1=v.webkitAudioDecodedByteCount||0; const t1=v.currentTime;
    window.scrollTo(0,0);
    await new Promise(r=>setTimeout(r,1500));
    const c2=v.webkitAudioDecodedByteCount||0;
    await new Promise(r=>setTimeout(r,1200));
    const c3=v.webkitAudioDecodedByteCount||0;
    return {paused:v.paused,ctBefore:t1,ctAfter:v.currentTime,decodedDeltaAfterPause:c3-c2,decodedGrowingBefore:c1>=0};
  })()`);
  record("V2", "滑出屏幕自动暂停且进度保留", v2 && v2.paused === true && Math.abs(v2.ctAfter - v2.ctBefore) < 0.3, JSON.stringify(v2));
  record("V3", "暂停后音频解码停止（声音同步停止的客观证据）",
    v2 && v2.decodedDeltaAfterPause === 0, `暂停后音频解码字节增量=${v2 && v2.decodedDeltaAfterPause}`);

  const v4 = await evalJs(ws, `(async()=>{
    const v=document.querySelectorAll("video")[0];
    v.scrollIntoView({block:"center"});
    await new Promise(r=>setTimeout(r,1200));
    const notAuto=!v.paused;
    const before=v.currentTime;
    await v.play().catch(()=>{});
    await new Promise(r=>setTimeout(r,700));
    return {notAuto:notAuto,before:before,after:v.currentTime,paused:v.paused};
  })()`);
  record("V4", "滑回不自动播放、再次点击从原进度继续",
    v4 && v4.notAuto === false && v4.paused === false && v4.after > v4.before, JSON.stringify(v4));

  const v5 = await evalJs(ws, `(async()=>{
    const vs=document.querySelectorAll("video");
    const b=vs[1];
    b.scrollIntoView({block:"center"});
    await new Promise(r=>setTimeout(r,600));
    await b.play().catch(()=>{});
    await new Promise(r=>setTimeout(r,900));
    return {aPaused:vs[0].paused,bPaused:b.paused,playing:[...vs].filter(v=>!v.paused).length};
  })()`);
  record("V5", "同时最多一个视频播放", v5 && v5.aPaused === true && v5.bPaused === false && v5.playing === 1, JSON.stringify(v5));

  const v6 = await evalJs(ws, `(async()=>{
    const tw=[...document.querySelectorAll(".tweet")].find(t=>t.querySelector("video"));
    tw.querySelector(".content").click();
    await new Promise(r=>setTimeout(r,1400));
    const dv=document.querySelector("#detail video");
    const bg=[...document.querySelectorAll("#feed video")].filter(v=>!v.paused).length;
    let played=false;
    if(dv){try{await dv.play();played=true}catch(e){}}
    await new Promise(r=>setTimeout(r,800));
    const detailAudioBefore=dv?dv.webkitAudioDecodedByteCount:null;
    document.getElementById("dtClose").click();
    await new Promise(r=>setTimeout(r,600));
    const after=dv?dv.webkitAudioDecodedByteCount:null;
    await new Promise(r=>setTimeout(r,900));
    const after2=dv?dv.webkitAudioDecodedByteCount:null;
    return {bgPlaying:bg,detailVideo:!!dv,played:played,anyPlaying:[...document.querySelectorAll("video")].filter(v=>!v.paused).length,
            detailAudioStopped:(after!==null&&after2!==null)?(after2-after===0):null};
  })()`);
  record("V6", "详情打开后背景视频停止；关闭详情后详情视频停止",
    v6 && v6.bgPlaying === 0 && v6.played === true && v6.anyPlaying === 0 && v6.detailAudioStopped === true, JSON.stringify(v6));

  const v7 = await evalJs(ws, `(async()=>{
    const v=document.querySelectorAll("video")[0];
    v.scrollIntoView({block:"center"});
    await new Promise(r=>setTimeout(r,700));
    await v.play().catch(()=>{});
    await new Promise(r=>setTimeout(r,700));
    const playingBefore=!v.paused;
    Object.defineProperty(document,"hidden",{configurable:true,get:()=>true});
    Object.defineProperty(document,"visibilityState",{configurable:true,get:()=>"hidden"});
    document.dispatchEvent(new Event("visibilitychange"));
    await new Promise(r=>setTimeout(r,500));
    return {playingBefore:playingBefore,pausedAfter:v.paused};
  })()`);
  record("V7", "切换标签页到后台自动暂停", v7 && v7.playingBefore === true && v7.pausedAfter === true, JSON.stringify(v7));

  const v8 = await evalJs(ws, `(async()=>{
    const inp=document.getElementById("volNum");
    inp.value="150"; inp.dispatchEvent(new Event("change"));
    await new Promise(r=>setTimeout(r,300));
    const v=document.querySelectorAll("video")[0];
    const at150={stored:localStorage.getItem("ar-vol"),vol:v.volume,muted:v.muted};
    inp.value="0"; inp.dispatchEvent(new Event("change"));
    await new Promise(r=>setTimeout(r,300));
    const v2=document.querySelectorAll("video")[0];
    const at0={stored:localStorage.getItem("ar-vol"),vol:v2.volume};
    inp.value="100"; inp.dispatchEvent(new Event("change"));
    await new Promise(r=>setTimeout(r,300));
    return {at150:at150,at0:at0,stored100:localStorage.getItem("ar-vol")};
  })()`);
  record("V8", "数字音量 0-150% 仍生效并记忆",
    v8 && v8.at150.stored === "150" && v8.at0.stored === "0" && v8.stored100 === "100", JSON.stringify(v8));

  /* ---------- R. 前端回归 ---------- */
  await send(ws, "Page.navigate", { url: baseUrl });
  await new Promise(r => setTimeout(r, 2500));
  await waitVideos(ws, 2);
  const r = await evalJs(ws, `(async()=>{
    const tweets=[...document.querySelectorAll(".tweet")];
    const imgs=document.querySelectorAll("#feed .media img").length;
    const vids=document.querySelectorAll("#feed video").length;
    const navUsers=document.querySelectorAll("#navUsers .nav-item").length;
    const st=await fetch("/api/admin/state").then(x=>x.json()).catch(()=>({}));
    // 立即更新按钮存在（需打开抽屉渲染订阅列表）
    document.getElementById("adminBtn").click();
    await new Promise(r2=>setTimeout(r2,800));
    const subCards=document.querySelectorAll("#subList .sub").length;
    const runBtns=document.querySelectorAll("#subList .op.run").length;
    const batchBtn=!!document.getElementById("batchBtn");
    document.getElementById("drawerClose").click();
    await new Promise(r2=>setTimeout(r2,400));
    // 详情与评论
    const tw=[...document.querySelectorAll(".tweet")].find(t=>t.querySelector(".media img"));
    tw.querySelector(".content").click();
    await new Promise(r2=>setTimeout(r2,1300));
    const detailOpen=document.getElementById("detailMask").style.display==="flex";
    const cmtHtml=document.getElementById("cmtBox")?document.getElementById("cmtBox").innerHTML:"";
    document.getElementById("dtClose").click();
    return {tweets:tweets.length,imgs:imgs,vids:vids,navUsers:navUsers,subsApi:(st.subscriptions||[]).length,
            subCards:subCards,runBtns:runBtns,batchBtn:batchBtn,detailOpen:detailOpen,
            commentSection:cmtHtml.length>0,version:st.version};
  })()`);
  record("R1", "时间线/图片/视频/博主列表正常",
    r && r.tweets >= 8 && r.imgs > 0 && r.vids === 2 && r.navUsers >= 3, JSON.stringify({t: r && r.tweets, i: r && r.imgs, v: r && r.vids, n: r && r.navUsers}));
  record("R2", "订阅管理/立即更新/全部更新按钮正常",
    r && r.subCards >= 3 && r.runBtns === r.subCards && r.batchBtn === true, JSON.stringify({subs: r && r.subCards, run: r && r.runBtns, batch: r && r.batchBtn}));
  record("R3", "帖子详情与评论展示正常",
    r && r.detailOpen === true && r.commentSection === true, JSON.stringify({open: r && r.detailOpen, cmt: r && r.commentSection}));
  record("R4", "后端版本与订阅接口正常（任务反馈依赖接口可用）",
    r && r.subsApi > 0 && !!r.version, `subs=${r && r.subsApi} version=${r && r.version}`);
  await shot(ws, "final_03_timeline.png");

  fs.writeFileSync(path.join(shotDir, "final_test_result.json"), JSON.stringify(results, null, 2), "utf-8");
  const failed = results.filter(x => !x.pass);
  console.log(`\n=== 合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length} ===`);
  ws.close();
  process.exit(failed.length ? 2 : 0);
})().catch(e => { console.error("脚本异常:", e.message); process.exit(1); });