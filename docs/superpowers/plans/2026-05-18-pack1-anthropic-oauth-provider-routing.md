# Pack1 — Anthropic Claude Code OAuth / Provider Routing Replay Plan

> 历史归档，非当前实现。基座为 ba38f908（2026-05-18），与 upstream/main=3f808d0a 之后的结构不再对应。

## 0. Context

This plan is for the real upstream sync of `haibin-with-ai/nanobot`.

Do **not** execute it in the production checkout:

```bash
/root/git_code/nanobot
```

Execute only in the isolated replay worktree:

```bash
/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
```

Current replay branch:

```bash
sync-upstream-2026-05-replay
```

Replay base:

```bash
upstream/main ba38f908
```

Production outage root cause on 2026-05-18 was an unfinished upstream merge in the live checkout that left conflict markers in `nanobot/nanobot.py`. This pack must not repeat that mistake.

## 1. Goal

Replay the fork's production-critical Anthropic Claude Code OAuth and provider-routing behavior onto upstream `main`, without dragging old runtime architecture into the new upstream base.

This pack must preserve these behaviors:

1. `anthropic_claude_code` is a first-class OAuth provider option.
2. Claude Code OAuth access tokens are sent as `Authorization: Bearer ...`, not as `x-api-key`.
3. Claude Code required beta headers are sent only in OAuth mode.
4. OAuth credentials can be loaded from nanobot's own credential store and migrated from Claude CLI credentials.
5. Expired or near-expiry OAuth tokens refresh automatically before requests.
6. Refreshed tokens update both the credential store and the live Anthropic SDK client.
7. Claude Code model aliases are converted to Claude Code-compatible names when OAuth mode is active.
8. Adaptive thinking works for newer Claude models without forcing incompatible temperature or beta fields.
9. Prompt-cache markers and usage parsing remain correct.
10. Tool IDs passed to Anthropic are valid Anthropic tool-use IDs, including cross-provider IDs from fallback flows.
11. `openai_codex` and `github_copilot` backend routing remains intact when the provider factory creates providers.

## 2. Explicit non-goals

This pack must **not** implement:

- Discord mention filtering. That belongs to Pack2.
- TTS or voice transcription. That belongs to Pack2.
- session runtime metadata. That belongs to Pack3.
- command rewrite / rtk. That belongs to Pack4.
- subagent model override or trace logging. That belongs to Pack5.
- memory/consolidation/context pruning. That belongs to Pack6.
- grep/search/message/workspace tool behavior. That belongs to Pack7.
- SOUL/bootstrap/docs behavior. That belongs to Pack8.

Do not cherry-pick a broad local commit if it pulls one of these unrelated changes into Pack1. Split manually instead.

## 3. Source commits from the fork

Use these local commits as source material, not as blind cherry-pick targets:

```text
a5b8e468 feat: add Anthropic Claude Code OAuth provider
f1bf59c2 fix: correct OAuth token handling for Anthropic Claude Code
4a0fb3be test: add Anthropic OAuth betas tests
9091fb6d fix: do not pass placeholder api_key to Anthropic client in OAuth mode
a46f1fa3 feat: add Anthropic OAuth token auto-refresh
fb81df11 fix(oauth): write credentials with 0600 permissions
04b5d64a fix(anthropic): update auth_token in-place after OAuth refresh
ba1a435a fix(oauth): remove double margin subtraction from token expiry
8d0e9f4b fix(anthropic): support opus-4.7/sonnet-4.7 adaptive thinking, fix cache markers and usage parsing
b3b63008 fix(anthropic): default claude-4.7 models to adaptive reasoning
46e15f76 fix: _make_single_provider missing openai_codex and github_copilot backend routing
99cfab0b fix(anthropic): sanitize cross-provider tool IDs
```

If a commit contains mixed changes, extract only the provider-routing part.

## 4. Files expected to change

### Production code

```text
nanobot/providers/anthropic_provider.py
nanobot/providers/oauth_store.py
nanobot/providers/registry.py
nanobot/providers/factory.py
nanobot/config/schema.py
nanobot/cli/commands.py
nanobot/nanobot.py
nanobot/providers/base.py
nanobot/providers/openai_compat_provider.py
```

### Tests

