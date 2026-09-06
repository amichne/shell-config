import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm, mkdir, stat, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { argumentsFor, artifact, generate, snapshot } from '../ai/main.mjs';
import { parseConfig, parseModels, select, catalog } from '../ai/config.mjs';
import { render, run } from '../ai/core.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const cli = resolve(root, 'ai/main.mjs');
const example = JSON.parse(await readFile(resolve(root, 'config/ai/config.json'), 'utf8'));
const clone = () => structuredClone(example);
const configOf = (raw = clone()) => parseConfig(raw, '/tmp/config.json');
const base = configOf();

async function fixture(t, kind = 'stdin') {
  const dir = await mkdtemp(`${tmpdir()}/ai tests with spaces `);
  t.after(() => rm(dir, { recursive: true, force: true }));
  const log = resolve(dir, 'invocation.json');
  const driver = resolve(dir, 'fake harness.mjs');
  await writeFile(driver, `
import { readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
const args = process.argv.slice(2);
const file = args.indexOf('--prompt-file');
const prompt = file >= 0 ? readFileSync(args[file + 1], 'utf8') : readFileSync(0, 'utf8');
writeFileSync(process.env.LOG, JSON.stringify({ args, input: prompt, cwd: process.cwd(), marker: process.env.MARKER, promptFile: file >= 0 ? args[file + 1] : null }));
if (process.env.CHANGE) { writeFileSync('staged.txt', 'changed during generation\\n'); spawnSync('git', ['add', 'staged.txt']); }
if (process.env.MOVE_HEAD) { spawnSync('git', ['checkout', '-b', 'concurrent']); }
if (process.env.EXIT) { process.stdout.write('untrusted partial output'); process.exit(Number(process.env.EXIT)); }
process.stdout.write(process.env.RESULT || 'answer');
`);
  const raw = clone();
  raw.defaultHarness = 'fake';
  const args = kind === 'stdin' ? [] : kind === 'file' ? ['--prompt-file', '{promptFile}'] : ['--prompt', '{prompt}'];
  raw.harnesses = {
    fake: { command: [process.execPath, driver], prompt: { kind, args }, modelArgs: ['--model', '{model}'], effortArgs: ['--effort={effort}'], models: [{ id: 'model-a', efforts: ['low', 'high'] }], env: { LOG: log, MARKER: 'configured value' } },
    other: { command: [process.execPath, driver], prompt: { kind: 'stdin', args: [] }, models: [], env: { LOG: log } },
  };
  const path = resolve(dir, 'config.json');
  const env = { ...process.env, AI_CONFIG: path, XDG_STATE_HOME: resolve(dir, 'state'), RESULT: 'answer' };
  const save = () => writeFile(path, JSON.stringify(raw));
  await save();
  const invoke = (args, extra = {}) => spawnSync(process.execPath, [cli, ...args], { cwd: dir, env: { ...env, ...extra }, input: '', encoding: 'utf8', timeout: 10_000 });
  return { dir, log, driver, raw, path, env, save, invoke };
}
function git(cwd, ...args) {
  const result = spawnSync('git', args, { cwd, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
}
async function repository(t, unborn = false) {
  const f = await fixture(t);
  git(f.dir, 'init', '-b', 'main');
  git(f.dir, 'config', 'user.name', 'Fixture');
  git(f.dir, 'config', 'user.email', 'fixture@example.invalid');
  git(f.dir, 'config', 'commit.gpgsign', 'false');
  if (!unborn) git(f.dir, 'commit', '--allow-empty', '-m', 'initial');
  await writeFile(resolve(f.dir, 'staged.txt'), 'staged version\n');
  git(f.dir, 'add', 'staged.txt');
  await writeFile(resolve(f.dir, 'staged.txt'), 'unstaged version\n');
  await writeFile(resolve(f.dir, 'untracked.txt'), 'private unstaged data\n');
  return f;
}

test('shipped config parses and provides three native adapters', () => {
  assert.deepEqual([...base.harnesses.keys()], ['codex', 'copilot', 'pi']);
  assert.equal(base.harnesses.get('copilot').prompt.kind, 'argument');
});
test('unknown fields and malformed config fail at the boundary', () => {
  for (const mutate of [
    (r) => r.unknown = true, (r) => r.timeoutMs = 0, (r) => r.defaultHarness = 'missing',
    (r) => r.harnesses.codex.command = 'codex exec', (r) => r.harnesses.codex.prompt.kind = 'shell',
    (r) => r.harnesses.codex.modelArgs = ['--model'], (r) => r.harnesses.codex.env = { X: 3 },
    (r) => r.harnesses.codex.prompt = { kind: 'argument', args: ['--prompt'] },
    (r) => r.harnesses.codex.modelsCommand = ['models'],
  ]) { const raw = clone(); mutate(raw); assert.throws(() => configOf(raw)); }
});
test('catalog rejects duplicate IDs, duplicate efforts, controls, and reserved IDs', () => {
  for (const value of [null, [{ id: 'a' }, { id: 'a' }], [{ id: 'a\n' }], [{ id: 'default' }], [{ id: 'a', efforts: ['high', 'high'] }]]) {
    assert.throws(() => parseModels(value));
  }
});
test('selection is model-dependent and overrides do not carry stale effort', () => {
  const saved = { harness: 'codex', model: 'gpt-5.4', effort: 'high' };
  const models = base.harnesses.get('codex').models;
  assert.deepEqual(select(base, saved, {}, models), saved);
  assert.equal(select(base, saved, { model: 'gpt-5.3-codex' }, models).effort, null);
  assert.deepEqual(select(base, saved, { harness: 'pi' }, base.harnesses.get('pi').models), { harness: 'pi', model: null, effort: null });
  assert.throws(() => select(base, saved, { model: 'default', effort: 'high' }, models));
  assert.throws(() => select(base, saved, { model: 'not-configured' }, models));
});
test('placeholder rendering is one pass, does not interpret shell syntax, and rejects unavailable fields', () => {
  const prompt = '$(touch /tmp/no); "quoted"\n{model}';
  assert.deepEqual(render(['--prompt', '{prompt}'], { prompt }), ['--prompt', prompt]);
  assert.throws(() => render(['{effort}'], { prompt }));
});
test('parser rejects ambiguous input and mutation flags on non-Git actions', () => {
  for (const args of [['ask', 'text', '--file', 'x'], ['ask', '--prompt', 'x', '--edit'], ['ask', '--yes'], ['commit', '--yes', '--dry-run'], ['--config', '--model', 'x'], ['script', '--unknown'], ['nope']]) {
    assert.throws(() => argumentsFor(args));
  }
  assert.equal(argumentsFor(['ask', '--', '-literal']).text, '-literal');
});
test('Git artifacts reject escape codes, ambiguous branch names and fenced commit output', () => {
  for (const value of ['', 'feat/good\nother', '$(touch a)', '-bad', '@{-1}']) assert.throws(() => artifact('branch', value));
  assert.throws(() => artifact('commit', '\x1b[2Jsubject'));
  assert.throws(() => artifact('commit', '```\nsubject\n```'));
  assert.equal(artifact('script', '```sh\n#!/bin/sh\necho ok\n```'), '#!/bin/sh\necho ok');
});
for (const kind of ['stdin', 'argument', 'file']) {
  test(`${kind} transport preserves quotes, newlines, Unicode and shell metacharacters`, async (t) => {
    const f = await fixture(t, kind);
    const prompt = `- literal 'quotes' "double" $() ; $(touch ${f.dir}/INJECTED)\nUnicode λ {model}`;
    const result = f.invoke(['ask', `--prompt=${prompt}`, '--model', 'model-a', '--effort', 'high']);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, 'answer\n');
    const log = JSON.parse(await readFile(f.log, 'utf8'));
    assert.equal(log.marker, 'configured value');
    assert.equal(log.cwd, f.dir);
    assert.ok(log.args.includes('--effort=high'));
    assert.ok((kind === 'argument' ? log.args.at(-1) : log.input).endsWith(prompt));
    assert.equal((await readdir(f.dir)).includes('INJECTED'), false);
    if (log.promptFile) await assert.rejects(stat(log.promptFile), { code: 'ENOENT' });
  });
}
test('headless failure preserves exit status and suppresses partial stdout', async (t) => {
  const f = await fixture(t);
  const result = f.invoke(['ask', 'hello'], { EXIT: '37' });
  assert.equal(result.status, 37);
  assert.equal(result.stdout, '');
});
test('missing executable has an actionable exit status', async (t) => {
  const f = await fixture(t);
  f.raw.harnesses.fake.command = ['/no/such/harness']; await f.save();
  const result = f.invoke(['ask', 'hello']);
  assert.equal(result.status, 127);
  assert.match(result.stderr, /ENOENT/);
});
test('input limits reject before invoking the harness; output limits reject all stdout', async (t) => {
  const f = await fixture(t);
  f.raw.maxInputBytes = 10; await f.save();
  assert.equal(f.invoke(['ask', 'hello']).status, 2);
  await assert.rejects(stat(f.log), { code: 'ENOENT' });
  f.raw.maxInputBytes = 65536; f.raw.maxOutputBytes = 3; await f.save();
  const result = f.invoke(['ask', 'hello']);
  assert.equal(result.status, 2); assert.equal(result.stdout, '');
});
test('script accepts a prompt file, emits source only, and never executes it', async (t) => {
  const f = await fixture(t);
  const file = resolve(f.dir, 'prompt with spaces.txt');
  await writeFile(file, 'Create a small script');
  const script = '#!/bin/sh\ntouch SHOULD_NOT_EXIST';
  const result = f.invoke(['script', '--file', file], { RESULT: script });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, `${script}\n`);
  await assert.rejects(stat(resolve(f.dir, 'SHOULD_NOT_EXIST')), { code: 'ENOENT' });
});
test('stdin composes with positional instructions without shell evaluation', async (t) => {
  const f = await fixture(t);
  const result = spawnSync(process.execPath, [cli, 'ask', 'Explain'], { cwd: f.dir, env: f.env, input: 'input\n$(nope)', encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(JSON.parse(await readFile(f.log, 'utf8')).input, /Explain\n\ninput\n\$\(nope\)$/);
});
test('empty prompt never invokes the harness', async (t) => {
  const f = await fixture(t);
  assert.equal(f.invoke(['ask']).status, 2);
  await assert.rejects(stat(f.log), { code: 'ENOENT' });
});
test('instruction files resolve relative to config, not the working directory', async (t) => {
  const f = await fixture(t);
  await writeFile(resolve(f.dir, 'instructions.txt'), 'SHARED INSTRUCTIONS');
  f.raw.instructions.ask = { file: 'instructions.txt' }; await f.save();
  assert.equal(f.invoke(['ask', 'hello']).status, 0);
  assert.match(JSON.parse(await readFile(f.log, 'utf8')).input, /^SHARED INSTRUCTIONS\n\nhello$/);
});
test('dynamic catalog consumes checked JSON and rejects unsupported effort before generation', async (t) => {
  const f = await fixture(t);
  delete f.raw.harnesses.fake.models;
  f.raw.harnesses.fake.modelsCommand = [process.execPath, '-e', 'console.log(JSON.stringify([{id:"dynamic",efforts:["low"]}]))'];
  await f.save();
  assert.equal(f.invoke(['ask', 'hello', '--model', 'dynamic', '--effort', 'low']).status, 0);
  await rm(f.log);
  assert.equal(f.invoke(['ask', 'hello', '--model', 'dynamic', '--effort', 'high']).status, 2);
  await assert.rejects(stat(f.log), { code: 'ENOENT' });
});
test('selection state is scoped by config path; per-call overrides leave it untouched', async (t) => {
  const f = await fixture(t);
  const stateDir = resolve(f.dir, 'state/ai'); await mkdir(stateDir, { recursive: true });
  const state = JSON.stringify({ version: 1, selections: { [f.path]: { harness: 'fake', model: 'model-a', effort: 'high' } } });
  const path = resolve(stateDir, 'selection.json'); await writeFile(path, state);
  assert.equal(f.invoke(['ask', 'hi']).status, 0);
  assert.ok(JSON.parse(await readFile(f.log, 'utf8')).args.includes('--effort=high'));
  assert.equal(f.invoke(['ask', 'hi', '--harness', 'other']).status, 0);
  assert.deepEqual(JSON.parse(await readFile(f.log, 'utf8')).args, []);
  assert.equal(await readFile(path, 'utf8'), state);
});
test('commit proposes from staged changes, not unstaged or untracked files', async (t) => {
  const f = await repository(t);
  const before = await snapshot(f.dir);
  const result = f.invoke(['commit', '--dry-run'], { RESULT: 'feat: add staged file' });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(await snapshot(f.dir), before);
  const prompt = JSON.parse(await readFile(f.log, 'utf8')).input;
  assert.match(prompt, /staged version/);
  assert.doesNotMatch(prompt, /unstaged version|private unstaged data/);
});
for (const unborn of [false, true]) {
  test(`commit applies exactly the staged fixture (${unborn ? 'unborn' : 'existing'} HEAD)`, async (t) => {
    const f = await repository(t, unborn);
    const result = f.invoke(['commit', '--yes'], { RESULT: 'feat: add staged file' });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(git(f.dir, 'log', '-1', '--format=%s'), 'feat: add staged file');
    assert.equal(git(f.dir, 'show', 'HEAD:staged.txt'), 'staged version');
    assert.equal(await readFile(resolve(f.dir, 'staged.txt'), 'utf8'), 'unstaged version\n');
    assert.equal(git(f.dir, 'ls-files', 'untracked.txt'), '');
  });
}
for (const change of ['CHANGE', 'MOVE_HEAD']) {
  test(`commit rejects a stale proposal after ${change}`, async (t) => {
    const f = await repository(t);
    const head = git(f.dir, 'rev-parse', 'HEAD');
    const result = f.invoke(['commit', '--yes'], { RESULT: 'feat: add staged file', [change]: '1' });
    assert.equal(result.status, 2);
    assert.match(result.stderr, /changed; regenerate/);
    assert.equal(git(f.dir, 'rev-parse', 'HEAD'), head);
  });
}
test('empty staging and noninteractive unapproved commits fail before generation', async (t) => {
  const f = await repository(t);
  assert.equal(f.invoke(['commit']).status, 2);
  await assert.rejects(stat(f.log), { code: 'ENOENT' });
  git(f.dir, 'reset');
  const result = f.invoke(['commit', '--yes']);
  assert.equal(result.status, 2); assert.match(result.stderr, /No staged changes/);
  await assert.rejects(stat(f.log), { code: 'ENOENT' });
});
test('Git hook failure propagates without bypassing native hooks', async (t) => {
  const f = await repository(t);
  const head = git(f.dir, 'rev-parse', 'HEAD');
  await writeFile(resolve(f.dir, '.git/hooks/pre-commit'), '#!/bin/sh\nexit 1\n', { mode: 0o755 });
  const result = f.invoke(['commit', '--yes'], { RESULT: 'feat: add file' });
  assert.equal(result.status, 1);
  assert.equal(git(f.dir, 'rev-parse', 'HEAD'), head);
});
test('branch dry-run is nonmutating; --yes creates and switches using native Git', async (t) => {
  const f = await repository(t);
  const before = await snapshot(f.dir);
  const preview = f.invoke(['branch', 'new feature', '--dry-run'], { RESULT: 'feat/new-feature' });
  assert.equal(preview.status, 0, preview.stderr);
  assert.equal(preview.stdout, 'feat/new-feature\n');
  assert.deepEqual(await snapshot(f.dir), before);
  assert.equal(f.invoke(['branch', 'new feature', '--yes'], { RESULT: 'feat/new-feature' }).status, 0);
  assert.equal(git(f.dir, 'branch', '--show-current'), 'feat/new-feature');
  assert.equal(git(f.dir, 'rev-parse', 'HEAD'), before.head);
});
test('invalid branch output cannot mutate Git', async (t) => {
  const f = await repository(t);
  const before = await snapshot(f.dir);
  for (const RESULT of ['feat/a..b', 'first\nsecond', '-dangerous', 'main']) {
    assert.notEqual(f.invoke(['branch', 'name', '--yes'], { RESULT }).status, 0);
    assert.deepEqual(await snapshot(f.dir), before);
  }
});
test('harness timeout is bounded and discards partial stdout', async () => {
  await assert.rejects(run([process.execPath, '-e', 'console.log("partial"); setTimeout(()=>{}, 10000)'], { timeoutMs: 100, group: true }), { code: 124 });
});
test('timeout terminates the harness process group, including descendants', async (t) => {
  const f = await fixture(t);
  const marker = resolve(f.dir, 'descendant-survived');
  const descendant = `setTimeout(()=>require('fs').writeFileSync(${JSON.stringify(marker)},'bad'),700)`;
  await assert.rejects(run([process.execPath, '-e', `require('child_process').spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{stdio:'inherit'}); setTimeout(()=>{},10000)`], { timeoutMs: 250, group: true }), { code: 124 });
  await new Promise((r) => setTimeout(r, 850));
  await assert.rejects(stat(marker), { code: 'ENOENT' });
});
test('SIGINT cancels the CLI and its harness without emitting a partial result', async (t) => {
  const f = await fixture(t);
  const ready = resolve(f.dir, 'ready');
  f.raw.harnesses.fake.command = [process.execPath, '-e', `require('fs').writeFileSync(${JSON.stringify(ready)},'ready'); console.log('partial'); setTimeout(()=>{},10000)`];
  await f.save();
  const child = spawn(process.execPath, [cli, 'ask', 'hello'], { cwd: f.dir, env: f.env });
  child.stdin.end();
  let stdout = ''; child.stdout.on('data', (data) => stdout += data);
  const closed = new Promise((r) => child.on('close', (code) => r(code)));
  for (let n = 0; n < 100; n++) {
    try { await stat(ready); break; } catch { await new Promise((r) => setTimeout(r, 20)); }
  }
  await stat(ready);
  child.kill('SIGINT');
  assert.equal(await closed, 130);
  assert.equal(stdout, '');
});
test('installed launcher uses an explicit runtime path without modifying startup', async (t) => {
  const f = await fixture(t);
  const result = spawnSync(resolve(root, 'bin/ai'), ['--help'], { env: { ...f.env, AI_RUNTIME_DIR: resolve(root, 'ai') }, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /^Usage:/);
});
