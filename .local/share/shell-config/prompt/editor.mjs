import {defaults, scenarios, gitParts, generateTOML, display, validateSettings} from './model.mjs';

const $ = selector => document.querySelector(selector);
const controls = $('#controls');
const fragmentToken = location.hash.slice(1);
if (fragmentToken) sessionStorage.setItem('prompt-editor-token', fragmentToken);
const token = fragmentToken || sessionStorage.getItem('prompt-editor-token') || '';
history.replaceState(null, '', location.pathname);
let settings = structuredClone(defaults);
let currentGit = scenarios.working.git;
let scenarioKey = 'working';
let canUndo = false;
let toastTimer;

const colorLabels = {
  directory: 'Directory', git: 'Git', muted: 'Muted', success: 'Success', error: 'Error', background: 'Surface'
};

for (const [key, label] of Object.entries(colorLabels)) {
  const node = document.createElement('label');
  node.className = 'swatch';
  node.innerHTML = `<span>${label}</span><input type="color" name="color-${key}" aria-label="${label} color">`;
  $('#swatches').append(node);
}
for (const [key, scenario] of Object.entries(scenarios)) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'scenario';
  button.dataset.scenario = key;
  button.textContent = scenario.name;
  button.addEventListener('click', () => selectScenario(key));
  $('#scenarios').append(button);
}

function showToast(message, error = false) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.style.borderColor = error ? 'var(--red)' : 'var(--line)';
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 3200);
}

function syncForm() {
  for (const [key, value] of Object.entries(settings)) {
    if (key === 'colors' || key === 'version') continue;
    const input = controls.elements[key];
    if (!input) continue;
    if (input instanceof RadioNodeList) {
      input.value = String(value);
    } else if (input.type === 'checkbox') {
      input.checked = value;
    } else input.value = value;
  }
  for (const [key, value] of Object.entries(settings.colors)) controls.elements[`color-${key}`].value = value;
}

function readForm() {
  const next = structuredClone(settings);
  for (const key of ['blankLine','duration','exitStatus','branch','remote','sync','changes','clean','worktree','pr']) next[key] = controls.elements[key].checked;
  for (const key of ['lines','pathDepth','durationMs']) next[key] = Number(controls.elements[key].value);
  for (const key of ['frame','marker','labels']) next[key] = controls.elements[key].value;
  for (const key of Object.keys(next.colors)) next.colors[key] = controls.elements[`color-${key}`].value;
  const valid = validateSettings(next);
  if (valid.kind === 'valid') settings = valid.value;
}

function span(className, text) {
  const el = document.createElement('span');
  el.className = className;
  el.textContent = text;
  return el;
}

function renderPrompt() {
  document.documentElement.style.setProperty('--directory', settings.colors.directory);
  document.documentElement.style.setProperty('--git', settings.colors.git);
  document.documentElement.style.setProperty('--muted-color', settings.colors.muted);
  document.documentElement.style.setProperty('--success', settings.colors.success);
  document.documentElement.style.setProperty('--error', settings.colors.error);
  $('#terminal').style.setProperty('--terminal-bg', settings.colors.background);
  $('#depth-value').textContent = settings.pathDepth;
  $('#duration-value').textContent = settings.durationMs < 1000 ? `${settings.durationMs}ms` : `${settings.durationMs / 1000}s`;

  const source = scenarioKey === 'actual' ? {directory: currentGit.root ? display(currentGit.root.replace(/^\/Users\/[^/]+/, '~')) : '~/code/shell-config', command: 'git status', duration: 600, exit: 0, git: currentGit} : scenarios[scenarioKey];
  const prompt = $('#prompt');
  prompt.replaceChildren();
  const context = document.createElement('div');
  context.className = 'prompt-line';
  if (settings.frame === 'subtle') context.append(span('frame', '╭─ '));
  context.append(span('directory', source.directory), document.createTextNode('  '));
  for (const part of gitParts(settings, source.git)) {
    context.append(span(`git ${part.type}`, part.text), document.createTextNode('  '));
  }
  if (settings.duration && source.duration >= settings.durationMs) context.append(span('duration', `took ${(source.duration / 1000).toFixed(source.duration % 1000 ? 1 : 0)}s`), document.createTextNode('  '));
  if (settings.exitStatus && source.exit !== 0) context.append(span('exit', `exit:${source.exit}`));

  const input = document.createElement('div');
  input.className = 'prompt-line';
  if (settings.lines === 2) {
    if (settings.frame === 'subtle') input.append(span('frame', '╰─ '));
    input.append(span(source.exit ? 'error-marker' : 'success-marker', settings.marker), document.createTextNode(' '), span('command', source.command), span('cursor', ''));
    prompt.append(context, input);
  } else {
    context.append(span(source.exit ? 'error-marker' : 'success-marker', settings.marker), document.createTextNode(' '), span('command', source.command), span('cursor', ''));
    prompt.append(context);
  }
  $('#scenario-name').textContent = scenarioKey === 'actual' ? 'This repository' : source.name;
  renderFacts(source.git);
}

