import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const screenshotsDir = join(__dirname, '..', 'docs', 'screenshots');
mkdirSync(screenshotsDir, { recursive: true });

const chromeExe = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const userDataDir = 'C:\\Users\\LENOVO\\AppData\\Local\\Temp\\chrome_cdp_profile';

console.log('Launching headless Chrome on port 9222...');
const chrome = spawn(chromeExe, [
  '--remote-debugging-port=9222',
  '--headless=new',
  `--user-data-dir=${userDataDir}`,
  '--no-first-run',
  '--no-default-browser-check',
  '--window-size=1440,900',
  'about:blank',
], { stdio: 'ignore' });

const cleanup = () => {
  try {
    chrome.kill('SIGKILL');
  } catch {}
};
process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(1); });

// Wait for CDP to be responsive
let cdpReady = false;
for (let i = 0; i < 30; i++) {
  await new Promise(r => setTimeout(r, 500));
  try {
    const res = await fetch('http://127.0.0.1:9222/json/version');
    if (res.ok) {
      cdpReady = true;
      break;
    }
  } catch {}
}

if (!cdpReady) {
  cleanup();
  console.error('Chrome CDP did not become ready in time.');
  process.exit(1);
}

console.log('Chrome CDP is ready. Connecting WebSocket...');
const pages = await (await fetch('http://127.0.0.1:9222/json/list')).json();
let page = pages.find(p => p.type === 'page');
if (!page) {
  // Create a new target
  const newTarget = await (await fetch('http://127.0.0.1:9222/json/new', { method: 'PUT' })).json();
  page = newTarget;
}

const socket = new WebSocket(page.webSocketDebuggerUrl);

let nextId = 0;
const pending = new Map();

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

socket.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) reject(msg.error);
    else resolve(msg.result);
  }
};

await new Promise((resolve, reject) => {
  socket.onopen = resolve;
  socket.onerror = reject;
});

await send('Page.enable');
await send('Runtime.enable');
await send('Network.enable');
await send('Emulation.setDeviceMetricsOverride', {
  width: 1440,
  height: 900,
  deviceScaleFactor: 1,
  mobile: false,
});

async function evalInPage(expression) {
  const res = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (res.exceptionDetails) {
    throw new Error(res.exceptionDetails.text || 'Eval exception');
  }
  return res.result?.value;
}

async function waitFor(expression, timeoutMs = 20000, pollIntervalMs = 500) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const val = await evalInPage(expression);
      if (val) return val;
    } catch {}
    await new Promise(r => setTimeout(r, pollIntervalMs));
  }
  throw new Error(`Timeout waiting for: ${expression}`);
}

async function navigate(url) {
  console.log(`Navigating to ${url}...`);
  await send('Page.navigate', { url });
  // Wait for initial load
  await new Promise(r => setTimeout(r, 2000));
}

async function capture(fileName) {
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  const outPath = join(screenshotsDir, fileName);
  writeFileSync(outPath, shot.data, 'base64');
  console.log(`Saved screenshot: ${outPath}`);
}

