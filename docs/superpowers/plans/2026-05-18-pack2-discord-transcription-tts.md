# Pack2 — Discord UX, Voice Transcription, and TTS Replay Plan

> 历史归档，非当前实现。基座为 ba38f908（2026-05-18），与 upstream/main=3f808d0a 之后的结构不再对应。

## 0. Context

This plan is for the isolated upstream replay worktree only:

```bash
/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
# branch: sync-upstream-2026-05-replay
# replay base: upstream/main ba38f908
```

Do **not** run this pack in the production checkout:

```bash
/root/git_code/nanobot
```

Do **not** implement this plan while writing it. This document is the handoff for a later execution agent.

The current replay branch already contains Pack1 work, so the working tree is ahead of `upstream/main`. Pack2 must replay only Discord UX, transcription, and TTS behavior. Treat provider-routing/OAuth, runtime identity, cron, memory, CLI, deployment, and generalized architecture cleanup as other packs.

Pack2 is deliberately not a cherry-pick plan. The fork Discord implementation used a custom Gateway websocket + REST client. Upstream now uses `discord.py` with richer channel/thread handling, streaming edits, slash-command registration, attachment helpers, and send retry semantics. Replay behavior onto the upstream shape. Do not drag the old websocket channel back in like a suitcase full of bricks.

## 1. Goal

Replay the fork's production Discord user-experience behavior onto the upstream `discord.py` channel architecture:

1. Ignore Discord messages that mention another bot but do not mention this nanobot instance.
2. Continue allowing webhook-originated Discord messages through the bot-message filter.
3. Add Discord voice/audio attachment transcription via the existing upstream transcription provider layer, defaulting to Groq Whisper.
4. Add TTS support:
   - TTS provider layer with Edge TTS and Fish Audio.
   - Config schema for TTS.
   - `/tts`, `/tts on`, `/tts off` built-in command storing session metadata.
   - Propagate `session.metadata["tts"]` into outbound message metadata as `_session_tts`.
   - Propagate `_session_tts` into `MessageTool`-sent same-turn messages.
   - Let Discord synthesize outbound text into an audio attachment when TTS is active, with optional future voice-message path isolated behind a fallback, not required for first working replay.

The output of this pack should be a working, tested Pack2 commit that preserves upstream channel behavior and adds fork UX features surgically.

## 2. Non-goals

Do not include any of these in Pack2:

- Pack1 Anthropic Claude Code OAuth/provider routing work, including `anthropic_provider.py`, OAuth token stores, or cross-provider tool ID sanitization. Commit `99cfab0b` contains both Discord and Anthropic changes; take only the Discord part.
- Pack3/4/5/6/7/8 work from the master upstream-sync plan.
- Replacing upstream `discord.py` implementation with the fork's websocket Gateway implementation.
- Rewriting channel/session architecture.
- Changing provider registry behavior except where transcription/TTS needs existing API keys.
- Implementing a new general audio pipeline for every channel. Pack2 only wires Discord inbound transcription and Discord outbound TTS.
- Adding Azure TTS unless the current fork/upstream has a real provider implementation. The inspected fork file `nanobot/tts/azure.py` is just `# Removed - awaiting review`, so do not invent Azure support.
- Making Discord voice-message upload the primary required path. Fork history attempted voice-message upload, reverted to MP3 attachment, then re-attempted a 3-step voice upload. The inspected `origin/main` final file did **not** contain voice-upload code, only MP3 attachment mode. Implement MP3 attachment first. If a later executor finds a tested voice-upload implementation in a newer source, it may add it behind fallback tests, but that is optional and must not block Pack2 completion.

## 3. Source commits

Use these as behavioral references, not blind cherry-picks:

- `99cfab0b fix(discord): ignore messages mentioning other bots; fix(anthropic): sanitize cross-provider tool IDs`
  - Take only the Discord mention-filter behavior.
  - Ignore the Anthropic provider part in this pack.
- `7d368391 feat(discord): allow webhook messages through bot filter`
  - Take webhook allowance semantics.
- `2feda867 feat(discord): add voice message transcription via Groq Whisper`
  - Use current upstream `nanobot/providers/transcription.py`, not the old inline fork provider.
- TTS chain commits:
  - `de1d0065 feat(tts): add edge-tts dependency`
  - `33626311 feat(tts): add TTSConfig to schema`
  - `6affaa9a feat(tts): implement TTS provider layer with edge-tts`
  - `a1f216a0 feat(tts): add /tts on|off command`
  - `97a0c4b5 feat(tts): inject session_tts state into outbound metadata`
  - `d03cc703 feat(tts): integrate TTS into Discord channel layer`
  - `893f1915 feat(tts): add Fish Audio provider with voice cloning support`
  - `2fc9fe80 feat(tts): add Fish Audio speed config (default 1.2x)`
  - `af2a44bf feat(discord): send TTS as voice message (ogg/opus), skip text when TTS active`
  - `5f912d6d fix(tts): revert to MP3 attachment mode (voice message 400 error)`
  - `a37a6452 fix(tts): implement proper 3-step Discord voice message upload flow`
  - `4535918f fix(tts): pass session_tts to MessageTool so TTS triggers on tool-sent messages`
  - `e7225168 fix(tts): preserve session_metadata in _prepare_tools, init _session_metadata in MessageTool`

