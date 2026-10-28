# Virtus architecture decisions and implementation handoff

Date: 2026-10-28

Status: implemented on 2026-10-27; automated verification recorded below. This document records
the user's final decisions after an architecture review. Implement the requirements
below, following existing repository instructions and checking the current code
before making changes. Earlier proposals superseded here must not be implemented.

The application changes described here are implemented. README.md and devs.md now
describe the resulting behavior and persistence limits. The decisions below remain
the implementation requirements.

## 1. Product purpose and boundaries

Virtus is a local, single-user agentic chat application. The model gets its own
Docker computer so its commands, package installations, and ordinary filesystem
changes do not damage the host's unrelated files or packages.

- Keep Docker as the execution foundation and Flask as the local controller.
- Keep built-in command/file tools and ordinary MCP server processes in Docker.
- Internet connectivity is essential for online MCP servers, search, downloads,
  and package installation. Network restriction is not a product goal.
- File sharing uses uploads copied into the managed workspace.
- A Folder/Project is a persistent workspace and environment shared by its chats.
  Chats retain separate histories. Standalone chats have their own runtimes.
- Keep the local, single-process application design. No multi-user service,
  distributed infrastructure, or replacement desktop framework is requested.

## 2. Persistence: shared managed workspaces

Folder chats share the app-managed workspace. Host-directory sharing has been removed.
Do not add compatibility or migration handling for the removed feature.

### Accepted persistence limits

- Stop/start, idle shutdown, and ordinary app restart should reuse the existing
  container and retain its installed packages.
- Workspace data remains separately persistent through container recreation.
- Recreating a container for a sandbox/base-image upgrade may reset installed
  packages and other files outside the persistent workspace. The user accepts this.
- Do not build an upgrade/rebase/migration framework to preserve arbitrary OS state.
- State these limits accurately in the UI and documentation. Do not promise that
  the entire computer survives every kind of recreation.

Likely touchpoints: `routes_conversations.py`,
`static/js/conversations.js`, `templates/index.html`, `README.md`, `devs.md`.

## 3. Original review point 1: warn before a destructive chat move

### Final decision

Keep the existing move behavior. Add a clear warning and confirmation; do not
implement file copying, workspace preservation, or an additional workspace entity.

When a standalone chat moves into a Folder, its old standalone runtime/workspace
is removed. Its files are not copied into the destination Folder.

Suggested warning text:

> Moving this chat to a Folder permanently deletes all files in its current chat
> workspace and resets its installed environment. Its message history is kept.
> Workspace files will not be copied to the Folder.

### Acceptance criteria

- Show the warning before the destructive move is submitted, next to the explicit
  confirmation action. Cancelling leaves the chat and workspace untouched.
- Apply the warning to the destructive standalone-to-Folder transition. Do not
  falsely claim that moving a chat out of a shared Folder deletes that Folder's
  shared files, or that a no-op move wipes anything.
- Retain the existing backend ordering that confirms sandbox removal before
  removing owned workspace data and preserves saved data when removal fails.
- Preserve message history and the destination Folder's existing files.

Likely touchpoints: `static/js/conversations.js`, `templates/index.html`,
`routes_conversations.py`, move-related tests in `tests/test_routes.py`.

## 4. Original review point 2: reconcile effective container configuration

### Final decision

Implement a complete, shared runtime specification and verify relevant effective
Docker settings before reusing a container. Image and mount checks alone are
insufficient. Saved CPU/memory settings must correspond to what is actually applied.

### Required behavior

- Centralize expected creation/reuse settings so normal reuse and name-conflict
  recovery enforce the same rules.
- Validate the configured image and sandbox identity, complete expected mount set,
  sources/destinations, read/write flags, and recursive read-only policy.
- Reconcile CPU, memory, and PID limits and the app's actual isolation requirements:
  privileged mode disabled, expected reduced capabilities, and no-new-privileges.
  Unexpected host namespace/device access must not be accepted as an equivalent
  safe runtime configuration.
- Compare normalized Docker values rather than raw formatting where appropriate
  (for example, configured memory text versus inspected bytes).
