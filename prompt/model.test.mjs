import test from 'node:test';
import assert from 'node:assert/strict';
import {
  defaults,
  encodeSettings,
  generateTOML,
  gitANSI,
  gitParts,
  parseStatus,
  validateSettings,
} from './model.mjs';

const repository = {
  kind: 'repository',
  branch: {kind: 'named', name: 'feature/prompt-editor', oid: 'a'.repeat(40)},
  tracking: {kind: 'tracked', name: 'origin/feature/prompt-editor', ahead: 2, behind: 1},
  counts: {staged: 1, modified: 3, untracked: 0, conflicted: 0, stashed: 1},
  worktree: {kind: 'linked', name: 'shell-config.prompt', total: 3, locked: false, prunable: false},
  pr: {kind: 'open', number: 42, draft: false, base: 'main', stale: false},
};

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