Read the final fork state from `origin/main` for TTS provider files because the final file state already incorporates the useful parts of this chain.

## 4. Files expected to change

Expected production files:

- `pyproject.toml`
  - Add `edge-tts` dependency in the appropriate dependency group.
- `nanobot/config/schema.py`
  - Add `TTSConfig`.
  - Add `Config.tts`.
  - Do not disturb existing `ChannelsConfig.transcription_provider` and `transcription_language`, already present upstream.
- `nanobot/tts/__init__.py`
  - New package marker/export file.
- `nanobot/tts/base.py`
  - New `TTSProvider` interface and `TTSError`.
- `nanobot/tts/edge.py`
  - New Edge TTS provider.
- `nanobot/tts/fish.py`
  - New Fish Audio provider.
- `nanobot/tts/factory.py`
  - New provider factory.
- `nanobot/tts/service.py`
  - New TTS service and trigger logic.
- `nanobot/command/builtin.py`
  - Add `/tts` command spec, handler, and router registrations.
- `nanobot/agent/loop.py`
  - Inject session TTS state into outbound metadata.
  - Ensure same-turn `MessageTool` context includes session metadata.
- `nanobot/agent/tools/message.py`
  - Carry `_session_tts` into same-target tool-sent outbound messages.
- `nanobot/channels/manager.py`
  - Instantiate and inject `TTSService` into Discord channel when available/enabled, or use a generic constructor signature inspection if the manager already has that pattern. Do not require non-Discord channels to accept TTS.
- `nanobot/channels/discord.py`
  - Mention-other-bot filter.
  - Webhook bot-filter behavior.
  - Inbound audio attachment transcription.
  - Outbound TTS attachment generation and send.

Expected test files:

- `tests/channels/test_discord_channel.py`
  - Add tests for mention-other-bot filter, webhook bot allowance, audio transcription markers, TTS attachment behavior.
- `tests/providers/test_tts.py` or `tests/tts/test_service.py`
  - Add unit tests for provider factory/service trigger logic. Prefer `tests/tts/test_service.py` if introducing a `tts` package test namespace.
- `tests/command/test_builtin_commands.py` or existing built-in command test file if present.
  - Add `/tts` command tests.
- `tests/agent/test_message_tool.py` or existing agent loop/tool tests.
  - Add metadata propagation test for `MessageTool` and/or `_assemble_outbound`.

Do not modify `nanobot/providers/anthropic_provider.py` in Pack2.

## 5. Upstream baseline observations

These are verified against the isolated worktree at plan time.

### 5.1 Worktree and branch

```bash
git -C /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge status --short --branch
# ## sync-upstream-2026-05-replay...upstream/main [ahead 2]

git -C /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge merge-base HEAD upstream/main
# ba38f9083291a899d62c9b4b2a7b46429c39b062
```

The branch is correct and based on `upstream/main ba38f908`.

### 5.2 Discord channel baseline

`nanobot/channels/discord.py` is now `discord.py` based.

Important current shape:

- `DiscordConfig` has fields including:
  - `enabled`
  - `token`
  - `allow_from`
  - `allow_channels`
  - `intents`
  - `group_policy: Literal["mention", "open"]`
  - slash command fields
  - proxy fields
  - emoji fields
- `DiscordChannel.__init__(self, config, bus)` currently has no TTS argument.
- `DiscordBotClient` owns outbound sending through `send_outbound()` and `_send_file()` using `discord.py` channel methods.
- Inbound handling path:
  - `DiscordBotClient.on_message()` delegates to `DiscordChannel._handle_discord_message()`.
  - Compatibility alias `DiscordChannel._on_message()` delegates to `_handle_discord_message()`.
  - `_handle_discord_message()` currently drops only self messages via `_bot_user_id`, drops system messages, builds `sender_id`, `channel_id`, content, metadata, downloads attachments, then calls `_handle_message(...)`.
  - Current docstring explicitly says other bots are allowed. Pack2 must reverse that only for the specific case “message mentions another bot and does not mention this bot”. Do not blanket-drop all bot authors.
- Attachment path:
  - `_download_attachments()` downloads to `get_media_dir("discord")` and returns marker strings and media paths.
  - `_compose_inbound_content()` appends markers to content.
- Group mention logic:
  - `_should_respond_in_group(message, content)` already detects self mention using `message.mentions`, `message.raw_mentions`, and raw `<@id>` content.

### 5.3 Transcription baseline

`nanobot/providers/transcription.py` already exists upstream. It contains:

- `OpenAITranscriptionProvider`
- `GroqTranscriptionProvider`
- retry helper `_post_transcription_with_retry(...)`
- support for `language`

`nanobot/config/schema.py` already includes channel-level transcription settings:

```python
class ChannelsConfig(Base):
    transcription_provider: str = "groq"
    transcription_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
```

`BaseChannel` already has class attributes:

```python
transcription_provider: str = "groq"
transcription_api_key: str = ""
transcription_api_base: str = ""
transcription_language: str | None = None
```

So Pack2 should not add a duplicate transcription provider layer. Wire Discord to the existing one.

### 5.4 TTS baseline

Upstream worktree currently has no `nanobot/tts` package and no `TTSConfig` in `nanobot/config/schema.py`.

Fork final state contains:

