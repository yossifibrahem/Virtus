# Configuration reference

[Showcase](../README.md) · [Setup](SETUP.md) · [Usage](USAGE.md)

## In-App Settings

Open the settings panel in the browser to configure:

| Setting | Description |
|---|---|
| API Key | Provider API key — stored server-side, never in `localStorage` |
| Base URL | OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` |
| Model | Model ID for the next request |
| System Prompt | Optional instruction prepended to every conversation |

Memory controls are in the **Chat** tab. You can enable or disable memory, add or edit entries, delete individual entries, or clear them all. The **Appearance** tab controls the theme, accent color, fonts, and display preferences.

## Runtime settings

Resource and file-handling limits are managed server-side in `~/.virtus/advanced_config.json` or through environment variables. The Workspace tab in the current UI shows an overview and data-deletion controls; it does not expose these limits.

| Setting | Description |
|---|---|
| Sandbox Image | Docker image name for sandbox containers |
| Container Memory | Memory limit per container (e.g. `512m`, `1g`) |
| Container CPUs | CPU quota per container |
| Turn duration / tool calls | Defaults: 900 seconds / 100 calls per turn |
| Storage safeguards | Defaults: minimum 512 MiB free; maximum 10 GiB per managed workspace |
| Container Idle Timeout | Seconds before idle containers are stopped; `0` disables |
| Max File Preview Bytes | Maximum bytes loaded for in-browser text file preview |
| Max File List Entries | Maximum workspace directory entries returned |
| Max Upload Bytes | Maximum file upload size |

Local model providers typically use these API base URLs:

```
http://localhost:11434/v1    # Ollama
http://localhost:1234/v1     # LM Studio
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Overrides the saved API key |
| `OPENAI_BASE_URL` | — | Overrides the saved API base URL |
| `OPENAI_API_BASE` | — | Fallback alias for `OPENAI_BASE_URL` |
| `VIRTUS_HOST` | `127.0.0.1` | Flask development-server bind address |
| `VIRTUS_PORT` | `8080` | Flask development-server port |
| `VIRTUS_DEBUG` | disabled | Enable Flask debug mode with `1`, `true`, `yes`, or `on` |
| `VIRTUS_OPENAI_TIMEOUT` | `120` | OpenAI-compatible request/read timeout in seconds |
| `VIRTUS_MCP_TOOL_TIMEOUT` | `120` | Maximum MCP tool-call duration in seconds |
| `VIRTUS_TOOL_APPROVAL_TIMEOUT` | `600` | Maximum wait for a tool approval decision in seconds |
| `VIRTUS_CONFIG_FILE` | `~/.virtus/config.json` | Server-side API config path |
| `VIRTUS_DATABASE_FILE` | `~/.virtus/virtus.sqlite3` | Local SQLite database for conversations, folders, and chat images |
| `VIRTUS_CONFIG_CACHE_TTL` | `5` | Seconds to cache API config reads |
| `VIRTUS_ADVANCED_CONFIG_FILE` | `~/.virtus/advanced_config.json` | Advanced/container settings config path |
| `VIRTUS_MCP_CONFIG_FILE` | `~/.virtus/mcp.json` | MCP server config path |
| `VIRTUS_MCP_CONFIG_CACHE_TTL` | `5` | Seconds to cache MCP config reads |
| `VIRTUS_SANDBOX_IMAGE` | `virtus-sandbox:latest` | Locally built Docker image for sandbox containers |
| `VIRTUS_CONTAINERS_ROOT` | `~/.virtus/containers` | Host directory for standalone-chat and shared-folder workspaces |
| `VIRTUS_CONTAINER_MEMORY` | `512m` | Memory limit per sandbox container |
| `VIRTUS_CONTAINER_CPUS` | `1` | CPU quota per sandbox container |
| `VIRTUS_CONTAINER_PREFIX` | `virtus-chat-` | Prefix for generated container names |
| `VIRTUS_CONTAINER_IDLE_TIMEOUT` | `600` | Seconds before idle containers are stopped; `0` disables |
| `VIRTUS_MAX_CONTENT_LENGTH` | `62914560` | Flask request body cap (bytes) |
| `VIRTUS_CORS_ORIGINS` | `http://localhost:8080,...` | Comma-separated allowed origins |
| `VIRTUS_ALLOWED_HOSTS` | loopback hostnames | Additional comma-separated hostnames allowed to serve the local UI |
| `VIRTUS_DESKTOP_PORT` | `38492` | Loopback port for the desktop UI |
| `VIRTUS_MAX_FILE_PREVIEW_BYTES` | `524288` | Max bytes for in-browser text file preview |
| `VIRTUS_MAX_FILE_LIST_ENTRIES` | `500` | Max workspace directory entries returned |
| `VIRTUS_TURN_TIMEOUT_SECONDS` | `900` | Maximum duration per model turn |
| `VIRTUS_TURN_TOOL_LIMIT` | `100` | Maximum tool calls per model turn |
| `VIRTUS_MIN_FREE_DISK_BYTES` | `536870912` | Minimum free space before execution |
| `VIRTUS_MAX_WORKSPACE_BYTES` | `10737418240` | Maximum managed workspace size before execution is blocked |
| `VIRTUS_MAX_UPLOAD_BYTES` | `52428800` | Max file upload size (bytes) |

