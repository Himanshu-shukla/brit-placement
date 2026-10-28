/* Brit Placement background runner (MV3 service worker).
   - Auto-apply runner: every minute, claims the next queued application for the learner
     who owns this Chrome profile, applies in a background tab, reports the outcome.
   - Board search: every 2 hours, opens the loops' search pages on boards that block
     servers (Indeed, Totaljobs, CV-Library, Reed...) and posts the listed jobs back. */

const DEFAULTS = {backend: 'http://127.0.0.1:8770', token: '', learnerId: '', runner: false, searcher: false,
                  companySites: true, nextAllowedAt: 0, lastResult: null, todayCount: 0};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let busy = false;

async function settings() { return {...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS)))}; }

async function api(path, method = 'GET', body) {
  const cfg = await settings();
  if (!cfg.token) throw new Error('Enter the backend admin key in the extension popup');
  const response = await fetch(cfg.backend.replace(/\/$/, '') + '/api/' + path, {
    method, headers: {'X-Placement-Key': cfg.token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Backend error ${response.status}`);
  return data;
}

function schedule() {
  chrome.alarms.create('brit-runner', {periodInMinutes: 1});
  chrome.alarms.create('brit-search', {periodInMinutes: 120, delayInMinutes: 3});
}
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'brit-runner') runQueue(false).catch(err => note('error', err.message));
  if (alarm.name === 'brit-search') runSearch(false).catch(err => note('error', err.message));
});

async function note(status, message, extra = {}) {
  await chrome.storage.local.set({lastResult: {status, message: String(message || '').slice(0, 300), at: new Date().toISOString(), ...extra}});
}

async function waitComplete(tabId, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab) return false;
    if (tab.status === 'complete') return true;
    await sleep(400);
  }
  return true;
}

async function runEngine(tabId, ctx) {
  await chrome.scripting.executeScript({target: {tabId}, files: ['engine.js']});
  const [result] = await chrome.scripting.executeScript({
    target: {tabId}, func: context => window.__britEngine.step(context), args: [ctx]
  });
  return result && result.result;
}

/* Trusted input through chrome.debugger for widgets that ignore scripted events
   (react-select dropdowns on Greenhouse, typeaheads). Chrome shows a "debugging" bar while attached. */
const cdp = (target, method, params = {}) => chrome.debugger.sendCommand(target, method, params);

async function withDebugger(tabId, task) {
  const target = {tabId};
  await chrome.debugger.attach(target, '1.3');
  try { return await task(target); } finally { await chrome.debugger.detach(target).catch(() => {}); }
}

async function centreOf(tabId, selector) {
  const [result] = await chrome.scripting.executeScript({target: {tabId}, args: [selector], func: sel => {
    const el = document.querySelector(sel);
    if (!el) return null;
    el.scrollIntoView({block: 'center'});
    const box = el.getBoundingClientRect();
    return {x: box.left + Math.min(box.width / 2, 40), y: box.top + box.height / 2};
  }});
  return result && result.result;
}

async function trustedClick(target, tabId, selector) {
  const point = await centreOf(tabId, selector);
  if (!point) return false;
  for (const type of ['mousePressed', 'mouseReleased']) await cdp(target, 'Input.dispatchMouseEvent', {type, ...point, button: 'left', clickCount: 1});
  return true;
}

async function trustedKey(target, key, code, keyCode) {
  await cdp(target, 'Input.dispatchKeyEvent', {type: 'rawKeyDown', key, code, windowsVirtualKeyCode: keyCode});
  await cdp(target, 'Input.dispatchKeyEvent', {type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode});
}

async function readOptions(tabId, selectors) {
  const found = {};
  await withDebugger(tabId, async target => {
    for (const selector of selectors.slice(0, 30)) {
      if (!(await trustedClick(target, tabId, selector))) { found[selector] = []; continue; }
      await sleep(400);
      const [result] = await chrome.scripting.executeScript({target: {tabId}, func: () => [...document.querySelectorAll('[role="option"]')]
        .filter(o => o.getBoundingClientRect().height > 0).map(o => o.innerText.trim()).filter(Boolean).slice(0, 80)});
      found[selector] = (result && result.result) || [];
      await trustedKey(target, 'Escape', 'Escape', 27);
    }
  });
  return found;
}

async function trustedFill(tabId, fills) {
  await withDebugger(tabId, async target => {
    for (const fill of fills.slice(0, 30)) {
      if (!(await trustedClick(target, tabId, fill.selector))) continue;
      await chrome.scripting.executeScript({target: {tabId}, args: [fill.selector], func: sel => document.querySelector(sel)?.select?.()});
      await trustedKey(target, 'Backspace', 'Backspace', 8);
      await cdp(target, 'Input.insertText', {text: String(fill.value).slice(0, 60)});
      await sleep(600);
      await trustedKey(target, 'Enter', 'Enter', 13);
      await sleep(250);
    }
  });
}

/* Drive one application to a final state. Follows navigations and tabs opened by
   "Apply" buttons (Easy Apply flows, Indeed Apply, company sites). */
async function applyJob(payload, existingTabId = null, options = {}) {
  const ownTab = existingTabId === null;
  let tabId = existingTabId ?? (await chrome.tabs.create({url: payload.application.url, active: false})).id;
  const opened = [];
  const onCreated = tab => { if (tab.openerTabId === tabId) opened.push(tab.id); };
  chrome.tabs.onCreated.addListener(onCreated);
  const ctx = {profile: payload.profile, answers: payload.answers, cv: payload.cv, job: payload.job,
               applicationId: payload.application.id, submit: options.submit !== false, afterSubmit: false,
               trusted: true, comboOptions: {}};
  let idle = 0, steps = 0;
  try {
    for (let round = 0; round < 35; round++) {
      await waitComplete(tabId);
      await sleep(1500 + Math.random() * 1500);
      if (opened.length) {
        const next = opened.pop();
        if (ownTab) chrome.tabs.remove(tabId).catch(() => {});
        tabId = next;
        continue;
      }
      let result;
      try { result = await runEngine(tabId, ctx); }
      catch (error) { result = {state: 'navigating', reason: String(error.message || error)}; }
      result = result || {state: 'navigating'};
      steps++;
      switch (result.state) {
        case 'submitted': return {status: 'confirmed', evidence: result.evidence, steps};
        case 'already_applied': return {status: 'already_applied', evidence: result.evidence, steps};
        case 'ready': return {status: 'prepared', reason: 'Filled; ready for manual submit', steps};
        case 'filled': return {status: 'prepared', reason: `${result.filled} field(s) filled`, unknown: result.unknown, steps};
        case 'need_options':
          Object.assign(ctx.comboOptions, await readOptions(tabId, result.selectors || []).catch(() =>
            Object.fromEntries((result.selectors || []).map(s => [s, []]))));
          continue;
        case 'trusted_fill':
          await trustedFill(tabId, result.fills || []).catch(() => {});
          continue;
        case 'external':
          await chrome.tabs.update(tabId, {url: result.url});
          idle = 0; continue;
        case 'continue': case 'navigating':
          if (result.clicked === 'submit') ctx.afterSubmit = true;
          idle = 0; continue;
        case 'no_form':
          if (ctx.afterSubmit && ++idle >= 2) return {status: 'needs_review', reason: 'Submitted but no confirmation detected — check the inbox', steps};
          if (++idle >= 3) return {status: 'needs_review', reason: result.reason, steps};
          continue;
        default:
          return {status: 'needs_review', reason: `${result.state}: ${result.reason || ''}`.trim(), unknown: result.unknown, steps, url: result.url};
      }
    }
    return {status: 'needs_review', reason: 'Too many steps without finishing', steps};
  } finally {
    chrome.tabs.onCreated.removeListener(onCreated);
    if (ownTab) for (const id of [tabId, ...opened]) chrome.tabs.remove(id).catch(() => {});
  }
}

async function runQueue(force) {
  if (busy) return {skipped: 'busy'};
  const cfg = await settings();
  if ((!cfg.runner && !force) || !cfg.learnerId || !cfg.token) return {skipped: 'runner off'};
  if (!force && Date.now() < cfg.nextAllowedAt) return {skipped: 'waiting'};
  busy = true;
  try {
    const payload = await api('extension/next', 'POST', {learner_id: Number(cfg.learnerId), include_server: !!cfg.companySites});
    if (!payload.application) {
      await note('idle', `Queue empty · ${payload.applied_today || 0}/${payload.limit || ''} applied today`);
      return {idle: true};
    }
    await note('applying', `${payload.job.title} — ${payload.job.company}`);
    let outcome;
    try { outcome = await applyJob(payload); }
    catch (error) { outcome = {status: 'error', reason: String(error.message || error)}; }
    const saved = await api('extension/result', 'POST', {application_id: payload.application.id, ...outcome});
    const [low, high] = payload.delay || [45, 120];
    await chrome.storage.local.set({nextAllowedAt: Date.now() + 1000 * (low + Math.random() * Math.max(0, high - low))});
    await note(saved.status, `${payload.job.title} — ${payload.job.company}: ${outcome.evidence || outcome.reason || saved.status}`);
    return saved;
  } finally { busy = false; }
}

async function runSearch(force) {
  const cfg = await settings();
  if ((!cfg.searcher && !force) || !cfg.token) return {skipped: true};
  const tasks = (await api('extension/search-tasks')).slice(0, 16);
  let total = 0;
  for (const task of tasks) {
    const tab = await chrome.tabs.create({url: task.url, active: false});
    try {
      await waitComplete(tab.id, 40000);
      await sleep(3000);
      for (let i = 0; i < 3; i++) {
        await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => {
          const list = document.querySelector('.jobs-search-results-list, .scaffold-layout__list > div, [class*="results" i]');
          (list || document.scrollingElement).scrollBy(0, 2500);
        }}).catch(() => {});
        await sleep(1200);
      }
      await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['engine.js']});
      const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: board => window.__britEngine.harvest(board), args: [task.board]});
      const jobs = (result && result.result) || [];
      if (jobs.length) total += (await api('jobs/harvest', 'POST', {board: task.board, jobs})).saved;
    } catch (error) {
      await note('error', `Search ${task.board}: ${error.message}`);
    } finally {
      chrome.tabs.remove(tab.id).catch(() => {});
    }
    await sleep(4000 + Math.random() * 6000);
  }
  if (total) await api('loops/run-all', 'POST', {}).catch(() => {});
  await note('search', `Board search saved ${total} job(s) from ${tasks.length} page(s)`);
  return {saved: total, pages: tasks.length};
}

async function applyHere(tabId, submit) {
  const cfg = await settings();
  if (!cfg.learnerId) throw new Error('Choose the candidate for this Chrome profile first');
  await chrome.scripting.executeScript({target: {tabId}, files: ['engine.js']});
  const [info] = await chrome.scripting.executeScript({target: {tabId}, func: () => window.__britEngine.jobInfo()});
  const payload = await api('extension/apply-now', 'POST', {learner_id: Number(cfg.learnerId), job: info.result});
  busy = true;
  try {
    const outcome = await applyJob(payload, tabId, {submit});
    const saved = await api('extension/result', 'POST', {application_id: payload.application.id, ...outcome});
    await note(saved.status, `${payload.job.title}: ${outcome.evidence || outcome.reason || saved.status}`);
    return {...outcome, result: saved.status};
  } finally { busy = false; }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const reply = promise => { promise.then(sendResponse, error => sendResponse({error: String(error.message || error)})); return true; };
  if (message.type === 'brit-answers') return reply(api('questions/answer', 'POST', message.payload));
  if (message.type === 'apply-here') return reply(applyHere(message.tabId, message.submit !== false));
  if (message.type === 'run-queue') return reply(runQueue(true));
  if (message.type === 'run-search') return reply(runSearch(true));
  if (message.type === 'harvest-here') return reply((async () => {
    await chrome.scripting.executeScript({target: {tabId: message.tabId}, files: ['engine.js']});
    const [result] = await chrome.scripting.executeScript({target: {tabId: message.tabId}, func: () => window.__britEngine.harvest()});
    const jobs = (result && result.result) || [];
    return jobs.length ? api('jobs/harvest', 'POST', {board: 'manual', jobs}) : {saved: 0};
  })());
  return false;
});