- `nanobot/tts/base.py`
- `nanobot/tts/edge.py`
- `nanobot/tts/fish.py`
- `nanobot/tts/factory.py`
- `nanobot/tts/service.py`
- `nanobot/tts/azure.py` as only `# Removed - awaiting review`

Final fork `origin/main:nanobot/channels/discord.py` uses MP3 attachment generation in `send()`:

```python
if self._tts_service and msg.content and not msg.metadata.get("_progress"):
    session_tts = msg.metadata.get("_session_tts", False)
    sender_name = msg.metadata.get("sender_name")
    if self._tts_service.should_trigger(session_tts=session_tts, sender_name=sender_name):
        audio_path = await self._tts_service.synthesize(msg.content)
        if audio_path:
            msg.media.insert(0, str(audio_path))
```

It sends text as well as the audio attachment. The later “skip text when TTS active / voice message” behavior is not present in inspected final fork. Therefore replay MP3 attachment behavior unless tests and source prove a newer voice-message path is safe.

### 5.5 Built-in command baseline

`nanobot/command/builtin.py` upstream uses:

- `BuiltinCommandSpec` tuple for help.
- `CommandContext` with `ctx.msg`, `ctx.session`, `ctx.key`, `ctx.args`, `ctx.loop`.
- Existing handlers construct `OutboundMessage` directly, not `ctx.make_response`.
- Register command near bottom with `router.exact(...)` / `router.prefix(...)`.

Fork `/tts` handler used `ctx.make_response`, but upstream `CommandContext` does not have `make_response`. Port behavior, not the helper.

### 5.6 MessageTool baseline

`nanobot/agent/tools/message.py` already has:

- `_default_metadata: ContextVar[dict]`
- `set_context(self, ctx: RequestContext)` stores `ctx.metadata`
- `__call__` uses `metadata = dict(self._default_metadata.get()) if same_target else {}`

It does not currently guarantee `_session_tts` in same-target sends. Pack2 should add that without breaking existing metadata propagation.

### 5.7 Agent loop baseline

`nanobot/agent/loop.py` already passes inbound metadata to tool context via `_set_tool_context(...)` and `RequestContext(metadata=dict(metadata or {}))`.

`_assemble_outbound(...)` currently creates `outbound_metadata` only for Slack thread and `origin_message_id`. Pack2 should add `_session_tts` based on the active session metadata at response time.

### 5.8 Channel manager baseline

`nanobot/channels/manager.py` currently constructs channels from config and dispatches outbound messages. It does not mention TTS.

Pack2 should instantiate one `TTSService(config.tts)` in `ChannelManager` only when TTS config exists and is enabled enough to construct safely. Inject it only into Discord. Keep non-Discord constructors unchanged.

## 6. Design decisions

### 6.1 Do not cherry-pick old Discord channel

The old fork channel is websocket/REST. Upstream channel is `discord.py`. Cherry-picking old files would erase upstream thread/channel behavior, slash commands, streaming, proxy handling, and tests. That is not a migration; that is vandalism with commit hashes.

### 6.2 Mention filter semantics

Desired behavior:

- Drop if the message mentions another bot and does **not** mention this bot.
- Accept if it mentions this bot, even if it also mentions another bot.
- Accept normal human messages in open-policy contexts.
- Preserve mention-only policy: in guild channels with `group_policy="mention"`, still require this bot mention.
- Do not blanket-drop bot-authored messages, because webhook messages and multi-agent workflows may matter.

Implement as a helper on `DiscordChannel`, e.g.:

```python
def _message_mentions_self(self, message: discord.Message, content: str) -> bool: ...
def _mentions_other_bot_only(self, message: discord.Message, content: str) -> bool: ...
```

Avoid duplicating slightly different self-mention logic inside `_should_respond_in_group` and the new filter. The old fork had this logic inline in `_should_handle_message`; upstream should get a small helper.

Edge cases to cover:

- `message.mentions` objects may have `.bot` and `.id`.
- Some tests use `SimpleNamespace` doubles.
- `message.raw_mentions` may include self id.
- Raw content may contain `<@BOT_ID>` or `<@!BOT_ID>`.

### 6.3 Webhook message semantics

In old fork, author bot messages were ignored unless `webhook_id` existed, and own interaction followup webhook messages were filtered by app id. In upstream `discord.py`, webhook messages appear as messages with `message.webhook_id` and usually `message.author.bot == True`.

Since upstream currently only drops self messages by id, Pack2 must make the intended behavior explicit and tested:

- Do not drop a message solely because `message.author.bot` is true when `message.webhook_id` is set.
- If introducing a generic bot-author guard, it must be:

```python
if getattr(message.author, "bot", False) and not getattr(message, "webhook_id", None):
    return False
```

Add that guard only if implementation introduces bot-author filtering. If no bot-author guard is added, webhook messages already pass and the test should lock that behavior.

But do not add a broad bot-author drop unless necessary. The main source behavior to preserve is “webhooks pass”. The existing upstream code already passes most other bot authors; the new mention-other-bot filter handles the dangerous bot-addressing case.

### 6.4 Transcription provider selection

Use `nanobot.providers.transcription`:

- `GroqTranscriptionProvider`
- `OpenAITranscriptionProvider`

Provider selection should respect channel/global config already present:

