# Lumen AI Chat

A local-first AI chatbot that gives the LLM its own computer inside Docker, separate from the host machine, with streaming, shared Folder/Project workspaces, MCP support, and a zero-build-step frontend.

Lumen is distributed as an installable desktop application for normal users while keeping a straightforward source-development workflow. The backend is Flask, and the existing browser UI remains the product interface rather than being replaced by Electron or a native chat window.

<img width="2940" height="1662" alt="Untitled design" src="https://github.com/user-attachments/assets/f4d3b00f-a986-4081-bd41-b74ed4afbdf0" />

## Current Product Direction

- **Desktop experience:** small macOS menu-bar and Windows tray applications run Lumen locally and open the same chat UI in the user's default browser.
- **User prerequisite:** Docker Desktop is the only external runtime users install. Python, Node.js, npm, Git, and the Lumen source tree are included or handled internally.
- **Sandbox installation:** the app bundles `Dockerfile.sandbox`. After explicit confirmation on first run, Docker pulls `ubuntu:24.04`, installs the sandbox runtimes and packages, and creates the local `lumen-sandbox:latest` image.
- **Execution and sharing:** model commands run inside Docker. Standalone chats have separate workspaces; chats in a Folder/Project share one. File sharing is explicit, and networking is managed directly through Docker. Share files through uploads or explicit host-directory mounts during Folder/Project creation. Mounted directories default to read-only; read-write sharing allows edits and deletion of original host files. Sharing cannot currently be changed after creation.
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
- Persistent MCP stdio session pooling — sessions are opened per chat or shared Folder runtime and reused across turns until the container stops
- Approve or deny individual tool calls; enable auto-approval per server
- Tool activity renders inline: arguments, running state, and results

**Persistent memory**

- The model remembers facts across all conversations via `~/.lumen/memory.md`
- Enabled by default, optional global memory stays outside Docker. The built-in `update_memory` tool saves concise facts using revision checks and atomic writes.
- Container settings let you inspect, edit, clear, and disable memory. Disabling preserves it, stops context injection and removes its tool on subsequent model requests; chat history already containing facts remains.
- Memory is limited to 32 KiB, with eight recoverable prior versions in `memory.history.json`. Existing content is preserved. Legacy container memory mounts cause a one-time compatibility reset on next use: workspace files remain, installed packages reset. Memory toggles never reset computers.
- Memory contents are injected into the system prompt at the start of every turn

**Folders**

- Group related conversations into folders and open a folder home with its chats and shared files
- Create, rename, delete, and search folders; move existing conversations into or out of a folder
- Mount host directories during creation or from **Folder settings**, with a read-only toggle for each directory
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
- Preview text/code/Markdown files inline; download workspace files up to 50 MiB
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

Source development requires Python 3.10+ and Docker running Linux containers.
Normal desktop users install only Docker Desktop. Folder mounts with read-only
access require Docker support for `bind-recursive=readonly` and a daemon kernel
of 5.12 or later.

```bash
# 1. Clone
git clone https://github.com/yossifibrahem/lumen-ai-chat.git
cd lumen-ai-chat

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Build the Docker sandbox image (required for native and MCP tools)
identity="$(python -c 'import build_info; print(build_info.sandbox_identity())')"
docker build --build-arg "LUMEN_SANDBOX_IDENTITY=$identity" -f Dockerfile.sandbox -t lumen-sandbox:latest .

# 5. Start the app
python app.py
```

Open **http://localhost:8080**, then open the settings panel to enter your API key, base URL, and model name.

The development server binds to `127.0.0.1` with debug mode disabled by default. For deliberate LAN testing on a trusted network, set `LUMEN_HOST=0.0.0.0`, configure `LUMEN_ALLOWED_HOSTS` and `LUMEN_CORS_ORIGINS`, and open `http://<your-computer-ip>:8080` from the other device.

> Source and desktop runs use the local `lumen-sandbox:latest` image. The desktop app contains the complete Docker build context, so first-run setup needs Docker Desktop but no host Python, Node.js, Git, or source checkout.

### Configuring tools

The built-in tools `bash`, `view`, `write`, and `edit` are provided by Lumen itself, with server-owned schemas and container-only execution. They work without an MCP server. Optional MCP servers provide additional tools; their code and dependencies must be available inside the container. Node.js, Python, `uv`, and `mcp-remote` are bundled for these integrations. MCP discovery returns only additional tools, while native tools are injected directly into each chat request.

