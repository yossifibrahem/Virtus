# Lumen AI Chat

A local-first AI chatbot with real-time streaming, folder-based chat organization, isolated Docker sandboxes, MCP server support, and a zero-build-step frontend.

Lumen is distributed as an installable desktop application for normal users while keeping a straightforward source-development workflow. The backend is Flask, and the existing browser UI remains the product interface rather than being replaced by Electron or a native chat window.

<img width="2940" height="1662" alt="Untitled design" src="https://github.com/user-attachments/assets/f4d3b00f-a986-4081-bd41-b74ed4afbdf0" />

## Current Product Direction

- **Desktop experience:** small macOS menu-bar and Windows tray applications run Lumen locally and open the same chat UI in the user's default browser.
- **User prerequisite:** Docker Desktop is the only external runtime users install. Python, Node.js, npm, Git, and the Lumen source tree are included or handled internally.
- **Sandbox installation:** the app bundles `Dockerfile.sandbox`. After explicit confirmation on first run, Docker pulls `ubuntu:24.04`, installs the sandbox runtimes and packages, and creates the local `lumen-sandbox:latest` image.
- **MCP boundary:** user-configured servers run only inside Lumen's containers. No MCP server is bundled or automatically registered; configure servers through settings or `mcp.json`.
- **Distribution:** desktop artifacts are an Apple Silicon macOS 14+ DMG and a portable Windows x64 `onedir` ZIP. The Windows package must remain intact; the executable is not a standalone distribution.
- **Data ownership:** chats, settings, memory, and workspaces remain under `~/.lumen/`; replacing or uninstalling the app does not remove them.
- **Image delivery:** Lumen does not pull a prebuilt application image from a container registry. The release workflow verifies the same Dockerfile, and each user builds the pinned image locally.

```text
Lumen AI Chat.app ──> Waitress + Flask on 127.0.0.1:38492 ──> browser UI
        │
        └──> first-run Docker build ──> lumen-sandbox:latest ──> per-chat/folder containers
                                                        └──> user-configured MCP servers
```

---

## Features

**Model support**

- Any OpenAI-compatible API — OpenAI, Ollama, LM Studio, or a local proxy
- Configure API key, base URL, model, and system prompt from the browser UI
- Proxied model-list fetching so you can pick models without leaving the chat

**Streaming**

- Responses stream over Server-Sent Events (SSE) token-by-token
- Cancel mid-stream; the partial response is saved automatically
- Reattach to an in-progress stream if you navigate away and return

**MCP (Model Context Protocol)**

- Add MCP servers through the UI or `mcp.json`
- Tool metadata keeps the MCP server name separate from the model-facing tool name
- Persistent MCP stdio session pooling — sessions are opened once per conversation and reused across all turns until the container stops
- Approve or deny individual tool calls; enable auto-approval per server
- Tool activity renders inline: arguments, running state, and results

**Persistent memory**

- The model remembers facts across all conversations via `~/.lumen/memory.md`
- Mounted read-write into every container at `/memory.md` so the model can update it using its file tools
- Memory contents are injected into the system prompt at the start of every turn

**Folders**

- Group related conversations into folders and open a folder home with its chats and shared files
- Create, rename, delete, and search folders; move existing conversations into or out of a folder
- Add folder-level instructions that apply to every chat in the folder
- Chats in a folder keep separate message histories while sharing one Docker sandbox and workspace

**Isolated Docker sandboxes**

- Standalone conversations get their own Docker container and workspace directory; conversations in a folder share the folder's runtime
- The workspace is mounted at `/workspace` inside the container
- MCP servers run inside the container — code execution is isolated from the host
- Containers are stopped automatically when idle (configurable timeout)
- Stale containers from previous sessions are cleaned up at startup

**File handling**

- Upload files into the conversation workspace via the browser panel
- Browse nested workspace directories in an expandable file tree
- Preview text/code/Markdown files inline; download any workspace file
- Access a folder's shared workspace before opening or creating a chat
- Images are stored by SHA-256 content hash and sent as vision inputs to compatible models

**UI**

