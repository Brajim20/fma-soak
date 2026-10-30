// Evidence screenshot for the FMA soak (task 3.8), self-contained so it also runs on a CI runner.
// usage: FLEET_URL=https://... FLEET_UI_TOKEN=... node shot.mjs <path> <out.png> [search text]
// The token is injected as the UI's auth cookie (never typed, never on the command line).
// Playwright comes from PW_DIR (CI sets it), else soak/tools (`npm i playwright` there), else the current directory.
import { createRequire } from 'module';
import path from 'path';
import { fileURLToPath } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const candidates = [process.env.PW_DIR, here, process.cwd()].filter(Boolean);
let chromium;
for (const dir of candidates) {
  try { ({ chromium } = createRequire(path.join(dir, '/'))('playwright')); break; } catch {}
}
if (!chromium) { console.error(`playwright not found in ${candidates.join(', ')}`); process.exit(3); }

const [target, out, search] = process.argv.slice(2);
const base = (process.env.FLEET_URL || '').replace(/\/$/, '');
const token = process.env.FLEET_UI_TOKEN;
if (!base || !token || !target || !out) { console.error('need FLEET_URL, FLEET_UI_TOKEN, <path>, <out.png>'); process.exit(2); }

const u = new URL(base);
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true, reducedMotion: 'reduce' });
await context.addCookies([{ name: u.protocol === 'https:' ? '__Host-token' : 'token', value: token, url: u.origin + '/', secure: u.protocol === 'https:', sameSite: 'Lax' }]);
const page = await context.newPage();
await page.goto(u.origin + target, { waitUntil: 'load', timeout: 60000 });
await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
if (/\/(login|apionlyuser)/.test(page.url())) { console.error(`ended on ${new URL(page.url()).pathname}: token rejected, expired or API-only`); await browser.close(); process.exit(1); }
if (search) {
  const box = page.locator("input[placeholder^='Search']").first();
  await box.waitFor({ timeout: 15000 }).catch(() => {});
  await box.fill(search).catch(() => {});
}
await page.waitForTimeout(2500);
await page.screenshot({ path: out, animations: 'disabled', caret: 'hide' });
await browser.close();
console.log(out);