```text
tests/providers/test_anthropic_token_refresh.py
tests/providers/test_oauth_refresh.py
tests/providers/test_oauth_store.py
tests/providers/test_anthropic_adaptive_thinking.py
tests/providers/test_anthropic_thinking.py
tests/providers/test_anthropic_tool_result.py
tests/providers/test_github_copilot_routing.py
tests/providers/test_openai_codex_provider.py
tests/test_make_provider_fallback.py
```

Some files may already exist upstream under different names. Do not duplicate tests. Extend the upstream test file if that is the cleaner home.

## 5. Upstream baseline observations

At `upstream/main ba38f908`:

- `ProviderSpec.is_oauth` already exists.
- `openai_codex` and `github_copilot` are already OAuth provider specs.
- `factory.py` already has backend branches for `openai_codex`, `github_copilot`, `anthropic`, and `bedrock`.
- Anthropic provider already supports:
  - prompt caching,
  - thinking blocks,
  - adaptive thinking,
  - streaming thinking deltas,
  - usage extraction,
  - generated Anthropic-style tool IDs.
- Upstream does **not** yet contain the fork's `nanobot/providers/oauth_store.py` for Anthropic Claude Code OAuth.
- Upstream does **not** yet contain `anthropic_claude_code` in `ProvidersConfig`.
- Upstream does **not** yet contain the fork's Claude Code OAuth token refresh path.

This means Pack1 should adapt the fork's OAuth layer to upstream's current provider architecture. Do not overwrite upstream's newer Anthropic provider wholesale.

## 6. Design decisions

### 6.1 Provider identity

Add a provider spec:

```python
ProviderSpec(
    name="anthropic_claude_code",
    keywords=("anthropic-claude-code", "claude-code"),
    env_key="",
    display_name="Anthropic Claude Code",
    backend="anthropic",
    is_oauth=True,
    supports_prompt_caching=True,
)
```

The explicit provider name should be usable in config:

```json
{
  "agents": {
    "defaults": {
      "provider": "anthropic_claude_code",
      "model": "anthropic/claude-opus-4-6"
    }
  }
}
```

### 6.2 Config schema

Add to `ProvidersConfig`:

```python
anthropic_claude_code: ProviderConfig = Field(
    default_factory=ProviderConfig,
    exclude=True,
)
```

Reason: OAuth credentials are not normal config secrets and should not be serialized into user config templates.

### 6.3 OAuth token detection

OAuth mode should activate when either condition is true:

1. provider registry name is `anthropic_claude_code`, or
2. the supplied key looks like a Claude Code OAuth access token, e.g. `sk-ant-oat...`.

Do not rely only on key prefix. In real production config, OAuth credentials may be loaded from the store and `api_key` may be absent at provider-construction time.

### 6.4 Anthropic SDK construction

In normal Anthropic API-key mode:

```python
AsyncAnthropic(api_key=api_key, base_url=api_base, default_headers=extra_headers, max_retries=0)
```

In Claude Code OAuth mode:

- do **not** send OAuth token as `api_key` if that makes the SDK also emit `x-api-key`;
- send OAuth token through `Authorization: Bearer <token>`;
- include Claude Code required beta headers;
- preserve `max_retries=0` so retries remain centralized in `LLMProvider._run_with_retry`.

The live client must be recreated or safely updated after token refresh. If upstream SDK internals allow `auth_token` mutation, cover it with a test. If not, recreate the client through a small private method.

Prefer a small method:

```python
def _build_client(self) -> AsyncAnthropic: ...
```

so refresh does not duplicate constructor logic.

### 6.5 Credential store

Add `nanobot/providers/oauth_store.py` with:

- `OAuthCredentials`
- `OAuthCredentialStore`
- `refresh_anthropic_token`
- `TOKEN_REFRESH_MARGIN_MS = 5 * 60 * 1000`

Credential source priority:

1. nanobot store: `get_data_dir() / "oauth_credentials.json"`
2. Claude CLI store: `~/.claude/.credentials.json`

On save:

- create parent directories;
- write JSON;
- chmod file to `0600`.

Do not write back to Claude CLI's file. Treat it as read-only import material.

### 6.6 Refresh behavior

Before every Anthropic request in OAuth mode:

```python
await self._ensure_valid_token()
```

