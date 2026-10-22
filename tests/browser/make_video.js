/* 用 headless Chrome 的 MediaRecorder 录制一段真实可播放的 webm，供本地视频验收使用。
   不依赖 ffmpeg。用法：node local-dev/make_video.js <chrome-ws-url> <out.webm>
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

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, { perMessageDeflate: false, maxPayload: 256 * 1024 * 1024 });
    ws.on("open", () => resolve(ws));
    ws.on("error", reject);
  });
}

function send(ws, id, method, params) {
  return new Promise((resolve, reject) => {
    const onMsg = (raw) => {
      const msg = JSON.parse(raw);
      if (msg.id === id) {
        ws.off("message", onMsg);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    };
    ws.on("message", onMsg);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

(async () => {
  const ws = await connect(wsUrl);
  await send(ws, 1, "Runtime.enable", {});
  const script = `
    (async () => {
      const c = document.createElement('canvas');
      c.width = 640; c.height = 360;
      const ctx = c.getContext('2d');
      const stream = c.captureStream(30);
      const rec = new MediaRecorder(stream, { mimeType: 'video/webm' });
      const chunks = [];
      rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
      rec.start();
      const t0 = performance.now();
      await new Promise(done => {
        function frame() {
          const t = (performance.now() - t0) / 1000;
          ctx.fillStyle = 'hsl(' + ((t * 90) % 360) + ',70%,45%)';
          ctx.fillRect(0, 0, 640, 360);
          ctx.fillStyle = '#fff';
          ctx.font = 'bold 48px sans-serif';
          ctx.fillText('X Archive ' + t.toFixed(1) + 's', 40, 200);
          if (t < 3) requestAnimationFrame(frame); else done();
        }
        frame();
      });
      rec.stop();
      const blob = await new Promise(r => { rec.onstop = () => r(new Blob(chunks, { type: 'video/webm' })); });
      const buf = await blob.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let s = '';
      for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
      return btoa(s);
    })()
  `;
  const res = await send(ws, 2, "Runtime.evaluate", {
    expression: script, awaitPromise: true, returnByValue: true, timeout: 60000,
  });
  const b64 = res.result && res.result.value;
  if (!b64) throw new Error("no data: " + JSON.stringify(res).slice(0, 400));
  const buf = Buffer.from(b64, "base64");
  fs.writeFileSync(outPath, buf);
  console.log("webm written:", outPath, buf.length, "bytes");
  ws.close();
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });