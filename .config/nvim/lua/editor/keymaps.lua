local M = {}

function M.setup()
  local function map(mode, keys, action, description)
    vim.keymap.set(mode, keys, action, { silent = true, desc = description })
  end

  for _, direction in ipairs({ 'h', 'j', 'k', 'l' }) do
    map('n', '<C-' .. direction .. '>', '<C-W>' .. direction, 'Move to ' .. direction .. ' window')
    map('n', '<M-' .. direction .. '>', '<C-W>' .. direction:upper(), 'Move split ' .. direction)
  end
  map('n', '<Esc>', '<cmd>nohlsearch<CR>', 'Clear search highlight')
  map('n', '<leader>ff', function() require('fff').find_files() end, 'Find files')
  map('n', '<leader>fg', function() require('fff').live_grep() end, 'Search file contents')
  map('n', '<leader>?', function() require('which-key').show({ global = true }) end, 'Show keybindings')
  map('n', '<leader>cf', function() require('editor.lsp').format_current_buffer() end, 'Format current buffer')
  map('n', '<leader>ca', vim.lsp.buf.code_action, 'Code actions')
  map('n', '<leader>cr', vim.lsp.buf.rename, 'Rename symbol')
  map('n', '<leader>cd', vim.diagnostic.open_float, 'Show line diagnostics')
  map('n', '<leader>gd', vim.lsp.buf.definition, 'Go to definition')
  map('n', '<leader>gr', vim.lsp.buf.references, 'Find references')
  map('n', '<leader>gs', function() require('gitsigns').preview_hunk() end, 'Preview Git change')
  map('n', '<leader>gb', function() require('gitsigns').blame_line({ full = true }) end, 'Blame current line')
  map('n', '<leader>gD', function() require('gitsigns').diffthis() end, 'Diff against Git index')
  map('n', ']h', function() require('gitsigns').nav_hunk('next') end, 'Next Git change')
  map('n', '[h', function() require('gitsigns').nav_hunk('prev') end, 'Previous Git change')
  map('n', '<leader>hh', '<cmd>help<CR>', 'Neovim help')
  map('n', '<leader>ht', '<cmd>Tutor<CR>', 'Learn Neovim motions')
  map('n', '<leader>hc', '<cmd>checkhealth editor<CR>', 'Check editor dependencies')
  map('i', '<C-Space>', vim.lsp.completion.get, 'Request LSP completion')
end

return M