Rules:

- if no credential store, do nothing;
- if no credentials, log warning and continue with current token;
- if token is not expired within margin, do nothing;
- if expired, refresh with refresh token;
- save new credentials;
- update in-memory expiry;
- update the live Anthropic client auth token;
- protect refresh with `asyncio.Lock`;
- re-check expiry after acquiring lock so concurrent requests refresh only once.

### 6.7 Claude Code model naming

When OAuth mode is active, normalize Claude model names to Claude Code-compatible names.

Examples:

```text
anthropic/claude-opus-4-6   -> claude-opus-4-6
anthropic/claude-sonnet-4-6 -> claude-sonnet-4-6
```

Do not change model names for normal API-key Anthropic mode unless upstream already does so.

### 6.8 Adaptive thinking

Preserve upstream's adaptive thinking implementation unless the fork has a narrower production fix.

Acceptance rules:

- `reasoning_effort="adaptive"` sends `thinking={"type": "adaptive"}`.
- Claude 4.6+ or 4.7+ models default to adaptive when no explicit `reasoning_effort` is provided, if that is the current fork behavior we choose to preserve.
- Do not send `temperature` when adaptive thinking forbids it.
- Do not send incompatible beta headers in non-OAuth mode.
- Do not regress existing upstream thinking tests.

### 6.9 Tool ID sanitization

Anthropic tool IDs must be valid Anthropic IDs. Cross-provider fallback may pass IDs from OpenAI-compatible providers.

Implement a deterministic sanitizer:

```python
def _sanitize_tool_id(value: Any) -> str:
    ...
```

Rules:

- preserve valid `toolu_...` IDs;
- convert invalid IDs into stable `toolu_<sha1-prefix>` IDs;
- use the same sanitizer for:
  - assistant `tool_use` IDs,
  - user `tool_result.tool_use_id` references.

This avoids a mismatch where tool calls are sanitized but tool results still reference the original invalid ID.

## 7. TDD task sequence

Follow this exact order. Do not write implementation for a task before its failing test exists.

### Task 1 — provider registry and config schema

Write/extend tests that assert:

```python
find_by_name("anthropic_claude_code").is_oauth is True
find_by_name("anthropic_claude_code").backend == "anthropic"
Config().providers has attribute anthropic_claude_code
```

Run:

```bash
python3 -m pytest tests/config/test_config_migration.py tests/providers/test_providers_init.py -q
```

Expected RED: tests fail because provider/config does not exist.

Implement:

- add registry spec;
- add schema field.

Run same tests until green.

Commit:

```bash
git commit -am "feat(provider): register Anthropic Claude Code OAuth provider"
```

If new test files were added, include them explicitly.

### Task 2 — OAuth credential store

Add tests for:

- `OAuthCredentials.is_expired()` past expiry;
- not expired when far future;
- expired when within 5-minute margin;
- save/load round trip;
- saved file mode is `0600` on POSIX;
- missing store returns `None`;
- Claude CLI credential loading;
- nanobot store takes precedence over Claude CLI file;
- refresh token request posts correct payload;
- refresh raises `RuntimeError` on HTTP failure;
- invalid JSON raises `RuntimeError`.

Run:

```bash
python3 -m pytest tests/providers/test_oauth_store.py tests/providers/test_oauth_refresh.py -q
```

Expected RED: module missing.

Implement `nanobot/providers/oauth_store.py`.

Run tests until green.

Commit:

```bash
git add nanobot/providers/oauth_store.py tests/providers/test_oauth_store.py tests/providers/test_oauth_refresh.py
git commit -m "feat(provider): add Anthropic OAuth credential store"
```

### Task 3 — Anthropic OAuth client construction

Add tests that instantiate `AnthropicProvider` in OAuth mode and assert:

- OAuth token is not passed as normal `api_key` if that would emit `x-api-key`;
- default headers include `Authorization: Bearer ...`;
- Claude Code beta headers are present in OAuth mode;
- non-OAuth Anthropic mode still passes `api_key` normally;
- normal mode does not receive Claude Code beta headers.

Prefer monkeypatching `anthropic.AsyncAnthropic` and inspecting constructor kwargs.

Run:

```bash
python3 -m pytest tests/providers/test_anthropic_oauth_client.py -q
```

