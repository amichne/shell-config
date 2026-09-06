#!/usr/bin/env python3
"""PTY acceptance for the real composer/confirmation and a fixture selector protocol.

No AI credentials or real harnesses are used. Pass --fzf /path/to/fzf to also
exercise the installed fzf UI; otherwise selection uses a deterministic fixture.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def session(args, env, cwd, interactions=()):
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execve(NODE, [NODE, str(ROOT / "ai/main.mjs"), *args], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    output = b""
    pending = list(interactions)
    deadline = time.monotonic() + 8
    status = None
    try:
        while time.monotonic() < deadline:
            if pending and pending[0][0] in output:
                _, keys = pending.pop(0)
                output = b""  # wait for fresh output before the next interaction
                os.write(fd, keys)
            if select.select([fd], [], [], 0.02)[0]:
                try:
                    part = os.read(fd, 65536)
                    output += part
                    # fzf's height mode asks the terminal for its cursor position.
                    if b"\x1b[6n" in part:
                        os.write(fd, b"\x1b[1;1R")
                except OSError:
                    pass
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                assert not pending, ("missing terminal interaction", pending, output)
                return os.waitstatus_to_exitcode(status), output.decode(errors="replace")
        raise AssertionError(("PTY timeout", args, output))
    finally:
        os.close(fd)
        if status is None or not done:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fzf", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ai PTY with spaces ") as temp:
        directory = Path(temp)
        env = {**os.environ, "TERM": "xterm-256color", "AI_CONFIG": str(directory / "config.json"),
               "XDG_STATE_HOME": str(directory / "state"), "LOG": str(directory / "prompt.txt"),
               "STEP": str(directory / "step"), "PLAN": "0,1,2"}
        for name in ("FZF_DEFAULT_OPTS", "FZF_DEFAULT_OPTS_FILE"):
            env.pop(name, None)
        harness = directory / "harness.mjs"
        harness.write_text("import{readFileSync,writeFileSync}from'node:fs';writeFileSync(process.env.LOG,readFileSync(0));console.log('fixture answer');")
        selector = directory / "selector.mjs"
        selector.write_text("""import{readFileSync,writeFileSync,existsSync}from'node:fs';
const step=existsSync(process.env.STEP)?Number(readFileSync(process.env.STEP)):0;
const chosen=Number(process.env.PLAN.split(',')[step]);
writeFileSync(process.env.STEP,String(step+1));
if(chosen<0)process.exit(130);
console.log(readFileSync(0,'utf8').trimEnd().split('\\n')[chosen]);""")
        config = {"version": 1, "defaultHarness": "fixture", "instructions": dict.fromkeys(["ask", "commit", "branch", "script"], "INSTRUCTIONS"),
                  "ui": {"fzf": [NODE, str(selector)]}, "harnesses": {
                      "fixture": {"command": [NODE, str(harness)], "prompt": {"kind": "stdin"},
                                  "modelArgs": ["--model", "{model}"], "effortArgs": ["--effort", "{effort}"],
                                  "models": [{"id": "fixture-model", "efforts": ["low", "high"]}]}}}
        config_path = directory / "config.json"
        config_path.write_text(json.dumps(config))
        original = config_path.read_bytes()
        code, output = session(["--config"], env, directory)
        assert code == 0, output
        state_path = directory / "state/ai/selection.json"
        state = state_path.read_bytes()
        chosen = json.loads(state)["selections"][str(config_path.resolve())]
        assert chosen == {"harness": "fixture", "model": "fixture-model", "effort": "high"}, chosen
        assert state_path.stat().st_mode & 0o777 == 0o600
        assert config_path.read_bytes() == original
        for plan in ("0,-1", "0,1,-1"):
            (directory / "step").unlink()
            code, output = session(["--config"], {**env, "PLAN": plan}, directory)
            assert code == 130, output
            assert state_path.read_bytes() == state
        print("PASS selector protocol: persisted selection, cancellation, private state, unchanged config")
        code, output = session(["ask"], env, directory, [(b"> ", b"first line\rsecond line\r\x04")])
        assert code == 0, output
        assert (directory / "prompt.txt").read_text().endswith("first line\nsecond line")
        (directory / "prompt.txt").unlink()
        code, output = session(["ask"], env, directory, [(b"> ", b"\x03")])
        assert code == 130, output
        assert not (directory / "prompt.txt").exists()
        print("PASS real multiline composer: Ctrl-D submits, Ctrl-C cancels before harness invocation")
        subprocess.run(["git", "init", "-b", "main", str(directory)], check=True, capture_output=True)
        (directory / "staged.txt").write_text("staged\n")
        subprocess.run(["git", "-C", str(directory), "add", "staged.txt"], check=True)
        code, output = session(["commit"], env, directory, [(b"[y/N] ", b"n\r")])
        assert code == 130, output
        assert subprocess.run(["git", "-C", str(directory), "rev-parse", "--verify", "HEAD"], capture_output=True).returncode != 0
        print("PASS real Git confirmation: decline leaves unborn HEAD unchanged")
        if args.fzf:
            config["ui"]["fzf"] = [str(args.fzf.resolve()), "--height=40%", "--layout=reverse"]
            config_path.write_text(json.dumps(config))
            code, output = session(["--config"], env, directory, [
                (b"Harness", b"fixture\r"), (b"Model", b"fixture-model\r"), (b"Effort", b"high\r")])
            assert code == 0, output
            assert json.loads(state_path.read_text())["selections"][str(config_path.resolve())] == chosen
            print("PASS installed fzf: harness/model/effort selection")
        else:
            print("NOT RUN: real fzf rendering (supply --fzf); the selector above is a fixture")


if __name__ == "__main__":
    main()
