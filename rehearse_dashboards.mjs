// The upgrade rehearsal's browser half: open EVERY dashboard as a reader would, and count its charts — drawn,
// in error, still loading. Run inside the Docker VM (a Chromium image) on the kit's network:
//   node rehearse_dashboards.mjs <base url> <user> <out.json>        (password in DEVKIT_REHEARSAL_PW)
// The first tab of each dashboard is what a reader sees on opening it; that is what is counted.
import { createRequire } from 'module';
import { writeFileSync } from 'fs';
const require = createRequire('/app/package.json');
const { chromium } = require('playwright');

const [base, user, out] = process.argv.slice(2);
const browser = await chromium.launch({ args: ['--disable-dev-shm-usage'] });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1400 }, locale: 'fr-FR' });
// a reader's browser: Superset mounts every chart at once when navigator.webdriver is set
await ctx.addInitScript(() => Object.defineProperty(navigator, 'webdriver', { get: () => false }));
const html = await (await ctx.request.get(`${base}/login/`)).text();
const csrf = (html.match(/name="csrf_token"[^>]*value="([^"]+)"/) || html.match(/id="csrf_token"[^>]*value="([^"]+)"/))[1];
await ctx.request.post(`${base}/login/`, { form: { username: user, password: process.env.DEVKIT_REHEARSAL_PW, csrf_token: csrf } });
if ((await ctx.request.get(`${base}/api/v1/me/`)).status() !== 200) throw new Error('sign-in failed');

const list = await (await ctx.request.get(`${base}/api/v1/dashboard/?q=(columns:!(id,dashboard_title),page_size:100)`)).json();
const page = await ctx.newPage();
const result = {};
for (const d of list.result.sort((a, b) => a.id - b.id)) {
  let failedRequests = 0;
  const onRes = (r) => { if (/\/api\/v1\/chart\/data|\/superset\/explore_json\//.test(r.url()) && r.status() >= 400) failedRequests++; };
  page.on('response', onRes);
  const resp = await page.goto(`${base}/superset/dashboard/${d.id}/`, { waitUntil: 'domcontentloaded' });
  let counts = { holders: 0, errors: 0, loading: 0 }, stable = 0;
  for (let i = 0; i < 120; i++) {           // a minute at most
    await page.waitForTimeout(500);
    counts = await page.evaluate(() => {
      const holders = [...document.querySelectorAll('.dashboard-component-chart-holder')];
      const err = (h) => !!h.querySelector('.ant-alert-error, [data-test="error-alert"], .alert-danger');
      const busy = (h) => !!h.querySelector('.loading, [data-test="loading-indicator"]');
      return { holders: holders.length, errors: holders.filter(err).length, loading: holders.filter(busy).length };
    });
    if (counts.holders && counts.loading === 0) { if (++stable >= 4) break; } else stable = 0;
  }
  page.off('response', onRes);
  const drawn = counts.holders - counts.errors - counts.loading;
  result[`${d.id} ${d.dashboard_title}`] =
    `page ${resp.status()}, ${counts.holders} charts on the first tab: ${drawn} drawn, ${counts.errors} in error, ` +
    `${counts.loading} still loading; ${failedRequests} failed chart request(s)`;
  console.log(`  ${d.id} ${d.dashboard_title}: ${result[`${d.id} ${d.dashboard_title}`]}`);
}
writeFileSync(out, JSON.stringify(result, null, 1));
await browser.close();
