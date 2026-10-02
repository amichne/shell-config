local M = {}

-- Neovim owns these configurations; executables remain managed by the shell toolchain.
M.servers = {
  { name = 'ty', config = {
    cmd = { 'ty', 'server' }, filetypes = { 'python' },
    root_markers = { 'ty.toml', 'pyproject.toml', 'setup.py', 'setup.cfg', '.git' },
  } },
  { name = 'ruff', config = {
    cmd = { 'ruff', 'server' }, filetypes = { 'python' },
    root_markers = { 'pyproject.toml', 'ruff.toml', '.ruff.toml', '.git' },
    on_attach = function(client) client.server_capabilities.hoverProvider = false end,
  } },
  { name = 'biome', config = {
    cmd = { 'biome', 'lsp-proxy' },
    filetypes = { 'javascript', 'javascriptreact', 'typescript', 'typescriptreact', 'json', 'jsonc', 'css', 'graphql' },
    root_markers = { 'biome.json', 'biome.jsonc' }, workspace_required = true,
  } },
  { name = 'kotlin_lsp', config = {
    cmd = { 'kotlin-lsp', '--stdio' }, filetypes = { 'kotlin' }, workspace_required = true,
    root_markers = { 'settings.gradle.kts', 'settings.gradle', 'pom.xml', 'build.gradle.kts', 'build.gradle', 'workspace.json' },
  } },
}

---@class EditorServerStatus
---@field server string
---@field executable string
---@field outcome 'ENABLED'|'MISSING_EXECUTABLE'
---@return EditorServerStatus[]
function M.status()
  local rows = {}
  for _, server in ipairs(M.servers) do
    rows[#rows + 1] = {
      server = server.name, executable = server.config.cmd[1],
      outcome = vim.fn.executable(server.config.cmd[1]) == 1 and 'ENABLED' or 'MISSING_EXECUTABLE',
    }
  end
  return rows
end

function M.setup()
  for _, server in ipairs(M.servers) do
    vim.lsp.config(server.name, server.config)
    if vim.fn.executable(server.config.cmd[1]) == 1 then vim.lsp.enable(server.name) end
  end

  vim.api.nvim_create_autocmd('LspAttach', {
    group = vim.api.nvim_create_augroup('editor.lsp', { clear = true }),
    callback = function(event)
      local client = vim.lsp.get_client_by_id(event.data.client_id)
      if client and client:supports_method('textDocument/completion') then
        vim.lsp.completion.enable(true, client.id, event.buf, { autotrigger = true })
      end
    end,
  })
end

---@alias EditorFormatterSelection { outcome: 'NO_FORMATTER' }|{ outcome: 'AMBIGUOUS_FORMATTER', count: integer }|{ outcome: 'FORMATTER_SELECTED', id: integer, name: string }
---@param clients vim.lsp.Client[]
---@return EditorFormatterSelection
function M.select_formatter(clients)
  if #clients == 0 then return { outcome = 'NO_FORMATTER' } end
  if #clients > 1 then return { outcome = 'AMBIGUOUS_FORMATTER', count = #clients } end
  return { outcome = 'FORMATTER_SELECTED', id = clients[1].id, name = clients[1].name }
end

function M.format_current_buffer()
  local selection = M.select_formatter(vim.lsp.get_clients({ bufnr = 0, method = 'textDocument/formatting' }))
  if selection.outcome ~= 'FORMATTER_SELECTED' then
    vim.notify('Format buffer: ' .. selection.outcome .. '. Check :lsp and :checkhealth editor.', vim.log.levels.WARN)
    return selection
  end
  -- Selecting one advertised formatter prevents multiple servers from rewriting the buffer in turn.
  vim.lsp.buf.format({ bufnr = 0, id = selection.id, timeout_ms = 3000 })
end

return M
