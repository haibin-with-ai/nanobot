# 2026-05-18 nanobot upstream sync plan

## Current facts

- Production checkout: `/root/git_code/nanobot`
- Production gateway service: `nanobot-gateway.service`
- Root cause of current outage: unfinished merge left conflict markers in `nanobot/nanobot.py`, causing `SyntaxError` on service restart.
- Production has been restored by aborting merge and restarting the gateway.
- Divergence from merge base `79234d23`:
  - fork/origin: 173 commits ahead
  - upstream: 796 commits ahead
- Direct merge from local branch to `upstream/main` produced 34 conflicted files and 614 conflict markers.

## Principle

Do not merge upstream into the live production checkout. All sync work happens in an isolated worktree.

Do not manually resolve the 34-file mega-conflict as the primary path. The conflicts concentrate in runtime core files: agent loop, runner, provider layer, Discord channel, cron, session manager, and config schema. A textual merge can pass syntax while silently breaking behavior.

Use `upstream/main` as the new base and replay local fork features in coherent feature packs.

## Worktree

Working branch:

```bash
/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
# branch: sync-upstream-2026-05-replay
# base: upstream/main ba38f908
```

## Local feature packs to replay

### Pack A — production-critical runtime identity and session metadata

Purpose: keep the current Discord/runtime experience and session audit fields.

Relevant local commits:
- `ecbd6c5a` channel_name in runtime context for Discord
- `d19396da` runtime context and model in JSONL logs
- `956d06f9` remove provider_name from runtime context
- `a0817fac` token usage per assistant message
- `1dbe6e34` LLM response timing per assistant message
- `bc8845e9` sender_name in runtime context
- `6f210ba0` sender_id and sender_name on user messages
- `f94bb61e` preserve runtime metadata on /new clear
- `0fc40b97` decouple session identity from date

Tests:
- session manager/runtime context tests
- Discord channel metadata tests
- targeted JSONL persistence tests

### Pack B — Anthropic Claude Code OAuth and provider routing

Purpose: preserve current production auth path and model routing.

Relevant local commits:
- `a5b8e468` Anthropic Claude Code OAuth provider
- `f1bf59c2` OAuth token handling
- `4a0fb3be` OAuth betas tests
- `9091fb6d` remove placeholder api_key in OAuth mode
- `a46f1fa3` token auto-refresh
- `fb81df11` credentials file 0600
- `04b5d64a` update auth_token in-place
- `ba1a435a` token expiry margin fix
- `8d0e9f4b` adaptive thinking/cache/usage parsing
- `b3b63008` adaptive defaults
- `46e15f76` openai_codex/github_copilot backend routing fix
- `99cfab0b` sanitize cross-provider tool IDs

Tests:
- `tests/providers/test_anthropic_token_refresh.py`
- Anthropic provider tests
- provider factory/routing tests

### Pack C — Discord improvements, mention filter, transcription, TTS

Purpose: preserve current Discord UX.

Relevant local commits:
- `2feda867` voice message transcription via Groq Whisper
- TTS chain `de1d0065` through `e7225168`
- `7d368391` allow webhook messages through bot filter
- `99cfab0b` ignore messages mentioning other bots

Tests:
- `tests/channels/test_discord_channel.py`
- `tests/providers/test_transcription.py` if present after replay
- TTS unit tests added with feature

### Pack D — command rewrite hook / rtk migration

Purpose: keep cross-cutting command rewrite behavior without embedding it in ExecTool.

Relevant local commits:
- `2713688e` original rtk rewrite support
- `88cd2924` CommandRewriteConfig schema
- `47392fb5` CommandRewriteHook
- `1fad5206` inject hook into main loop
- `094c7223` propagate hook into subagent runs
- `d24cdec0` remove rtk rewrite from ExecTool
- `63375b87`, `8cb53e21` docs
- `f6070e4d` accept rtk exit code 3

Tests:
- agent hook tests
- shell/exec tool tests
- config schema tests

### Pack E — subagent model override and trace/logging

Purpose: preserve independent subagent provider config and LLM logs.

Relevant local commits:
- `083902c3` subagent model/reasoning/max_tokens config
- `0aa55f57` subagent overrides
- `92a5c899` independent provider for subagent
- `f0296b79` provider test
- `e96865e4` AgentHookContext model field
- `e7c78354` per-spawn TraceHook + independent llm_logs file
- `3bf4e69b` propagate layout.llm_logs_dir
- `0a38f353` LLM req/resp + toolcall logs
- `1bf703ef`, `d32655ec`, `10db29bc`, `82e66e95` logging refinements
- `6e2e9860` per-spawn model override

Tests:
- subagent tests
- spawn tool tests
- LLM logging tests

### Pack F — memory/consolidation/context pruning

Purpose: preserve local memory behavior that upstream may now partially supersede.

Relevant local commits:
- `b461e878`, `9c0de5b6`, `25a01a76` context pruning
- `6a38c378` configurable consolidation trigger ratio
- related autocompact/consolidator commits in local branch

Tests:
- `tests/agent/test_context_pruner.py`
- `tests/agent/test_consolidation_ratio.py`
- `tests/agent/test_consolidator.py`
- `tests/agent/test_loop_consolidation_tokens.py`

### Pack G — tools and workspace behavior

Purpose: preserve operational local behavior.

Relevant local commits/features:
- `b19d219b` extra_allowed_paths, unless intentionally dropped by later `e99b4f4c`
- `11f6df84` ripgrep backend for grep
- message tool suppress/deliver behavior as required by current runtime
- workspace layout and `llm_logs_dir`

Tests:
- filesystem/search/message/spawn tests

### Pack H — local docs and assistant bootstrap

Purpose: preserve local instructions only where they still make sense.

Relevant commits:
- `75266d8e` CLAUDE.md
- `5369da7e`, `55de6e88`, `0b2565a8`, `ca66cb44`, `74681de2` bootstrap/soul changes
- local superpowers plans/changelog commits

Tests:
- context builder/bootstrap tests

## Execution strategy

For each pack:

1. Create a fresh checkpoint commit before the pack if working tree is clean.
2. Cherry-pick the smallest coherent sequence, not the entire local branch.
3. If cherry-pick conflicts, resolve against upstream architecture, not by preserving old call sites blindly.
4. Add or preserve tests that prove the behavior.
5. Run targeted tests for the pack.
6. Commit the pack.

After all packs:

```bash
python3 -m compileall nanobot
python3 -m pytest <targeted test groups>
python3 -m pytest
python3 -m ruff check nanobot tests
```

Then run a gateway smoke test from the worktree before replacing production.

## Production rollout

Do not deploy by editing the live checkout during merge.

Recommended rollout:

1. Push replay branch to origin.
2. Stop `nanobot-gateway.service`.
3. Fast-forward or reset production checkout to the tested branch commit.
4. Start service.
5. Verify:
   - `systemctl --user is-active nanobot-gateway`
   - `journalctl --user -u nanobot-gateway --since '5 minutes ago'`
   - Discord READY
   - one actual DM round-trip

## Abort criteria

Stop and reassess if:

- More than three pack-level cherry-pick conflict rounds reveal coupled architecture changes.
- A pack requires editing three or more unrelated runtime subsystems.
- Full tests fail in upstream-only baseline before local replay.