- `self.config.transcription_provider` if `DiscordConfig` inherits/copies it, or `self.transcription_provider` set by `ChannelManager` from `config.channels.transcription_provider`.
- `self.config.transcription_language` or channel/global language.
- API key/base should come from existing provider config resolution where possible:
  - Groq env key is already registered in provider registry as `GROQ_API_KEY`.
  - OpenAI env/provider config can use `OPENAI_API_KEY`.

Keep it simple and testable:

```python
def _make_transcription_provider(self):
    provider = (self.config.transcription_provider or "groq").lower()
    if provider == "openai": return OpenAITranscriptionProvider(...)
    return GroqTranscriptionProvider(...)
```

If the constructors read env by default, pass only explicit configured values where available. Do not add a new credential-store system here.

### 6.5 Audio detection

Treat an attachment as transcribable when either:

- `attachment.content_type` starts with `audio/`, or
- filename suffix is one of `.ogg`, `.mp3`, `.wav`, `.m4a`, `.flac`, `.webm`.

After successful transcription, add a content marker:

```text
[transcription: <text>]
```

Still keep the downloaded media path in `media_paths` unless there is a strong reason not to. Fork skipped the generic attachment marker after transcription but still downloaded the file. In upstream tests, preserve `media` for downloaded attachments so multimodal/context tooling can still see the file.

If transcription fails or returns empty, fall back to normal attachment marker:

```text
[attachment: /path/to/file]
```

### 6.6 TTS provider layer

Port final fork files with small upstream-style cleanup:

- `TTSError` exception.
- `TTSProvider.synthesize(text, output_path) -> Path`.
- `EdgeTTSProvider` uses `edge_tts.Communicate(text, voice).save(str(output_path))`.
- `FishTTSProvider` uses `httpx.AsyncClient` against `https://api.fish.audio/v1/tts` and writes MP3 bytes.
- `create_provider(config, voice_override=None)` supports only `edge` and `fish`.
- Do not expose Azure until there is a real implementation.

Config shape:

```python
class TTSConfig(Base):
    enabled: bool = False
    provider: Literal["edge", "fish"] | str = "edge"
    voice: str = "zh-CN-XiaoxiaoNeural"
    max_text_length: int = Field(default=2000, ge=1, le=10000)
    auto_tts_senders: list[str] = Field(default_factory=list)
    fish_api_key: str = ""
    fish_reference_id: str = ""
    fish_speed: float = 1.2
```

Use `Literal` only if it does not make existing unknown configs fail too early. A plain `str` plus factory error is acceptable.

### 6.7 TTS trigger semantics

`TTSService.should_trigger(...)` returns true only if `config.enabled` and any of these is true:

- `session_tts` is true.
- `skill_meta.get("tts")` is truthy.
- `sender_name` case-insensitively matches `auto_tts_senders`.

For Pack2, session TTS is the main production behavior.

### 6.8 `/tts` session metadata

Command behavior:

- `/tts on`
  - `session.metadata["tts"] = True`
  - save session
  - response: `🔊 TTS enabled for this session.`
- `/tts off`
  - remove or set false. Prefer remove to match fork.
  - save session
  - response: `🔇 TTS disabled for this session.`
- `/tts`
  - report current state and usage.

Build an `OutboundMessage` directly like upstream handlers:

```python
metadata = {**dict(ctx.msg.metadata or {}), "render_as": "text"}
return OutboundMessage(channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=content, metadata=metadata)
```

Add `BuiltinCommandSpec("/tts", "Toggle TTS", "Toggle text-to-speech for this session.", "volume-2", "[on|off]")` and register both exact and prefix.

### 6.9 Session metadata propagation

There are two outbound paths to cover:

1. Normal assistant response assembled by `AgentLoop._assemble_outbound(...)`.
2. Tool-sent messages through `MessageTool`.

Normal response:

- Add `_session_tts` to outbound metadata based on active session metadata.
- The least invasive way is to pass `ctx.session` or `session_tts` into `_assemble_outbound(...)`, or set it in `_state_respond` after assembly.
- Prefer explicit parameter if editing the signature is small:

```python
ctx.outbound = self._assemble_outbound(..., session_tts=ctx.session.metadata.get("tts", False))
```

Then:

```python
if session_tts:
    outbound_metadata["_session_tts"] = True
```

Do not add `_session_tts: False` unless tests need it. Metadata noise is a poor substitute for state.

Tool path:

- `MessageTool.set_context()` already receives `RequestContext.metadata`.
- Ensure `AgentLoop._set_tool_context(...)` gets metadata containing `_session_tts` before tools execute.
- The clean place is when the session is restored/created and before provider call/tools are prepared, merge:

```python
metadata = dict(ctx.msg.metadata or {})
if ctx.session.metadata.get("tts"):
    metadata["_session_tts"] = True
self._set_tool_context(..., metadata=metadata, session_key=ctx.session_key)
```

- In `MessageTool.__call__`, same-target messages already copy `_default_metadata`; keep that. If an implementation adds `_session_metadata`, ensure it does not wipe existing `_default_metadata` from upstream.

### 6.10 Discord outbound TTS

Add optional `tts_service` to `DiscordChannel.__init__`:

```python
def __init__(self, config: Any, bus: MessageBus, tts_service: TTSService | None = None):
    ...
    self._tts_service = tts_service
```

`DiscordBotClient.send_outbound()` can access `self._channel._tts_service` before normal media loop:

- Skip progress messages: `msg.metadata.get("_progress")`.
- Require text content.
- Trigger with:
  - `session_tts=bool(msg.metadata.get("_session_tts"))`
  - `sender_name=msg.metadata.get("sender_name")`
