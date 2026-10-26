# Lumen developer guide

## Application model

Lumen gives the model a local computer inside Docker, separate from the machine
running Flask. The browser and Flask manage chats, settings, approvals, and file
transfers. Model commands and MCP server processes execute inside the container.
Relative tool paths start at `/workspace`; absolute tool paths refer to the
container filesystem. Native tools may use the rest of that filesystem, install
packages, and access the network under Docker's normal bridge settings.

A standalone chat owns one runtime. Chats in a Folder/Project share one runtime
and `/workspace`, with separate histories and Folder instructions. The Folder owns
its files even when it has no chats. Deleting or moving its last chat must preserve
that workspace. Deleting the Folder explicitly deletes its chats and workspace.
Moving a chat switches its runtime; it does not copy files between workspaces.

Sharing is explicit: uploads enter `/workspace/uploads/`. The app mounts its
managed workspace and shared `/memory.md`. Explicit host directory mounts belong only to Folder/Project settings, persist in `folders.json`,
and appear at `/workspace/mounts/<name>`. They default to Docker-enforced recursive
read-only access (`bind-recursive=readonly`, daemon Linux kernel 5.12+). Reuse checks
verify the recursive mount policy as well as source, destination, and RW flags.
Permission changes remove the old sandbox successfully before saving settings;
Docker failures return an error and preserve the previous configuration. Mount
changes require idle chats and operations and preserve the workspace and original
host files. Host sources must exist;
missing sources fail instead of being silently created. Native selection uses
macOS AppleScript, Windows FolderBrowserDialog, or Linux Zenity/KDialog, with a
manual path fallback. MCP arguments and environment values are container values, never authorization to mount or execute host paths.

Docker networking defaults to `bridge`; `none` or a named Docker network may be
configured. Host and shared-container network namespaces are rejected. Reachable
host/LAN services are still reachable through bridge networking.

## Run and verify

