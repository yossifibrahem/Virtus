# Agent Guide for Lumen AI Chat

This file is the working guide for agents modifying this repository. It reflects the current post-cleanup codebase, including server-side API provider settings, typed internal streaming events, cached conversation indexing, persistent cross-turn MCP session pooling, and chat branching for edited messages/regenerated responses.

## Project at a glance

Lumen is a self-hosted Flask chat UI for OpenAI-compatible chat-completions APIs. It supports:

- Streaming responses over Server-Sent Events (SSE)
- OpenAI-compatible API base/model/temperature settings from the browser UI
- Server-side API key storage through `app_config.py` and `/api/settings`
- Local filesystem conversation persistence under `~/.lumen/`
- Per-conversation workspace directories mounted as `/workspace`
- Docker sandbox containers per conversation
- MCP server configuration through `mcp.json` and the UI
- MCP tool discovery, model-facing tool payloads, tool-call execution, approval/deny UI, and tool-result rendering
- Persistent cross-turn MCP stdio session reuse through `McpSessionPool`
- Image uploads stored by content hash
- Regular file uploads stored in the conversation workspace
- Markdown, code highlighting, KaTeX, voice input, theming, conversation search, and branch navigation for edits/regenerations

The app is intentionally lightweight: no database, no frontend framework, no bundler/build step, and plain browser ES modules served directly by Flask.

## Product and packaging direction

Treat these as release invariants unless the product direction is explicitly changed:

- The end-user UI stays in the default browser. The macOS menu-bar and Windows tray applications are launchers and bundled backend runtimes, not Electron or native chat clients.
- Docker Desktop is the only end-user prerequisite. Each desktop package contains Python, backend dependencies, web assets, and `Dockerfile.sandbox`.
- First-run setup builds `lumen-sandbox:latest` locally after user confirmation. Do not add a prebuilt registry-image pull requirement.
- MCP servers are entirely user-configured. Do not inject a default server or reserve a server ID. A replacement Python server must be supplied and configured separately.
- User state remains in `~/.lumen/`. App replacement and image/container recreation must preserve conversations, settings, memory, and mounted workspaces.
- Distribution targets are Apple Silicon macOS 14+ DMG and portable Windows x64 PyInstaller `onedir` ZIP. Intel macOS, ARM64 Windows, installers, signing, and automatic updates are deferred.

## Repository map

```text
.
├── app.py                         # Flask app factory, startup checks, CORS, shutdown cleanup
├── build_info.py                  # Source/frozen version, image, resource-root, desktop-port metadata
├── docker_cli.py                  # Finder-safe Docker CLI discovery and invocation
├── desktop_launcher.py            # macOS rumps menu bar + single-process Waitress launcher
├── windows_desktop_launcher.py    # Windows tray + named mutex + Waitress launcher
├── app_config.py                  # Server-side API provider config and API key storage
├── advanced_config.py             # Server-side container/file settings; env-lock support; written by UI
├── runtime_requirements.py        # Docker availability + sandbox image checks; streaming build log
├── fs_utils.py                    # atomic_replace helper for safe temp-file writes (Windows retry logic)
├── docker_path_utils.py           # Cross-platform Docker volume path conversion for Windows host support
├── routes_startup.py              # Setup screen, health, Docker/image checks, streaming sandbox build
├── routes_conversations.py        # Conversation CRUD, workspace path, container status, danger-delete
├── routes_chat.py                 # Streaming, cancel, approve, settings, advanced/container settings, model list
├── routes_mcp.py                  # MCP config, tool discovery, direct tool calls
├── routes_files.py                # Workspace file listing, upload, preview, download, image storage
├── chat_turn_service.py           # Long-running chat turn orchestration; re-exports resolve_tool_approval
├── title_service.py               # Auto-generated title: _SET_TITLE_TOOL, _messages_to_text, _extract_title, generate_title
├── tool_approval.py               # Approval gate: _pending_approvals dict, lock, request_tool_approval, resolve_tool_approval
├── streaming.py                   # Typed OpenAI streaming event generator + SSE helpers
├── mcp_service.py                 # MCP config, tool discovery, invocation; re-exports _build_server_params
├── mcp_session_pool.py            # McpSessionPool: worker coroutine, session lifecycle, retry logic
├── mcp_adapters.py                # Host/container MCP launch helpers
├── container_service.py           # Docker container lifecycle and command wrapping
├── workspace_service.py           # Workspace listing, reading, upload, download path safety
├── store.py                       # Filesystem persistence for conversations/images + cached index
├── Dockerfile.sandbox             # Required per-chat sandbox image
├── requirements-desktop.txt       # Waitress, rumps, and PyInstaller desktop dependencies
├── packaging/                     # Platform PyInstaller specs and release build scripts
├── .github/workflows/release.yml  # Cross-platform tests, sandbox smoke, and desktop artifacts
├── gunicorn.conf.py               # Single-worker/threaded production default
├── requirements.txt               # Flask, CORS, OpenAI SDK, MCP SDK
├── requirements-dev.txt           # Adds pytest and pytest-mock on top of requirements.txt
├── pytest.ini                     # Test discovery config
├── README.md                      # User-facing project description and setup docs
├── devs.md                        # This agent/developer guide
├── templates/index.html           # Full app shell and modal markup
├── tests/
│   ├── conftest.py                # Shared fixtures and filesystem isolation
│   ├── test_app_config.py         # Server-side API config tests
│   ├── test_store.py              # Image storage, conversation CRUD, cached index tests
│   ├── test_workspace_service.py  # Path safety, listing, reading, upload, _unique_path
│   ├── test_chat_turn_service.py  # Turn orchestration helpers, approvals, title generation
│   ├── test_streaming.py          # Typed stream event ordering, cancellation, tool accumulation
│   ├── test_mcp_service.py        # Config cache, run_async, McpSessionPool behavior
│   ├── test_mcp_adapters.py       # Docker process option and volume extraction helpers
│   ├── test_container_service.py  # Container naming, lifecycle helpers, idle reaping
│   ├── test_tool_approval.py      # Approval gate lifecycle, concurrent approvals, cancel-event unblocking
│   ├── test_title_service.py      # Title tool definition, message-to-text, title extraction
│   └── test_routes.py             # Flask HTTP integration tests via test client
└── static/
    ├── css/                       # CSS entrypoint and module files
    └── js/
        ├── app.js                 # Browser bootstrapping and event binding
        ├── api.js                 # Thin fetch wrapper
        ├── chat.js                # Chat orchestration exports / compatibility surface
        ├── chat_attachments.js    # Image/file attachment lifecycle helpers
        ├── chat_edit.js           # Edit, resend, regenerate helpers
        ├── chat_branches.js       # Branch snapshots/switching for edits and regenerations
        ├── chat_payloads.js       # API message/payload construction
        ├── chat_send.js           # Send flow and stream start/handling
        ├── stream_consumer.js     # SSE response reader and error response helpers
        ├── conversations.js       # Conversation CRUD and sidebar list
        ├── customization.js       # Color mode/font/accent/customization state
        ├── dom.js                 # Shared DOM lookup helpers
        ├── file_panel.js          # Workspace browser/preview/download UI
        ├── format.js              # Formatting helpers
        ├── icons.js               # SVG icon strings
        ├── markdown.js            # Markdown rendering and safe file-link enhancement
        ├── mcp.js                 # MCP config UI, tool loading, enable/auto-approve toggles
        ├── app_policy.js          # App-level system prompt guidance
        ├── mcp_tool_ui.js         # Generic tool-result rendering helpers
        ├── renderer.js            # Re-exports all public symbols from renderer sub-modules
        ├── renderer_core.js       # scrollToBottom, stickToBottom, messagesEl, createMessageRow
        ├── renderer_groups.js     # Block grouping, tryGroupBlock, updateGroupLabel, attachCollapsible, prepareAssistantRow
        ├── renderer_thinking.js   # createThinkingBlock, updateThinkingBlock, finalizeThinkingBlock, appendThinkingBlock
        ├── renderer_attachments.js # normalizeContentAttachments, renderAttachmentCard, getRawText, appendContentParts
        ├── renderer_tools.js      # Tool strip states, cancelAllToolApprovals, appendToolResultInline
        ├── renderer_actions.js    # Copy/edit/regenerate buttons, branch arrows, inline edit UI
        ├── settings.js            # API/model/chat settings UI
        ├── state.js               # Shared browser state and localStorage keys
        ├── storage.js             # localStorage wrapper
        ├── ui.js                  # General UI helpers
        ├── voice.js               # Web Speech API integration
        └── tool_adapters/         # Tool-specific display/rendering adapters
```

Root-level duplicate test files should not exist. In particular, keep `test_mcp_service.py` only under `tests/`.

## How to run locally

Install dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the test suite:

```bash
pytest
```

Run the Flask app:

```bash
python app.py
```

Open:

```text
http://localhost:8080
```

Build the required source-development sandbox image before using MCP tools:

```bash
identity="$(python -c 'import build_info; print(build_info.sandbox_identity())')"
docker build --build-arg "LUMEN_SANDBOX_IDENTITY=$identity" -f Dockerfile.sandbox -t lumen-sandbox:latest .
```

Production-ish entrypoint:

```bash
gunicorn -c gunicorn.conf.py "app:create_app()"
```

`gunicorn.conf.py` intentionally uses one worker and multiple threads because active stream state and cancellation events are currently process-local.

## Persistence and runtime locations

The backend stores user data outside the repo by default:

```text
~/.lumen/
├── config.json            # server-side API provider config, unless LUMEN_CONFIG_FILE overrides it
├── advanced_config.json   # container/file settings written by the UI; env vars take precedence
├── mcp.json               # MCP server config, unless LUMEN_MCP_CONFIG_FILE overrides it
├── memory.md              # persistent cross-chat memory; mounted read-write into every container at /memory.md
├── conversations/         # one JSON file per conversation
├── containers/            # one workspace directory per conversation
└── images/                # uploaded images keyed by SHA-256 hash
```

Important environment variables:

```text
OPENAI_API_KEY               overrides saved API key
OPENAI_BASE_URL              overrides saved API base URL
OPENAI_API_BASE              fallback alias for API base URL
LUMEN_HOST                   default: 127.0.0.1; set 0.0.0.0 only for deliberate LAN access
LUMEN_PORT                   default: 8080
LUMEN_DEBUG                  disabled by default; accepts 1/true/yes/on
LUMEN_OPENAI_TIMEOUT         default: 120 seconds
LUMEN_MCP_TOOL_TIMEOUT       default: 120 seconds
LUMEN_TOOL_APPROVAL_TIMEOUT  default: 600 seconds
LUMEN_CONFIG_FILE            path to server-side config JSON
LUMEN_CONFIG_CACHE_TTL       default: 5 seconds
LUMEN_ADVANCED_CONFIG_FILE   path to advanced/container config JSON; default ~/.lumen/advanced_config.json
LUMEN_MCP_CONFIG_FILE        path to MCP config JSON; default ~/.lumen/mcp.json
LUMEN_SANDBOX_IMAGE          default: lumen-sandbox:latest  [env-locks the UI field]
LUMEN_CONTAINERS_ROOT        default: ~/.lumen/containers
LUMEN_CONTAINER_MEMORY       default: 512m  [env-locks the UI field]
LUMEN_CONTAINER_CPUS         default: 1  [env-locks the UI field]
LUMEN_CONTAINER_NETWORK      default: bridge  [env-locks the UI field]
LUMEN_CONTAINER_PREFIX       default: lumen-chat-
LUMEN_CONTAINER_IDLE_TIMEOUT default: 600 seconds; 0 disables idle reaping  [env-locks the UI field]
LUMEN_MAX_CONTENT_LENGTH     default: 60 MiB Flask request body cap
LUMEN_CORS_ORIGINS           default: http://localhost:8080,http://127.0.0.1:8080
LUMEN_MAX_FILE_PREVIEW_BYTES default: 512 KiB  [env-locks the UI field]
LUMEN_MAX_FILE_LIST_ENTRIES  default: 500  [env-locks the UI field]
LUMEN_MAX_UPLOAD_BYTES       default: 50 MiB  [env-locks the UI field]
LUMEN_MCP_CONFIG_CACHE_TTL   default: 5 seconds
```