- Generate audio path.
- Prepend to a local `media` list, do not mutate shared `OutboundMessage` in surprising ways if avoidable:

```python
media = list(msg.media or [])
if audio_path:
    media.insert(0, str(audio_path))
```

Then send `media` through existing `_send_file` path. Preserve text chunks, matching final fork state and avoiding silent responses when Discord rejects voice upload.

Do not call TTS for slash command administrative responses unless `_session_tts` is true and service enabled. That is acceptable; if noisy, tests can assert `render_as=text` does not suppress TTS only if required. Current fork did not use `render_as` to suppress TTS.

### 6.11 ChannelManager injection

Create TTS service once, not per message.

Possible implementation:

```python
self._tts_service = None
if getattr(config, "tts", None) and config.tts.enabled:
    from nanobot.tts.service import TTSService
    self._tts_service = TTSService(config.tts)
```

Then when constructing Discord:

```python
if name == "discord":
    channel = channel_cls(channel_config, self.bus, tts_service=self._tts_service)
else:
    channel = channel_cls(channel_config, self.bus)
```

If current manager uses dynamic channel loading with uniform constructors, add a tiny helper `_instantiate_channel(name, cls, config)` and test it. Do not add compatibility shims inside every channel constructor. The call site owns the extra dependency.

## 7. TDD task sequence

Run tasks in order. Each task starts with failing tests, then minimal implementation.

### Task 1 — Pin current branch and baseline before touching code

Commands:

```bash
cd /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
git status --short --branch
git rev-parse --abbrev-ref HEAD
git merge-base HEAD upstream/main
```

Expected:

- Branch is `sync-upstream-2026-05-replay`.
- Merge base is `ba38f9083291a899d62c9b4b2a7b46429c39b062`.
- If the working tree is dirty with someone else's changes, stop. Do not overwrite.

No production checkout commands.

### Task 2 — Add Discord mention/webhook filter tests

Edit only `tests/channels/test_discord_channel.py`.

Add tests using existing `_make_message` helper:

1. `test_on_message_ignores_message_mentioning_other_bot_only`
   - Configure `group_policy="open"`, `allow_from=["*"]` so group mention policy does not explain the drop.
   - Set `channel._bot_user_id = "999"`.
   - Create guild message with `mentions=[SimpleNamespace(id=555, bot=True)]`, content `"<@555> do thing"`.
   - Assert `_handle_message` not called.

2. `test_on_message_accepts_when_self_and_other_bot_mentioned`
   - Same config.
   - Mentions include other bot and self bot: `SimpleNamespace(id=555, bot=True)`, `SimpleNamespace(id=999, bot=True)`.
   - Assert handled once.

3. `test_on_message_accepts_webhook_author_even_if_author_bot`
   - Message with `author_bot=True`, `webhook_id="abc"` if `_make_message` supports it; otherwise extend helper with `webhook_id` attribute.
   - No other bot mentions.
   - Assert handled once.

Run:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -k 'other_bot or webhook' -q
```

Expect failures before implementation.

### Task 3 — Implement Discord mention/webhook filter

Edit `nanobot/channels/discord.py`.

Implementation notes:

- Add helper to detect self mention. Reuse in `_should_respond_in_group`.
- Add helper to detect “mentions other bot and not self”.
- Call this from `_should_accept_inbound(...)` before group policy check.
- Keep self-loop guard at top of `_handle_discord_message`.
- Do not add old websocket payload code.

Run:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -k 'other_bot or webhook or mention or ignores_self' -q
```

### Task 4 — Add transcription tests for Discord audio attachments

Edit `tests/channels/test_discord_channel.py`.

Add tests:

1. `test_on_message_transcribes_audio_attachment`
   - Use `_FakeAttachment(..., filename="voice.ogg")` and set content type if helper supports it; otherwise extend `_FakeAttachment` with `content_type` attribute.
   - Monkeypatch `get_media_dir` to `tmp_path`.
   - Monkeypatch Discord channel/provider transcription call to return `"hello from voice"`. Prefer patching a new private method like `channel._transcribe_audio = AsyncMock(return_value="hello from voice")` after implementation shape is known. If writing test first, assert intended private method exists by monkeypatching with `raising=False`.
   - Assert handled content contains `[transcription: hello from voice]`.
   - Assert downloaded media path exists in `handled[0]["media"]`.

2. `test_on_message_audio_attachment_falls_back_to_attachment_marker_when_transcription_empty`
   - `_transcribe_audio` returns `""`.
   - Assert content contains `[attachment:`.

Run:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -k 'transcribes_audio or transcription_empty' -q
```

Expect failure before implementation.

### Task 5 — Wire Discord transcription to existing provider layer

Edit `nanobot/channels/discord.py` only unless provider constructor needs import path adjustment.

Implementation outline:

```python
_AUDIO_SUFFIXES = {".ogg", ".mp3", ".wav", ".m4a", ".flac", ".webm"}

def _is_audio_attachment(self, attachment: discord.Attachment) -> bool:
    content_type = (getattr(attachment, "content_type", None) or "").lower()
    filename = getattr(attachment, "filename", "") or ""
    return content_type.startswith("audio/") or Path(filename).suffix.lower() in _AUDIO_SUFFIXES