function renderFacts(git) {
  const outside = git.kind === 'outside';
  const unavailable = git.kind !== 'repository';
  $('#fact-branch').textContent = outside ? 'Outside a repository' : unavailable ? 'Unavailable' : git.branch.kind === 'named' ? git.branch.name : `detached ${git.branch.oid.slice(0,7)}`;
  $('#fact-remote').textContent = unavailable ? '—' : git.tracking.kind === 'tracked' ? `${git.tracking.name} · ${git.tracking.ahead} ahead, ${git.tracking.behind} behind` : git.tracking.kind === 'untracked' ? 'No upstream' : `${git.tracking.name} · counts unavailable`;
  const w = unavailable ? null : git.worktree;
  $('#fact-worktree').textContent = !w || w.kind === 'unknown' ? 'Unknown' : `${w.kind === 'main' ? 'Main' : w.name} · ${w.total} total`;
  const p = unavailable ? null : git.pr;
  $('#fact-pr').textContent = !p ? '—' : p.kind === 'open' ? `#${p.number} → ${p.base}${p.draft ? ' · draft' : ''}${p.stale ? ' · stale' : ''}` : p.kind === 'none' ? `No open PR${p.stale ? ' · stale' : ''}` : p.kind === 'unavailable' ? 'Unavailable' : 'Not checked';
}

function selectScenario(key) {
  scenarioKey = key;
  for (const button of document.querySelectorAll('.scenario')) button.setAttribute('aria-pressed', String(button.dataset.scenario === key));
  renderPrompt();
}

async function api(path, payload = {}) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json','X-Editor-Token':token}, body:JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}

controls.addEventListener('input', () => { readForm(); renderPrompt(); });
$('#apply').addEventListener('click', async () => {
  const button = $('#apply'); button.disabled = true; button.textContent = 'Validating…';
  try {
    await api('/api/apply', settings); canUndo = true; $('#undo').disabled = false;
    $('#status').textContent = 'Applied — press Enter in your terminal'; showToast('Prompt applied. Press Enter in any active shell.');
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = 'Apply to shell'; }
});
$('#undo').addEventListener('click', async () => {
  try { await api('/api/undo'); canUndo = false; $('#undo').disabled = true; $('#status').textContent = 'Last apply restored'; showToast('Last applied prompt restored.'); }
  catch (error) { showToast(error.message, true); }
});
$('#refresh-pr').addEventListener('click', async () => {
  const button = $('#refresh-pr'); button.disabled = true; button.textContent = 'Checking…';
  try { const result = await api('/api/refresh-pr'); currentGit = result.git; selectScenario('actual'); showToast(result.result.kind === 'open' ? `Found PR #${result.result.number}.` : result.result.kind === 'none' ? 'No open PR for this branch.' : 'PR lookup unavailable.', result.result.kind === 'unavailable'); }
  catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = 'Refresh PR'; }
});
$('#download').addEventListener('click', () => {
  const blob = new Blob([generateTOML(settings)], {type:'text/plain'});
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'starship.toml'; link.click(); URL.revokeObjectURL(link.href);
  showToast('Downloaded starship.toml.');
});

try {
  const response = await fetch('/api/state');
  const state = await response.json();
  const valid = validateSettings(state.settings);
  if (valid.kind === 'valid') settings = valid.value;
  currentGit = state.git;
  canUndo = state.canUndo;
  $('#undo').disabled = !canUndo;
  const actual = document.createElement('button');
  actual.type = 'button'; actual.className = 'scenario'; actual.dataset.scenario = 'actual'; actual.textContent = 'This repository'; actual.addEventListener('click', () => selectScenario('actual'));
  $('#scenarios').prepend(actual);
  syncForm(); selectScenario('actual');
} catch (error) {
  syncForm(); selectScenario('working'); showToast('Could not read local state; using examples.', true);
}
