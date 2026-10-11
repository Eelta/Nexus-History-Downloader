const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const scraper = fs.readFileSync(
  path.join(__dirname, '..', 'nexus-dashboard', 'scraper.py'), 'utf8',
);
const extractSource = scraper.match(/HISTORY_EXTRACT_JS\s*=\s*r"""([\s\S]*?)"""/);
assert.ok(extractSource, 'the history extraction script must exist');

function historyRow(downloadedAt) {
  const cells = ['Example mod', downloadedAt, 'author', 'category', '2026-04-01 14:02'];
  return {
    innerText: cells.join('\n'),
    querySelectorAll(selector) {
      assert.equal(selector, 'td');
      return cells.map(innerText => ({ innerText }));
    },
  };
}

function modLink(row) {
  return {
    href: 'https://www.nexusmods.com/skyrimspecialedition/mods/42',
    closest(selector) {
      return selector === 'tr' || selector === 'li,article,div' ? row : null;
    },
  };
}

test('extract every download row while deduplicating links inside a row', () => {
  const older = historyRow('2026-03-01 12:00');
  const latest = historyRow('2026-04-01 16:55');
  const links = [modLink(older), modLink(older), modLink(latest), modLink(latest)];
  const document = {
    body: { innerText: '2 downloads; 1 to 2 of 2 entries' },
    querySelectorAll(selector) {
      assert.equal(selector, 'a[href]');
      return links;
    },
  };
  const result = vm.runInNewContext(`(${extractSource[1]})()`, { document });
  assert.equal(result.items.length, 2, 'same-mod rows must reach Python deduplication');
  assert.equal(result.items[0].cells[1], '2026-03-01 12:00');
  assert.equal(result.items[1].cells[1], '2026-04-01 16:55');
  assert.equal(result.items[0].modId, 42);
  assert.equal(result.items[1].modId, 42);
  assert.ok(result.hints.includes('entries:2'));
});