try {
  // 1. /live (replay finished, hero card and step list)
  console.log('\n--- 1. /live (replay finished, hero card and step list) ---');
  await navigate('https://vasooli-paypal.onrender.com/live');
  await waitFor("document.body.innerText.includes('Mission Control')");
  
  // Switch to replay mode
  await evalInPage(`(() => {
    const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Recorded Replay'));
    if (btn) btn.click();
  })()`);
  await new Promise(r => setTimeout(r, 1500));
  
  // Verify hero card and step list
  const hasMissionControl = await evalInPage("document.body.innerText.includes('Mission Control') && document.body.innerText.includes('Verified by PayPal webhook')");
  console.log('Mission Control replay ready:', hasMissionControl);
  await evalInPage('window.scrollTo(0, 0)');
  await new Promise(r => setTimeout(r, 500));
  await capture('01-mission-control.png');

  // 2. /live Why card
  console.log('\n--- 2. /live Why card ---');
  await evalInPage(`(() => {
    const heading = Array.from(document.querySelectorAll('h2, h3, div')).find(el => el.textContent.includes('Why: Diagnostic & Decision Explanations'));
    const card = heading?.closest('.rounded-xl') || heading;
    if (card) {
      card.scrollIntoView({ block: 'center', behavior: 'instant' });
    }
  })()`);
  await new Promise(r => setTimeout(r, 600));
  await capture('02-why-card.png');

  // 3. /rules after clicking conservative example and previewing
  console.log('\n--- 3. /rules (conservative example + preview) ---');
  await navigate('https://vasooli-paypal.onrender.com/rules');
  await waitFor("document.body.innerText.includes('Plain-English Rule Proposal')");
  
  // Click Conservative chip
  await evalInPage(`(() => {
    const chip = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Conservative'));
    if (chip) chip.click();
  })()`);
  await new Promise(r => setTimeout(r, 600));

  // Click Preview Proposal
  await evalInPage(`(() => {
    const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Preview Proposal'));
    if (btn) btn.click();
  })()`);
  
  // Wait for preview results
  await waitFor("document.body.innerText.includes('Safety bounds') || document.body.innerText.includes('Diff vs active rulebook') || document.body.innerText.includes('Proposal')");
  await new Promise(r => setTimeout(r, 1000));
  await evalInPage(`(() => {
    const preview = Array.from(document.querySelectorAll('h2, h3')).find(el => el.textContent.includes('Preview') || el.textContent.includes('Impact'));
    if (preview) {
      preview.closest('section')?.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  })()`);
  await new Promise(r => setTimeout(r, 500));
  await capture('03-rules.png');

  // 4. /agent after running three scenarios
  console.log('\n--- 4. /agent (three scenarios) ---');
  await navigate('https://vasooli-paypal.onrender.com/agent');
  await waitFor("document.body.innerText.includes('Try to break it')");
  
  // Wait for scenarios to load
  await waitFor("document.querySelectorAll('button').length > 5");
  await new Promise(r => setTimeout(r, 1000));

  // Run Scenario 1
  console.log('Running scenario 1...');
  await evalInPage(`(() => {
    const btns = Array.from(document.querySelectorAll('button')).filter(b => b.querySelector('.group-hover\\\\:text-indigo-600') || b.textContent.includes('Bypass') || b.textContent.includes('Scenario') || b.className.includes('group relative'));
    if (btns[0]) btns[0].click();
  })()`);
  await new Promise(r => setTimeout(r, 2000));

  // Run Scenario 2
  console.log('Running scenario 2...');
  await evalInPage(`(() => {
    const btns = Array.from(document.querySelectorAll('button')).filter(b => b.querySelector('.group-hover\\\\:text-indigo-600') || b.textContent.includes('Bypass') || b.textContent.includes('Scenario') || b.className.includes('group relative'));
    if (btns[1]) btns[1].click();
  })()`);
  await new Promise(r => setTimeout(r, 2000));

  // Run Scenario 3
  console.log('Running scenario 3...');
  await evalInPage(`(() => {
    const btns = Array.from(document.querySelectorAll('button')).filter(b => b.querySelector('.group-hover\\\\:text-indigo-600') || b.textContent.includes('Bypass') || b.textContent.includes('Scenario') || b.className.includes('group relative'));
    if (btns[2]) btns[2].click();
  })()`);
  await new Promise(r => setTimeout(r, 2500));

  await evalInPage('window.scrollTo(0, 150)');
  await new Promise(r => setTimeout(r, 500));
  await capture('04-agent.png');

  // 5. /ledger
  console.log('\n--- 5. /ledger ---');
  await navigate('https://vasooli-paypal.onrender.com/ledger');
  await waitFor("document.body.innerText.includes('Audit Ledger') || document.body.innerText.includes('Ledger')");
  await new Promise(r => setTimeout(r, 1500));
  await evalInPage('window.scrollTo(0, 0)');
  await capture('05-ledger.png');

  // 6. /audit-trail (the newest event)
  console.log('\n--- 6. /audit-trail ---');
  await navigate('https://vasooli-paypal.onrender.com/audit-trail');
  await waitFor("document.body.innerText.includes('Audit Trail') || document.body.innerText.includes('Diagnosis')");
  await new Promise(r => setTimeout(r, 1500));
  await evalInPage('window.scrollTo(0, 0)');
  await capture('06-audit-trail.png');

  // 7. /studio (data loaded)
  console.log('\n--- 7. /studio ---');
  await navigate('https://vasooli-paypal.onrender.com/studio');
  await waitFor("document.body.innerText.includes('Studio') || document.body.innerText.includes('Assistant')");
  await new Promise(r => setTimeout(r, 2000));
  await evalInPage('window.scrollTo(0, 0)');
  await capture('07-studio.png');

  console.log('\nAll 7 screenshots captured successfully!');
} catch (err) {
  console.error('Error during screenshot capture:', err);
} finally {
  socket.close();
  cleanup();
}