Expected RED.

Implement:

- `credential_store` and/or `provider_name` constructor argument;
- `_is_oauth` flag;
- `_build_client()` helper;
- OAuth header construction.

Update factory so it passes enough provider identity for Anthropic to know whether it is in `anthropic_claude_code` mode.

Run tests until green.

Commit:

```bash
git add nanobot/providers/anthropic_provider.py nanobot/providers/factory.py tests/providers/test_anthropic_oauth_client.py
git commit -m "feat(anthropic): support Claude Code OAuth client mode"
```

### Task 4 — token refresh integration

Add/port tests from fork:

```text
tests/providers/test_anthropic_token_refresh.py
```

Required cases:

- valid token does not refresh;
- expired token refreshes before request;
- concurrent requests refresh only once;
- refreshed credentials are saved;
- live provider client uses the new token after refresh;
- refresh failure returns or raises through existing provider error path without corrupting old credentials.

Run:

```bash
python3 -m pytest tests/providers/test_anthropic_token_refresh.py -q
```

Expected RED.

Implement:

- `_ensure_valid_token()`;
- `_update_oauth_token()`;
- refresh lock;
- call `_ensure_valid_token()` before `messages.create()` in both `chat()` and `chat_stream()` if streaming also uses the same client.

Run targeted tests until green.

Commit:

```bash
git add nanobot/providers/anthropic_provider.py tests/providers/test_anthropic_token_refresh.py
git commit -m "feat(anthropic): refresh Claude Code OAuth tokens"
```

### Task 5 — provider factory routing for OAuth providers

Add tests that prove:

- `model="openai-codex/..."` routes to `OpenAICodexProvider`;
- `model="github_copilot/..."` routes to `GitHubCopilotProvider`;
- explicit `provider="anthropic_claude_code"` with Claude model routes to `AnthropicProvider` in OAuth mode;
- OAuth providers are not selected as fallback providers unless explicitly named;
- gateway/local/provider matching still works for normal OpenAI-compatible providers.

Run:

```bash
python3 -m pytest tests/providers/test_github_copilot_routing.py tests/providers/test_openai_codex_provider.py tests/test_make_provider_fallback.py -q
```

Expected RED only where routing is missing.

Implement minimal changes in:

```text
nanobot/providers/factory.py
nanobot/nanobot.py
nanobot/config/schema.py
```

Do not rewrite provider matching wholesale.

Commit:

```bash
git add nanobot/providers/factory.py nanobot/nanobot.py nanobot/config/schema.py tests/providers/test_github_copilot_routing.py tests/providers/test_openai_codex_provider.py tests/test_make_provider_fallback.py
git commit -m "fix(provider): preserve OAuth backend routing"
```

### Task 6 — adaptive thinking and cache/usage parity

Add/port focused tests for the fork behavior:

- `claude-opus-4-6` / `claude-sonnet-4-6` / future `4.7` patterns default or accept adaptive thinking as intended;
- adaptive thinking omits temperature if Anthropic rejects temperature with adaptive mode;
- prompt cache markers are preserved on system/user/tool blocks;
- usage includes prompt, completion, total, cache creation/read, and `cached_tokens` where present;
- no incompatible beta headers are sent in normal non-OAuth mode.

Run:

```bash
python3 -m pytest \
  tests/providers/test_anthropic_adaptive_thinking.py \
  tests/providers/test_anthropic_thinking.py \
  tests/providers/test_cached_tokens.py \
  tests/providers/test_prompt_cache_markers.py \
  -q
```

Expected RED only for fork-specific missing behavior.

Implement minimal changes in:

```text
nanobot/providers/anthropic_provider.py
nanobot/providers/base.py
nanobot/providers/openai_compat_provider.py
```

Only touch `openai_compat_provider.py` if required by a shared `usage` or `reasoning_effort` test. Otherwise leave upstream alone.

Commit:

```bash
git add nanobot/providers/anthropic_provider.py nanobot/providers/base.py nanobot/providers/openai_compat_provider.py tests/providers/test_anthropic_adaptive_thinking.py
git commit -m "fix(anthropic): preserve adaptive thinking and usage parsing"
```

### Task 7 — cross-provider tool ID sanitization

Add tests that build messages with non-Anthropic tool call IDs, e.g.:

```text
call_abc123
chatcmpl-tool-1
123
```

Assert converted Anthropic request contains stable `toolu_...` IDs and matching `tool_result.tool_use_id` values.

Run:

```bash
python3 -m pytest tests/providers/test_anthropic_tool_result.py -q
```

Expected RED.

Implement `_sanitize_tool_id()` and apply it in both tool-use and tool-result conversion paths.

Commit:

```bash
git add nanobot/providers/anthropic_provider.py tests/providers/test_anthropic_tool_result.py
git commit -m "fix(anthropic): sanitize cross-provider tool ids"
```

### Task 8 — CLI login/logout/status wiring

If upstream CLI already has generic OAuth login/logout infrastructure, extend it. Do not duplicate a separate Anthropic-only command tree unless upstream architecture requires it.

Add tests for:

- `nanobot provider login anthropic-claude-code` resolves provider;
- `nanobot provider logout anthropic-claude-code` removes nanobot OAuth credentials;
- status display marks it as OAuth provider and does not require `ANTHROPIC_API_KEY`.

Run:

```bash
python3 -m pytest tests/cli/test_commands.py tests/command/test_model_command.py -q
```

Commit:

```bash
git add nanobot/cli/commands.py tests/cli/test_commands.py tests/command/test_model_command.py
git commit -m "feat(cli): wire Anthropic Claude Code OAuth commands"
```

If CLI support is intentionally postponed, document it in the commit message and leave provider runtime working. Do not half-wire broken commands.

## 8. Pack-level verification

After all tasks in this pack:

```bash
python3 -m compileall nanobot
python3 -m pytest \
  tests/providers/test_oauth_store.py \
  tests/providers/test_oauth_refresh.py \
  tests/providers/test_anthropic_token_refresh.py \
  tests/providers/test_anthropic_adaptive_thinking.py \
  tests/providers/test_anthropic_thinking.py \
  tests/providers/test_anthropic_tool_result.py \
  tests/providers/test_cached_tokens.py \
  tests/providers/test_prompt_cache_markers.py \
  tests/providers/test_github_copilot_routing.py \
  tests/providers/test_openai_codex_provider.py \
  tests/test_make_provider_fallback.py \
  tests/providers/test_providers_init.py \
  -q
python3 -m ruff check \
  nanobot/providers/anthropic_provider.py \
  nanobot/providers/oauth_store.py \
  nanobot/providers/factory.py \
  nanobot/providers/registry.py \
  nanobot/config/schema.py \
  nanobot/cli/commands.py \
  tests/providers \
  tests/cli \
  tests/command
```

Do not claim Pack1 is done unless these commands pass or every failure is explicitly documented as unrelated upstream baseline failure.

## 9. Manual smoke check after tests

From the worktree, run a non-production smoke check:

```bash
python3 - <<'PY'
from nanobot.config.schema import Config
from nanobot.providers.factory import make_provider

cfg = Config()
cfg.agents.defaults.provider = "anthropic_claude_code"
cfg.agents.defaults.model = "anthropic/claude-opus-4-6"
provider = make_provider(cfg)
print(type(provider).__name__)
print(getattr(provider, "default_model", None))
print(getattr(provider, "_is_oauth", None))
PY
```

Expected:

```text
AnthropicProvider
anthropic/claude-opus-4-6
True
```

Do not send a real Anthropic request in this pack unless the user explicitly approves using live credentials.

## 10. Rollback plan

Because this pack is implemented as several small commits, rollback is one of:

```bash
git revert <pack1-commit-range>
```

or, before merging to production:

```bash
git reset --hard upstream/main
```

Never rollback by editing the production checkout during service runtime.

## 11. Completion criteria

Pack1 is complete only when:

- Anthropic Claude Code OAuth provider is registered and config-valid.
- OAuth credentials store and refresh tests pass.
- Anthropic client construction tests prove Bearer-auth mode and normal API-key mode both work.
- Factory routing tests prove OAuth providers are not lost.
- Adaptive thinking/cache/usage tests pass.
- Tool ID sanitization tests pass.
- Pack-level compile, targeted pytest, and ruff checks pass.
- The final commit range is documented for later Packs.

