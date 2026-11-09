// Exercise the dashboard's real progress rendering without a browser or live job.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../dashboard/app.js', import.meta.url), 'utf8');
const functionSource = source.slice(source.indexOf('function displayJob('), source.indexOf('\nfunction showTake('));
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {
    hidden: false, value: 0, textContent: '',
    removeAttribute(name) { delete this[name]; },
  });
  return elements.get(id);
};
const context = vm.createContext({$, Date, Math, String,
  bootstrap: {charts: {bibideba: {title: 'BIBBIDIBA'}}},
  setText(element, text) { element.textContent = text; },
  setRenderDisabled() {}, updateExportControls() {}, currentJob: null,
});
vm.runInContext(functionSource, context);
const run = {status: 'running', stage: 'Planning contacts', progress: 12,
  settings: {chart: 'bibideba', start_s: 65, end_s: 75},
  planning_progress: {phase: 'search', completed: 42, total: 100, attempt: 2,
    elapsed_s: 130, updated_at: Date.now()/1000, chart_ms: 4000, states: 256}};
context.displayJob(run);
assert.equal($('planning-status').hidden, false);
assert.equal($('planning-progress').value, 42);
assert.match($('planning-label').textContent, /42\.0% · 42 \/ 100 items/);
assert.match($('planning-label').textContent, /retry/);
assert.match($('planning-detail').textContent, /2:10 elapsed · chart 69\.00 s · 256 alternatives/);
run.planning_progress = {...run.planning_progress, phase: 'geometry', completed: null, total: null};
context.displayJob(run);
assert.equal($('planning-progress').value, undefined); // indeterminate work
assert.match($('planning-label').textContent, /Checking arc input regions/);
run.planning_progress = null;
context.displayJob(run);
assert.equal($('planning-status').hidden, true);
console.log('Dashboard planning progress: counts, elapsed time, retry, indeterminate phase, and stage transition passed.');