- Use supported in-place resource updates where practical, or recreate a mismatched
  container at a safe idle boundary. Never interrupt active shared operations or
  silently claim that an unapplied setting is effective.
- If recreation is required, make the environment reset clear. Preserve the managed
  workspace and existing failure-safe cleanup behavior. Snapshotting is out of scope.
- Surface errors instead of reusing a container known to violate the expected
  configuration. Coordinate lifecycle changes with runtime locks and MCP sessions.

### Important exception: networking

Do not reconcile a saved application network setting, enforce a selected network
mode, or undo network attachments managed directly through Docker. Point 4's network
decision below supersedes that part of the original review.

Likely touchpoints: `container_service.py`, `sandbox_runtime.py`,
`advanced_config.py`, container settings UI and `tests/test_container_service.py`.

## 5. Original review point 3: optional global memory with a built-in tool

### Final decision

Keep LLM-maintained global memory across chats for stable user facts such as name
and preferences. A small JSON store is sufficient; no vector database or
external memory service is requested.

Replace unrestricted shared-file writes with an application-managed built-in memory
tool. Remove memory-maintenance instructions from the application policy and rely
on the tool schema to explain when and how memory should be updated.

Memory must be optional. Turning it completely off means the application supplies
neither remembered context nor a memory tool to subsequent model requests.

### Canonical store and update tool

- Keep the canonical memory under application control in `~/.virtus/memory.json`,
  as a JSON object with a string `content` field.
- Provide a server-owned built-in tool such as `update_memory`. Its schema describes
  remembering stable user facts/preferences, correcting outdated facts, and keeping
  entries concise. It should not encourage saving whole histories or transient
  command/task state.
- The LLM may invoke it automatically while memory is enabled. Do not introduce a
  confirmation dialog for every ordinary memory update.
- Serialize updates across all chats, perform atomic writes, and detect/reject stale
  updates instead of silently losing another chat's changes. Use bounded input,
  output, and total memory size, with bounded recoverable version history.
- The implementation may use revisions, exact-match patches, or structured updates;
  the specific schema is an implementation choice. Return enough information for
  the model to resolve conflicts rather than blindly retrying a stale overwrite.
- Reserve the tool name against MCP collisions and validate all arguments. This
  tool may manage only the app-owned memory store; it is not arbitrary host file
  access and must not accept arbitrary host paths or execute host commands.
- Present injected memory as clearly identified remembered data, subordinate to
  application/user instructions. Keep its context contribution bounded.
- Provide settings controls to enable/disable, inspect, edit, and clear memory.
  User edits and clears need the same concurrency/versioning discipline.

### Complete disable behavior

- Persist the enable/disable preference on the backend and expose it in settings;
  it must survive reloads and app restarts.
- When disabled, do not read/inject global memory into model context and omit the
  memory tool schema. Check the setting server-side again at tool invocation, so
  stale/forged tool calls cannot update memory while disabled.
- Subsequent model requests in an ongoing tool loop should also respect the setting.
  A request already sent cannot be retroactively changed, and disabling memory does
  not erase information already discussed in the chat history.
- Preserve stored memory when disabled so enabling it later restores the feature.
  Clearing memory is a separate, explicit action.
- Remove the memory paragraph from `static/js/app_policy.js`. No replacement policy
  paragraph should instruct the model to maintain memory; use the tool schema.
- The default enabled/disabled state was not explicitly selected. Preserve existing
  effective behavior (enabled) unless a different default is agreed; document the
  chosen default. Complete disable behavior is mandatory regardless of default.

### Keep canonical memory outside the sandbox

Canonical memory stays on the host and is accessed through context and the narrow
built-in tool. Memory toggles and updates do not recreate the computer or reset packages.

Folder-specific memory was discussed as a possible distinction, but a second memory
system is not required in this implementation. Global cross-chat user memory is the
approved requirement.

Likely touchpoints: `memory_service.py`, `builtin_tools.py`, `chat_turn_service.py`,
`container_service.py`, `app_config.py` or a dedicated persisted settings module,
settings routes/UI, `static/js/app_policy.js`, related tool, media, packaging, and
container tests as needed.

## 6. Original review point 4: delegate networking to Docker

### Final decision

Remove the application's separate network setting and override. Use Docker's default
networking. Normal internet access is desired; file and package isolation remain
the focus.

### Required changes

- Remove `container_network` from advanced defaults, allowed settings, UI fields,
  frontend settings payloads, and validation.
- Stop honoring `VIRTUS_CONTAINER_NETWORK`. Ignore legacy saved values rather than
  allowing old settings to force a network override.
- Remove `--network` from the generated `docker run` command. Docker chooses its
  normal default network, usually `bridge`.
- Remove app-specific network-mode rejection logic that exists only for this
  setting. Do not add host networking or another replacement override.
- Do not add custom network profiles, egress proxies, firewalls, allowlists, or
  offline mode as part of this work.
- Preserve existing containers' Docker-managed network attachments. Do not recreate
  them just to normalize networking; newly created containers use Docker defaults.
- Keep the command/file/package isolation controls and the local web application's
  existing origin/CSRF protections. These are separate concerns.
- Update README, developer documentation, model-facing environment description,
  and tests to describe the resulting behavior accurately.

### Current implementation status

A network-removal implementation was made earlier in this discussion, then removed.
At handoff preparation the working tree was clean and the earlier implementation
was absent: `container_network`, its UI, and the `--network` flag were present again.
The current implementation removes this setting and override; regression tests cover it.

This document records the approved networking direction. The README now describes
Docker-managed networking.

## 7. Original review point 5: disk and agent-execution safeguards

### Final decision

Add practical disk safeguards and bounded agent execution. CPU/memory/PID limits
alone do not prevent disk exhaustion or indefinitely repeated tool calls.

### Required behavior

- Account for managed workspace storage and check available disk space. Account for
  Docker/container storage where observable too; the host workspace and Docker's
  VM/image storage may reside on different filesystems.
- Check before operations and monitor active execution sufficiently to stop/pause
  work when free space becomes critically low. Checking only after a long command
  finishes is not adequate protection.
- Report a clear reason and preserve chat history and existing files when limits
  are reached. Do not automatically delete host-shared files or user artifacts to
  recover space.
- Add per-turn wall-clock and tool-call budgets. Individual command timeouts do not
  bound a turn that repeatedly requests more commands.
- Enforce budgets in the backend, including MCP calls/waits where applicable, and
  use actual cancellation/cleanup rather than merely marking a turn complete while
  work continues in the background.
- Bound retained logs, tool output, and active stream replay storage. Preserve
  coherent tool-call/result history and clean completion/error signaling when a
  budget is exhausted. Retain existing output bounds where already sufficient.
- Coordinate stopping a runtime with other chats in the same Folder; clearly report
  shared-runtime interruption when stopping the container is necessary.
- Make limits configurable and validate them. Exact thresholds, budgets, and UI
  placement were not selected in the discussion; choose reasonable documented
  defaults and avoid excessive new configuration complexity.

Hard quotas are useful when the storage backend supports them reliably, but a custom
storage/VM architecture solely to implement quotas is not required. Disk accounting
and low-space checks are safeguards, not a guaranteed hard quota; document that
distinction. Upload limits alone do not constrain files created through Bash.

Likely touchpoints: `advanced_config.py`, `sandbox_runtime.py`,
`chat_turn_service.py`, `routes_chat.py`, `streaming.py`, `mcp_session_pool.py`,
`container_files.py`, settings and resource-related tests.

## 8. Explicitly deferred or rejected work

- Original point 6: browser/HTML preview isolation changes, frontend dependency
  vendoring, and related hardening. Ignore for now.
- Original point 7: separating credentialed MCP connectors into another broker or
  container and redesigning approval enforcement. Ignore for now.
- Docker commit/snapshot persistence and arbitrary environment replay.
- Automatic migration/merging of old container files onto new base images.
- A VM replacement, Kubernetes, or new multi-user/server infrastructure.
- Preserving/copying a standalone workspace when moving its chat into a Folder;
  the chosen change is the warning only.
- A new public workspace abstraction that replaces the existing Folder model.
- Additional per-Folder memory or a vector database.

Do not resurrect these proposals simply because they appeared in the initial review.

## 9. Implementation order and verification

Suggested order (an implementation choice):

1. Keep Folder workspaces shared and add the destructive-move warning.
2. Remove network configuration/overrides and update the corresponding docs.
3. Implement optional host-managed memory, with a JSON store.
4. Complete runtime configuration reconciliation, checking the managed workspace mount.
5. Add disk/turn/replay safeguards and update remaining documentation.

Use focused regression tests for the actual behavioral changes, then run the
existing suite and JavaScript syntax checks. In particular verify:

- Folder creation and ordinary metadata edits remain functional.
- Destructive move cancellation preserves data; the accepted move still deletes
  only the owned standalone workspace and preserves history/destination files.
- Resource/isolation mismatches are reconciled or rejected through every container
  reuse path; active Folder work is not silently interrupted.
- Concurrent memory writes do not silently lose updates. Disabled memory contributes
  no injected context or tool, rejects update calls, and exposes no app-managed
  writable memory mount. Toggling it does not recreate containers.
- Legacy memory and saved settings migrate predictably.
- Container creation passes no network override, obsolete network settings are
  ignored, and unrelated isolation settings remain effective.
- Disk/turn/replay limits stop work cleanly and preserve consistent saved history.

Keep tests isolated in temporary storage. Real Docker tests should use disposable,
uniquely named runtimes rather than modifying a user's existing computers.

At the original review, the suite reported 613 passed and 10 skipped. The discarded
network patch briefly reported 614 passed and 10 skipped. These are historical
results, not validation of this handoff or future implementation. Run current checks
and report actual results. Live Docker isolation was not verified in that review.

After implementation, update `README.md` and `devs.md` to describe implemented
behavior, remove contradictory instructions (particularly writable memory mounts
and network settings), and update this document's status.


## 10. Implementation verification (2026-10-27)

Implemented the destructive-move warning, Docker-managed
networking, optional revision-checked app-owned memory with JSON storage,
effective runtime validation, disk monitoring, cancellable turn/tool budgets, bounded
output/replay and corresponding settings/documentation. Memory defaults enabled.

Validation: 644 passed, 10 skipped, 1 deselected using the repository Python environment.
The deselected macOS menu-bar artwork test aborts in native AppKit in this headless
execution environment; the initial complete-suite attempt encountered that abort.
Live Docker tests remain opt-in and were not executed. Regression tests use temporary
storage and mocked Docker, plus a disposable host subprocess to verify active low-space
interruption. All first-party JavaScript modules pass syntax checks.

## 11. Review fixes and verification (2026-10-27)

The follow-up review findings are fixed:

- Container creation explicitly selects root UID/GID and private IPC/cgroup
  namespaces. Reuse accepts equivalent root-user representations, preventing
  repeated environment resets caused by image or daemon defaults.
- Storage recovery remains available through file-panel reads/downloads. Normal agent
  execution and uploads remain storage-limited; recovery does not enable arbitrary
  Bash/MCP bypasses.
- Failed authoritative-history reloads block turn and branch saves and new edits
  until recovery. A stale in-flight turn cannot overwrite history after another
  view reopens the chat.
- The pre-existing MCP 2.x timeout incompatibility is corrected in both persistent
  sessions and one-shot discovery/invocation. Timeout values are numeric seconds.

Validation after these fixes: 670 passed, 10 skipped, 1 AppKit test deselected,
using `.venv312/bin/pytest` with MCP 2.0.0. The older `.venv` contains MCP 1.27.1
and does not satisfy the declared `mcp>=2.0.0,<3` dependency.
All first-party JavaScript modules pass syntax checks and the diff passes whitespace
checks. A real disposable local stdio MCP server verifies discovery, calls,
cancellation, and reconnection. Live Docker tests were not run. The changed file
worker requires rebuilding the sandbox image through the normal startup flow.

The local development app was restarted with `.venv312/bin/python app.py`.
Its health check passed at `http://127.0.0.1:8080`. The setup requirements check
reported the sandbox image already matches the current worker identity, so setup
did not request a rebuild. No live Docker integration tests were performed.
