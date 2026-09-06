import { readFile, realpath, mkdir, rename, writeFile, unlink } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { AiError, argv, executable, identifier, object, requireThat, run, text } from './core.mjs';

export const ACTIONS = Object.freeze(['ask', 'commit', 'branch', 'script']);
const selectionKeys = ['harness', 'model', 'effort'];
const integer = (value, label) => {
  requireThat(Number.isSafeInteger(value) && value > 0, `${label}: expected a positive integer`);
  return value;
};
export function parseModels(value) {
  requireThat(Array.isArray(value), 'models: expected an array');
  const ids = new Set();
  return Object.freeze(value.map((entry) => {
    object(entry, ['id', 'efforts'], 'model');
    const id = identifier(entry.id, 'model.id');
    requireThat(!ids.has(id), `Duplicate model ${id}`);
    ids.add(id);
    const efforts = argv(entry.efforts ?? [], 'model.efforts', true).map((effort) => identifier(effort, 'effort'));
    requireThat(new Set(efforts).size === efforts.length, `Duplicate effort for ${id}`);
    return Object.freeze({ id, efforts: Object.freeze(efforts) });
  }));
}
export function parseConfig(raw, path) {
  object(raw, ['version', 'defaultHarness', 'timeoutMs', 'maxInputBytes', 'maxOutputBytes', 'ui', 'instructions', 'harnesses'], 'config');
  requireThat(raw.version === 1, 'config.version must be 1');
  object(raw.harnesses, Object.keys(raw.harnesses ?? {}), 'harnesses');
  const directory = dirname(path);
  const harnesses = new Map(Object.entries(raw.harnesses).map(([name, value]) => {
    identifier(name, 'harness name');
    object(value, ['command', 'prompt', 'modelArgs', 'effortArgs', 'models', 'modelsCommand', 'env'], name);
    object(value.prompt, ['kind', 'args'], `${name}.prompt`);
    requireThat(['stdin', 'argument', 'file'].includes(value.prompt.kind), `${name}: unknown prompt transport`);
    const promptArgs = argv(value.prompt.args ?? [], `${name}.prompt.args`, true);
    const expected = { stdin: null, argument: '{prompt}', file: '{promptFile}' }[value.prompt.kind];
    requireThat(!expected || promptArgs.some((part) => part.includes(expected)), `${name}: prompt.args must contain ${expected}`);
    const environment = value.env ?? {};
    object(environment, Object.keys(environment), `${name}.env`);
    for (const [key, val] of Object.entries(environment)) {
      requireThat(/^[A-Za-z_][A-Za-z0-9_]*$/u.test(key), `${name}: invalid environment key ${key}`);
      text(val, `${name}.env.${key}`, true);
    }
    requireThat(!(value.models && value.modelsCommand), `${name}: choose models or modelsCommand, not both`);
    const modelArgs = argv(value.modelArgs ?? [], `${name}.modelArgs`, true);
    const effortArgs = argv(value.effortArgs ?? [], `${name}.effortArgs`, true);
    requireThat(!modelArgs.length || modelArgs.some((part) => part.includes('{model}')), `${name}: modelArgs must contain {model}`);
    requireThat(!effortArgs.length || effortArgs.some((part) => part.includes('{effort}')), `${name}: effortArgs must contain {effort}`);
    return [name, Object.freeze({
      command: executable(argv(value.command, `${name}.command`), directory),
      prompt: Object.freeze({ kind: value.prompt.kind, args: promptArgs }),
      modelArgs, effortArgs, env: Object.freeze({ ...environment }),
      models: parseModels(value.models ?? []),
      modelsCommand: value.modelsCommand ? executable(argv(value.modelsCommand, `${name}.modelsCommand`), directory) : null,
    })];
  }));
  requireThat(harnesses.has(raw.defaultHarness), 'defaultHarness must name a configured harness');
  const ui = object(raw.ui ?? {}, ['fzf', 'editor', 'inputCommand'], 'ui');
  const instructions = object(raw.instructions, ACTIONS, 'instructions');
  for (const action of ACTIONS) {
    if (typeof instructions[action] === 'string') text(instructions[action], `instructions.${action}`);
    else { object(instructions[action], ['file'], `instructions.${action}`); text(instructions[action].file, 'instruction file'); }
  }
  return Object.freeze({
    path, directory, harnesses, defaultHarness: raw.defaultHarness,
    timeoutMs: integer(raw.timeoutMs ?? 180_000, 'timeoutMs'),
    maxInputBytes: integer(raw.maxInputBytes ?? 65_536, 'maxInputBytes'),
    maxOutputBytes: integer(raw.maxOutputBytes ?? 1_048_576, 'maxOutputBytes'),
    ui: Object.freeze({
      fzf: executable(argv(ui.fzf ?? ['fzf', '--height=40%', '--layout=reverse', '--border'], 'ui.fzf'), directory),
      editor: executable(argv(ui.editor ?? [process.env.VISUAL || process.env.EDITOR || 'vim'], 'ui.editor'), directory),
      inputCommand: ui.inputCommand ? executable(argv(ui.inputCommand, 'ui.inputCommand'), directory) : null,
    }),
    instructions: Object.freeze({ ...instructions }),
  });
}
export async function loadConfig(file) {
  const path = await realpath(file ?? process.env.AI_CONFIG ?? resolve(process.env.XDG_CONFIG_HOME ?? resolve(homedir(), '.config'), 'ai/config.json'));
  return parseConfig(JSON.parse(await readFile(path, 'utf8')), path);
}
export async function catalog(config, harness) {
  const models = harness.modelsCommand ? parseModels(JSON.parse(await run(harness.modelsCommand, {
    cwd: config.directory, env: { ...process.env, ...harness.env },
    timeoutMs: Math.min(config.timeoutMs, 10_000), maxBytes: config.maxOutputBytes, group: true,
  }))) : harness.models;
  requireThat(!models.length || harness.modelArgs.length, 'Catalog requires modelArgs');
  requireThat(!models.some((model) => model.efforts.length) || harness.effortArgs.length, 'Catalog efforts require effortArgs');
  return models;
}
export function select(config, saved, flags, models) {
  const harness = flags.harness ?? saved?.harness ?? config.defaultHarness;
  requireThat(config.harnesses.has(harness), `Unknown harness ${harness}; run ai --config`);
  const explicit = (value) => value === 'default' ? null : value;
  const model = flags.model !== undefined ? explicit(flags.model) : harness === saved?.harness ? saved.model : null;
  const effort = flags.effort !== undefined ? explicit(flags.effort) : harness === saved?.harness && model === saved?.model ? saved.effort : null;
  const entry = model === null ? null : models.find((candidate) => candidate.id === model);
  requireThat(model === null || entry, `Unknown model ${model}; update the catalog or run ai --config`);
  requireThat(effort === null || entry?.efforts.includes(effort), `Effort ${effort} is not configured for ${model ?? 'the harness default model'}`);
  return Object.freeze({ harness, model, effort });
}
export function statePath() {
  return resolve(process.env.XDG_STATE_HOME ?? resolve(homedir(), '.local/state'), 'ai/selection.json');
}
async function readState() {
  try {
    const raw = object(JSON.parse(await readFile(statePath(), 'utf8')), ['version', 'selections'], 'state');
    requireThat(raw.version === 1, 'Unknown state version');
    object(raw.selections, Object.keys(raw.selections ?? {}), 'state.selections');
    return raw;
  } catch (error) {
    if (error.code === 'ENOENT') return { version: 1, selections: {} };
    throw error;
  }
}
export async function savedSelection(config) {
  const entry = (await readState()).selections[config.path];
  if (entry === undefined) return null;
  object(entry, selectionKeys, 'saved selection');
  identifier(entry.harness, 'saved harness');
  for (const key of ['model', 'effort']) if (entry[key] !== null) identifier(entry[key], `saved ${key}`);
  return entry;
}
export async function saveSelection(config, selection) {
  const state = await readState();
  state.selections[config.path] = selection;
  const path = statePath();
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600, flag: 'wx' });
    await rename(temporary, path);
  } finally { await unlink(temporary).catch((error) => { if (error.code !== 'ENOENT') throw error; }); }
}
export async function instruction(config, action) {
  const value = config.instructions[action];
  const result = typeof value === 'string' ? value : await readFile(resolve(config.directory, value.file), 'utf8');
  requireThat(result.trim(), `Empty instructions for ${action}`);
  return result;
}
