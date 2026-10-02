-- Keep the editor useful while dependency failures remain visible in :checkhealth editor.
require('editor.options').setup()
require('editor.keymaps').setup()
require('editor.lsp').setup()

local result = require('editor.plugins').setup()
if result.outcome ~= 'READY' then
  vim.schedule(function()
    vim.notify('Editor plugins: ' .. result.outcome .. ' at ' .. result.stage ..
      '. Run :checkhealth editor for the required dependency or pin.', vim.log.levels.ERROR)
  end)
end
