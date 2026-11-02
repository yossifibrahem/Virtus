# Using Virtus

[Showcase](../README.md) · [Setup](SETUP.md) · [Configuration](CONFIGURATION.md)

## Basic Chat

1. Open the desktop app, or follow the [source setup](SETUP.md#developer-quick-start) and open `http://localhost:8080`.
2. Create a new conversation from the sidebar.
3. Open settings and enter your API key, base URL, and model.
4. Type a message and press **Enter**. Use **Shift + Enter** for a newline.

## Organizing Chats with Folders

1. Click **New folder** in the sidebar and give the folder a name.
2. Open the folder to create chats or browse its shared workspace.
3. Use a conversation's menu to move an existing chat into a folder or back to **Recent**.
4. Use the folder's menu and choose **Folder settings** to rename it or edit the instructions that apply to its chats.

Each chat retains its own message history, but chats in the same folder use one shared Docker container and `/workspace`. Folder instructions are used in place of the global system prompt for chats in that folder. The folder retains its files when its last chat is deleted or moved out. Moving a chat switches workspaces without copying files. Deleting a folder permanently deletes its chats, shared container, and managed workspace files.

## Uploading Files

Use the **+** button in the chat composer to attach files or images. Uploaded workspace files appear under `/workspace/uploads/` inside the active sandbox. Open **Workspace files** to expand directories, preview files in the browser, or download workspace files up to 50 MiB directly. Previews support text, rendered Markdown and HTML, images (PNG, JPEG, GIF, WebP, BMP, TIFF), PDFs, and Jupyter notebooks. PDFs show the complete document in an app-styled continuous preview, with pages rendered near the viewport; notebooks show twenty cells with saved text and image outputs, without executing code. Previous/Next controls navigate longer notebooks. PDF previews show only rendered pages. Notebook previews also offer a text view and Copy for the displayed batch. When you are working in a folder, every chat in that folder sees the same files.

## Using MCP Tools

1. Build the sandbox image if you have not already, using the content-identity command in [Build the Sandbox Image](SETUP.md#build-the-sandbox-image).
2. Open the MCP settings panel and add a server (command, args, optional env vars).
3. Apply the settings and click **Reload tools** in the MCP tab. Without an open chat, discovery uses a temporary discovery runtime that stops after listing tools.
4. Send a message asking the model to use a tool.
5. Approve or deny the tool call when prompted (or enable auto-approval per server).

Tool activity is shown inline with the tool name, arguments, running state, and result. Failed MCP transports are reported without automatically replaying the tool call; a later request can reopen the connection.

## Cancelling a Stream

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

Standalone chats have separate workspaces. Chats in a Folder share a runtime and workspace. The application mounts only its managed workspace. Install MCP server code inside the container and use container paths in configuration.

Commands run as container root, with reduced capabilities, no-new-privileges, CPU/memory limits and a 256-process limit. Docker chooses the normal default networking for new containers; existing Docker-managed network attachments are preserved, including reachable host/LAN services. The app supplies no network override and ignores obsolete saved network preferences and environment values. This is not a network air gap, a disk quota, or a guarantee against Docker/kernel vulnerabilities. Do not expose the Docker socket or run privileged containers. Local API requests require a same-origin browser session and a CSRF token; additional served hostnames must be explicitly listed in `VIRTUS_ALLOWED_HOSTS`.

The file panel transfers files through the container rather than following sandbox-created symlinks on the host. Uploads and downloads are bounded to 50 MiB per file. Text preview limits default to 512 KiB (maximum 4 MiB); directory listings default to 500 entries (maximum 5000). Visual previews use a 20 MiB input limit. Images and notebook outputs reuse the View worker’s 1600-pixel visual bounds and bounded text; PDFs display the original file without text extraction. Source views display up to 2000 lines or 128 Ki characters with an explicit shortening notice; Copy retains the loaded text and Download saves the original file. Smaller upload limits are supported. Unchanged previews reuse their loaded content after a file-version check, avoiding repeated reads, image conversion, and notebook parsing. Uploads, downloads, and PDF ranges transfer raw bytes after a small JSON metadata header; visual previews use bounded JSON/base64 transport. Transfers remain buffered and bounded by the file size limits. Tool images are stored separately per conversation; raw media is expanded only when constructing the model request.

After updating, rebuild the sandbox image through the startup screen. Helper sources participate in the sandbox identity, so older containers are recreated automatically. Native tool names `bash`, `view`, `write`, and `edit` are reserved; rename conflicting MCP tools.

If a workspace reaches a storage limit, the file panel remains available for browsing,
previews, and downloads. Uploads and ordinary agent execution remain blocked until
space is recovered.

## Persistence and execution safeguards

Stop/start, idle shutdown and normal app restarts reuse compatible containers and retain installed packages. An image, mount, resource or isolation mismatch can require recreation at an idle boundary; workspace files remain but installed packages and other container-layer state reset. CPU, memory, PID limits, capability restrictions, no-new-privileges and namespace/device isolation are checked before reuse, including name-conflict recovery. There are no environment snapshots.

Moving a standalone chat into a Folder requires an explicit warning/confirmation: its old workspace files are permanently deleted and its environment reset; its message history and the destination Folder's files remain. Cancelling changes nothing. Moving out of a Folder preserves that Folder's shared workspace.

Execution safeguards default to 900 seconds and 100 tool calls per turn, 512 MiB minimum free space, and 10 GiB per managed workspace. Override these on the server with `VIRTUS_TURN_TIMEOUT_SECONDS`, `VIRTUS_TURN_TOOL_LIMIT`, `VIRTUS_MIN_FREE_DISK_BYTES`, and `VIRTUS_MAX_WORKSPACE_BYTES`. Checks run before work and approximately every two seconds during execution, including MCP waits. Managed storage accounting skips symlink targets and host shares. Docker filesystem free/used space is checked inside running containers when observable, since Docker Desktop may use a separate VM disk. Unavailable Docker storage telemetry does not imply unlimited space. These are best-effort safeguards, not hard quotas; fast writers can exceed thresholds between checks.

Reaching limits cancels execution and may stop a shared runtime to terminate descendants. This can interrupt other activity in the Folder. Existing files and chat history are retained; the app never deletes user artifacts or shared host files to recover space. Model output is bounded to 2 MiB per response/turn, MCP tool text to 64 KiB, and replay to 8 MiB per active stream. On replay exhaustion the turn stops and the browser reloads authoritative saved history. Ordinary built-in output limits remain in force.
