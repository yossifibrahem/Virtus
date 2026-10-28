// Defaults applied both on first load and when resetting settings.
export const SETTINGS_DEFAULTS = {
  apiBase:      'https://api.openai.com/v1',
  serverHasApiKey: false,
  model:        '',
  systemPrompt: '',
  // Model parameters
  temperature:        0.7,
  // Chat behaviour
  autoGenerateTitles: true,
  enterToSend:        true,
  autoScrollStreaming:true,
};

export const CUSTOMIZATION_DEFAULTS = {
  showSuggestionChips:   true,
  hideToolBlocks:         true,
  groupSequentialBlocks:  true,
  hideThinkingTokens:     true,
  fontSize:              'medium',    // 'small' | 'medium' | 'large'
  fontFamily:            'geist',     // 'geist' | 'system'
  theme:                 'auto',      // 'dark' | 'light' | 'auto'
  accentColor:           '#DDB37E',   // preset swatch
  customAccentColor:     '',          // user custom hex
};

export const STORAGE_KEYS = {
  settings:          'virtus_settings',
  mcpTools:          'virtus_mcp_tools',
  mcpServerSettings: 'virtus_mcp_server_settings',
  models:            'virtus_models',
  lastConv:          'virtus_last_conv',
  sidebar:           'virtus_sidebar',
  expandedFolders:   'virtus_expanded_folders',
  filePanelOpen:     'virtus_file_panel_open',
  customization:     'virtus_customization',
};

// Single mutable state object shared across all modules.
export const state = {
  convId:            null,
  folderId:          null,
  folderSystemPrompt:'',
  messages:          [],   // OpenAI API message history
  displayLog:        [],   // Serialisable render log (messages + tool results)
  mcpTools:          [],
  // Per-server settings: { [serverName]: { enabled: bool, autoApprove: bool } }
  mcpServerSettings: {},
  isStreaming:       false,
  streamId:          null,
  ...SETTINGS_DEFAULTS,
  ...CUSTOMIZATION_DEFAULTS,
};