## Installation

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
inside Ubuntu during first-run setup. A content identity derived from
`.dockerignore`, `Dockerfile.sandbox`, `sandbox_tools.py`, and `sandbox_files.py` determines whether
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
| Turn duration / tool calls | Defaults: 900 seconds / 100 calls per turn |
| Storage safeguards | Defaults: minimum 512 MiB free; maximum 10 GiB per managed workspace |
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
| `LUMEN_CONTAINER_PREFIX` | `lumen-chat-` | Prefix for generated container names |
| `LUMEN_CONTAINER_IDLE_TIMEOUT` | `600` | Seconds before idle containers are stopped; `0` disables |
| `LUMEN_MAX_CONTENT_LENGTH` | `62914560` | Flask request body cap (bytes) |
| `LUMEN_CORS_ORIGINS` | `http://localhost:8080,...` | Comma-separated allowed origins |
| `LUMEN_ALLOWED_HOSTS` | loopback hostnames | Additional comma-separated hostnames allowed to serve the local UI |
| `LUMEN_DESKTOP_PORT` | `38492` | Loopback port for the desktop UI |
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
├── memory.md              # App-managed cross-chat memory, never mounted into containers
├── folders.json           # Folder names, IDs, instructions, and explicit directory mounts
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

1. Click **New folder** in the sidebar and give the folder a name. Optionally use **Browse…** to select host directories, or enter an absolute host path and click **Add path**. Each directory defaults to **Read only**; turn it off to let the LLM modify the original host files.
2. Open the folder to create chats, browse its shared workspace, or add folder-level instructions.
3. Use a conversation's menu to move an existing chat into a folder or back to **Recent**.
4. After creation, **Folder settings** displays saved paths and permissions without editing controls. Names and instructions remain editable, including when a shared directory is disconnected. Restore unavailable directories before running tools.

Each chat retains its own message history, but chats in the same folder use one shared Docker container and `/workspace`. Folder instructions are used in place of the global system prompt for chats in that folder. The folder retains its files when its last chat is deleted or moved out. Moving a chat switches workspaces without copying files. Deleting a folder permanently deletes its chats, shared container, and managed workspace files. Mounted host directories are kept. Shared directories appear at `/workspace/mounts/<directory-name>` for all chats and the file panel. Docker Desktop must have permission to access the selected host paths. Browse opens a native chooser on the machine running Lumen (macOS/Windows; Linux requires Zenity or KDialog).

### Uploading Files

Open the workspace panel, upload a file, and it will appear under `/workspace/uploads/` inside the active sandbox. Expand directories in the file tree, preview files in the browser, or download workspace files up to 50 MiB directly. Previews support text, rendered Markdown and HTML, images (PNG, JPEG, GIF, WebP, BMP, TIFF), PDFs, and Jupyter notebooks. PDFs show the complete document in an app-styled continuous preview, with pages rendered near the viewport; notebooks show twenty cells with saved text and image outputs, without executing code. Previous/Next controls navigate longer notebooks. PDF previews show only rendered pages. Notebook previews also offer a text view and Copy for the displayed batch. When you are working in a folder, every chat in that folder sees the same files.

### Using MCP Tools

