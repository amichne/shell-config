local M = {}

M.lazy_commit = '306a05526ada86a7b30af95c5cc81ffba93fef97'
M.spec = {
  {
    'dmtrKovalenko/fff', commit = '95fd777c2529fc7b4d7572dabff64cc07268f2c5', -- v0.11.0
    lazy = false,
    build = function() require('fff.download').download_or_build_binary() end,
    opts = { debug = { enabled = false, show_scores = false } },
  },
  {
    'folke/which-key.nvim', commit = '3aab2147e74890957785941f0c1ad87d0a44c15a', lazy = false,
    opts = {
      preset = 'modern', delay = 250, icons = { mappings = false },
      spec = {
        { '<leader>f', group = 'Find' }, { '<leader>c', group = 'Code' },
        { '<leader>g', group = 'Git and navigation' }, { '<leader>h', group = 'Help' },
      },
    },
  },
  {
    'lewis6991/gitsigns.nvim', commit = '070a5d7b985546cc57e1fc61e5bc507fecac6045',
    event = { 'BufReadPre', 'BufNewFile' }, opts = {},
  },
}

---@alias EditorBootstrapOutcome 'READY'|'MISSING_GIT'|'CLONE_FAILED'|'CHECKOUT_FAILED'|'PIN_UNVERIFIED'|'PIN_MISMATCH'
---@class EditorBootstrapResult
---@field outcome EditorBootstrapOutcome
---@field stage 'PREFLIGHT'|'CLONE'|'CHECKOUT'|'VERIFY'|'SETUP'
---@return EditorBootstrapResult
function M.setup()
  local path = vim.fn.stdpath('data') .. '/lazy/lazy.nvim'
  if vim.fn.executable('git') ~= 1 then return { outcome = 'MISSING_GIT', stage = 'PREFLIGHT' } end
  if not vim.uv.fs_stat(path) then
    local clone = vim.system({ 'git', 'clone', '--filter=blob:none',
      'https://github.com/folke/lazy.nvim.git', path }, { text = true }):wait(30000)
    if clone.code ~= 0 then return { outcome = 'CLONE_FAILED', stage = 'CLONE' } end
    local checkout = vim.system({ 'git', '-C', path, 'checkout', '--detach', M.lazy_commit }, { text = true }):wait(10000)
    if checkout.code ~= 0 then return { outcome = 'CHECKOUT_FAILED', stage = 'CHECKOUT' } end
  end
  local head = vim.system({ 'git', '-C', path, 'rev-parse', 'HEAD' }, { text = true }):wait(10000)
  if head.code ~= 0 then return { outcome = 'PIN_UNVERIFIED', stage = 'VERIFY' } end
  if vim.trim(head.stdout) ~= M.lazy_commit then return { outcome = 'PIN_MISMATCH', stage = 'VERIFY' } end
  vim.opt.rtp:prepend(path)
  require('lazy').setup(M.spec, {
    lockfile = vim.fn.stdpath('state') .. '/lazy-lock.json',
    checker = { enabled = false }, change_detection = { notify = false },
  })
  return { outcome = 'READY', stage = 'SETUP' }
end

return M