async def _transcribe_audio(self, path: Path) -> str:
    provider_name = (getattr(self.config, "transcription_provider", None) or self.transcription_provider or "groq").lower()
    language = getattr(self.config, "transcription_language", None) or self.transcription_language
    if provider_name == "openai":
        provider = OpenAITranscriptionProvider(language=language, ...)
    else:
        provider = GroqTranscriptionProvider(language=language, ...)
    return await provider.transcribe(path)
```

Exact constructor signatures must be checked in `nanobot/providers/transcription.py` while implementing.

Modify `_download_attachments()`:

- After file write and `media_paths.append(str(file_path))`, if audio:
  - `transcription = await self._transcribe_audio(file_path)`
  - if truthy, append `[transcription: ...]` and `continue`
- Else append existing attachment marker.

Run:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -k 'attachment or transcri' -q
python3 -m pytest tests/providers/test_transcription.py -q
```

### Task 6 — Add TTS provider/service tests

Create `tests/tts/test_service.py` or `tests/providers/test_tts.py`. Prefer `tests/tts/test_service.py` with package directory if test style allows.

Tests:

1. `test_tts_service_does_not_trigger_when_disabled`
   - Build `TTSConfig(enabled=False)`.
   - Assert `should_trigger(session_tts=True)` is false.

2. `test_tts_service_triggers_on_session_tts_when_enabled`
   - `TTSConfig(enabled=True)`.
   - Assert true.

3. `test_tts_service_triggers_on_sender_name_case_insensitive`
   - `auto_tts_senders=["Peppa"]`, `sender_name="peppa"`.
   - Assert true.

4. `test_tts_service_truncates_text_and_returns_path`
   - Monkeypatch `nanobot.tts.service.create_provider` to fake provider that records text and writes bytes.
   - `max_text_length=5`.
   - `await synthesize("abcdef")`.
   - Assert fake saw `"abcde"`, returned path exists.

5. `test_tts_factory_rejects_unknown_provider`
   - `TTSConfig(provider="bogus")`.
   - Assert `ValueError` from `create_provider`.

These tests will fail until files/schema exist.

Run:

```bash
python3 -m pytest tests/tts/test_service.py -q
```

### Task 7 — Add TTS config and provider layer

Edit/create:

- `pyproject.toml`
- `nanobot/config/schema.py`
- `nanobot/tts/__init__.py`
- `nanobot/tts/base.py`
- `nanobot/tts/edge.py`
- `nanobot/tts/fish.py`
- `nanobot/tts/factory.py`
- `nanobot/tts/service.py`

Port from `origin/main` final files, with upstream lint style.

`pyproject.toml` dependency:

- Add `edge-tts>=7.0.0` or the fork-pinned equivalent if commit `de1d0065` shows a specific version. If uncertain, use a conservative lower bound matching current PyPI compatibility and no upper bound only if project style allows. Existing project has many `<major` caps; prefer `edge-tts>=7.0.0,<8.0.0` if compatible.

Run:

```bash
python3 -m pytest tests/tts/test_service.py -q
python3 -m python - <<'PY'
from nanobot.config.schema import Config
c = Config()
assert hasattr(c, 'tts')
print(c.tts.model_dump())
PY
```

If `edge_tts` is not installed in the environment, tests must monkeypatch imports so importing `nanobot.tts.service` does not import `edge_tts` until `EdgeTTSProvider` is created. The factory already lazy-imports provider modules; keep it that way.

### Task 8 — Add `/tts` command tests

Find existing command tests:

```bash
find tests -maxdepth 3 -type f | grep -E 'command|builtin|router'
```

If there is an existing built-in command test file, use it. Otherwise create `tests/command/test_tts_command.py`.

Test cases:

1. `/tts` reports off by default.
2. `/tts on` sets `session.metadata["tts"] is True` and saves session.
3. `/tts off` removes `tts` metadata and saves session.
4. `build_help_text()` contains `/tts`.
5. `register_builtin_commands()` routes both exact `/tts` and prefix `/tts on` to handler.

Use small fakes for `ctx.loop.sessions` if existing tests do that. Do not instantiate the entire agent loop just to test a command.

Run:

```bash
python3 -m pytest tests/command -k tts -q
```

### Task 9 — Implement `/tts` command

Edit `nanobot/command/builtin.py`.

Implementation checklist:

- Add command spec near `/dream` or after `/dream-restore`.
- Add `async def cmd_tts(ctx: CommandContext) -> OutboundMessage:`.
- Resolve session with `ctx.session or ctx.loop.sessions.get_or_create(ctx.key)`.
- Save via `ctx.loop.sessions.save(session)` when mutating.
- Return `OutboundMessage` directly with render-as-text metadata.
- Register:

```python
router.exact("/tts", cmd_tts)
router.prefix("/tts ", cmd_tts)
```

Run:

```bash
python3 -m pytest tests/command -k tts -q
python3 -m pytest tests/channels/test_discord_channel.py -k slash -q
```

Slash tests matter because Discord slash command registration reads built-in command specs.

### Task 10 — Add session metadata propagation tests

Add focused tests for both paths.

Normal outbound path options:

- If existing agent loop tests can instantiate `AgentLoop`, add a test around `_assemble_outbound(...)` or `_state_respond(...)`.
- If not practical, add the smallest unit test for the method you change.

Expected assertion:

