/* 截图工具：按视口尺寸抓取 Reader 首页，用于人工与视觉验收。
   用法：node tests/browser/shoot.js <page-ws-url> <base-url> <out-dir>
*/
const fs = require("fs");
const path = require("path");
let WebSocket;
try { WebSocket = require("ws"); } catch (e) {
  WebSocket = require(process.env.CDP_WS || path.join(__dirname, "..", "..", "local-dev", "cdp", "node_modules", "ws"));
}
const [wsUrl, baseUrl, outDir] = process.argv.slice(2);
let msgId = 0;
const pending = new Map();

function send(ws, method, params) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

const VIEWPORTS = [
  { name: "desktop_1920", w: 1920, h: 1080, dpr: 1, mobile: false },
  { name: "desktop_1440", w: 1440, h: 900, dpr: 1, mobile: false },
  { name: "tablet_834", w: 834, h: 1112, dpr: 2, mobile: true },
  { name: "mobile_390", w: 390, h: 844, dpr: 2, mobile: true },
];

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
  fs.mkdirSync(outDir, { recursive: true });

  for (const vp of VIEWPORTS) {
    await send(ws, "Emulation.setDeviceMetricsOverride",
      { width: vp.w, height: vp.h, deviceScaleFactor: vp.dpr, mobile: vp.mobile });
    await send(ws, "Page.navigate", { url: baseUrl });
    await new Promise(r => setTimeout(r, 2600));
    const s = await send(ws, "Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(outDir, `${vp.name}.png`), Buffer.from(s.data, "base64"));
    // 记录是否出现水平溢出
    const ov = await send(ws, "Runtime.evaluate", {
      expression: `JSON.stringify({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,bodySw:document.body.scrollWidth})`,
      returnByValue: true,
    });
    console.log(`${vp.name}: saved  overflow=${ov.result.value}`);
  }
  await send(ws, "Emulation.clearDeviceMetricsOverride");
  ws.close();
  console.log("shots done");
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });