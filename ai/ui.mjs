import { createInterface } from 'node:readline';
import { readFile, writeFile } from 'node:fs/promises';
import { AiError, bounded, requireThat, run } from './core.mjs';

export function terminal() {
  requireThat(process.stdin.isTTY && process.stderr.isTTY, 'Interactive input requires a terminal; supply --prompt/--file, pipe stdin, or use --yes for a reviewed Git action');
}
export async function pick(config, label, options) {
  terminal();
  const rows = options.map((value, i) => `${i}\t${value ?? '(harness default)'}`);
  const chosen = (await run([...config.ui.fzf, '--no-multi', '--delimiter=\t', '--with-nth=2..', `--prompt=${label}> `], {
    input: `${rows.join('\n')}\n`, timeoutMs: 0,
  })).trimEnd();
  requireThat(rows.includes(chosen), 'Selector did not return one configured option');
  return options[rows.indexOf(chosen)];
}
export async function confirm(question) {
  terminal();
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  return new Promise((resolve, reject) => {
    rl.once('SIGINT', () => { reject(new AiError('Cancelled', 130)); rl.close(); });
    rl.once('close', () => resolve(false));
    rl.question(`${question} [y/N] `, (answer) => {
      resolve(answer.trim().toLowerCase() === 'y');
      rl.close();
    });
  });
}
export async function compose(config, directory, edit) {
  terminal();
  if (edit) {
    const file = `${directory}/input.txt`;
    await writeFile(file, '', { mode: 0o600 });
    await run([...config.ui.editor, file], { inherit: true, timeoutMs: 0 });
    return bounded(await readFile(file, 'utf8'), config.maxInputBytes, 'Prompt');
  }
  if (config.ui.inputCommand) return run(config.ui.inputCommand, {
    input: null, timeoutMs: 0, maxBytes: config.maxInputBytes,
  });
  process.stderr.write('Enter your prompt. Enter adds a line; Ctrl-D on an empty line sends; Ctrl-C cancels.\n');
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  const lines = [];
  return new Promise((resolve, reject) => {
    let bytes = 0;
    rl.setPrompt('> ');
    rl.on('line', (line) => {
      bytes += Buffer.byteLength(line) + 1;
      if (bytes > config.maxInputBytes) {
        reject(new AiError(`Prompt exceeds ${config.maxInputBytes} bytes`));
        rl.close();
      } else { lines.push(line); rl.prompt(); }
    });
    rl.once('SIGINT', () => { reject(new AiError('Cancelled', 130)); rl.close(); });
    rl.once('close', () => resolve(lines.join('\n')));
    rl.prompt();
  });
}
export async function stdinText(maxBytes) {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    requireThat(size <= maxBytes, `Input exceeds ${maxBytes} bytes; nothing was truncated`);
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}
