// Exercise production download polling: multiple failed jobs must settle.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../nexus-dashboard/static/app.js'), 'utf8');
const code = source.slice(source.indexOf('async function dlTick()'), source.indexOf('/* 多任务下载进度'));
let jobs = [];
const notifications = [];
const scope = vm.createContext({
  api: async () => ({jobs}), isJunkJob: () => false,
  pruneJobSeen() {}, renderDlList() {}, dlSeen: {}, Date,
  showDoneNotice: (file) => notifications.push(['done', file]),
  addToast: (title, message) => notifications.push(['error', message]),
});
vm.runInContext(code, scope);
const startedAt = new Date().toISOString();

async function main() {
  jobs = [1, 2, 3, 4].map(id => ({id: `failed-${id}`, state: 'error', startedAt, error: `error-${id}`}));
  for (let i = 0; i < 20; i++) await scope.dlTick();
  assert.equal(notifications.length, 4, 'Each failed job is announced exactly once across repeated polls');
  assert.equal(new Set(notifications.map(n => n[1])).size, 4);

  jobs.reverse();
  await scope.dlTick();
  assert.equal(notifications.length, 4, 'Reordering retained history must not repeat errors');

  jobs.unshift({id: 'success', state: 'done', startedAt, savePath: 'done.zip'});
  jobs.unshift({id: 'new-error', state: 'error', startedAt, error: 'new error'});
  for (let i = 0; i < 5; i++) await scope.dlTick();
  assert.equal(notifications.length, 6, 'A completion and a new failure each produce one notification');
  assert.equal(notifications.filter(n => n[0] === 'done').length, 1);

  jobs.push({id: 'old-error', state: 'error', startedAt: new Date(Date.now() - 600000).toISOString()});
  await scope.dlTick();
  assert.equal(notifications.length, 6, 'Old history is not announced');
  console.log('Download notification polling tests passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
