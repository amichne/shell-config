local M = {}

function M.check()
  local health = vim.health
  health.start('Editor dependencies')
  if vim.fn.has('nvim-0.12') == 1 then health.ok('Neovim 0.12+ APIs available')
  else health.error('Neovim 0.12+ is required') end
  for _, command in ipairs({ 'git', 'curl', 'fd', 'rg' }) do
    if vim.fn.executable(command) == 1 then health.ok(command .. ' available')
    else health.error(command .. ' missing from PATH') end
  end
  for _, row in ipairs(require('editor.lsp').status()) do
    if row.outcome == 'ENABLED' then health.ok(row.server .. ': executable available; inspect :lsp for attachment')
    else health.warn(row.server .. ': MISSING_EXECUTABLE (' .. row.executable .. ')') end
  end

  health.start('Pinned editor plugins')
  local plugins = require('editor.plugins')
  local pins = { { 'lazy.nvim', plugins.lazy_commit } }
  for _, spec in ipairs(plugins.spec) do pins[#pins + 1] = { spec[1]:match('/([^/]+)$'), spec.commit } end
  for _, pin in ipairs(pins) do
    local path = vim.fn.stdpath('data') .. '/lazy/' .. pin[1]
    if vim.fn.executable('git') == 1 and vim.uv.fs_stat(path) then
      local result = vim.system({ 'git', '-C', path, 'rev-parse', 'HEAD' }, { text = true }):wait(10000)
      if result.code == 0 and vim.trim(result.stdout) == pin[2] then health.ok(pin[1] .. ': pinned commit')
      else health.error(pin[1] .. ': PIN_UNVERIFIED; expected ' .. pin[2]) end
    else health.warn(pin[1] .. ': not installed; run :Lazy sync after Git is available') end
  end
  health.info('Run :FFFHealth for the native search backend and :checkhealth which-key for mapping diagnostics')
end

return M
