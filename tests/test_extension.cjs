// Offline checks of the Chrome extension package. The apply engine itself is exercised in a
// real browser by tests/test_engine_browser.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const dir = path.join(__dirname, '../extension');

const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
assert.equal(manifest.manifest_version, 3);
for (const permission of ['scripting', 'storage', 'tabs', 'alarms']) assert.ok(manifest.permissions.includes(permission), permission);
assert.ok(manifest.host_permissions.includes('<all_urls>'));
assert.equal(manifest.background.service_worker, 'background.js');
for (const file of ['background.js', 'engine.js', 'popup.js', 'popup.html', 'popup.css']) assert.ok(fs.existsSync(path.join(dir, file)), file);

// The engine registers itself once and exposes step / jobInfo / harvest.
const sandbox = {window: {}, document: {}, location: {hostname: 'example.org', href: 'https://example.org/'}};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(dir, 'engine.js'), 'utf8'), sandbox);
assert.equal(sandbox.__britEngine.version, 3);
for (const fn of ['step', 'jobInfo', 'harvest']) assert.equal(typeof sandbox.__britEngine[fn], 'function');

// The background worker wires alarms and the message types the popup and engine use.
const listeners = {};
const hook = name => ({addListener: fn => { listeners[name] = fn; }, removeListener() {}});
const chrome = {runtime: {onInstalled: hook('installed'), onStartup: hook('startup'), onMessage: hook('message')},
  alarms: {create: (name, info) => { (listeners.alarms ||= {})[name] = info; }, onAlarm: hook('alarm')},
  storage: {local: {get: async () => ({}), set: async () => {}}}, tabs: {onCreated: hook('created')}};
const worker = {chrome, fetch: async () => ({ok: true, json: async () => ({})}), setTimeout, console};
vm.createContext(worker);
vm.runInContext(fs.readFileSync(path.join(dir, 'background.js'), 'utf8'), worker);
listeners.installed();
assert.equal(listeners.alarms['brit-runner'].periodInMinutes, 1);
assert.equal(listeners.alarms['brit-search'].periodInMinutes, 120);
assert.equal(listeners.message({type: 'unknown'}, {}, () => {}), false);
assert.equal(listeners.message({type: 'brit-answers', payload: {fields: []}}, {}, () => {}), true);

const popup = fs.readFileSync(path.join(dir, 'popup.html'), 'utf8');
for (const id of ['runner', 'searcher', 'company-sites', 'apply-here', 'fill-here', 'harvest-here', 'learner']) assert.ok(popup.includes(`id="${id}"`), id);
console.log('PASS: MV3 manifest, engine exports, background alarms and messaging, popup controls');