- Markdown, syntax-highlighted code blocks, and KaTeX math rendering
- Voice input via the browser Web Speech API
- Auto-generated conversation titles after the first exchange
- Create, rename, delete, move, and search conversations from the dedicated search modal
- Responsive sidebar with a compact desktop mode and off-canvas mobile layout
- Switch between edited-message and regenerated-response branches with arrows in the message footer
- Customizable color mode, accent color, font size, sidebar, timestamps, and more — stored in `localStorage`

**Architecture**

- No database — conversations are plain JSON files under `~/.lumen/`
- No frontend build step — Flask serves `templates/` and `static/` directly
- No bundler — browser ES modules imported with `type="module"`

---

## Windows x64 Installation

The Windows build is a portable, unsigned ZIP. It contains the complete
PyInstaller `onedir` application; keep the extracted folder together.

1. Install Docker Desktop for Windows and allow its normal setup to finish.
2. Download `Lumen-AI-Chat-<version>-windows-x64.zip` and its `.sha256` file.
3. Verify the checksum, then extract the entire ZIP to a writable local folder.
4. Run `Lumen AI Chat.exe` from the extracted `Lumen AI Chat` folder. Windows
   may show an unsigned-application warning on first launch.
5. If Docker is stopped, use **Docker Status** in the tray and click **Start
   Docker** in Lumen's setup page. When prompted, click **Install Lumen Tools**.

Lumen remains in the notification area with **Open Lumen**, **Docker Status**,
**Open Logs**, and **Quit Lumen** actions. Quit stops the local listener, MCP
sessions, and running Lumen containers before removing the tray icon. Cleanup
has a single 15-second deadline; stopped containers remain available for fast
reuse. Chats and workspaces stay under `%USERPROFILE%\.lumen`.

The first sandbox installation needs an internet connection. The app includes its Python runtime, and Docker installs the sandbox runtimes and packages. MCP servers are configured separately.

## macOS Alpha Installation

The prebuilt desktop alpha targets **Apple Silicon Macs running macOS 14 or newer**. It opens Lumen in the default browser and keeps the local server available from a small menu-bar item; it does not install Python or Node.js on the Mac.

