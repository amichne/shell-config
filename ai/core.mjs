import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { isAbsolute, resolve } from 'node:path';

export class AiError extends Error {
  constructor(message, code = 2) { super(message); this.code = code; }
}
export function requireThat(condition, message) {
  if (!condition) throw new AiError(message);
}
export function object(value, keys, label) {
  requireThat(value !== null && typeof value === 'object' && !Array.isArray(value), `${label}: expected an object`);
  for (const key of Object.keys(value)) requireThat(keys.includes(key), `${label}: unknown field ${key}`);
  return value;
}
export function text(value, label, empty = false) {
  requireThat(typeof value === 'string' && (empty || value.length > 0) && !value.includes('\0'), `${label}: expected ${empty ? 'a' : 'a nonempty'} string without NUL`);
  return value;
}
export function argv(value, label, empty = false) {
  requireThat(Array.isArray(value) && (empty || value.length > 0), `${label}: expected an argv array`);
  const result = value.map((part) => text(part, label, true));
  if (!empty) requireThat(result[0].length > 0, `${label}: empty executable`);
  return Object.freeze(result);
}
export function identifier(value, label) {
  text(value, label);
  requireThat(!/[\s\x00-\x1f\x7f]/u.test(value) && value !== 'default', `${label}: invalid identifier (default is reserved)`);
  return value;
}
export function bounded(value, max, label) {
  requireThat(Buffer.byteLength(value) <= max, `${label} exceeds ${max} bytes; nothing was truncated`);
  return value;
}
export function executable(command, directory) {
  const [file, ...args] = command;
  const expanded = file.startsWith('~/') ? resolve(homedir(), file.slice(2)) : file;
  return [expanded.includes('/') && !isAbsolute(expanded) ? resolve(directory, expanded) : expanded, ...args];
}
export function render(args, values) {
  return args.map((arg) => arg.replace(/\{([a-zA-Z]+)\}/gu, (_, key) => {
    requireThat(Object.hasOwn(values, key), `Unknown or unavailable placeholder {${key}}`);
    return values[key];
  }));
}

// Only this boundary starts subprocesses. Prompts are data, never shell source.
export function run(command, {
  input = '', cwd = process.cwd(), env = process.env, timeoutMs = 30_000,
  maxBytes = 1_048_576, ok = [0], group = false, inherit = false,
} = {}) {
  return new Promise((resolveResult, reject) => {
    const child = spawn(command[0], command.slice(1), {
      cwd, env, shell: false, detached: group,
      stdio: inherit ? 'inherit' : [input === null ? 'inherit' : 'pipe', 'pipe', 'inherit'],
    });
    const chunks = [];
    let size = 0;
    let failure;
    let escalation;
    const signal = (name) => {
      try { group ? process.kill(-child.pid, name) : child.kill(name); }
      catch (error) { if (error.code !== 'ESRCH') failure ??= error; }
    };
    const stop = (error) => {
      if (failure) return;
      failure = error;
      signal('SIGTERM');
      escalation = setTimeout(() => signal('SIGKILL'), 200);
    };
    const interrupted = () => stop(new AiError('Cancelled', 130));
    const terminated = () => stop(new AiError('Terminated', 143));
    process.once('SIGINT', interrupted);
    process.once('SIGTERM', terminated);
    const timer = timeoutMs ? setTimeout(() => stop(new AiError(`${command[0]} timed out`, 124)), timeoutMs) : undefined;
    child.once('error', (error) => {
      failure ??= new AiError(`${command[0]}: ${error.message}`, error.code === 'ENOENT' ? 127 : 2);
    });
    child.stdout?.on('data', (data) => {
      size += data.length;
      if (size > maxBytes) stop(new AiError(`${command[0]} output exceeds ${maxBytes} bytes`));
      else chunks.push(data);
    });
    child.stdin?.on('error', (error) => {
      // A failing harness can close stdin before reading it; preserve its exit status.
      if (error.code !== 'EPIPE') stop(error);
    });
    if (child.stdin) child.stdin.end(input);
    child.once('close', (code, childSignal) => {
      clearTimeout(timer);
      clearTimeout(escalation);
      if (failure && group) signal('SIGKILL'); // also reap descendants after the leader exits
      process.removeListener('SIGINT', interrupted);
      process.removeListener('SIGTERM', terminated);
      if (failure) return reject(failure);
      if (!ok.includes(code)) return reject(new AiError(`${command[0]} exited ${code ?? childSignal}`, code ?? 130));
      resolveResult(Buffer.concat(chunks).toString('utf8'));
    });
  });
}