Use Python 3.10+ and a running Docker daemon with Linux containers. The packaged
desktop app includes its Python runtime; source development requires dependencies:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python app.py
```

The development server listens on `127.0.0.1:8080`. The desktop launchers use
Waitress on loopback port 38492 and open the same browser UI. Startup presents a
setup screen if Docker or the sandbox image is unavailable. Docker startup and
image installation require the setup page's explicit confirmation.

```sh
python -m pytest
find static/js -name '*.js' -print0 | xargs -0 -n1 node --check
```

The default tests mock Docker/API transport and use temporary storage. Real Docker
tests are opt-in and create disposable containers; the package installation test
uses apt/pip/npm registries:

```sh
docker build --build-arg LUMEN_SANDBOX_IDENTITY="$(python -c 'import build_info; print(build_info.sandbox_identity())')" -f Dockerfile.sandbox -t lumen-sandbox:builtins-test .
LUMEN_RUN_DOCKER_TESTS=1 python -m pytest tests/test_builtin_docker.py
python packaging/smoke_sandbox.py --image lumen-sandbox:builtins-test
```

## Code map

| Module | Responsibility |
| --- | --- |
| `app.py` | App factory, startup cleanup, idle reaper startup, bounded shutdown |
| `request_security.py` | Allowed hostnames, same-origin session, CSRF protection |
| `routes_startup.py`, `runtime_requirements.py` | Setup page, Docker checks, image installation |
| `routes_chat.py` | Turn workers, SSE replay, cancellation, approvals, settings |
| `chat_turn_service.py` | Model/tool loop, memory injection, throttled turn persistence |
| `streaming.py` | Typed model deltas, tool argument accumulation, SSE encoding |
| `builtin_tools.py` | Server-owned schemas, validation, Docker tool dispatch |
| `sandbox_tools.py` | Container-only Read/Write/Edit implementation |
| `sandbox_runtime.py` | Shared runtime serialization, bounded subprocess I/O, cancellation |
| `container_service.py` | Runtime identity, Docker lifecycle, mounts, idle cleanup |
| `container_files.py` | Host transport and upload name/size checks for the file panel |
| `sandbox_files.py` | Container-only listing, preview, upload, download, path checks |
| `routes_files.py` | File panel and image/media HTTP endpoints |
| `folder_mounts.py` | Folder mount validation, runtime mount lookup, native directory chooser |
| `routes_conversations.py`, `store.py` | Chat/Folder CRUD, runtime ownership, atomic JSON persistence |
| `mcp_adapters.py` | Wrap explicit MCP commands and environment for `docker exec` |
| `mcp_service.py` | MCP config, discovery, one-shot calls, persistent pool registry |
| `mcp_session_pool.py` | Persistent stdio connections on a dedicated async worker |
| `tool_approval.py` | Pending MCP approval requests and cancellation |
| `tool_media.py` | Conversation-scoped tool images and model request expansion |
| `memory_service.py` | Shared memory template and bounded prompt reads |
| `title_service.py` | Optional title generation |
| `app_config.py`, `advanced_config.py` | API credentials and runtime/file settings |
| `docker_cli.py`, `docker_path_utils.py` | Docker executable resolution and platform paths |
| `build_info.py` | Desktop version and sandbox content identity |

## Runtime lifecycle

`store.runtime_id()` returns the chat ID or `folder_<folder_id>`. Use that ID for
containers, workspaces, operation locks, and MCP pools; use the chat ID for message
history, streams, and tool media.

`ensure_container()` serializes creation per runtime. Existing container metadata
is read with one inspect call; a second call resolves the configured image ID.
Both labels (image reference and sandbox identity), the actual image ID, and the
complete bind mount set must match before reuse. Old images or unexpected mounts
cause recreation. Workspaces survive recreation; packages installed into the
container's writable layer do not.

Native tools, file panel operations, and MCP calls serialize through the same
runtime lock. The idle reaper acquires that lock without waiting and rechecks the
activity timestamp before stopping a runtime. Locks are weakly held so unused
runtime IDs do not accumulate lock objects. Idle containers stop after the
configured timeout (default 600 seconds); their persistent MCP pools are closed.
Discovery uses `mcp-discovery`, which is stopped after listing tools. Empty Folder
runtimes remain known during startup cleanup.

App shutdown closes MCP pools and kills running app containers concurrently within
one deadline. Containers remain available for restart. Stale containers are
removed on startup. Bind mount contents remain under the app's data directory.

## Tools and file transport

Every turn receives the built-in `bash`, `read`, `write`, and `edit` schemas. These
names are reserved and conflicting MCP discovery fails. Native tools run
automatically within the user's request. MCP approvals remain configurable.

Bash runs a fresh noninteractive shell, with a default timeout of 120 seconds and
maximum of 600. Its supervisor removes background descendants after the shell
exits. Cancellation, timeout, abnormal termination, or retained transport pipes
can stop the shared runtime. Other chats in that Folder may be interrupted.

Read returns bounded text, two-level directory listings, images, PDF pages, or
saved notebook cells; notebooks are never executed. Write/Edit accept at most
1 MiB of UTF-8 text. Edit requires exact matches and checks file fingerprints
before replacing content. `/memory.md` is updated in place because it is a bind
mount. See the tool schemas in `builtin_tools.py` for precise limits.

The file panel is deliberately confined to `/workspace`. Its worker resolves
paths inside Docker, rejects traversal and escaping symlinks, rejects special
files, and bounds reads. The host must never follow container-created workspace
symlinks to implement preview/download/upload. Tests exercise the real worker
with a temporary root, replacing only the Docker transport.

Preview and listing settings are passed in each worker request. Preview defaults
to 512 KiB (maximum 4 MiB); listings default to 500 entries (maximum 5000). Uploads
and downloads are limited to 50 MiB per file; smaller upload limits are supported.
The HTTP request limit defaults to 60 MiB total. JSON/base64 transport is bounded,
but large transfers temporarily require more memory than their raw file size.
JSON response ceilings account for escaped preview text and listing metadata.

## Model turns and persistence

A turn owns one API client and closes it after completion or failure. It reads up
to 32 KiB of shared memory, adds native tools, streams model output, executes tool
calls, and repeats until the assistant finishes. Uploaded and tool images are
persisted by reference and expanded server-side only when constructing the model request. Do
not fetch/re-encode stored images in the browser or cache their base64 data there.

`TurnRecorder` saves partial output at most once per 0.75 seconds, with forced
writes at tool boundaries and finalization. Model stream errors preserve partial
text and clear the active stream marker. Every emitted tool call gets a matching
result, even if validation, approval, execution, or cancellation prevents it from
running. Invalid arguments never execute with empty defaults. After optional title
generation, finalization saves history before sending `assistant_done` for success,
failure, or cancellation. The browser consumes that history before saving branches
and keeps the composer busy until the terminal SSE marker. Conversation JSON uses
explicit UTF-8 encoding on every platform; existing indented JSON remains readable.

`routes_chat.py` stores active replay events and cancellation state in process
memory. Keep one application worker process; use threads for concurrency.
`gunicorn.conf.py` supplies one worker and four threads. Replay buffers retain
encoded active-turn events until completion; encoding once freezes histories and
avoids repeating JSON serialization for each reader. Completed approval prompts
become replay comments without shifting cursors. Keep-alives maintain idle SSE
connections. Approval controls never block the reader: timeouts and results must
still be consumed. Pending decisions survive navigation and are scoped to the
current turn; stopping one chat must not deny another chat’s tools. No database,
cross-process stream broker, or persistent background job service is present.

## MCP configuration and session ownership

Configure servers in the UI or `~/.lumen/mcp.json`:

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

Commands and scripts must exist inside the container. Only explicit configured
environment variables are forwarded to the container, plus `PWD=/workspace`.
Host environment variables are used by the host Docker client. Tildes in MCP
environment values are passed literally; use absolute container paths when needed.

Persistent pools reuse stdio sessions across turns within the runtime. AnyIO
contexts must open, initialize, invoke, and close in the same async worker task.
Keep the queue-based worker model and close contexts in reverse order. Failed
initialization must close partially opened resources. Failed tool transports are
not automatically retried: a tool may already have produced side effects. The
failed session is discarded and a later request can open a fresh connection.

## Storage, configuration, and releases

User state is under `~/.lumen/`: conversations, folders, images, workspaces,
`config.json`, `advanced_config.json`, `mcp.json`, and `memory.md`. File paths can
be overridden by the environment variables documented in README. API keys remain
server-side; browser settings and model request bodies do not carry them.
Configuration precedence is environment, saved JSON, then defaults. The advanced
settings UI marks values locked by environment variables.

Desktop releases build from `.github/workflows/release.yml`. macOS ships an Apple
Silicon macOS 14+ DMG; Windows ships an x64 PyInstaller `onedir` ZIP that must remain
intact. See README for build commands and `WINDOWS-PACKAGING-LESSONS.md` for Windows
runtime and signing constraints. Packaging includes both container workers,
`Dockerfile.sandbox`, and `.dockerignore`. All participate in sandbox identity;
worker changes require rebuilding the image. Desktop-only changes do not change
sandbox compatibility.

Lumen is a trusted local application, not an authenticated multi-user service.
Docker runs model processes as container root with reduced capabilities,
no-new-privileges, CPU/memory limits, and a 256-process limit. Shared workspaces
and memory are writable by their runtimes. There is no disk quota or network air
gap. Keep the Docker socket and unrelated host paths out of container mounts.