- When active session metadata has `{"tts": True}`, returned `OutboundMessage.metadata["_session_tts"] is True`.
- When false/missing, no `_session_tts` key or it is false, whichever implementation chooses. Be consistent.

MessageTool path:

- Instantiate `MessageTool` with fake send callback.
- Set context with `RequestContext(..., metadata={"_session_tts": True})`.
- Call tool with same target.
- Assert captured outbound metadata includes `_session_tts: True`.
- Call tool with different target.
- Assert metadata does not inherit `_session_tts` unless cross-target behavior is explicitly desired. Current upstream intentionally copies default metadata only for same target; preserve that.

Run:

```bash
python3 -m pytest tests/agent -k 'tts or message_tool' -q
```

### Task 11 — Implement session metadata propagation

Edit:

- `nanobot/agent/loop.py`
- `nanobot/agent/tools/message.py` only if tests show current `_default_metadata` is insufficient.

Implementation checklist:

- Do not remove existing metadata behavior for Slack thread, origin message id, message id, media delivery recording, or WebSocket safeguards.
- Ensure `_set_tool_context(...)` receives metadata with `_session_tts` after session is known and before tools may send messages.
- Ensure final outbound response gets `_session_tts` from `ctx.session.metadata`.

Likely minimal implementation:

1. Add helper in loop:

```python
def _metadata_with_session_tts(self, metadata: dict | None, session: Session | None) -> dict:
    meta = dict(metadata or {})
    if session and session.metadata.get("tts"):
        meta["_session_tts"] = True
    return meta
```

2. Use it at tool-context setup call sites.
3. Pass `session_tts` into `_assemble_outbound` or add it after assembly in `_state_respond`.

Run:

```bash
python3 -m pytest tests/agent -k 'tts or message_tool' -q
```

### Task 12 — Add Discord outbound TTS tests

Edit `tests/channels/test_discord_channel.py`.

Use existing fake Discord channel/client send patterns.

Test cases:

1. `test_send_outbound_prepends_tts_audio_when_session_tts_enabled`
   - Create `DiscordChannel(..., tts_service=fake_tts)` after constructor supports it.
   - Fake service:
     - `should_trigger(...)` records `session_tts=True` and returns true.
     - `synthesize(...)` writes `tmp_path / "tts.mp3"` and returns it.
   - Use `DiscordBotClient.send_outbound(OutboundMessage(..., content="hello", metadata={"_session_tts": True}))` with fake channel object whose `send(file=...)` captures file sends and text sends.
   - Assert first file send is `tts.mp3` and text still sends.

2. `test_send_outbound_does_not_tts_progress_messages`
   - Metadata contains `_progress: True`, `_session_tts: True`.
   - Assert synthesize not called.

3. `test_send_outbound_tts_failure_still_sends_text`
   - `synthesize` returns `None`.
   - Assert text send occurs.

Run:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -k 'tts' -q
```

### Task 13 — Implement Discord outbound TTS and ChannelManager injection

Edit:

- `nanobot/channels/discord.py`
- `nanobot/channels/manager.py`

Discord implementation:

- Import TTS type only under `TYPE_CHECKING` to avoid hard dependency at import time:

```python
if TYPE_CHECKING:
    from nanobot.tts.service import TTSService
```

- Add `supports_tts = True` on `DiscordChannel` if channel convention uses it.
- Add constructor argument and `self._tts_service`.
- In `DiscordBotClient.send_outbound()`, before media loop:

```python
media = list(msg.media or [])
if self._channel._tts_service and msg.content and not (msg.metadata or {}).get("_progress"):
    ...
    if audio_path:
        media.insert(0, str(audio_path))
```

- Iterate over `media`, not `msg.media`.
- Do not skip text in first implementation.

ChannelManager implementation:

- Add `_tts_service` initialization in `__init__` after config is stored.
- Catch TTS service construction errors and log once; do not prevent bot startup if Fish credentials are missing unless factory constructor itself raises. A disabled TTS config should not import `edge_tts`.
- Inject only for Discord channel.

Run:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -k 'tts or send_outbound' -q
```

### Task 14 — Add/adjust ChannelManager TTS injection tests

If existing ChannelManager tests exist, add there. Otherwise add a small test file.

Test:

- Given `Config(tts.enabled=True, channels.discord.enabled=True)` and monkeypatched Discord channel class/factory, manager passes a non-None `tts_service` only to Discord.
- Given `tts.enabled=False`, manager passes `None` or omits the argument depending on implementation.

Run:

```bash
python3 -m pytest tests/channels -k 'manager and tts' -q
```

If the manager is hard to unit test because channel discovery is dynamic, cover via a tiny private helper `_make_channel(...)` and test that helper. Do not overbuild.

### Task 15 — Targeted pack regression run

After all implementation tasks pass individually, run:

```bash
python3 -m pytest tests/channels/test_discord_channel.py -q
python3 -m pytest tests/providers/test_transcription.py -q
python3 -m pytest tests/tts/test_service.py -q
python3 -m pytest tests/command -k 'tts or builtin or router' -q
python3 -m pytest tests/agent -k 'tts or message_tool' -q
python3 -m compileall nanobot/channels/discord.py nanobot/tts nanobot/command/builtin.py nanobot/agent/loop.py nanobot/agent/tools/message.py nanobot/config/schema.py
```

