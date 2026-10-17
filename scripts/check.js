const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const mini = path.join(root, 'miniprogram');

function walk(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const file = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : [file];
  });
}

function json(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function assert(condition, message) { if (!condition) throw new Error(message); }

const files = walk(mini);
for (const file of files) {
  if (file.endsWith('.js')) new vm.Script(fs.readFileSync(file, 'utf8'), { filename: file });
  if (file.endsWith('.json')) json(file);
}

const project = json(path.join(root, 'project.config.json'));
const app = json(path.join(mini, 'app.json'));
assert(project.compileType === 'miniprogram', 'compileType must be miniprogram');
assert(project.miniprogramRoot === 'miniprogram/', 'miniprogramRoot must be miniprogram/');
assert(Array.isArray(app.pages) && app.pages.length >= 1, 'app.pages is empty');
assert(new Set(app.pages).size === app.pages.length, 'app.pages contains duplicates');
for (const page of app.pages) {
  for (const extension of ['js', 'json', 'wxml', 'wxss']) {
    const file = path.join(mini, `${page}.${extension}`);
    assert(fs.existsSync(file), `Missing page file: ${file}`);
  }
}
const tabs = app.tabBar && app.tabBar.list;
assert(Array.isArray(tabs) && tabs.length >= 2 && tabs.length <= 5, 'tabBar requires 2-5 items');
for (const tab of tabs) {
  assert(app.pages.includes(tab.pagePath), `Tab page not registered: ${tab.pagePath}`);
  assert(typeof tab.text === 'string' && tab.text.length > 0, 'Tab requires text');
}

process.stdout.write(`Static check passed: ${files.filter(file => file.endsWith('.js')).length} JS, ${app.pages.length} pages, ${tabs.length} tabs.\n`);
