#!/usr/bin/env python3
"""Prove work profile completion through the existing native Zsh/ZLE probe."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("work_completion_probe", ROOT / "terminal/configure.py")
configure = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = configure
spec.loader.exec_module(configure)


class WorkCompletionTest(unittest.TestCase):
    def setUp(self):
        self.handler = ROOT / "config/zsh/completions/_work"
        if not self.handler.is_file():
            self.handler = Path.home() / ".config/zsh/completions/_work"
        self.assertTrue(self.handler.is_file(), "The work completion handler is absent")
        temporary = tempfile.TemporaryDirectory(prefix="work completion tests with spaces ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tools = self.root / "tools"
        tools.mkdir()
        self.log = self.root / "metadata requests.jsonl"
        self.forbidden = self.root / "auth or search executed"
        self.custom = self.root / "custom config with spaces.json"
        self.custom.write_text("{}\n")
        work = tools / "work"
        work.write_text("#!" + sys.executable + "\n" + f'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
selected = os.environ.get("WORK_CONFIG")
remaining = list(args)
if remaining[:1] == ["--config"] and len(remaining) >= 2:
    selected = remaining[1]
    remaining = remaining[2:]
elif remaining and remaining[0].startswith("--config="):
    selected = remaining.pop(0).split("=", 1)[1]
with Path({str(self.log)!r}).open("a") as stream:
    stream.write(json.dumps({{"args": args, "selected": selected}}) + "\\n")
if remaining != ["profiles", "--names"]:
    raise SystemExit(90)
if selected == "metadata-fails":
    raise SystemExit(7)
print("inbox")
if selected == {str(self.custom)!r}:
    print("custom_queue")
else:
    print("review_queue")
    print("team-a")
''')
        work.chmod(0o755)
        for command in ("gh", "acli"):
            tool = tools / command
            tool.write_text("#!" + sys.executable + "\nfrom pathlib import Path\n"
                            + f"Path({str(self.forbidden)!r}).write_text('forbidden')\nraise SystemExit(90)\n")
            tool.chmod(0o755)
        self.path = str(tools) + os.pathsep + os.environ["PATH"]

    def candidates(self, args, prefix, *, work_config=None):
        # The production probe intentionally builds a clean environment. Seed
        # this one fixture variable in its existing script to test inheritance.
        script = configure.COMPLETION_PROBE_SCRIPT
        if work_config is not None:
            script = "export WORK_CONFIG=" + shlex.quote(str(work_config)) + "\n" + script
        with patch.dict(os.environ, {"PATH": self.path}), patch.object(configure, "COMPLETION_PROBE_SCRIPT", script):
            observed = configure.probe_completion(self.handler, "work", tuple(args), prefix)
        self.assertIsInstance(observed, configure.CompletionMatches, observed)
        self.assertFalse(self.forbidden.exists(), "Completion executed an authentication/search tool")
        if self.log.exists():
            for record in self.requests():
                args = record["args"]
                self.assertEqual(args[-2:], ["profiles", "--names"], record)
        return observed.candidates

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_top_level_saved_profile_and_builtin_inbox_match_the_prefix(self):
        self.assertEqual(self.candidates((), "review_"), ("review_queue",))
        self.assertEqual(self.candidates((), "in"), ("inbox",))
        self.assertTrue(self.requests(), "Profile metadata was never read")

    def test_list_profile_matches_the_prefix(self):
        self.assertEqual(self.candidates(("list",), "review_"), ("review_queue",))

    def test_profile_option_matches_the_prefix(self):
        self.assertEqual(self.candidates(("--profile",), "team"), ("team-a",))
        self.assertEqual(self.candidates((), "--profile=team"), ("team-a",))

    def test_default_profile_matches_the_prefix(self):
        self.assertEqual(self.candidates(("profiles", "default"), "review_"), ("review_queue",))

    def test_custom_config_path_with_spaces_is_forwarded_as_one_literal_argument(self):
        for args in (("--config", str(self.custom), "list"), ("--config=" + str(self.custom), "list")):
            with self.subTest(args=args):
                self.assertEqual(self.candidates(args, "custom_"), ("custom_queue",))
                self.assertEqual(self.requests()[-1]["args"], ["--config", str(self.custom), "profiles", "--names"])

    def test_work_config_is_inherited_by_the_local_metadata_command(self):
        self.assertEqual(self.candidates((), "custom_", work_config=self.custom), ("custom_queue",))
        self.assertEqual(self.requests()[-1], {"args": ["profiles", "--names"], "selected": str(self.custom)})

    def test_config_path_substitution_syntax_remains_literal_data(self):
        marker = self.root / "substitution executed"
        literal = str(self.root) + "/$(touch " + shlex.quote(str(marker)) + ")"
        self.assertEqual(self.candidates(("--config=" + literal, "list"), "review_"), ("review_queue",))
        self.assertEqual(self.requests()[-1]["selected"], literal)
        self.assertFalse(marker.exists(), "Config path executed a shell substitution")

    def test_metadata_failure_offers_no_profile_candidates(self):
        self.assertEqual(self.candidates(("list",), "review_", work_config="metadata-fails"), ())


if __name__ == "__main__":
    unittest.main()
