const byId = id => document.getElementById(id);
const notify = (message, error = false) => { byId('message').textContent = message; byId('message').classList.toggle('error', error); };
const send = message => new Promise(resolve => chrome.runtime.sendMessage(message, resolve));

async function backendCall(path) {
  const {backend, token} = await chrome.storage.local.get(['backend', 'token']);
  const response = await fetch((backend || 'http://127.0.0.1:8770').replace(/\/$/, '') + '/api/' + path, {headers: {'X-Placement-Key': token || ''}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Backend error');
  return data;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || !/^https:\/\//.test(tab.url || '')) throw new Error('Open an HTTPS job page first');
  return tab;
}

async function loadLearners() {
  const learners = await backendCall('learners');
  const {learnerId} = await chrome.storage.local.get('learnerId');
  const select = byId('learner');
  select.replaceChildren();
  const blank = document.createElement('option'); blank.value = ''; blank.textContent = 'Choose candidate'; select.appendChild(blank);
  for (const person of learners.filter(x => x.active)) {
    const option = document.createElement('option');
    option.value = String(person.id);
    option.textContent = `${person.name} · ${person.email}${person.permission_to_apply ? '' : ' (no consent)'}`;
    select.appendChild(option);
  }
  if (learnerId) select.value = String(learnerId);
  notify(`${learners.length} candidate(s) loaded`);
}

async function showState() {
  const data = await chrome.storage.local.get(['runner', 'searcher', 'companySites', 'lastResult', 'backend', 'token']);
  byId('runner').checked = !!data.runner;
  byId('searcher').checked = !!data.searcher;
  byId('company-sites').checked = data.companySites !== false;
  byId('backend').value = data.backend || 'http://127.0.0.1:8770';
  byId('token').value = data.token || '';
  byId('runner-state').textContent = data.runner ? 'RUNNING' : 'OFF';
  byId('runner-state').classList.toggle('on', !!data.runner);
  const last = data.lastResult;
  byId('last').textContent = last ? `${new Date(last.at).toLocaleTimeString()} · ${last.status}: ${last.message}` : '';
  if (!data.token) byId('setup').open = true;
}

async function guard(task) {
  try { await task(); } catch (error) { notify(error.message || String(error), true); }
}

byId('connect').addEventListener('click', () => guard(async () => {
  await chrome.storage.local.set({backend: byId('backend').value.trim() || 'http://127.0.0.1:8770', token: byId('token').value.trim()});
  await loadLearners();
}));
byId('learner').addEventListener('change', () => chrome.storage.local.set({learnerId: byId('learner').value}));
for (const [id, key] of [['runner', 'runner'], ['searcher', 'searcher'], ['company-sites', 'companySites']]) {
  byId(id).addEventListener('change', async () => {
    if (id === 'runner' && byId('runner').checked && !byId('learner').value) {
      byId('runner').checked = false; return notify('Choose the candidate for this Chrome profile first', true);
    }
    await chrome.storage.local.set({[key]: byId(id).checked});
    showState();
  });
}
byId('run-queue').addEventListener('click', () => guard(async () => {
  notify('Applying to the next queued job…');
  const result = await send({type: 'run-queue'});
  if (result?.error) throw new Error(result.error);
  notify(result?.idle ? 'Queue is empty' : result?.skipped ? `Skipped: ${result.skipped}` : `Result: ${result?.status}`);
  showState();
}));
byId('run-search').addEventListener('click', () => guard(async () => {
  notify('Searching job boards in background tabs…');
  const result = await send({type: 'run-search'});
  if (result?.error) throw new Error(result.error);
  notify(`Saved ${result?.saved || 0} job(s) from ${result?.pages || 0} search page(s)`);
}));
for (const [id, submit] of [['apply-here', true], ['fill-here', false]]) {
  byId(id).addEventListener('click', () => guard(async () => {
    const tab = await activeTab();
    notify(submit ? 'Applying on this page…' : 'Filling this application…');
    const result = await send({type: 'apply-here', tabId: tab.id, submit});
    if (result?.error) throw new Error(result.error);
    notify(`${result.result}: ${result.evidence || result.reason || ''}`, result.result === 'needs_review');
    showState();
  }));
}
byId('harvest-here').addEventListener('click', () => guard(async () => {
  const tab = await activeTab();
  const result = await send({type: 'harvest-here', tabId: tab.id});
  if (result?.error) throw new Error(result.error);
  notify(`Saved ${result.saved} job(s) from this page`);
}));

showState().then(async () => {
  const {token} = await chrome.storage.local.get('token');
  if (token) guard(loadLearners);
  activeTab().then(tab => { byId('page').textContent = tab.title.slice(0, 120); }).catch(() => {});
});