If a directory/test path does not exist, replace with the actual test file created in earlier tasks and note it in the commit message.

## 8. Pack-level verification

Minimum verification before declaring Pack2 complete:

```bash
cd /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge

# No conflict markers in touched files
grep -R "<<<<<<<\|=======\|>>>>>>>" \
  nanobot/channels/discord.py \
  nanobot/channels/manager.py \
  nanobot/config/schema.py \
  nanobot/command/builtin.py \
  nanobot/agent/loop.py \
  nanobot/agent/tools/message.py \
  nanobot/tts tests || true

# Syntax/import sanity
python3 -m compileall \
  nanobot/channels/discord.py \
  nanobot/channels/manager.py \
  nanobot/config/schema.py \
  nanobot/command/builtin.py \
  nanobot/agent/loop.py \
  nanobot/agent/tools/message.py \
  nanobot/tts

# Targeted tests
python3 -m pytest tests/channels/test_discord_channel.py -q
python3 -m pytest tests/providers/test_transcription.py -q
python3 -m pytest tests/tts/test_service.py -q
python3 -m pytest tests/command -k 'tts or builtin or router' -q
python3 -m pytest tests/agent -k 'tts or message_tool' -q

# Lint touched production paths if ruff is available
python3 -m ruff check \
  nanobot/channels/discord.py \
  nanobot/channels/manager.py \
  nanobot/config/schema.py \
  nanobot/command/builtin.py \
  nanobot/agent/loop.py \
  nanobot/agent/tools/message.py \
  nanobot/tts
```

If `edge_tts` is missing locally before dependencies are installed, provider tests should still pass because imports are lazy. A real Edge provider smoke requires installing dependencies and belongs in manual smoke.

## 9. Manual smoke check

Manual smoke is for a configured staging/run-from-worktree environment, not the production checkout.

Preparation:

```bash
cd /root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge
python3 -m pip install -e '.[discord]'
python3 -m pip install 'edge-tts>=7.0.0,<8.0.0'
```

Use a test config, not production secrets committed to disk:

```yaml
tts:
  enabled: true
  provider: edge
  voice: zh-CN-XiaoxiaoNeural
channels:
  transcriptionProvider: groq
  transcriptionLanguage: zh
  discord:
    enabled: true
    token: ${DISCORD_TEST_TOKEN}
    allowFrom: ["*"]
    groupPolicy: mention
```

Smoke checklist:

1. Start nanobot from the worktree with the test config.
2. Discord READY appears in logs.
3. In a guild channel, mention another bot only. Nanobot should not respond.
4. Mention nanobot. Nanobot should respond.
5. Send a webhook-originated message in an allowed channel. Nanobot should process it if channel/user filters allow it.
6. Send a Discord voice/audio attachment. Nanobot should include `[transcription: ...]` in the inbound prompt or visible behavior should show it understood the audio.
7. Send `/tts` and confirm it reports off.
8. Send `/tts on`; next assistant response should include an MP3 audio attachment and still include text.
9. Send a tool-triggering prompt that causes `MessageTool` to send a same-chat message; with TTS on, that tool-sent message should also get audio.
10. Send `/tts off`; next response should not include a TTS audio attachment.

Do not restart or edit the live `nanobot-gateway.service` during Pack2 smoke unless the main sync owner explicitly schedules deployment.

## 10. Rollback plan

Before Pack2 implementation commit:

```bash
git status --short
git diff --stat
```

If Pack2 is uncommitted and bad:

```bash
git restore \
  pyproject.toml \
  nanobot/config/schema.py \
  nanobot/command/builtin.py \
  nanobot/agent/loop.py \
  nanobot/agent/tools/message.py \
  nanobot/channels/manager.py \
  nanobot/channels/discord.py

git clean -fd -- nanobot/tts tests/tts
```

If Pack2 has been committed and must be reverted:

```bash
git revert <pack2-commit-sha>
```

If only TTS is broken but mention/transcription are good, do **not** hide failures by adding broad try/except everywhere. Revert or disable only the TTS wiring:

- Set `tts.enabled: false` in runtime config for smoke.
- Or revert the TTS injection commit if Pack2 was split into multiple commits.

The root rollback principle: remove the bad feature at the call site. Do not add compatibility aliases or silent swallowers in provider code to make broken configuration look successful.

## 11. Completion criteria

Pack2 is complete only when all are true:

- The work was done only in `/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge`.
- No production checkout files under `/root/git_code/nanobot` were edited.
- `nanobot/channels/discord.py` remains `discord.py` based; old websocket Gateway implementation was not restored.
- Tests prove:
  - messages mentioning another bot only are ignored;
  - messages mentioning nanobot are accepted;
  - webhook bot messages are allowed;
  - Discord audio attachments are transcribed through the provider abstraction;
  - TTS service trigger logic works;
  - `/tts on|off` mutates session metadata;
  - `_session_tts` reaches normal outbound responses;
  - `_session_tts` reaches same-target `MessageTool` sends;
  - Discord outbound TTS adds an audio attachment and still sends text;
  - TTS failures do not block text response.
- Targeted verification commands in section 8 pass.
- No conflict markers remain in touched files.
- No Pack1/Anthropic, Pack3+, deployment, or unrelated refactor changes are mixed into the Pack2 commit.

The migration is a replay, not archaeology cosplay. Preserve the behavior users feel; leave the dead transport architecture in the grave.