Variables marked `[env-locks the UI field]` are managed by `advanced_config.py`. When set, `public_advanced_config()` marks the key as `<key>_env_locked: true` so the browser disables the corresponding form control.

Browser `localStorage` keys such as `lumen_settings`, `lumen_models`, and `lumen_mcp_server_settings` are unrelated to the uppercase environment variables.

## Backend architecture

### `app.py`

- Creates the Flask app.
- Sets `MAX_CONTENT_LENGTH` from `LUMEN_MAX_CONTENT_LENGTH` to cap request body size globally.
- Configures CORS from `LUMEN_CORS_ORIGINS`, defaulting to localhost origins.
- Checks Docker and the sandbox image at startup, then lets the browser show friendly setup actions instead of exiting.
- Registers all five route-group blueprints (`routes_startup`, `routes_conversations`, `routes_chat`, `routes_mcp`, `routes_files`) directly instead of a single monolithic blueprint.
- Calls stale container cleanup at startup.
- Registers shutdown cleanup through `atexit` and `SIGTERM`, guarded against double execution.

### `app_config.py`

Server-side provider config lives here. Sensitive API keys should not be stored in browser `localStorage` or sent in `/api/chat/stream` request bodies.

Key behavior:

- Config defaults to `~/.lumen/config.json`.
- `LUMEN_CONFIG_FILE` can override the config path.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_API_BASE` override saved config at load time.
- `public_config()` returns only safe browser metadata: `api_base` and `has_api_key`.
- `save_config()` persists allowed keys atomically with a temp-file replace.
- Blank API key updates intentionally keep the existing saved key.

### `advanced_config.py`

Server-side container and file-handling settings live here. Complements `app_config.py` (which owns API provider settings) with a separate file so they can be stored, overridden, and cached independently.

Key behavior:

- Config defaults to `~/.lumen/advanced_config.json`.
- `LUMEN_ADVANCED_CONFIG_FILE` can override the config path.
- Values are resolved in three-tier priority: **env var > `advanced_config.json` > hardcoded default**.
- Env vars that are set at import time are stored in `_ENV_LOCKED`. `save_advanced_config()` silently ignores any env-locked key so the operator value can never be overwritten from the UI.
- `public_advanced_config()` returns every allowed key plus a `<key>_env_locked: bool` flag so the browser can disable the corresponding field and show which env var controls it.
- Integer keys (`container_idle_timeout`, `max_file_preview_bytes`, `max_file_list_entries`, `max_upload_bytes`) are cast to `int`; string keys are stripped and returned as strings.
- Results are cached for `LUMEN_CONFIG_CACHE_TTL` seconds (shared TTL with `app_config`); `save_advanced_config()` invalidates the cache immediately.
- Writes use temp-file replace via `fs_utils.atomic_replace`.

### `runtime_requirements.py`

Owns Docker availability and sandbox image checks. Used by `app.py` at startup and by `routes_startup.py` at runtime.

Key behavior:

- `check_docker()` — resolves the Docker CLI, then runs a bounded server-version probe to verify the selected Docker context can reach a usable daemon without hanging app startup.
- `check_sandbox_image()` — first confirms the exact canonical image reference exists, then compares its `com.lumen.sandbox.identity` label with the normalized Docker/MCP input digest. Query or inspection failures are retry-only states, never installation prompts.
- `check_requirements()` — returns the first unmet requirement, or ok.
- `build_sandbox_image_stream()` — generator that yields `("log", {"line": str})`, `("done", status_dict)`, or `("error", status_dict)` tuples for SSE streaming from `routes_startup.py`.
- The sandbox image name is read from `advanced_config.load_advanced_config()["sandbox_image"]` so `LUMEN_SANDBOX_IMAGE` and UI changes take effect without code changes. Docker boundaries canonicalize untagged names to `:latest` without rewriting saved settings.

### `fs_utils.py`

Shared filesystem utility. Contains `atomic_replace(src, dst)`, which does a safe temp-file replace with Windows-specific retry logic: `os.replace()` can raise `PermissionError` on Windows when another thread momentarily holds the destination file open. Up to 5 retries with linear back-off prevent transient file-lock races from surfacing as 500 errors. All modules that write persistent JSON (`app_config`, `advanced_config`, `mcp_service`, `store`) use this helper.

### `docker_path_utils.py`

Cross-platform Docker volume path conversion. On Linux and macOS, host paths can be used directly as Docker volume mount sources and targets. On Windows, this is impossible because drive-letter colons (`D:\foo`) conflict with Docker's volume spec separator, and Linux containers cannot have `D:\foo` as a mount target.

Key functions:

- `host_path_to_docker_src(path_str)` — converts `D:\foo\bar` → `D:/foo/bar` on Windows; identity on Linux/macOS.
- `host_path_to_container_path(path_str)` — converts `D:\foo\bar` → `/d/foo/bar` for a valid Linux mount target; identity on Linux/macOS.
- `make_volume_spec(host_path, mode="ro")` — builds a complete `source:target:mode` spec using the two helpers above.
- `translate_arg_for_container(arg)` — rewrites absolute Windows path arguments to their in-container equivalents; no-op on Linux/macOS and for non-path strings.
- `parse_volume_source(spec)` — extracts the host-side source from a volume spec string without misreading drive-letter colons as field separators.

`mcp_adapters.py` and `container_service.py` use these helpers wherever they build Docker volume specs or rewrite MCP server command arguments.

### `routes_startup.py`

Owns all startup and runtime-environment routes. Kept separate from conversation routes because these routes are about the host environment, not user data.

Key routes:
- `GET /` — serves the app shell or the setup screen if requirements are unmet
- `GET /health` — liveness probe for container orchestrators
- `GET /api/startup/requirements` — current Docker/image requirement status (JSON)
- `POST /api/startup/build-sandbox-image/stream` — streams `docker build` output as SSE after same-origin token validation

The SSE stream emits three event types: `log` (one build output line), `done` (build succeeded, data is `RequirementStatus` JSON), and `error` (build failed, data is `RequirementStatus` JSON). The frontend reads the authenticated POST response as a stream and appends lines to the details panel in real time. Startup mutations require a short-lived session token and an exact same-loopback-origin request; only one image build may run at a time, and disconnecting the stream terminates its Docker client.

### `routes_conversations.py`

Handles conversation CRUD, workspace path lookup, container status, and the danger-delete endpoint.

Key routes:
- `GET/POST/PUT/DELETE /api/conversations` and `/api/conversations/<conv_id>`
- `GET /api/conversations/<conv_id>/workspace`
- `GET /api/conversations/<conv_id>/container`

Conversation update is whitelisted. `PUT /api/conversations/<conv_id>` only accepts allowed user-facing fields such as `title` and `system_prompt`; do not reintroduce `data.update(_body())`.

### `routes_chat.py`

Owns streaming and all chat-adjacent routes. Module-level streaming state dictionaries live here:

- `_cancel_events`: `stream_id -> threading.Event`
- `_active_streams`: `stream_id -> replayable stream state`

Because these are in-memory, active stream reattach works only within the same Python process.

Key routes:
- `POST /api/chat/stream` — start or reattach to a streaming chat turn
- `POST /api/chat/cancel` — cancel an active stream
- `POST /api/chat/approve` — approve or deny a pending MCP tool call
- `GET/POST /api/settings` — read/write server-side API provider config
- `GET/POST /api/container-settings` — read/write advanced container and file-handling config
- `GET /api/models` — proxy model-list fetch

### `routes_mcp.py`

Handles MCP configuration and tool operations:

- `GET/POST /api/mcp/config` — load/save `mcp.json`
- `GET /api/mcp/tools` — discover MCP tools from configured servers
- `POST /api/mcp/call` — directly invoke one MCP tool

### `routes_files.py`

Handles workspace files and image uploads (81 lines):

- `GET /api/conversations/<conv_id>/files` — list workspace entries
- `POST /api/conversations/<conv_id>/files` — upload a file to the workspace
- `GET /api/conversations/<conv_id>/files/content` — preview text files
- `GET /api/conversations/<conv_id>/files/download` — download workspace files
- `POST /api/images` — store image uploads by SHA-256 hash
- `GET /api/images/<image_id>` — serve stored images

### `chat_turn_service.py`

This is the core backend orchestration layer for a chat turn. It re-exports `resolve_tool_approval` from `tool_approval.py` so that `routes_chat.py` needs no additional import.

Key responsibilities:

- Read `~/.lumen/memory.md` and inject its contents into the system message via `_inject_memory()` before the streaming loop.
- Create an OpenAI client from `app_config.load_config()` with the bounded `LUMEN_OPENAI_TIMEOUT`.
- Stream model chunks through `streaming.stream_chat_completion()`.
- Accumulate text and `reasoning_content`.
- Persist partial assistant output using `TurnRecorder`.
- Detect streamed tool calls.
- Pre-mount host volumes for all enabled MCP servers at turn start by calling `mcp_service.collect_all_extra_volumes()` and `container_service.ensure_container()`.
- Request frontend approval for tools unless the server is set to auto-approve.
- Invoke MCP tools through a persistent `mcp_service.McpSessionPool` (one pool per conversation, kept alive across turns) when tools are available.
- Append tool messages back into the API message history.
- Loop until the model completes without more tool calls.
- Emit final `assistant_done` and optional generated `title` events.

`TurnRecorder` saves conversation checkpoints during long streams and removes `active_stream_id` on finalize.

SSE event types produced/forwarded include:

```text
reasoning
done
text
tool_start
tool_calls
tool_approval_required
tool_running
tool_result
assistant_done
title
error
```

The model-facing messages and UI-facing `displayLog` are related but not identical. Be careful to update both when changing turn logic.

### `title_service.py`

Stateless and independently testable (80 lines). Contains:

- `_SET_TITLE_TOOL` — the tool definition sent to the model to elicit a title.
- `_messages_to_text(messages)` — converts message history to plain text for title prompting.
- `_extract_title(response)` — extracts the title string from the model's tool-call response.
- `generate_title(messages, client, model)` — orchestrates a single title-generation request.

Because this module is stateless, it can be imported and unit-tested without any Flask app context.

### `tool_approval.py`

Bounded approval subsystem (52 lines). Contains:

- `_pending_approvals` dict and its `threading.Lock`.
- `request_tool_approval(stream_id, call_id, tool_name, tool_args, publish, cancel_event)` — registers a pending approval and blocks until resolved, cancelled, or `LUMEN_TOOL_APPROVAL_TIMEOUT` expires.
- `resolve_tool_approval(approval_id, approved)` — unblocks the waiting turn with the decision.

`chat_turn_service` re-exports `resolve_tool_approval` at module level so `routes_chat.py` only needs to import from one place.

### `streaming.py`

- Wraps OpenAI streaming chat completions.
- Yields typed Python dictionaries internally, not already-serialized SSE strings.
- Merges incremental tool-call deltas by tool-call index.
- Emits `tool_start` as soon as a tool name appears.
- Emits `tool_calls` when the finish reason is `tool_calls`.
- Supports cancellation by closing the OpenAI stream when the cancellation event is set.
- Provides `sse_event(payload)` for HTTP-boundary serialization.
- Provides `make_streaming_response(generator)` for Flask streaming responses.

Do not restore the old internal SSE encode/decode round-trip. Keep stream internals dict-based and serialize only at the HTTP boundary.

### `mcp_service.py`

- Persists MCP config at `~/.lumen/mcp.json` by default; `LUMEN_MCP_CONFIG_FILE` can override the path.
- Caches MCP config for a short TTL to avoid reading `mcp.json` on every tool call.
- Validates only the top-level config shape: `{"mcpServers": {...}}`.
- Connects to each MCP server through stdio.
- Uses `mcp_adapters.apply_workspace_process_options()` to configure the container runtime for each MCP server.
- `collect_all_extra_volumes()` gathers the union of host mount volumes for a list of server names.
- `fetch_tools()` returns OpenAI-tool-like metadata for the frontend.
- `invoke_tool()` remains available for one-off calls.
- MCP config cache reads/writes are protected by a lock because Flask can run threaded.
- `run_async()` bridges async MCP calls into Flask sync route/service code.
- Re-exports `_build_server_params` so `mcp_session_pool.py` can import it without a circular dependency.

### `mcp_session_pool.py`

Contains the entire `McpSessionPool` class (168 lines) extracted from `mcp_service.py`. Responsibilities:

- Owns the dedicated worker coroutine for the whole turn.
- Manages session lifecycle: open, invoke, `ClientSession.__aexit__`, and `stdio_client.__aexit__` all happen in the same asyncio Task.
- Handles retry logic for transient session failures.
- Bounds each invocation with `LUMEN_MCP_TOOL_TIMEOUT` while preserving same-task cleanup semantics.
- Imports `_build_server_params` from `mcp_service` on demand to avoid a circular import.

### `mcp_adapters.py`

All MCP servers run inside the per-conversation Docker container. There is no host runtime fallback for normal chat execution.

Container runtime:

- Requires `conv_id` for real conversation execution; otherwise raises `ContainerConversationRequired` unless discovery behavior applies.
- Ensures the per-conversation Docker container is running.
- Mounts the conversation workspace as `/workspace`.
- Wraps the MCP command with `docker exec -i --workdir /workspace ...`.
- Injects explicit MCP env vars into the container with `docker exec --env`.
- Extracts absolute script/project paths from server args and mounts nearby project roots read-only.

### MCP discovery container

`/api/mcp/tools` can run without an open conversation. When `conv_id` is missing, it uses the reusable `mcp-discovery` container, lists MCP tools, then stops it for idle reuse.

Keep this behavior intact:

- Stale-container cleanup must skip `mcp-discovery`.
- Real chat tool discovery/calls must still use the actual conversation `conv_id`.
- Remote MCP servers may log harmless shutdown noise when discovery stops the container after tools load.

### `container_service.py`

- Creates and reuses Docker containers named with `LUMEN_CONTAINER_PREFIX` plus a sanitized conversation id.
- One host workspace directory is created per conversation under `LUMEN_CONTAINERS_ROOT`.
- Containers are started with `/workspace` mounted to the host workspace and `~/.lumen/memory.md` mounted read-write at `/memory.md`.
- The sandbox drops all capabilities, then adds back a minimal set: `CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID`.
- Provides stale container cleanup, container removal, workspace deletion, status inspection, and `docker exec` command wrapping.
- Uses per-conversation locks to avoid concurrent create/start races.
- The idle reaper stops conversation containers that have been idle beyond `LUMEN_CONTAINER_IDLE_TIMEOUT`; `0` disables idle reaping.
- The MCP discovery container is excluded from stale cleanup/reaping.

### `workspace_service.py`

Handles safe file operations inside the conversation workspace.

Important safety behaviors:

- Only `/workspace` paths are accepted for absolute-style paths.
- Parent traversal with `..` is rejected.
- Resolved paths must stay inside the workspace root.
- Upload filenames are sanitized.
- Duplicate upload names get `-1`, `-2`, etc.
- Uploads are stored under `/workspace/uploads/`.
- Preview is limited to text-like files under `LUMEN_MAX_FILE_PREVIEW_BYTES`.

### `store.py`

- Conversations are JSON files under `~/.lumen/conversations`.
- Images are stored under `~/.lumen/images` using SHA-256 filenames.
- `working_directory(conv_id)` delegates to `mcp_adapters.conversation_working_directory()`, which delegates to `container_service`.
- Writes use temp-file replace.
- `list_all()` uses a cached in-memory conversation summary index.
- The cached index is protected by `_index_lock`, a `threading.RLock()`, because threaded Flask/Gunicorn can mutate the index concurrently.

When editing the index, keep lock coverage around `_update_index_for()`, `_remove_index_entry()`, and `list_all()` read/copy access.

## Frontend architecture

The frontend is plain browser ES modules. `templates/index.html` defines the DOM shell and imports `static/js/app.js` with `type="module"`.

### Shared state

`static/js/state.js` exports a single mutable `state` object. Major fields include:

```js
state.convId
state.messages
state.displayLog
state.mcpTools
state.mcpServerSettings
state.isStreaming
state.streamId
state.apiBase
state.model
state.systemPrompt
state.temperature
state.autoGenerateTitles
state.enterToSend
state.autoScrollStreaming
state.serverHasApiKey
```

The API key should not be persisted in `state` or localStorage. The settings UI may accept a key and send it to `/api/settings`, but it should clear the input after saving and only retain `serverHasApiKey` metadata in browser state.

`chat.js` keeps active turn contexts in `activeTurns`. Each context owns its stream ID, cancellation flag, and `AbortController`; approval and cancel requests must use that context rather than the currently visible conversation's global state.

### App startup: `app.js`

- Loads server/client settings, customization, cached MCP tools, and conversation list.
- Binds sidebar, modal, settings, input, keyboard, model picker, MCP, voice, attachments, and file-panel events.
- Initializes icons.
- Opens the last active conversation if available.

Because chat behavior is now split across several modules, keep imports explicit and verify browser module exports after refactors. `chat.js` intentionally re-exports some helpers for compatibility, but new code should usually import from the owning module.

### Chat modules

The previous oversized `chat.js` has been decomposed.

- `chat.js`: compatibility surface and high-level chat exports.
- `chat_send.js`: send flow, stream start, stream event handling, cancellation.
- `chat_payloads.js`: builds model/API messages and chat request payloads.
- `chat_attachments.js`: pending image/file attachment processing and helpers such as `hasPendingAttachments`.
- `chat_edit.js`: edit, resend, regenerate, and related mapping helpers.
- `chat_branches.js`: captures branch suffixes, stores branch variants on `displayLog` entries, switches visible branch paths, and syncs the active visible branch before persistence-sensitive operations.
- `stream_consumer.js`: reads SSE responses and forwards raw event strings to a callback; also reads response error bodies.

`stream_consumer.readSSEStream(response, onEvent)` requires a callback. It should fail loudly if called incorrectly rather than silently swallowing stream data.

### Chat flow

When the user sends a message:

1. Pending attachments finish processing.
2. A conversation is created if none exists.
3. Images are uploaded to `/api/images` and stored as `image_ref` blocks in local message history.
4. Regular files are uploaded to `/api/conversations/<conv_id>/files` and their `/workspace/uploads/...` paths are attached to the user message metadata.
5. Any active visible branch is synced so later messages stay attached to the selected path.
6. The user message is appended to `state.messages` and `state.displayLog`.
7. The conversation is persisted before model streaming starts.
7. `buildApiMessages()` constructs the API payload:
   - Prepends system prompt and MCP policy prompt.
   - Adds file attachment context into user content.
   - Expands stored image refs to base64 `image_url` blocks for OpenAI-compatible vision input.
8. `/api/chat/stream` is called without an API key in the body.
9. The frontend reads SSE lines and updates the UI incrementally.
10. When a streamed turn completes, the active branch snapshot is synced and the conversation is saved again.

### Conversation history model

There are two parallel histories:

- `state.messages`: model/API-facing history.
- `state.displayLog`: UI-facing render log, including messages, thinking blocks, tool results, and branch metadata.

Do not assume they have the same indices. Use helper mapping logic when editing/regenerating messages. Branch variants are stored on the `displayLog` entry that owns the fork. Each variant contains the visible suffix for that branch so later messages remain tied to the selected path. Before switching branches, editing, regenerating, or sending a continuation, call the branch sync helper so the active path is not lost.

### MCP/app frontend: `mcp.js`, `app_policy.js`, `mcp_tool_ui.js`, `tool_adapters/`

`mcp.js` handles:

- Loading/saving the editable custom-server portion of `mcp.json`.
- Fetching tools from `/api/mcp/tools`.
- Caching tools in localStorage.
- Per-server enable/disable and auto-approve toggles.
- Per-server icon selection.
- Rendering grouped tool lists.

`app_policy.js` injects app-level model instructions. It tells the model:

- That it is running in Lumen AI Chat with per-chat workspace files.
- How to use uploaded file paths appended to user messages.
- To use `/workspace/...` links only for real workspace files.

Tool-specific behavior belongs in the MCP tool schemas, not in `app_policy.js`. Keep this file focused on app-level behavior only.

`mcp_tool_ui.js` and `tool_adapters/` control how tool calls/results are displayed. To add rich rendering for a new MCP tool, prefer adding a tool adapter rather than special-casing in `renderer.js`.

Tool names sent to the model come directly from `tool.name` in `chat_payloads.js`. Server identity is sent separately through `mcp_tool_meta` as `{ name, server, autoApprove }`, and `chat_turn_service.py` uses that metadata to dispatch the MCP call. Keep tool descriptions clean and semantic (`tool.description || tool.name`); do not prepend `[server]` labels to model-facing descriptions or display names. If collision handling is reintroduced later for multiple servers exposing the same tool name, document the new naming and dispatch contract here and keep the UI display name separate from the dispatch identifier.

Existing adapters:

- `exa.js`: Exa search/fetch/deep-research tools with custom card rendering

### Renderer: `renderer.js` and sub-modules

`renderer.js` is a small render coordinator and compatibility surface. It keeps the high-level message rendering path while delegating focused work to sub-modules. Existing importers (`chat_send.js`, `app.js`, etc.) should continue importing public renderer helpers from `renderer.js` unless they need a narrowly owned helper such as `refreshMessageFooter()` from `renderer_actions.js`.

The renderer sub-modules:

**`renderer_core.js`** — foundational DOM helpers used by every other renderer module:
- `scrollToBottom`, `stickToBottom`, `messagesEl`, `createMessageRow`

**`renderer_groups.js`** — sequential block grouping:
- `tryGroupBlock`, `updateGroupLabel`, `attachCollapsible`, `prepareAssistantRow`

**`renderer_thinking.js`** — thinking/reasoning block lifecycle:
- `createThinkingBlock`, `updateThinkingBlock`, `finalizeThinkingBlock`, `appendThinkingBlock`

**`renderer_attachments.js`** — content-part and attachment card rendering:
- `normalizeContentAttachments`, `renderAttachmentCard`, `getRawText`, `appendContentParts`

**`renderer_tools.js`** — tool strip states, approval UI, and inline result rendering:
- Tool strip state transitions (`tool_start` → approval → running → result)
- `cancelAllToolApprovals`, `appendToolResultInline`

**`renderer_actions.js`** — message footer actions:
- Copy, edit, regenerate, inline user-message edit UI, branch arrows, and `refreshMessageFooter()`.

When adding new renderer functionality, place it in the appropriate sub-module and re-export from `renderer.js` if broad callers need it. Do not grow `renderer.js` with footer/action logic.

### Workspace panel: `file_panel.js`

- Lists `/workspace` entries.
- Previews text/Markdown/code-like files.
- Downloads files through `/api/conversations/<conv_id>/files/download`.
- Refreshes after file uploads and tool results.
- Stores panel width in localStorage.

### Markdown and file links

`markdown.js` uses `marked`, `DOMPurify`, KaTeX, and highlight.js. It also enhances safe `/workspace/...` links into downloadable conversation-file links.

Only use `/workspace/...` links for files that already exist in the active conversation workspace.

## MCP config shape

All MCP servers run in the per-conversation container. `mcp_service.load_editable_config()` returns configured servers for the settings textarea, while `load_config()` returns an independent snapshot of that configuration. No server is bundled or automatically added. Python and `uv` are available in the sandbox for separately configured Python servers; server source and dependencies are supplied separately.

A typical custom server configuration:

```json
{
  "mcpServers": {
    "search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"]
    }
  }
}
```

With explicit environment variables:

```json
{
  "mcpServers": {
    "search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": {
        "BRAVE_API_KEY": "your-key-here"
      }
    }
  }
}
```

Server-level UI settings such as enabled, auto-approve, and icon are not stored in `mcp.json`; they are stored in browser localStorage under `lumen_mcp_server_settings`.

All server IDs are editable. Loading configuration does not rewrite or back up the user's file. Existing server definitions are preserved; obsolete definitions can be removed through settings.

## Desktop applications and releases

- `desktop_launcher.py` is a menu-bar process with no native chat window. It serves one Waitress process on `127.0.0.1:38492`, opens the default browser, holds an advisory single-instance lock, and exposes Open, Docker Status, Logs, and Quit actions.
- `windows_desktop_launcher.py` provides the equivalent Windows tray host. It uses a per-session named mutex, a windowed PyInstaller process, and one absolute 15-second Quit deadline. The listener closes before worker cleanup; MCP pools and running containers close concurrently; the mutex is released before the tray loop stops. A Windows-only watchdog records and forces an abnormal exit only if the coordinated path misses the deadline.
- `build_info.resource_root()` is the only supported way to locate bundled templates/static assets and the first-run Docker build context in a frozen app. Both source and packaged runs default to `lumen-sandbox:latest`.
- `build_info.sandbox_identity()` hashes only `.dockerignore` and `Dockerfile.sandbox`. Relative paths and line endings are normalized, so identity is invariant across desktop versions and platforms.
- `docker_cli.py` resolves Docker through explicit override, `PATH`, and standard platform locations because GUI applications inherit a restricted `PATH`. All Lumen-owned Windows Docker processes use no-window creation flags. Container and requirement modules must use this helper rather than invoking a literal `docker` executable.
- `packaging/build_macos.sh` creates an Apple Silicon, macOS 14+ `.app` and DMG. With no `MACOS_SIGN_IDENTITY` it ad-hoc signs; the release workflow can import signing credentials and notarize later.
- `packaging/build_windows.ps1` requires Python 3.12 x64, creates a complete PyInstaller `onedir` ZIP and checksum, extracts that ZIP, and runs frozen-resource/server smoke mode from the extracted copy. Never distribute only `Lumen AI Chat.exe`.
- The menu-bar item uses the bundled Lumen SVG artwork. Docker shutdown calls are bounded so an unresponsive daemon cannot prevent the app from quitting.
- The packaged setup page asks before starting Docker Desktop and before locally building the tools image. It streams Docker's base-layer, and dependency installation progress. A container sandbox-identity or underlying image-ID mismatch triggers recreation of the container only; mounted workspace data remains on the host.
- Build metadata supplies both the user-facing application version and Apple-compatible `CFBundleShortVersionString`/`CFBundleVersion` values. GitHub Actions uses its monotonic run number for release builds.
- `Dockerfile.sandbox` starts from `ubuntu:24.04` and installs Python, `uv`, Node.js, sandbox packages, and the optional `mcp-remote` bridge. It does not compile or bundle an MCP server. `packaging/smoke_sandbox.py` verifies the Python runtime and workspace mount in release CI.

## Development conventions

Follow the existing separation of concerns:

- Keep Flask route handlers thin.
- Put backend logic in service modules.
- Keep streaming and persistence behavior deliberate; partial responses are saved during generation.
- Keep `state.messages` and `state.displayLog` in sync conceptually, but do not merge them into one structure. Preserve branch metadata on `displayLog` when editing/regenerating.
- Prefer adding frontend tool adapters for tool-specific UI instead of hardcoding tool names in renderer logic.
- Keep workspace path handling strict. Do not weaken traversal checks.
- Avoid introducing a frontend build step unless the whole project intentionally moves that direction.
- Do not send API keys in chat/model request bodies. Use `app_config.py` and `/api/settings`.
- Remember that MCP servers run in Docker; ensure the sandbox image is built and Docker is running before testing MCP behavior.
- Be cautious with module-level Python state if changing deployment assumptions; multi-worker servers will not share active streams/cancellation events.
- Keep cached module-level state protected where threaded access can mutate it.

## Automated test suite

Run `pytest` from the project root. The exact collected test count can change as the suite evolves, so do not hardcode a pass count in docs; the expected status is a clean passing run with no collection errors or failures.

```bash
pytest
```

Tests are isolated: `conftest.py` redirects filesystem paths to `tmp_path`, patches the runtime requirement check in the app factory, and stubs container cleanup where needed. No Docker daemon, real API key, or running server is required for the unit/integration-style tests.

Coverage summary:

| File | Key contracts verified |
| --- | --- |
| `test_app_config.py` | Server-side config loading/saving, env overrides, public config, atomic persistence |
| `test_store.py` | SHA-256 image naming, media type validation, conversation CRUD, cached index ordering, index locking/concurrency |
| `test_workspace_service.py` | Path traversal rejection, `resolve_workspace_path` boundary check, preview size limit, upload rollback/collision handling |
| `test_chat_turn_service.py` | Tool approval flow, title extraction, safe tool args, tool message format, `TurnRecorder` throttle/finalize behavior |
| `test_streaming.py` | Typed event ordering, multi-delta tool name accumulation, parallel tool calls, cancellation closes stream, error+done events |
| `test_mcp_service.py` | Config cache, malformed-config handling, atomic writes, `run_async`, `McpSessionPool` same-task cleanup behavior (pool now in `mcp_session_pool.py`) |
| `test_mcp_adapters.py` | Docker exec param mutation, project-root detection, host mount extraction/deduplication |
| `test_build_info.py` | Explicit image-reference canonicalization and platform/line-ending invariant sandbox identity |
| `test_container_service.py` | Safe container names, content-identity compatibility, exec argv/env ordering, name-conflict handling, idle reaper behavior |
| `test_runtime_requirements.py` | Missing/transient/incompatible image states and identity-labelled builds |
| `test_app_shutdown.py` | Idempotent, concurrent MCP/container shutdown coordination |
| `test_windows_desktop_launcher.py` | Native mutex, listener close ordering, tray cleanup ordering, and packaged smoke-mode contract |
| `test_tool_approval.py` | Approval gate lifecycle, concurrent approvals in the same stream, cancel-event unblocking, slot cleanup after resolve/cancel |
| `test_title_service.py` | `_SET_TITLE_TOOL` shape, `_messages_to_text` role/content handling, `_extract_title` from model tool-call response |
| `test_routes.py` | HTTP routes, route error paths, settings routes, conversation update whitelist |

Also syntax-check frontend modules after JS refactors:

```bash
find static/js -name '*.js' -print0 | xargs -0 -n1 node --check
```

## Manual verification checklist

Run this checklist manually after changes that touch streaming, MCP, Docker, or frontend module exports:

1. Python files compile:

   ```bash
   python -m py_compile *.py
   ```

2. Frontend modules parse:

   ```bash
   find static/js -name '*.js' -print0 | xargs -0 -n1 node --check
   ```

3. Test suite passes:

   ```bash
   pytest
   ```

4. App starts:

   ```bash
   python app.py
   ```

5. Browser opens `/` and renders the app.
6. API settings save through `/api/settings`; API key field clears and only shows saved-key metadata.
7. Creating, renaming, opening, and deleting conversations works.
8. A basic chat response streams token-by-token.
9. Stop/cancel works during streaming.
10. Settings save and reload.
11. Model list fetch works for the chosen OpenAI-compatible endpoint.
12. Image upload sends image input successfully.
13. Regular file upload appears in `/workspace/uploads` and the file panel.
14. Workspace preview/download works for text files.
15. MCP config save/load works.
16. MCP tool reload discovers tools.
17. Tool calls render `tool_start`, approval, running, and final result states.
18. Auto-approved tool calls skip approval and still render running/result states.
19. Multi-tool turns against the same MCP server do not log AnyIO cancel-scope cleanup errors.
20. Reopening a conversation during an active stream can reattach if still in the same backend process.
21. Docker container is created for a new conversation and MCP tools are discoverable once the conversation is open.

## Known limitations and things to inspect before feature work

### 1. Active stream reattach is process-local

`routes_chat.py` stores active stream state in memory. This is fine for the dev server and a single Gunicorn worker, but it will not work across multiple worker processes without shared state.

Short-term deployment default: one worker with threads, as in `gunicorn.conf.py`.

Long-term fix: move cancellation and stream event delivery to Redis/pub-sub or another shared backend.

### 2. No authentication/user accounts

The app is local-first and self-hosted. Do not expose it publicly without adding authentication, stricter CORS, rate limiting, and stronger secret handling.

### 3. No database or persistent-data upgrade layer

Persistent JSON shapes must remain stable between releases.

## Safe editing advice for agents

Before editing:

1. Read `README.md` and this file first for intended behavior.
2. Read the backend service that owns the behavior, not just the route.
3. Read the corresponding frontend module; many features span backend, JS state, and renderer code.
4. Search for the event/type/string you plan to modify across the whole repo.
5. Preserve persistent JSON shapes.
6. Run the relevant tests before and after the change.

When changing chat streaming:

- Preserve event ordering.
- Preserve cancellation behavior.
- Preserve partial persistence.
- Keep `streaming.py` dict-based internally.
- Test plain responses and tool-call responses.
- Test switching away from and back to a streaming conversation.

When changing MCP:

- Test container runtime with Docker running.
- Test approval and auto-approval.
- Test tool display labels and result rendering.
- Avoid assuming all MCP result content is JSON.
- Preserve `McpSessionPool` same-task lifecycle semantics.

When changing workspace files:

- Keep `/workspace` path normalization strict.
- Never permit arbitrary absolute host paths through file panel routes.
- Treat upload filenames as untrusted.
- Keep preview limits to avoid loading huge files into the browser.

When changing frontend rendering or module boundaries:

- Render current live stream and historical `displayLog` consistently.
- Keep edit/regenerate actions aligned with `displayLog` to `messages` index mapping.
- Test branch arrows after editing a user message, regenerating an assistant response, continuing from a switched branch, and reloading the conversation.
- Avoid injecting unsanitized HTML unless it is controlled UI markup and escaped user data.
- Run `node --check` on all `static/js/**/*.js` files.
- Open the browser console and verify there are no missing module exports.

## Useful search commands

```bash
# Find route definitions
grep -R "@blueprint.route" -n .

# Find all stream event types
grep -R "tool_\|assistant_done\|reasoning\|stream_id\|tool_calls" -n *.py static/js

# Find MCP-specific code
grep -R "mcp\|MCP\|tool_calls\|tool_result\|McpSessionPool" -n *.py static/js tests

# Find workspace path handling
grep -R "workspace\|/workspace\|file:/" -n *.py static/js

# Check for accidental API key body usage
grep -R "api_key" -n static/js *.py tests

# Check for duplicate root tests
find . -maxdepth 1 -name 'test_*.py' -print
```

## Current non-goals / absent pieces

- No database layer
- No persistent-data upgrade layer
- No frontend package/build system
- No authentication/user accounts
- No shared backend state for multi-worker active stream reattach
- No server-side encryption for stored conversations, images, config, or workspaces

Keep these assumptions in mind before adding features that depend on users, accounts, distributed processes, or durable job queues.