Environment variables take precedence over saved runtime settings. The configuration API reports which settings are locked by an environment variable.

## Persistent Data

All runtime data is stored outside the repo under `~/.virtus/`:

```
~/.virtus/
├── config.json            # Server-side API provider config
├── advanced_config.json   # Container and file-handling settings
├── mcp.json               # MCP server configuration
├── memory.json            # App-managed cross-chat memory, never mounted into containers
├── memory.settings.json   # Whether cross-chat memory is enabled
├── memory.history.json    # Recoverable prior memory versions
├── virtus.sqlite3         # Conversations, histories, branches, folders, and image bytes
└── containers/            # Workspace directories for standalone chats and shared folders
```

The database is created automatically on first use with Python's built-in SQLite
support; no database service is required. The previous conversation JSON files and
`folders.json` are not imported. Uploaded and tool-result images are SQLite rows
owned by their chat; deleting the chat deletes its images. The image table is added
automatically to existing SQLite databases, preserving chats and folders. Old
file-based images are not imported. Workspace directories, settings, and memory
retain their existing storage.

SQLite uses WAL journaling and may create adjacent `virtus.sqlite3-wal` and
`virtus.sqlite3-shm` files while connections are open. Shut down Virtus before copying
its data directory for backup; copying only a live database file can omit recent
writes. A backup made while the app runs must use SQLite's backup API for the database
and also include the external workspace files. The database backup includes chat images.

## MCP Configuration

MCP servers are configured in `~/.virtus/mcp.json` (or the path set by `VIRTUS_MCP_CONFIG_FILE`). The settings panel can read and write this file from the browser.

All MCP servers run inside the active chat or folder's Docker sandbox with the workspace mounted at `/workspace`. Use `/workspace`-relative paths in server arguments.

**Example `mcp.json`:**

```json
{
  "mcpServers": {
    "example": {
      "command": "node",
      "args": ["/workspace/server.js"],
      "env": {"DATA_DIR": "/workspace/data"}
    }
  }
}
```

This example assumes you have placed your server at `/workspace/server.js` inside the sandbox. Virtus does not bundle or register an MCP server automatically.

Per-server UI settings (enabled, auto-approve, icon) are stored in browser `localStorage` under `virtus_mcp_server_settings` — not in `mcp.json`.

Runtime configuration contains only the servers in `mcp.json`. All server IDs are user-configurable, and loading configuration does not rewrite the file. Previously saved server definitions remain editable; remove any obsolete definitions in settings.

Model-facing tool names use the MCP tool name directly. The matching MCP server is sent separately in `mcp_tool_meta`, which lets the backend dispatch the call without adding server prefixes to the visible tool name. Tool descriptions should stay clean and semantic (`tool.description || tool.name`).
