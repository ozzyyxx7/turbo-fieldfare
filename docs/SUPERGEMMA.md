# SuperGemma support

TurboFieldfare can install and run one additional, allowlisted Gemma 4
checkpoint:

| Field | Pinned value |
| --- | --- |
| Profile | `supergemma` |
| Repository | `Jiunsong/supergemma4-26b-uncensored-mlx-4bit-v2` |
| Revision | `1ecb7582718b813fc4c7b5c3131b2b7053787f00` |
| `model.safetensors.index.json` SHA-256 | `df3133d5e9e400092664cb2197413a32035189ab7c41f4b000f75a284abdc512` |
| Installed directory in a checkout | `scratch/supergemma4.gturbo` |
| Server model ID | `supergemma-4-26b-a4b-uncensored` |

The importer accepts the checkpoint only when repository ID, resolved commit,
and index SHA-256 all match this entry. It records the source identity in the
manifest and verified-install receipt. Stock and SuperGemma use separate model
directories, tokenizer sidecars, and resume state, while the app remembers the
selected variant in its shared settings.

## Install and verify

The Mac app exposes **Variant** in the right-side Model section. Select
**SuperGemma 4 26B**, then choose **Download**.

The equivalent command is:

```bash
swift run -c release TurboFieldfareRepack \
  --source supergemma \
  --output scratch/supergemma4.gturbo \
  --overwrite
```

An interrupted install is resumable:

```bash
swift run -c release TurboFieldfareRepack \
  --source supergemma \
  --output scratch/supergemma4.gturbo \
  --overwrite \
  --resume
```

After installation, verify every recorded file without loading the model:

```bash
swift run -c release TurboFieldfareRepack \
  --verify-install \
  --input-gturbo scratch/supergemma4.gturbo
```

Running this command also upgrades source identity metadata written by older
versions of the verifier; it does not redownload model weights.

Run a deterministic raw-completion smoke test:

```bash
swift run -c release TurboFieldfareCLI \
  --model scratch/supergemma4.gturbo \
  --prompt "TurboFieldfare is" \
  --max-new 16 \
  --temperature 0
```

## Codex agent integration

The checkout includes a user-skill and zero-dependency STDIO MCP bridge at
`Integrations/Codex/supergemma-local`. From the repository root, build the
release server, link the skill, and register the bridge:

```bash
swift build -c release --product TurboFieldfareServer
repo_root="$(pwd -P)"
node_bin="$(command -v node)"
mkdir -p "$HOME/.agents/skills"
ln -s "$repo_root/Integrations/Codex/supergemma-local" \
  "$HOME/.agents/skills/supergemma-local"
codex mcp add supergemma -- \
  "$node_bin" \
  "$repo_root/Integrations/Codex/supergemma-local/scripts/mcp-server.mjs"
```

The link and registration commands intentionally fail if entries with those
names already exist. Inspect and update an existing installation rather than
overwriting it. Add these keys inside the generated
`[mcp_servers.supergemma]` section in `~/.codex/config.toml` so first load and
long requests have enough time:

```toml
startup_timeout_sec = 10
tool_timeout_sec = 900
enabled_tools = ["ask_supergemma", "supergemma_status", "stop_supergemma"]
```

Verify registration with `codex mcp get supergemma --json`, then restart an
already-open Codex surface once. Another Codex agent can explicitly invoke
`$supergemma-local` and use:

- `ask_supergemma` for a bounded reasoning, drafting, or code-review request.
- `supergemma_status` for a read-only lifecycle check.
- `stop_supergemma` to stop only the authenticated server that the bridge
  started.

The bridge uses a 4K context, starts the model lazily, authenticates every
loopback request with a private per-launch token, and shares TurboFieldfare's
per-user model lease with the server, CLI, and Mac app. Model output remains
untrusted advisory text; the calling Codex agent must verify it before acting.

## Memory choices on a 16 GB Mac

Start with 16 slots and a 4K context. The 8-slot option uses less memory and
now adapts chunked-prefill tile width to its cache budget. Use 24 or 32 only
after measuring a fixed prompt. The 64-slot option is exposed for controlled
experiments, adds about 4.84 GB over the default, and may be slower under
memory pressure. A 128-slot full cache is intentionally not available.

For comparable CLI runs, pass `--expert-cache-slots 8`, `16`, `24`, `32`, or
experimental `64`. Keep prompt, context, sampling, and generated-token count
fixed, and run only one local-model process at a time.

## Scope and caution

The checkpoint has the same language tensor names, shapes, dtypes,
quantization layout, and tokenizer vocabulary as the pinned stock source, so
it uses the existing Gemma 4 kernels. Its weights and chat template are
different. Treat behavior, safety, factuality, and tool-call quality as model
properties that require separate evaluation. TurboFieldfare does not endorse
or guarantee generated content.