1. Build the sandbox image if you have not already, using the content-identity command in [Build the Sandbox Image](#build-the-sandbox-image).
2. Open the MCP settings panel and add a server (command, args, optional env vars).
3. Save the config and click **Load Tools**. Without an open chat, discovery uses a temporary discovery runtime that stops after listing tools.
4. Send a message asking the model to use a tool.
5. Approve or deny the tool call when prompted (or enable auto-approval per server).

Tool activity is shown inline with the tool name, arguments, running state, and result. Failed MCP transports are reported without automatically replaying the tool call; a later request can reopen the connection.

### Cancelling a Stream

Click the stop button while a response is streaming. The server marks the stream as cancelled and saves whatever was already generated.

---

## Built-in sandbox tools

Every chat has four server-provided tools, even with no MCP servers configured:

| Tool | Arguments | Result |
| --- | --- | --- |
| `bash` | `command`, optional `timeout` (1–600 seconds; default 120) | Combined command output and exit status, bounded to 32 KiB. |
| `view` | `path`, optional `range` (`start:end`, inclusive and one-based) | Text lines, directory listing, image, PDF pages, or notebook cells. |
| `write` | `path`, `content` | Creates parents and writes UTF-8 text, including empty files. |
| `edit` | `path`, `old_text`, `new_text`, optional `replace_all` | Exact replacement; ambiguous matches fail unless explicitly replacing all. |

Relative paths start at `/workspace`; absolute paths are **container paths**. Text reads return at most 200 lines/32 KiB; directory reads list two levels and skip dotfiles and `node_modules`. PDF reads default to one page and allow up to five; notebooks default to 20 cells and never execute code. Images and PDF page renders require a vision-capable model. Write/Edit accept at most 1 MiB; use Bash for larger transformations.

Built-in tools run automatically. Bash uses a fresh shell each time: files and installed packages persist in the container, while shell variables and directory changes do not. There are no managed background jobs. Stop/timeout may stop the shared container to terminate every descendant, interrupting other activity in that Folder. Workspace files survive container recreation; installed packages in the container layer do not.

### Isolation and sharing

Standalone chats have separate workspaces. Chats in a Folder share a runtime and workspace. The application mounts its managed workspace and host directories explicitly selected during Folder creation. **MCP arguments never authorize host mounts**: install server code inside the container and use container paths. Explicit directory sharing belongs only to a Folder/Project; standalone chats and MCP configuration cannot add host mounts. Read-only permissions are enforced recursively by Docker bind mounts. This requires a Docker Engine with `bind-recursive=readonly` support and a daemon Linux kernel of 5.12 or later (including the Linux VM used by Docker Desktop). Unsupported engines fail rather than falling back to writable submounts.

Commands run as container root, with reduced capabilities, no-new-privileges, CPU/memory limits and a 256-process limit. Docker chooses the normal default networking for new containers; existing Docker-managed network attachments are preserved, including reachable host/LAN services. The app supplies no network override and ignores obsolete saved network preferences and environment values. This is not a network air gap, a disk quota, or a guarantee against Docker/kernel vulnerabilities. Do not expose the Docker socket or run privileged containers. Local API requests require a same-origin browser session and a CSRF token; additional served hostnames must be explicitly listed in `LUMEN_ALLOWED_HOSTS`.

The file panel transfers files through the container rather than following sandbox-created symlinks on the host. Uploads and downloads are bounded to 50 MiB per file. Text preview limits default to 512 KiB (maximum 4 MiB); directory listings default to 500 entries (maximum 5000). Visual previews use a 20 MiB input limit. Images and notebook outputs reuse the View worker’s 1600-pixel visual bounds and bounded text; PDFs display the original file without text extraction. Source views display up to 2000 lines or 128 Ki characters with an explicit shortening notice; Copy retains the loaded text and Download saves the original file. Smaller upload limits are supported. Unchanged previews reuse their loaded content after a file-version check, avoiding repeated reads, image conversion, and notebook parsing. Uploads, downloads, and PDF ranges transfer raw bytes after a small JSON metadata header; visual previews use bounded JSON/base64 transport. Transfers remain buffered and bounded by the file size limits. Tool images are stored separately per conversation; raw media is expanded only when constructing the model request.

After updating, rebuild the sandbox image through the startup screen. Helper sources participate in the sandbox identity, so older containers are recreated automatically. Native tool names `bash`, `view`, `write`, and `edit` are reserved; rename conflicting MCP tools.

If a workspace reaches a storage limit, the file panel remains available for browsing,
downloads, and explicit deletion of managed files or directories. Delete asks for
confirmation and does not follow symlinks or delete mounted host directories. Uploads
and ordinary agent execution remain blocked until space is recovered. Rebuild the
sandbox image after updating the file worker to enable this recovery action.

---

## Architecture

The browser manages conversations, approvals, and file transfers through Flask.
Flask sends provider requests and manages Docker; model commands, file tools, and
MCP servers execute inside the selected chat or Folder/Project container.
The file panel accesses `/workspace` through a container worker, never by following
container-created paths on the host. See [the developer guide](devs.md#code-map)
for the module map and runtime ownership rules.

### Chat Turn Flow

A single chat turn in `chat_turn_service.py`:

1. Build an OpenAI client from server-side config.
2. When enabled, inject bounded global memory as remembered data subordinate to instructions, along with its revision. Each subsequent model request rechecks the preference.
3. Resolve the chat or Folder runtime; tools start its sandbox when needed. MCP paths never create host mounts.
4. Stream model output via `streaming.py`; accumulate text and tool calls.
5. Validate each tool call’s JSON arguments. Native tools run inside the runtime automatically; additional MCP tools follow their approval settings and use the runtime’s persistent session pool. Append one result per call, including denied, failed, or cancelled calls.
6. Loop until the model finishes without further tool calls.
7. Optionally generate a title, save the completed or interrupted history, then emit `assistant_done` with that history. The terminal SSE marker releases the composer for another turn.

Partial output is saved at most once per 0.75 seconds by `TurnRecorder`, with forced saves at tool boundaries and completion. Cancellation and model errors preserve partial output. One API client is reused and closed for each turn; conversation JSON is stored as compact UTF-8. Uploaded images stay as small references in the browser and are expanded server-side only for provider requests. Errors preserve partial replies and are recorded in the display log. SSE replay uses encoded snapshots, skips resolved approval prompts, and sends keep-alives while waiting for tools or approvals.

### Frontend

Flask serves plain browser ES modules from `templates/` and `static/`, with no
frontend build step. `state.messages` holds model history; `state.displayLog`
holds rendered activity and conversation branches. They use different structures
and indices. File previews and tool renderers share source and media components.

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

Also lint frontend modules after any JS changes:

```bash
find static/js -name '*.js' -print0 | xargs -0 -n1 node --check
```

Real Docker tests are opt-in:

```sh
docker build --build-arg LUMEN_SANDBOX_IDENTITY="$(python -c 'import build_info; print(build_info.sandbox_identity())')" -f Dockerfile.sandbox -t lumen-sandbox:builtins-test .
LUMEN_RUN_DOCKER_TESTS=1 python -m pytest tests/test_builtin_docker.py
python packaging/smoke_sandbox.py --image lumen-sandbox:builtins-test
```

These tests use disposable containers and temporary workspaces; the package-install test accesses public apt/pip/npm registries.

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
- Keep file panel path safety checks in `sandbox_files.py`; never follow container-created workspace paths on the host.
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

**Active stream reattach is process-local.** Cancellation events and stream replay buffers are stored in process memory (`routes_chat.py`). This works fine with the default single-worker Gunicorn config but will not work across multiple worker processes. Run one worker process with multiple threads.

**Local application.** Lumen uses same-origin sessions and CSRF protection, with no multi-user login system. Run it on loopback for normal use.

**No database or persistent-data upgrade layer.** Keep conversation JSON shapes stable between releases.

---

## License

MIT — built for people who like local control, readable code, and AI tools that do not require a 14-step deployment ritual.


### Persistence and execution safeguards

Stop/start, idle shutdown and normal app restarts reuse compatible containers and retain installed packages. An image, mount, resource or isolation mismatch can require recreation at an idle boundary; workspace files remain but installed packages and other container-layer state reset. CPU, memory, PID limits, capability restrictions, no-new-privileges and namespace/device isolation are checked before reuse, including name-conflict recovery. There are no environment snapshots.

Moving a standalone chat into a Folder requires an explicit warning/confirmation: its old workspace files are permanently deleted and its environment reset; its message history and the destination Folder's files remain. Cancelling changes nothing. Moving out of a Folder preserves that Folder's shared workspace.

Execution safeguards default to 900 seconds and 100 tool calls per turn, 512 MiB minimum free space, and 10 GiB per managed workspace. Override these on the server with `LUMEN_TURN_TIMEOUT_SECONDS`, `LUMEN_TURN_TOOL_LIMIT`, `LUMEN_MIN_FREE_DISK_BYTES`, and `LUMEN_MAX_WORKSPACE_BYTES`. Checks run before work and approximately every two seconds during execution, including MCP waits. Managed storage accounting skips symlink targets and host shares. Docker filesystem free/used space is checked inside running containers when observable, since Docker Desktop may use a separate VM disk. Unavailable Docker storage telemetry does not imply unlimited space. These are best-effort safeguards, not hard quotas; fast writers can exceed thresholds between checks.

Reaching limits cancels execution and may stop a shared runtime to terminate descendants. This can interrupt other activity in the Folder. Existing files and chat history are retained; the app never deletes user artifacts or shared host files to recover space. Model output is bounded to 2 MiB per response/turn, MCP tool text to 64 KiB, and replay to 8 MiB per active stream. On replay exhaustion the turn stops and the browser reloads authoritative saved history. Ordinary built-in output limits remain in force.
