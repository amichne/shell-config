import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp, writeFile, readFile, rm, mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {performance} from 'node:perf_hooks';
import {readRepository} from './context.mjs';
import {
  defaults,
  encodeSettings,
  generateTOML,
  gitANSI,
  gitParts,
  gitText,
  parseStatus,
  parseUntracked,
  validateSettings,
} from './model.mjs';

const repository = {
  kind: 'repository',
  branch: {kind: 'named', name: 'feature/prompt-editor', oid: 'a'.repeat(40)},
  tracking: {kind: 'tracked', name: 'origin/feature/prompt-editor', ahead: 2, behind: 1},
  counts: {staged: 1, modified: 3, conflicted: 0, stashed: 1},
  untracked: {kind: 'known', count: 0},
  worktree: {kind: 'linked', name: 'shell-config.prompt', total: 3, locked: false, prunable: false},
  pr: {kind: 'open', number: 42, draft: false, base: 'main', stale: false},
};

const exec = promisify(execFile);
const runtimeEnv = {...process.env};
for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR']) delete runtimeEnv[key];
async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'prompt-git-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const repo = join(root, 'repo');
  await mkdir(repo);
  const git = (...args) => exec('/usr/bin/git', args, {cwd: repo, env: runtimeEnv});
  await git('init', '-b', 'fixture-main');
  await writeFile(join(repo, 'tracked'), 'initial\n');
  await git('add', 'tracked');
  await git('-c', 'user.name=Prompt Fixture', '-c', 'user.email=prompt@example.invalid', 'commit', '-m', 'fixture');
  // This readiness check is outside the prompt's measured execution budget.
  await git('rev-parse', '--show-toplevel');
  return {root, repo, git};
}
async function contextProcess(repo, env) {
  const code = `import {readRepository} from ${JSON.stringify(new URL('./context.mjs', import.meta.url).href)}; console.log(JSON.stringify(await readRepository(process.argv[1])));`;
  const result = await exec(process.execPath, ['--input-type=module', '-e', code, repo], {cwd: repo, env: {...runtimeEnv, ...env}, timeout: 4000});
  return JSON.parse(result.stdout);
}

test('Git context preserves remote, divergence, worktree, and pull request meaning', () => {
  assert.deepEqual(gitParts(defaults, repository).map(part => part.text), [
    'feature/prompt-editor',
    '→ origin/feature/prompt-editor',
    '2 ahead / 1 behind',
    '1 staged 3 modified 1 stashed',
    'wt:shell-config.prompt / 3 total',
    'PR #42 → main',
  ]);
});