1. Install and start [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Download the Apple Silicon DMG from the GitHub release.
3. Open the DMG and drag **Lumen AI Chat** into **Applications**.
4. Because the alpha is not notarized, Control-click the app on first launch, choose **Open**, and confirm macOS's warning. Later launches work normally.
5. Lumen opens its setup page. If Docker is stopped, click **Start Docker**. When prompted, click **Install Lumen Tools**. Docker pulls `ubuntu:24.04`, installs Python, Node.js, and the sandbox packages inside it, and creates `lumen-sandbox:latest` locally.

Lumen remains in the menu bar using the bundled Lumen artwork, with **Open Lumen**, **Docker Status**, **Open Logs**, and **Quit Lumen** actions. Application updates are manual: replace the old app with a newer DMG. Chats and workspaces stay under `~/.lumen/` and are not removed by an app update or uninstall.

Internet access is required during the first sandbox installation for the Ubuntu image and package repositories. No MCP server source is fetched or compiled by this build.

To uninstall, quit Lumen and move `Lumen AI Chat.app` to Trash. Remove `~/.lumen/` separately only if you also want to permanently delete chats, settings, memory, and workspace files. Docker images can be removed separately through Docker Desktop.

## Developer Quick Start

```bash
# 1. Clone
git clone https://github.com/yossifibrahem/lumen-ai-chat.git
cd lumen-ai-chat

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Build the Docker sandbox image (required for MCP tools)
identity="$(python -c 'import build_info; print(build_info.sandbox_identity())')"
docker build --build-arg "LUMEN_SANDBOX_IDENTITY=$identity" -f Dockerfile.sandbox -t lumen-sandbox:latest .

# 5. Start the app
python app.py
```

Open **http://localhost:8080**, then open the settings panel to enter your API key, base URL, and model name.

The development server binds to `127.0.0.1` with debug mode disabled by default. For deliberate LAN testing on a trusted network, set `LUMEN_HOST=0.0.0.0`, configure `LUMEN_CORS_ORIGINS`, and open `http://<your-computer-ip>:8080` from the other device.

> Source and desktop runs use the local `lumen-sandbox:latest` image. The desktop app contains the complete Docker build context, so first-run setup needs Docker Desktop but no host Python, Node.js, Git, or source checkout.

### Configuring tools

The sandbox has no built-in MCP server. Add a server through MCP settings or `~/.lumen/mcp.json`. Python and `uv` are available in the container for a replacement Python server; its code and dependencies must be supplied separately. Node.js and `mcp-remote` remain available for user-configured integrations. Until a server is configured, discovery returns no tools.

## Installation

### Source-development prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Needed only for source development; included in both desktop apps |
| Docker | 20.10+ | Required for MCP sandbox containers |
| OpenAI-compatible API | — | OpenAI, Ollama, LM Studio, or a compatible proxy |

### Python Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

```
flask>=3.0.0
flask-cors>=4.0.0
openai>=1.30.0
mcp>=2.0.0,<3
```

### Build the Sandbox Image

All MCP servers run inside the active chat or folder's Docker sandbox. Build the image once before starting the app:

```bash
identity="$(python -c 'import build_info; print(build_info.sandbox_identity())')"
docker build --build-arg "LUMEN_SANDBOX_IDENTITY=$identity" -f Dockerfile.sandbox -t lumen-sandbox:latest .
```

In PowerShell, compute the same value with `$identity = python -c "import
build_info; print(build_info.sandbox_identity())"`, then run the Docker command
with `--build-arg "LUMEN_SANDBOX_IDENTITY=$identity"`.

The default is the locally built `lumen-sandbox:latest` image. The packaged
app embeds `Dockerfile.sandbox` and installs its runtimes and packages
inside Ubuntu during first-run setup. A content identity derived only from
`.dockerignore` and `Dockerfile.sandbox` determines whether
the image needs rebuilding, so rebuilding or upgrading desktop-only code does
not invalidate it. The image remains configurable through
`LUMEN_SANDBOX_IMAGE` or advanced settings.

### Build the macOS DMG

On an Apple Silicon Mac running macOS 14 or newer, use Python 3.12 (the
release workflow pins this version so the frozen runtime keeps the macOS 14
deployment target):

```bash
python -m pip install -r requirements-dev.txt -r requirements-desktop.txt
LUMEN_BUILD_VERSION=0.1.0-alpha.1 packaging/build_macos.sh
```

The script renders the current favicon into an `.icns`, generates Finder bundle/build versions from `LUMEN_BUILD_VERSION`, freezes the one-process Waitress/menu-bar app, ad-hoc signs it when no identity is configured, and creates `dist/Lumen-AI-Chat-<version>-apple-silicon.dmg` with a SHA-256 checksum. Set `MACOS_SIGN_IDENTITY` after importing a Developer ID certificate to produce a distribution-signed app.

The release workflow builds and smoke-tests the ARM64 sandbox without publishing it, runs the Python tests, and publishes the DMG. End users build the same pinned sandbox locally on first run, so no container registry account is required.

To verify the sandbox Python runtime and workspace mount, run `python packaging/smoke_sandbox.py --image lumen-sandbox:latest`.

### Build the Windows ZIP

On Windows x64, use an x64 Python 3.12 environment:

```powershell
python -m pip install -r requirements-dev.txt -r requirements-desktop.txt
$env:LUMEN_BUILD_VERSION = "0.1.0-alpha.1"
.\packaging\build_windows.ps1
```

The script generates the icon and executable version resource, freezes the
windowed Waitress/tray host, and archives the complete `onedir` folder. It then
extracts the ZIP to a clean temporary directory and runs the frozen resource
and server smoke mode from that extraction. Successful output is
`dist\Lumen-AI-Chat-<version>-windows-x64.zip` plus a SHA-256 file.

### Production Deployment

For a single-process production-style deployment, use Gunicorn:

```bash
gunicorn -c gunicorn.conf.py "app:create_app()"
```

`gunicorn.conf.py` defaults to **one worker with multiple threads**. Active stream state (cancellation events, reattach buffers) is stored in process memory, so multiple worker processes are not supported until stream state is moved to shared storage.

---

## Configuration

### In-App Settings

Open the settings panel in the browser to configure:

| Setting | Description |
|---|---|
| API Key | Provider API key — stored server-side, never in `localStorage` |
| Base URL | OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` |
| Model | Model ID for the next request |
| System Prompt | Optional instruction prepended to every conversation |

Open the **Container / Advanced Settings** panel to configure:

| Setting | Description |
|---|---|
| Sandbox Image | Docker image name for sandbox containers |
| Container Memory | Memory limit per container (e.g. `512m`, `1g`) |
| Container CPUs | CPU quota per container |
| Container Network | Docker network mode |
| Container Idle Timeout | Seconds before idle containers are stopped; `0` disables |
| Max File Preview Bytes | Maximum bytes loaded for in-browser text file preview |
| Max File List Entries | Maximum workspace directory entries returned |
| Max Upload Bytes | Maximum file upload size |

> If a setting is locked by an environment variable, the field is disabled in the UI and shows which variable controls it.

Local model providers typically use:

```
http://localhost:11434/v1    # Ollama
http://localhost:1234/v1     # LM Studio
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Overrides the saved API key |
| `OPENAI_BASE_URL` | — | Overrides the saved API base URL |
| `OPENAI_API_BASE` | — | Fallback alias for `OPENAI_BASE_URL` |
| `LUMEN_HOST` | `127.0.0.1` | Flask development-server bind address |
| `LUMEN_PORT` | `8080` | Flask development-server port |
| `LUMEN_DEBUG` | disabled | Enable Flask debug mode with `1`, `true`, `yes`, or `on` |
| `LUMEN_OPENAI_TIMEOUT` | `120` | OpenAI-compatible request/read timeout in seconds |
| `LUMEN_MCP_TOOL_TIMEOUT` | `120` | Maximum MCP tool-call duration in seconds |
| `LUMEN_TOOL_APPROVAL_TIMEOUT` | `600` | Maximum wait for a tool approval decision in seconds |
| `LUMEN_CONFIG_FILE` | `~/.lumen/config.json` | Server-side API config path |
| `LUMEN_CONFIG_CACHE_TTL` | `5` | Seconds to cache API config reads |
| `LUMEN_ADVANCED_CONFIG_FILE` | `~/.lumen/advanced_config.json` | Advanced/container settings config path |
| `LUMEN_MCP_CONFIG_FILE` | `~/.lumen/mcp.json` | MCP server config path |
| `LUMEN_MCP_CONFIG_CACHE_TTL` | `5` | Seconds to cache MCP config reads |
| `LUMEN_SANDBOX_IMAGE` | `lumen-sandbox:latest` | Locally built Docker image for sandbox containers |
| `LUMEN_CONTAINERS_ROOT` | `~/.lumen/containers` | Host directory for standalone-chat and shared-folder workspaces |
| `LUMEN_CONTAINER_MEMORY` | `512m` | Memory limit per sandbox container |
| `LUMEN_CONTAINER_CPUS` | `1` | CPU quota per sandbox container |
| `LUMEN_CONTAINER_NETWORK` | `bridge` | Docker network mode |
| `LUMEN_CONTAINER_PREFIX` | `lumen-chat-` | Prefix for generated container names |
| `LUMEN_CONTAINER_IDLE_TIMEOUT` | `600` | Seconds before idle containers are stopped; `0` disables |
| `LUMEN_MAX_CONTENT_LENGTH` | `62914560` | Flask request body cap (bytes) |
| `LUMEN_CORS_ORIGINS` | `http://localhost:8080,...` | Comma-separated allowed origins |
| `LUMEN_MAX_FILE_PREVIEW_BYTES` | `524288` | Max bytes for in-browser text file preview |
| `LUMEN_MAX_FILE_LIST_ENTRIES` | `500` | Max workspace directory entries returned |
| `LUMEN_MAX_UPLOAD_BYTES` | `52428800` | Max file upload size (bytes) |

Environment variables for container/advanced settings (`LUMEN_SANDBOX_IMAGE`, `LUMEN_CONTAINER_*`, `LUMEN_MAX_*`) take the highest precedence and lock the corresponding UI fields so the operator value cannot be overwritten from the browser.

### Persistent Data

All runtime data is stored outside the repo under `~/.lumen/`:

```
~/.lumen/
├── config.json            # Server-side API provider config
├── advanced_config.json   # Container and file-handling settings (written by the UI)
├── mcp.json               # MCP server configuration
├── memory.md              # Persistent cross-chat memory; mounted at /memory.md in every container
├── folders.json           # Folder names, IDs, and folder-level instructions
├── conversations/         # One JSON file per conversation
├── containers/            # Workspace directories for standalone chats and shared folders
└── images/                # Uploaded images keyed by SHA-256 hash
```

### MCP Configuration

MCP servers are configured in `~/.lumen/mcp.json` (or the path set by `LUMEN_MCP_CONFIG_FILE`). The settings panel can read and write this file from the browser.

All MCP servers run inside the active chat or folder's Docker sandbox with the workspace mounted at `/workspace`. Use `/workspace`-relative paths in server arguments.

**Example `mcp.json`:**

```json
{
  "mcpServers": {
    "search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": {
        "BRAVE_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

Per-server UI settings (enabled, auto-approve, icon) are stored in browser `localStorage` under `lumen_mcp_server_settings` — not in `mcp.json`.

Runtime configuration contains only the servers in `mcp.json`. All server IDs are user-configurable, and loading configuration does not rewrite the file. Previously saved server definitions remain editable; remove any obsolete definitions in settings.

Model-facing tool names use the MCP tool name directly. The matching MCP server is sent separately in `mcp_tool_meta`, which lets the backend dispatch the call without adding server prefixes to the visible tool name. Tool descriptions should stay clean and semantic (`tool.description || tool.name`).

---

## Usage

### Basic Chat

1. Run `python app.py` and open `http://localhost:8080`.
2. Create a new conversation from the sidebar.
3. Open settings and enter your API key, base URL, and model.
4. Type a message and press **Enter**. Use **Shift + Enter** for a newline.

### Organizing Chats with Folders

1. Click **New folder** in the sidebar and give the folder a name.
2. Open the folder to create chats, browse its shared workspace, or add folder-level instructions.
3. Use a conversation's menu to move an existing chat into a folder or back to **Recent**.

Each chat retains its own message history, but chats in the same folder use one shared Docker container and `/workspace`. Folder instructions are used in place of the global system prompt for chats in that folder. Deleting a folder permanently deletes its chats, shared container, and workspace files.

### Uploading Files

Open the workspace panel, upload a file, and it will appear under `/workspace/uploads/` inside the active sandbox. Expand directories in the file tree, preview text files in the browser, or download any workspace file directly. When you are working in a folder, every chat in that folder sees the same files.

### Using MCP Tools

1. Build the sandbox image if you have not already, using the content-identity command in [Build the Sandbox Image](#build-the-sandbox-image).
2. Open the MCP settings panel and add a server (command, args, optional env vars).
3. Save the config and click **Load Tools** — a conversation must be open for tool discovery.
4. Send a message asking the model to use a tool.
5. Approve or deny the tool call when prompted (or enable auto-approval per server).

Tool activity is shown inline with the tool name, arguments, running state, and result.

### Cancelling a Stream

Click the stop button while a response is streaming. The server marks the stream as cancelled and saves whatever was already generated.

---

## Architecture

### Backend

| File | Responsibility |
|---|---|
| `app.py` | Flask app factory; startup requirement status handling; CORS; shutdown cleanup |
| `app_config.py` | Server-side API key and provider config storage; env var overrides; safe public metadata |
| `advanced_config.py` | Server-side container and file-handling settings; three-tier priority (env > file > defaults); UI-editable with env-lock support |
| `runtime_requirements.py` | Docker availability and sandbox image checks; streaming build log generator |
| `fs_utils.py` | `atomic_replace` helper for safe temp-file-replace writes; Windows retry logic |
| `docker_path_utils.py` | Cross-platform Docker volume path conversion — translates Windows drive-letter paths to valid Linux container mount targets |
| `routes_startup.py` | Setup screen, health probe, Docker/image requirement checks, streaming sandbox image build |
| `routes_conversations.py` | Conversation and folder CRUD, workspace path, container status, danger-delete |
| `routes_chat.py` | Streaming, cancel, approve, settings, advanced/container settings, model list |
| `routes_mcp.py` | MCP config, tool discovery, direct tool calls |
| `routes_files.py` | Workspace file listing, upload, preview, download, image storage |
| `chat_turn_service.py` | Full chat turn orchestration: streaming, tool approval, MCP calls, persistence |
| `title_service.py` | Auto-generated conversation title: `_SET_TITLE_TOOL` definition, text conversion, extraction, and `generate_title` |
| `tool_approval.py` | Pending tool-approval gate: `_pending_approvals` dict, lock, `request_tool_approval`, `resolve_tool_approval` |
| `streaming.py` | Typed OpenAI streaming event generator; SSE serialization helpers |
| `mcp_service.py` | MCP config, tool discovery, tool invocation; re-exports `_build_server_params` for use by the pool |
| `mcp_session_pool.py` | `McpSessionPool` class: worker coroutine, session lifecycle, retry logic for persistent cross-turn reuse |
| `mcp_adapters.py` | Wraps MCP commands for Docker `exec`; extracts and mounts host volume paths |
| `container_service.py` | Docker container lifecycle: create, start, stop, idle reaping, workspace management |
| `workspace_service.py` | Safe file operations inside the conversation workspace; path traversal protection |
| `store.py` | Filesystem persistence for conversations, folders, and images; cached conversation index |

### Chat Turn Flow

A single chat turn in `chat_turn_service.py`:

1. Build an OpenAI client from server-side config.
2. Read `~/.lumen/memory.md` and inject its contents into the system message.
3. Pre-mount MCP server volumes and ensure the chat or folder sandbox is running.
4. Stream model output via `streaming.py`; accumulate text and tool calls.
5. For each tool call: request approval (unless auto-approved), invoke via the persistent `McpSessionPool` (reused across turns in the active runtime), append the tool result to message history.
6. Loop until the model finishes without further tool calls.
7. Emit `assistant_done` and optionally a generated `title` event.

Partial output is saved during streaming by `TurnRecorder` so that cancelled or interrupted turns are not lost.

### Frontend

The frontend is plain browser ES modules — no build step, no framework. `templates/index.html` imports `static/js/app.js` with `type="module"`.

| Module | Responsibility |
|---|---|
| `state.js` | Single shared mutable state object |
| `app.js` | Bootstrap, event binding, startup loading |
| `chat_send.js` | Send flow, stream start, SSE event handling |
| `chat_payloads.js` | Builds API message payloads including images and file context |
| `chat_attachments.js` | Pending image and file attachment lifecycle |
| `chat_edit.js` | Edit, resend, and regenerate helpers |
| `chat_branches.js` | Branch snapshots and branch switching for edits/regenerations |
| `stream_consumer.js` | SSE response reader |
| `renderer.js` | Re-exports all public symbols from renderer sub-modules; sole import target for existing callers |
| `renderer_core.js` | `scrollToBottom`, `stickToBottom`, `messagesEl`, `createMessageRow` |
| `renderer_groups.js` | Block grouping, `tryGroupBlock`, `updateGroupLabel`, `attachCollapsible`, `prepareAssistantRow` |
| `renderer_thinking.js` | `createThinkingBlock`, `updateThinkingBlock`, `finalizeThinkingBlock`, `appendThinkingBlock` |
| `renderer_attachments.js` | `normalizeContentAttachments`, `renderAttachmentCard`, `getRawText`, `appendContentParts` |
| `renderer_tools.js` | Tool strip states, `cancelAllToolApprovals`, `appendToolResultInline` |
| `renderer_actions.js` | Copy/edit/regenerate buttons and branch arrows in message footers |
| `mcp.js` | MCP config UI, tool loading, enable/auto-approve toggles |
| `file_panel.js` | Workspace browser, preview, and download |
| `conversations.js` | Conversation/folder CRUD, search, folder home, and sidebar rendering |
| `settings.js` | API and chat settings UI |
| `markdown.js` | Markdown, code highlighting, KaTeX, safe workspace file links |
| `tool_adapters/` | Per-tool display adapters (`exa.js`; other tools use the generic renderer) |

The frontend maintains two parallel histories: `state.messages` (model/API-facing) and `state.displayLog` (UI-facing). These have different structures and indices — do not conflate them. Branches for edited messages and regenerated responses are stored in `displayLog` so the visible conversation can switch paths while preserving the saved model history.

---

## Testing

Install test dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the full suite:

```bash
pytest
```

Tests are fully isolated: `conftest.py` redirects filesystem paths to `tmp_path`, patches the runtime requirement check in the app factory, and stubs container operations. No running Docker daemon, API key, or live server is required.

| Test file | What it covers |
|---|---|
| `test_app_config.py` | Config load/save, env overrides, public config, atomic persistence |
| `test_store.py` | SHA-256 image naming, conversation/folder CRUD, shared runtimes, cached index, concurrency |
| `test_workspace_service.py` | Path traversal rejection, preview limits, upload collision handling |
| `test_chat_turn_service.py` | Tool approval, title extraction, `TurnRecorder` throttle/finalize |
| `test_streaming.py` | Event ordering, tool delta accumulation, parallel tools, cancellation |
| `test_mcp_service.py` | Config cache, malformed config, `McpSessionPool` same-task cleanup |
| `test_mcp_adapters.py` | Docker exec params, project-root detection, volume deduplication |
| `test_container_service.py` | Container naming, exec argv/env, name conflicts, idle reaper |
| `test_routes.py` | HTTP routes, folder/shared-workspace behavior, error paths, conversation update whitelist |
| `test_tool_approval.py` | Approval gate: request/resolve lifecycle, concurrent approvals, cancel-event unblocking |
| `test_title_service.py` | Title tool definition, message-to-text conversion, title extraction from model response |

Also lint frontend modules after any JS changes:

```bash
find static/js -name '*.js' -print0 | xargs -0 -n1 node --check
```

---

## Contributing

### Workflow

```bash
git clone https://github.com/yossifibrahem/lumen-ai-chat.git
cd lumen-ai-chat
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
git checkout -b feature/your-change
```

### Codebase Guidelines

- Keep Flask route handlers thin — business logic lives in service modules.
- Keep workspace path safety checks in `workspace_service.py`; never weaken traversal restrictions.
- Keep persistent data access inside `store.py` where possible.
- Do not send API keys in chat or model request bodies — use `app_config.py` and `/api/settings`.
- Avoid introducing a frontend build step unless the project intentionally adopts one.
- Keep browser code modular under `static/js/`; prefer adding tool adapters over hardcoding tool names in renderer modules.
- Update both `README.md` and `devs.md` when changing architecture, setup, configuration, or agent-facing behavior.

### Pull Request Checklist

- All tests pass (`pytest`) and frontend modules lint cleanly (`node --check`).
- Describe what changed and why; include manual test steps and results.
- Keep unrelated formatting changes out of functional PRs.
- Confirm the app still starts locally with Docker running.
- Confirm no API keys, conversation data, or local workspace files are committed.

### Reporting Issues

When filing a bug, please include the OS, browser and version, whether this is a desktop or source build, whether Docker is installed and running, reproduction steps, expected and actual behavior, and any relevant Lumen log or browser console output.

---

## Known Limitations

**Active stream reattach is process-local.** Cancellation events and stream replay buffers are stored in process memory (`routes_chat.py`). This works fine with the default single-worker Gunicorn config but will not work across multiple worker processes. Long-term fix: move stream state to Redis or a shared broker.

**No authentication.** Lumen is local-first and not hardened for public exposure. Do not deploy it publicly without adding authentication, rate limiting, and stricter CORS.

**No database or persistent-data upgrade layer.** Keep conversation JSON shapes stable between releases.

---

## License

MIT — built for people who like local control, readable code, and AI tools that do not require a 14-step deployment ritual.
