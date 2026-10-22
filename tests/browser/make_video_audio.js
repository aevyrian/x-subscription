/* 用 headless Chrome 录制带音轨的真实 webm（canvas 视频 + 振荡器音频），供播放状态验收。
   用法：node tests/browser/make_video_audio.js <chrome-ws-url> <out.webm>
*/
const fs = require("fs");
const path = require("path");
let WebSocket;
try {
  WebSocket = require("ws");
} catch (e) {
  WebSocket = require(process.env.CDP_WS || path.join(__dirname, "..", "..", "local-dev", "cdp", "node_modules", "ws"));
}
const [wsUrl, outPath] = process.argv.slice(2);
let msgId = 0;
const pending = new Map();

function send(ws, method, params) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
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
  const script = `
  (async () => {
    const c = document.createElement('canvas');
    c.width = 640; c.height = 360;
    const ctx = c.getContext('2d');
    const vstream = c.captureStream(30);
    // 音轨：440Hz 正弦，音量随时间起伏（便于判断声音是否停止）
    const ac = new AudioContext();
    const osc = ac.createOscillator();
    const gain = ac.createGain();
    const dest = ac.createMediaStreamDestination();
    osc.frequency.value = 440;
    gain.gain.value = 0.12;
    osc.connect(gain); gain.connect(dest);
    osc.start();
    const tracks = vstream.getVideoTracks().concat(dest.stream.getAudioTracks());
    const mixed = new MediaStream(tracks);
    const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus') ? 'video/webm;codecs=vp8,opus' : 'video/webm';
    const rec = new MediaRecorder(mixed, { mimeType: mime });
    const chunks = [];
    rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    rec.start();
    const t0 = performance.now();
    await new Promise(done => {
      function frame() {
        const t = (performance.now() - t0) / 1000;
        ctx.fillStyle = 'hsl(' + ((t * 60) % 360) + ',65%,42%)';
        ctx.fillRect(0, 0, 640, 360);
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 46px sans-serif';
        ctx.fillText('AUDIO TEST ' + t.toFixed(1) + 's', 30, 200);
        gain.gain.value = 0.08 + 0.08 * Math.abs(Math.sin(t * 2));
        if (t < 6) requestAnimationFrame(frame); else done();
      }
      frame();
    });
    rec.stop();
    const blob = await new Promise(r => { rec.onstop = () => r(new Blob(chunks, { type: mime })); });
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    osc.stop(); ac.close();
    return btoa(s);
  })()`;
  const res = await send(ws, "Runtime.evaluate", {
    expression: script, awaitPromise: true, returnByValue: true, timeout: 90000,
  });
  const b64 = res.result && res.result.value;
  if (!b64) throw new Error("no data: " + JSON.stringify(res).slice(0, 300));
  const buf = Buffer.from(b64, "base64");
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, buf);
  console.log("audio webm written:", outPath, buf.length, "bytes");
  ws.close();
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });