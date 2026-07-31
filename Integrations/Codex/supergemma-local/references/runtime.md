# SuperGemma local runtime

## Boundary

The integration is local to this Mac. It uses
`http://127.0.0.1:8080`. A managed launch generates a private bearer token and
requires it on every HTTP route; the token stays in the user-only runtime state
and is never returned as an MCP result. The endpoint has no TLS and is not a
remote service. Never proxy, tunnel, or expose it.

The configured API model ID is
`supergemma-4-26b-a4b-uncensored`. The managed server uses a 4,096-token
context and the installed model at
`scratch/supergemma4.gturbo`.

## MCP tools

- `supergemma_status` performs read-only process, port, model-ID, ownership,
  listener-PID, and memory-pressure checks.
- `ask_supergemma` serializes startup and inference across Codex processes,
  verifies that the exact listener rejects an unauthenticated probe before
  sending the private bearer, then checks the authenticated health response and
  exact model ID before sending one non-streaming Chat Completions request.
- `stop_supergemma` waits for an active managed request, then sends SIGTERM only
  when the private state, PID, process start identity, full expected launch
  arguments, bearer-authenticated response, and listener PID all match. It
  never force-kills a process.

The managed server remains available for later agents and requests until
`stop_supergemma` is called or the process exits.

If startup itself fails, the controller owns the exact `Popen` child and rolls
that child back with SIGTERM. It may use SIGKILL only on that still-unreaped
direct child after a 15-second grace period; this path never targets a PID
rediscovered from state or process scanning.

## Single-model ownership

TurboFieldfare Server, CLI, and the Mac app's DecodeService cooperatively
acquire the same per-user `flock` before loading a model. The lock is released
automatically by the kernel on process exit. This covers the official
TurboFieldfare executables for the current macOS account in both startup
directions:

- An open TurboFieldfare Mac app is a conservative blocker for managed-server
  startup.
- A later GUI or CLI model load fails while the managed server owns the model
  lease.
- Two MCP clients serialize startup and reuse the same verified server.

This is not a machine-wide security boundary against another macOS account,
`root`, or a same-user process that deliberately ignores or tampers with the
advisory lock.

The controller also rejects `TurboFieldfarePackageTests`,
`swiftpm-testing-helper`, `mlx_lm`, and `mlx-lm` model owners. It never
terminates these blockers.

## Runtime state

Private state and logs live under:

`~/Library/Application Support/TurboFieldfare/supergemma-local`

`server-state.json` identifies only a server started by this integration.
Stale state is ignored when the PID, process start identity, executable,
arguments, authenticated response, or listener PID no longer match. State,
locks, and logs are user-owned and private (`0700` directory, `0600` files).
The status command does not create this directory when it does not exist.

Common states:

- `stopped`: no target server or blocker.
- `starting`: an owned server is loading the model.
- `running`: the exact model is healthy.
- `blocked`: another protected model process is active.
- `conflict`: the target server and another protected process overlap.
- `external_server`: an expected-looking service is not owned and authenticated
  by this integration.
- `authentication_not_enforced`: the exact launched process accepted an
  unauthenticated request and is rejected as a stale or unsafe build.
- `wrong_model`, `port_conflict`, or `unknown_server`: port 8080 is not the
  expected SuperGemma service.

## Output handling

SuperGemma receives only text included in the MCP call. It has no filesystem,
shell, browser, or Codex tool access. The MCP server does not provide function
tools to SuperGemma.

`end_to_end_completion_tokens_per_second` measures the whole HTTP request
duration. Do not report it as pure model decode throughput.
