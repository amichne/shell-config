local M = {}

function M.setup()
  vim.g.mapleader = ' '
  vim.g.maplocalleader = ' '
  vim.opt.number = true
  vim.opt.cursorline = true
  vim.opt.showmatch = true
  vim.opt.ignorecase = true
  vim.opt.smartcase = true
  vim.opt.hlsearch = true
  vim.opt.incsearch = true
  vim.opt.tabstop = 2
  vim.opt.softtabstop = 2
  vim.opt.shiftwidth = 2
  vim.opt.expandtab = true
  vim.opt.autoindent = true
  vim.opt.colorcolumn = '120'
  vim.opt.clipboard = 'unnamedplus'
  vim.opt.wildmode = 'longest:full,full'
  vim.opt.termguicolors = true
  vim.opt.signcolumn = 'yes'
  vim.opt.splitbelow = true
  vim.opt.splitright = true
  vim.opt.scrolloff = 4
  vim.opt.completeopt = { 'menu', 'menuone', 'noselect', 'popup' }
  vim.opt.updatetime = 250
  vim.opt.timeoutlen = 500
  vim.opt.undofile = true
  vim.diagnostic.config({ severity_sort = true, float = { border = 'rounded' } })
end

return M
