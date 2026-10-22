// Run production polling functions with scripted API responses and a virtual timer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../nexus-dashboard/static/app.js'), 'utf8');
const statusCode = source.slice(source.indexOf('async function refreshStatus('), source.indexOf('/* ---------- “已下载”'));
const loginCode = source.slice(source.indexOf('function pollLoginImport('), source.indexOf('let noticeTimer ='));
const waitingCode = source.slice(source.indexOf('function waitForJobStart('), source.indexOf('async function startRefresh('));
const timers = new Map();
const notices = [];
let hidden = 0;
let loads = 0;
let nextTimer = 1;
let status;
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    classList: {add() {}, remove() {}, toggle() {}}, style: {},
    querySelector() { return element(id + '-inner'); },
  });
  return elements.get(id);
}
const state = {pollTimer: null};
const scope = vm.createContext({
  state, $: element, api: async () => status,
  setNotice: text => notices.push(text), hideNotice: () => hidden++,
  loadMods: async () => loads++, elapsedSec: () => 0,
  setInterval: callback => { const id = nextTimer++; timers.set(id, callback); return id; },
  clearInterval: id => timers.delete(id), Date,
});
vm.runInContext(statusCode + '\n' + loginCode + '\n' + waitingCode, scope);

async function main() {
  status = {auth: true, refresh_version: 7, job: {running: true}, refresh_error: 'old history timeout'};
  await scope.refreshStatus(true);
  assert.equal(notices.length, 0, 'An active retry must not display the previous failure');
  assert(hidden > 0);
  const firstTick = timers.get(state.pollTimer);
  status = {auth: true, refresh_version: 8, job: {running: false}, last_refresh: 'now'};
  await firstTick();
  assert.equal(loads, 1, 'Success before the first poll must still reload the mod list');
  assert.equal(notices.at(-1), '已刷新');
  assert.equal(state.pollTimer, null);

  notices.length = 0;
  status = {auth: true, refresh_version: 8, job: {running: false}, refresh_error: 'current failure'};
  await scope.refreshStatus(true);
  assert.equal(notices.at(-1), '刷新失败: current failure', 'A current completed failure must remain visible');

  status = {auth: true, refresh_version: 9, job: {running: false}, login: {state: 'done'}};
  scope.pollLoginImport();
  await [...timers.values()][0]();
  assert.equal(loads, 2, 'A scan completed before the login poll must reload the mod list');
  assert.equal(timers.size, 0);

  notices.length = 0;
  status = {auth: true, refresh_version: 9, job: {running: true}, refresh_error: 'old failure'};
  scope.waitForJobStart(null, 9);
  await [...timers.values()][0]();
  assert.equal(notices.length, 0, 'Waiting for a running retry must survive a stale error');
  assert(state.pollTimer, 'The running job should be monitored');
  console.log('PASS status polling: retry errors, fast completion and post-login results');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
