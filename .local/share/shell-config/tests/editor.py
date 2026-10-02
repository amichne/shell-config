#!/usr/bin/env python3
"""Check editor behavior in disposable XDG directories, without plugin downloads."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NVIM = shutil.which("nvim")


class EditorTest(unittest.TestCase):
    def run_lua(self, body, *, tools=()):
        self.assertIsNotNone(NVIM, "Neovim 0.12+ is required for this focused check")
        config = ROOT / "config/nvim"
        if not config.is_dir():
            config = Path.home() / ".config/nvim"
        self.assertTrue((config / "init.lua").is_file(), "The new Lua editor configuration is absent")
        with tempfile.TemporaryDirectory(prefix="editor tests with spaces ") as temporary:
            root = Path(temporary)
            tool_dir = root / "tools"
            tool_dir.mkdir()
            for name in tools:
                executable = tool_dir / name
                executable.write_text("#!/bin/sh\nexit 0\n")
                executable.chmod(0o755)
            probe = root / "probe.lua"
            probe.write_text("vim.opt.rtp:prepend(" + repr(str(config)) + ")\n" + body)
            env = dict(os.environ)
            env.update({
                "HOME": str(root / "home"), "PATH": str(tool_dir),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CACHE_HOME": str(root / "cache"),
            })
            result = subprocess.run(
                [NVIM, "--headless", "-u", "NONE", "-i", "NONE", "--noplugin", "-l", str(probe)],
                env=env, cwd=root, text=True, capture_output=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("EDITOR_CHECK_PASSED", result.stdout + result.stderr)

    def test_window_shortcuts_discoverable_actions_and_preserved_settings(self):
        self.run_lua("""
require('editor.options').setup()
require('editor.keymaps').setup()
assert(vim.g.mapleader == ' ')
assert(vim.o.number and vim.o.expandtab and vim.o.smartcase)
assert(vim.o.shiftwidth == 2 and vim.o.colorcolumn == '120')
for _, direction in ipairs({ 'h', 'j', 'k', 'l' }) do
  local move = vim.fn.maparg('<C-' .. direction .. '>', 'n', false, true)
  local arrange = vim.fn.maparg('<M-' .. direction .. '>', 'n', false, true)
  assert(move.rhs == '<C-W>' .. direction)
  assert(arrange.rhs == '<C-W>' .. direction:upper())
end
for _, key in ipairs({ 'ff', 'fg', '?', 'cf', 'ca', 'cr', 'gd', 'gr', 'gs' }) do
  local mapping = vim.fn.maparg(' ' .. key, 'n', false, true)
  assert(mapping.callback and mapping.desc and #mapping.desc > 0, key)
end
assert(vim.fn.maparg('f', 'n') == '', 'Native find-character motion was replaced')
assert(#vim.api.nvim_get_autocmds({ event = 'BufWritePre' }) == 0, 'Formatting must remain explicit')
print('EDITOR_CHECK_PASSED')
""")

    def test_missing_language_servers_are_reported_and_not_enabled(self):
        self.run_lua("""
local lsp = require('editor.lsp')
lsp.setup()
local rows = lsp.status()
assert(#rows == 4)
for _, row in ipairs(rows) do
  assert(row.outcome == 'MISSING_EXECUTABLE', vim.inspect(row))
  assert(not vim.lsp.is_enabled(row.server))
end
print('EDITOR_CHECK_PASSED')
""")

    def test_available_language_servers_use_native_configs(self):
        self.run_lua("""
local lsp = require('editor.lsp')
lsp.setup()
for _, row in ipairs(lsp.status()) do
  assert(row.outcome == 'ENABLED', vim.inspect(row))
  assert(vim.lsp.is_enabled(row.server))
end
assert(vim.deep_equal(vim.lsp.config.ty.cmd, { 'ty', 'server' }))
assert(vim.deep_equal(vim.lsp.config.ruff.cmd, { 'ruff', 'server' }))
assert(vim.deep_equal(vim.lsp.config.biome.cmd, { 'biome', 'lsp-proxy' }))
assert(vim.deep_equal(vim.lsp.config.kotlin_lsp.cmd, { 'kotlin-lsp', '--stdio' }))
assert(vim.lsp.config.kotlin_lsp.workspace_required)
assert(vim.lsp.config.biome.workspace_required)
print('EDITOR_CHECK_PASSED')
""", tools=("ty", "ruff", "biome", "kotlin-lsp"))

    def test_formatter_selection_fails_closed_on_missing_or_ambiguous_clients(self):
        self.run_lua("""
local select_formatter = require('editor.lsp').select_formatter
assert(select_formatter({}).outcome == 'NO_FORMATTER')
assert(select_formatter({ { id = 1, name = 'a' }, { id = 2, name = 'b' } }).outcome == 'AMBIGUOUS_FORMATTER')
local selected = select_formatter({ { id = 3, name = 'ruff' } })
assert(selected.outcome == 'FORMATTER_SELECTED' and selected.id == 3 and selected.name == 'ruff')
print('EDITOR_CHECK_PASSED')
""")

    def test_plugin_set_has_exact_upstream_commit_pins(self):
        self.run_lua("""
local plugins = require('editor.plugins')
assert(#plugins.spec == 3)
assert(plugins.lazy_commit:match('^[a-f0-9]+$') and #plugins.lazy_commit == 40)
local seen = {}
for _, spec in ipairs(plugins.spec) do
  assert(spec.commit:match('^[a-f0-9]+$') and #spec.commit == 40)
  seen[spec[1]] = true
end
assert(seen['dmtrKovalenko/fff'] and seen['folke/which-key.nvim'] and seen['lewis6991/gitsigns.nvim'])
print('EDITOR_CHECK_PASSED')
""")

    def test_bootstrap_reports_missing_git_before_downloading_or_loading_plugins(self):
        self.run_lua("""
local result = require('editor.plugins').setup()
assert(result.outcome == 'MISSING_GIT' and result.stage == 'PREFLIGHT')
assert(package.loaded.lazy == nil)
assert(vim.uv.fs_stat(vim.fn.stdpath('data') .. '/lazy/lazy.nvim') == nil)
print('EDITOR_CHECK_PASSED')
""")

    def test_bootstrap_rejects_an_unverified_existing_plugin_checkout(self):
        self.run_lua("""
local path = vim.fn.stdpath('data') .. '/lazy/lazy.nvim'
vim.fn.mkdir(path, 'p')
local result = require('editor.plugins').setup()
assert(result.outcome == 'PIN_MISMATCH' and result.stage == 'VERIFY')
assert(package.loaded.lazy == nil)
assert(vim.uv.fs_stat(path .. '/lua') == nil)
print('EDITOR_CHECK_PASSED')
""", tools=("git",))


if __name__ == "__main__":
    unittest.main()