test('terminal Git output carries the same semantic fields with bounded color escapes', () => {
  const output = gitANSI(defaults, repository);
  assert.match(output, /\u001b\[38;2;189;147;249;1mfeature\/prompt-editor\u001b\[0m/);
  assert.match(output, /PR #42 → main/);
  assert.equal(output.replaceAll(/\u001b\[[\d;]+m/g, ''), gitParts(defaults, repository).map(part => part.text).join(' · '));
});

test('porcelain parsing fails closed on incomplete data', () => {
  assert.deepEqual(parseStatus('# branch.head main\0'), {kind: 'unavailable', reason: 'missing-head'});
  assert.deepEqual(parseStatus('# branch.head main'), {kind: 'unavailable', reason: 'incomplete-status'});
});

test('generated Starship config is two-line, restrained, and excludes rejected modules', () => {
  const toml = generateTOML(defaults);
  assert.match(toml, /\$directory\$\{custom\.git_context\}.*\$line_break.*\$character/);
  assert.match(toml, /shell-prompt context --ansi --settings/);
  for (const rejected of ['hostname', 'memory_usage', 'battery', 'username']) {
    assert.equal(toml.includes(rejected), false, rejected);
  }
});

test('settings token round-trips Unicode and unknown settings are rejected', () => {
  const settings = structuredClone(defaults);
  settings.marker = '❯';
  const decoded = JSON.parse(Buffer.from(encodeSettings(settings), 'base64url').toString('utf8'));
  assert.deepEqual(decoded, settings);
  assert.equal(validateSettings({...settings, surprise: true}).kind, 'invalid');
});

test('unknown untracked evidence never renders a false clean worktree', () => {
  const g = {...repository, counts: {staged: 0, modified: 0, conflicted: 0, stashed: 0}, untracked: {kind: 'unavailable', reason: 'git-timeout'}};
  assert.match(gitText(defaults, g), /untracked unknown/);
  assert.doesNotMatch(gitText(defaults, g), /\bclean\b/);
  assert.match(gitText({...defaults, labels: 'compact'}, g), /\?unknown/);
  assert.match(gitText(defaults, {...g, counts: {...g.counts, staged: 1}}), /1 staged untracked unknown/);
});

test('parsing retains whether untracked files were actually requested', () => {
  const raw = '# branch.head main\0# branch.oid ' + 'a'.repeat(40) + '\0';
  const excluded = parseStatus(raw, 'none');
  assert.deepEqual(excluded.untracked, {kind: 'unavailable', reason: 'not-requested'});
  assert.match(gitText(defaults, excluded), /untracked unknown/);
  assert.doesNotMatch(gitText(defaults, excluded), /\bclean\b/);
  assert.deepEqual(parseStatus(raw).untracked, {kind: 'known', count: 0});
  assert.match(gitText(defaults, parseStatus(raw)), /\bclean\b/);
  assert.deepEqual(parseStatus(raw + '? file\0', 'none'), {kind: 'unavailable', reason: 'unexpected-untracked-entry'});
  assert.deepEqual(parseUntracked('file'), {kind: 'unavailable', reason: 'incomplete-untracked'});
  assert.deepEqual(parseUntracked('file\0\0'), {kind: 'unavailable', reason: 'incomplete-untracked'});
  assert.deepEqual(parseUntracked('line\nfile\0'), {kind: 'known', count: 1});
});

test('real repository reports proven tracked and untracked counts', async t => {
  const {repo, git} = await fixture(t);
  await writeFile(join(repo, 'tracked'), 'changed\n');
  await writeFile(join(repo, 'new-staged'), 'staged\n');
  await git('add', 'new-staged');
  await writeFile(join(repo, 'new-untracked'), 'untracked\n');
  const g = await readRepository(repo);
  assert.equal(g.kind, 'repository');
  assert.equal(g.branch.name, 'fixture-main');
  assert.deepEqual(g.counts, {staged: 1, modified: 1, conflicted: 0, stashed: 0});
  assert.deepEqual(g.untracked, {kind: 'known', count: 1});
  assert.match(gitText(defaults, g), /1 staged 1 modified 1 untracked/);
  assert.equal(g.evidence.every(e => Number.isFinite(e.elapsedMs) && e.elapsedMs >= 0), true);
  assert.equal(g.evidence.some(e => e.stage === 'untracked' && e.outcome === 'COMPLETE'), true);
});

test('slow untracked scan preserves tracked facts and stops its helper processes', async t => {
  const {root, repo, git} = await fixture(t);
  await writeFile(join(repo, 'tracked'), 'changed\n');
  await writeFile(join(repo, 'new-staged'), 'staged\n');
  await git('add', 'new-staged');
  const wrapper = join(root, 'git');
  const pidFile = join(root, 'helper.pid');
  await writeFile(wrapper, '#!/bin/sh\ndelay=0\n[ "$1" = ls-files ] && delay=1\nfor arg do [ "$arg" = --untracked-files=all ] && delay=1; done\nif [ "$delay" = 1 ]; then\n  /bin/sleep 30 &\n  printf "%s" "$!" > "$PROMPT_HELPER_PID"\n  wait\nelse\n  exec /usr/bin/git "$@"\nfi\n', {mode: 0o755});
  const started = performance.now();
  const g = await contextProcess(repo, {PATH: `${root}:/usr/bin:/bin`, PROMPT_HELPER_PID: pidFile});
  const elapsed = performance.now() - started;
  const pid = Number(await readFile(pidFile, 'utf8'));
  t.after(() => {try {process.kill(pid, 'SIGKILL');} catch {}});
  assert.equal(g.kind, 'repository');
  assert.equal(g.branch.name, 'fixture-main');
  assert.deepEqual(g.counts, {staged: 1, modified: 1, conflicted: 0, stashed: 0});
  assert.deepEqual(g.untracked, {kind: 'unavailable', reason: 'git-timeout'});
  assert.match(gitText(defaults, g), /1 staged 1 modified untracked unknown/);
  assert.equal(g.evidence.some(e => e.stage === 'untracked' && e.outcome === 'TIMED_OUT'), true);
  assert.ok(elapsed < 1200, `prompt context exceeded Starship's existing 1200ms budget: ${elapsed}ms`);
  assert.throws(() => process.kill(pid, 0), {code: 'ESRCH'});
});

test('tracked timeout fails closed and reports finite bounded evidence', async t => {
  const {root, repo} = await fixture(t);
  const wrapper = join(root, 'git');
  const pidFile = join(root, 'helper.pid');
  await writeFile(wrapper, '#!/bin/sh\nif [ "$1" = status ]; then\n  /bin/sleep 30 &\n  printf "%s" "$!" > "$PROMPT_HELPER_PID"\n  wait\nelse\n  exec /usr/bin/git "$@"\nfi\n', {mode: 0o755});
  const g = await contextProcess(repo, {PATH: `${root}:/usr/bin:/bin`, PROMPT_HELPER_PID: pidFile});
  const pid = Number(await readFile(pidFile, 'utf8'));
  t.after(() => {try {process.kill(pid, 'SIGKILL');} catch {}});
  assert.equal(g.kind, 'unavailable');
  assert.equal(g.reason, 'git-timeout');
  assert.equal(g.evidence.some(e => e.stage === 'status' && e.outcome === 'TIMED_OUT'), true);
  assert.equal(g.evidence.every(e => Object.keys(e).sort().join(',') === 'elapsedMs,outcome,stage'), true);
  assert.throws(() => process.kill(pid, 0), {code: 'ESRCH'});
});
