# Pack2 Spec — Discord UX, Voice Transcription, and TTS Replay

**Upstream base:** `upstream/main ba38f908` (with Pack1 already replayed)  
**Worktree:** `/root/git_code/nanobot/.worktrees/sync-upstream-2026-05-merge`  
**Plan reference:** `docs/superpowers/plans/2026-05-18-pack2-discord-transcription-tts.md`

---

## 1. 概述

本 spec 将 fork 生产环境的四项 Discord 用户体验行为 replay 到 upstream `discord.py` 架构上：

1. **Mention filter** — 忽略只 mention 其他 bot、未 mention 本 bot 的消息。
2. **Webhook 放行** — 不因 `author.bot` 而丢弃 webhook 消息。
3. **语音转写** — Discord 音频附件自动下载并走 transcription provider 转写，结果注入消息正文。
4. **TTS 回播** — 会话级 TTS 开关（`/tts on|off`），响应时自动合成语音并以音频附件发送；支持 `edge-tts` 与 `Fish Audio` 双 provider。

设计原则：**最小侵入、面向复用、优雅实现**。不恢复 fork 的旧 websocket Gateway channel，所有行为嫁接在 upstream 现有扩展点上。

---

## 2. 行为需求

### 2.1 Mention filter

- 若消息 mention 了另一个 bot，**且未 mention 本 bot**，则丢弃。
- 若消息 mention 了本 bot（即使同时 mention 其他 bot），则接受。
- 在 `group_policy="mention"` 的 guild channel 中，仍要求 mention 本 bot；mention filter 在 group-policy 检查**之前**或**之中**执行，不替代它。
- 不因为 `author.bot` 而 blanket-drop，以保留 webhook/多 agent 工作流。

### 2.2 Webhook 放行

- 如果未来引入 generic bot-author guard，必须保留例外：
  ```python
  if getattr(message.author, "bot", False) and not getattr(message, "webhook_id", None):
      return False
  ```
- 当前 upstream 仅按 self-id 过滤自身消息， webhook 消息本就可通过；测试应锁定此行为。

### 2.3 语音转写

- Discord 消息携带音频附件（`content_type.startswith("audio/")` 或后缀在已知音频列表）时：
  1. 下载到本地 media 目录。
  2. 调用 transcription provider（Groq Whisper 或 OpenAI Whisper）。
  3. 若转写结果非空，在消息 content 中追加 `[transcription: <text>]` 标记；不再追加普通 `[attachment: ...]` 标记。
- 转写失败（网络/空结果/异常）不阻塞消息处理，仅记录 warning。

### 2.4 TTS 回播

- 全局配置 `tts.enabled` 控制 TTS 模块是否加载。
- `/tts on` 将当前 session 的 `_session_tts` 设为 `True`；`/tts off` 设为 `False`。
- 当 session TTS 开启且全局 TTS 启用时，outbound 文本响应应附带一张 MP3 音频附件。
- `auto_tts_senders` 列表支持按 sender name（大小写不敏感）自动触发 TTS，无需用户手动 `/tts on`。
- MessageTool 向同一 chat 发送的消息也应触发 TTS（因为属于同一对话上下文）。
- TTS 合成失败不得阻塞文本发送；音频缺失时文本仍应送达。
- 文本超过 `max_text_length` 时截断后再合成。

---

## 3. 架构分析

### 3.1 upstream Discord channel 当前架构

```
discord.py on_message
  └── DiscordChannel._handle_discord_message
        ├── _should_respond_in_group      (mention / open policy)
        ├── _download_attachments         (图片/文件下载)
        ├── _compose_inbound_content      (拼接正文 + attachment markers)
        └── BaseChannel._handle_message
              └── bus.publish_inbound(InboundMessage)
                    └── AgentLoop._process_message
                          └── state machine (RESTORE → COMMAND → BUILD → RUN → RESPOND → SAVE)
                                └── _assemble_outbound → bus.publish_outbound(OutboundMessage)
                                      └── ChannelManager._dispatch_outbound
                                            └── DiscordChannel.send(msg)
```

关键类与文件：
- `nanobot/channels/discord.py` — `DiscordChannel`, `DiscordBotClient`, `DiscordConfig`
- `nanobot/channels/base.py` — `BaseChannel._handle_message`（统一 inbound 入口）
- `nanobot/channels/manager.py` — `ChannelManager` 持有 channel 实例，消费 outbound queue，调用 `channel.send()`
- `nanobot/bus/events.py` — `InboundMessage`, `OutboundMessage`（dataclass，metadata 为 `dict[str, Any]`）
- `nanobot/agent/loop.py` — `AgentLoop` 状态机；`_assemble_outbound` 组装最终响应
- `nanobot/agent/tools/message.py` — `MessageTool`；通过 `ContextVar` `_default_metadata` 向 same-target 发送传播 metadata
- `nanobot/command/router.py` — `CommandRouter`（priority / exact / prefix 三级路由）
- `nanobot/command/builtin.py` — `register_builtin_commands()` 注册内置 slash 命令
- `nanobot/config/schema.py` — Pydantic `BaseSettings` 根配置；`DiscordConfig` 定义在 `discord.py` 中而非 schema.py
- `nanobot/providers/transcription.py` — `GroqTranscriptionProvider`, `OpenAITranscriptionProvider`

### 3.2 Outbound pipeline

`ChannelManager` 在 `_dispatch_outbound` 中循环消费 `MessageBus.outbound_queue`：
1. stream delta 合并（`_coalesce_stream_deltas`）
2. duplicate suppression（非 stream 消息）
3. 调用 `ChannelManager._send_with_retry(channel, msg)`，最多 3 次指数退避重试
4. `channel.send(msg)` 负责平台特定投递（Discord 端为 `DiscordChannel.send`）

**TTS 注入点的选择**： outbound 已经过 `_assemble_outbound` 和 bus，最终到达 `DiscordChannel.send`。若 TTS 在 `AgentLoop` 或 `ChannelManager` 中注入，会污染跨 channel 的通用逻辑。因此将 TTS 合成放在 `DiscordChannel.send` 内部——仅影响 Discord，且音频以 `OutboundMessage.media` 形式附加，与现有附件发送路径复用。

### 3.3 MessageTool metadata 传播链

```
AgentLoop._state_build
  └── message_tool.start_turn()
        └── _default_metadata.set(dict(ctx.metadata))   # 复制 InboundMessage.metadata

MessageTool.send_message(...)
  └── same_target = (channel == default_channel and chat_id == default_chat_id)
      └── metadata = dict(_default_metadata.get()) if same_target else {}
          └── OutboundMessage(..., metadata=metadata)
```

因此，只要 `_session_tts` 被写入 `InboundMessage.metadata` 或 `MessageTool._default_metadata`，same-target 的 `MessageTool` 发送自然携带该标志。

---

## 4. 技术方案

### 4.1 Mention filter 的最小实现位置

**位置**：`DiscordChannel._handle_discord_message`，在 `_should_respond_in_group` 调用之前。

**理由**：过滤的是 Discord 特有的 mention 语义（`<@ID>`、`message.mentions`、`raw_mentions`），不是通用聊天平台概念。放在 `BaseChannel._handle_message` 之前，避免污染其他 channel。

**实现**：

```python
class DiscordChannel(BaseChannel):
    # ...
    def _mentions_other_bot_only(self, message: discord.Message, content: str) -> bool:
        """True when the message mentions another bot but does NOT mention self."""
        bot_user_id = self._bot_user_id
        if bot_user_id is None and self._client and self._client.user:
            bot_user_id = str(self._client.user.id)
        if bot_user_id is None:
            return False  # identity unknown; don't drop

        mentions_bot = any(str(u.id) == bot_user_id for u in message.mentions)
        if not mentions_bot:
            mentions_bot = bot_user_id in {str(uid) for uid in getattr(message, "raw_mentions", [])}
        if not mentions_bot:
            mentions_bot = f"<@{bot_user_id}>" in content or f"<@!{bot_user_id}>" in content

        if mentions_bot:
            return False

        # Does it mention any other bot?
        for user in message.mentions:
            if getattr(user, "bot", False) and str(user.id) != bot_user_id:
                return True
        return False
```

在 `_handle_discord_message` 中：

```python
if self._mentions_other_bot_only(message, content):
    return
```

**与 `_should_respond_in_group` 的关系**：`mention` policy 检查的是"是否 mention 本 bot"；`_mentions_other_bot_only` 检查的是"是否只 mention 其他 bot"。两者逻辑不重复，但可共用 bot_user_id 解析。建议把 bot_user_id 解析抽成 `_resolve_bot_user_id()` 小方法，供两者调用。

### 4.2 Webhook 放行策略

当前 upstream 仅过滤 self-message（`message.author.id == self._client.user.id`）。如果 Pack2 需要引入 bot-author 过滤（例如防止 bot 之间循环 @），必须显式保留 webhook 例外。

**实现**（仅在新增 bot-author guard 时）：

```python
if (
    getattr(message.author, "bot", False)
    and not getattr(message, "webhook_id", None)
    and str(message.author.id) != bot_user_id
):
    return
```

**如果未引入 bot-author guard**，则 webhook 消息本就通过，无需代码改动；只需在测试中覆盖此场景。

### 4.3 语音转写：复用 upstream transcription provider

**决策：复用现有 provider，不新建。**

`nanobot/providers/transcription.py` 已提供 `GroqTranscriptionProvider` 和 `OpenAITranscriptionProvider`，接口统一为 `async def transcribe(self, file_path: str | Path) -> str`。完全满足需求。

**配置来源**：
- `BaseChannel` 已有类属性：`transcription_provider = "groq"`，`transcription_api_key = ""`，`transcription_api_base = ""`，`transcription_language = None`。
- `DiscordConfig` 可新增可选字段 `transcription_provider`, `transcription_api_key`, `transcription_api_base`, `transcription_language`，fallback 到 `BaseChannel` 类属性或环境变量。

**调用链**：

```python
# DiscordChannel
_AUDIO_SUFFIXES = {".mp3", ".ogg", ".wav", ".m4a", ".webm", ".aac", ".flac"}

def _is_audio_attachment(self, attachment) -> bool:
    content_type = (getattr(attachment, "content_type", None) or "").lower()
    filename = getattr(attachment, "filename", "") or ""
    return content_type.startswith("audio/") or Path(filename).suffix.lower() in _AUDIO_SUFFIXES

async def _transcribe_audio(self, path: Path) -> str:
    provider_name = (
        getattr(self.config, "transcription_provider", None)
        or self.transcription_provider
        or "groq"
    ).lower()
    language = getattr(self.config, "transcription_language", None) or self.transcription_language

    if provider_name == "openai":
        from nanobot.providers.transcription import OpenAITranscriptionProvider
        provider = OpenAITranscriptionProvider(
            api_key=getattr(self.config, "transcription_api_key", None) or self.transcription_api_key or None,
            api_base=getattr(self.config, "transcription_api_base", None) or self.transcription_api_base or None,
            language=language,
        )
    else:
        from nanobot.providers.transcription import GroqTranscriptionProvider
        provider = GroqTranscriptionProvider(
            api_key=getattr(self.config, "transcription_api_key", None) or self.transcription_api_key or None,
            api_base=getattr(self.config, "transcription_api_base", None) or self.transcription_api_base or None,
            language=language,
        )
    return await provider.transcribe(path)
```

**修改 `_download_attachments`**：

```python
for attachment in message.attachments:
    # ... existing size / path checks ...
    if self._is_audio_attachment(attachment):
        try:
            transcription = await self._transcribe_audio(file_path)
        except Exception as e:
            self.logger.warning("Transcription failed: {}", e)
            transcription = ""
        if transcription:
            markers.append(f"[transcription: {transcription}]")
        else:
            markers.append(f"[attachment: {filename} - transcription failed]")
    else:
        media_paths.append(str(file_path))
        markers.append(f"[attachment: {file_path.name}]")
```

注意：音频附件下载后**不**加入 `media_paths`（避免把原始音频文件再当作图片/媒体传给 LLM），仅通过 transcription marker 进入 content。

### 4.4 TTS 模块设计

#### 4.4.1 模块边界与目录结构

TTS 作为**可选插件模块**存在，不侵入核心消息流：

```
nanobot/tts/
  __init__.py       # exports TTSService
  base.py           # TTSProvider ABC + TTSError
  factory.py        # create_provider(config, voice_override=None)
  edge.py           # EdgeTTSProvider (edge-tts)
  fish.py           # FishTTSProvider (Fish Audio API)
  service.py        # TTSService: trigger logic + synthesize() + temp file cleanup
```

**生命周期管理**：
- `TTSService` 实例由 `DiscordChannel` **懒加载持有**（`self._tts_service: TTSService | None = None`）。
- 理由：TTS 目前仅 Discord 需要。如果未来 Telegram/Slack 也需要 TTS，可将 service 提升到 `ChannelManager` 或 `AgentLoop` 中统一持有；当前保持最小范围。
- 懒加载条件：`config.tts is not None and config.tts.enabled`。若上游 Config 未配置 `tts`，或 `enabled=False`，则 `_tts_service` 始终为 `None`，所有 TTS 调用短路返回。

#### 4.4.2 Provider 抽象

```python
# nanobot/tts/base.py
from abc import ABC, abstractmethod
from pathlib import Path

class TTSError(Exception):
    pass

class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, output_path: Path) -> Path:
        """Convert text to audio file. Returns output_path on success, raises TTSError on failure."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...
```

- `EdgeTTSProvider`：依赖 `edge-tts` 库（已在 fork 验证），无 API key，免费。
- `FishTTSProvider`：依赖 `httpx`，需要 `api_key` + `reference_id`。

#### 4.4.3 TTSService 职责

```python
# nanobot/tts/service.py
class TTSService:
    def __init__(self, config: TTSConfig):
        self.config = config
        self._provider = create_provider(config)
        self._temp_dir = Path(tempfile.gettempdir()) / "nanobot_tts"
        self._temp_dir.mkdir(exist_ok=True)

    def should_trigger(self, session_tts: bool = False, skill_meta: dict | None = None, sender_name: str | None = None) -> bool:
        if not self.config.enabled:
            return False
        if session_tts:
            return True
        if skill_meta and skill_meta.get("tts"):
            return True
        if sender_name and self.config.auto_tts_senders:
            if sender_name.lower() in (s.lower() for s in self.config.auto_tts_senders):
                return True
        return False

    async def synthesize(self, text: str, voice: str | None = None) -> Path | None:
        if not text or not text.strip():
            return None
        if len(text) > self.config.max_text_length:
            text = text[:self.config.max_text_length]
        output_path = self._temp_dir / f"tts_{uuid.uuid4().hex[:8]}.mp3"
        try:
            provider = self._provider
            if voice and voice != self.config.voice:
                provider = create_provider(self.config, voice_override=voice)
            result = await provider.synthesize(text, output_path)
            logger.info("TTS generated: {} ({} bytes)", result.name, result.stat().st_size)
            return result
        except TTSError as e:
            logger.error("TTS synthesis failed: {}", e)
            return None
        except Exception as e:
            logger.error("Unexpected TTS error: {}", e)
            return None
```

#### 4.4.4 配置 schema

在 `nanobot/config/schema.py` 的 `Config` 下新增 `tts` 字段：

```python
class TTSConfig(Base):
    enabled: bool = False
    provider: str = "edge"
    voice: str = "zh-CN-XiaoxiaoNeural"
    max_text_length: int = 2000
    auto_tts_senders: list[str] = Field(default_factory=list)
    fish_api_key: str = ""
    fish_reference_id: str = ""
    fish_speed: float = 1.2

class Config(BaseSettings):
    # ... existing fields ...
    tts: TTSConfig = Field(default_factory=TTSConfig)
```

在 `DiscordConfig` 中可选注入 `tts`：

```python
class DiscordConfig(Base):
    # ... existing fields ...
    tts: TTSConfig | None = None
```

`DiscordChannel.__init__` 中：

```python
self._tts_service: TTSService | None = None
if getattr(config, "tts", None) is not None and config.tts.enabled:
    self._tts_service = TTSService(config.tts)
```

**向前兼容注意**：`tts` 字段对非 Discord channel 不可见，保持它们的构造函数不变。

#### 4.4.5 TTS 在 outbound 中的注入点

**位置**：`DiscordChannel.send`（覆盖 `BaseChannel.send`）。

**理由**：
- 只有 Discord 需要 TTS。
- `OutboundMessage.media` 已有成熟的发送路径（`_build_chunks` 会处理附件）。
- 失败时可直接 try/except 吞掉音频错误，保证文本继续发送。

**实现**：

```python
async def send(self, msg: OutboundMessage) -> None:
    # TTS injection
    if self._tts_service is not None:
        session_tts = msg.metadata.get("_session_tts", False)
        sender_name = msg.metadata.get("sender_name")  # or wherever sender name is stored
        if self._tts_service.should_trigger(session_tts=session_tts, sender_name=sender_name):
            try:
                audio_path = await self._tts_service.synthesize(msg.content)
                if audio_path:
                    msg.media = list(msg.media) + [str(audio_path)]
            except Exception:
                self.logger.exception("TTS failed for {}", msg.chat_id)
                # never block text

    # existing send logic (super().send or inline)
    # ... current DiscordChannel.send body ...
```

**向前兼容注意**：如果 upstream 未来重构 `BaseChannel.send` 的签名或引入 async 上下文差异，此 override 需同步调整。

### 4.5 `/tts` 命令：用 upstream command router

**决策：走 `CommandRouter.exact`，在 `builtin.py` 中注册。**

理由：
- `/tts` 是用户可直接输入的文本命令，与 `/new`, `/model` 同级。
- 不需要 Discord slash command 注册；走文本命令层统一处理。
- 不需要修改 `discord.py` 的 `CommandTree`。

**Handler 实现**：

```python
async def cmd_tts(ctx: CommandContext) -> OutboundMessage:
    if ctx.session is None:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="No active session.", metadata={"render_as": "text"},
        )
    args = ctx.args.strip().lower()
    if args == "on":
        ctx.session.metadata["_session_tts"] = True
        content = "TTS enabled for this session."
    elif args == "off":
        ctx.session.metadata["_session_tts"] = False
        content = "TTS disabled for this session."
    elif args == "":
        state = ctx.session.metadata.get("_session_tts", False)
        content = f"TTS is {'on' if state else 'off'} for this session."
    else:
        content = "Usage: `/tts on` or `/tts off`"
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )
```

在 `register_builtin_commands` 中注册：

```python
router.exact("/tts", cmd_tts)
router.prefix("/tts ", cmd_tts)
```

### 4.6 session_tts 状态如何传递到 outbound

#### 4.6.1 正常响应路径

`session.metadata["_session_tts"]` 在 `_save_turn` 时随 session 持久化到磁盘（`SessionManager` 保存整个 `session.metadata`）。因此跨 turn 状态天然持久。

**传递链**：

```
session.metadata["_session_tts"]
  └── AgentLoop._state_respond
        └── 在 _assemble_outbound 返回后，直接补丁 metadata
```

**最小侵入实现**（不改 `_assemble_outbound` 签名）：

```python
# AgentLoop._state_respond
async def _state_respond(self, ctx: TurnContext) -> str:
    ctx.outbound = self._assemble_outbound(
        ctx.msg,
        ctx.final_content,
        ctx.all_messages,
        ctx.stop_reason,
        ctx.had_injections,
        ctx.generated_media,
        ctx.on_stream,
        turn_latency_ms=ctx.turn_latency_ms,
    )
    # Pack2: propagate session TTS flag to outbound metadata
    if ctx.session and ctx.session.metadata.get("_session_tts"):
        ctx.outbound.metadata["_session_tts"] = True
    return "ok"
```

**为什么不改 `_assemble_outbound`**：`_assemble_outbound` 是纯组装函数，当前签名不依赖 `Session`。如果加入 `session` 参数，会改变 upstream 核心 API 的契约，未来 upstream 若调整该方法，合并冲突风险更大。在 `_state_respond` 中补丁 metadata 是外科手术式的修改，范围最小。

#### 4.6.2 MessageTool same-target 路径

`MessageTool` 发送 same-target 消息时，会复制 `_default_metadata` ContextVar 到 outbound。因此需要把 `_session_tts` 写入 `_default_metadata`。

当前 `MessageTool.start_turn()` 只复制 `RequestContext.metadata`（即 inbound message metadata），不读 `session.metadata`。

**方案比较**：

| 方案 | 侵入点 | 评价 |
|------|--------|------|
| A. 在 `AgentLoop._state_build` 中把 `_session_tts` 写入 `msg.metadata` | `InboundMessage` | 污染 inbound metadata；若 upstream 将 metadata 用于其他路由决策，可能产生副作用。 |
| B. 修改 `MessageTool.start_turn()` 接收 session | `MessageTool` 签名 | 需要改 Tool ABC，侵入性高。 |
| C. 在 `MessageTool` 上新增 `add_default_metadata(key, value)` 方法，由 `AgentLoop._state_build` 在 `start_turn()` 后调用 | `MessageTool` | 仅增加一个公共 setter，不改现有调用链，侵入最低。 |

**推荐方案 C**：

```python
# nanobot/agent/tools/message.py
class MessageTool(Tool, ContextAware):
    # ... existing ...
    def add_default_metadata(self, key: str, value: Any) -> None:
        """Inject an extra key into the default metadata for same-target sends."""
        try:
            meta = dict(self._default_metadata.get())
        except LookupError:
            meta = {}
        meta[key] = value
        self._default_metadata.set(meta)
```

在 `AgentLoop._state_build` 中：

```python
if message_tool := self.tools.get("message"):
    if isinstance(message_tool, MessageTool):
        message_tool.start_turn()
        if ctx.session and ctx.session.metadata.get("_session_tts"):
            message_tool.add_default_metadata("_session_tts", True)
```

### 4.7 MessageTool 如何触发 TTS

当 MessageTool 调用 `send_message` 到 same-target 时：
1. `metadata = dict(_default_metadata.get())` 会包含 `"_session_tts": True`。
2. `OutboundMessage(..., metadata=metadata)` 被 publish 到 bus。
3. `ChannelManager._dispatch_outbound` 将该消息路由到 `DiscordChannel.send(msg)`。
4. `DiscordChannel.send` 检查 `msg.metadata.get("_session_tts")`，触发 TTS 合成并追加到 `msg.media`。

因此 MessageTool 触发 TTS **不需要任何额外改动**，只要 `_session_tts` 能进入 `_default_metadata` 即可（见 4.6.2）。

---

## 5. 最小侵入评估

| 改动点 | 侵入等级 | 说明 |
|--------|----------|------|
| Mention filter helpers | **低** | 仅 `DiscordChannel` 内部新增方法，不改接口。 |
| Webhook guard | **低** | 若有 bot-author guard，仅一行条件；否则只加测试。 |
| 语音转写 | **中** | 修改 `DiscordChannel._download_attachments`（新增 audio 分支），新增 `_transcribe_audio`。不碰 provider 层。 |
| TTS config schema | **低** | 新增 `TTSConfig` 和 `Config.tts` 字段，Pydantic 默认工厂保证向后兼容。 |
| TTS 模块 (`nanobot/tts/`) | **低** | 纯新增包，不修改现有文件。 |
| `DiscordChannel.__init__` 懒加载 TTS | **低** | 仅检查 `config.tts` 并构造 service。 |
| `DiscordChannel.send` TTS 注入 | **中** | override `send` 方法，内嵌 TTS 逻辑。若 upstream 未来大幅重构 `send`，需同步。 |
| `/tts` builtin command | **低** | `builtin.py` 新增 handler + 一行注册。 |
| `AgentLoop._state_respond` metadata 补丁 | **低** | 两行注入，不改 `_assemble_outbound` 签名。 |
| `MessageTool.add_default_metadata` | **低** | 新增公共方法，不改现有行为。 |
| `AgentLoop._state_build` 调用 `add_default_metadata` | **低** | 两行注入，仅在 session TTS 开启时执行。 |

**总体评估**：所有改动均为"新增方法/模块"或"在现有函数末尾/条件分支中注入"，没有删除 upstream 代码，没有重命名 upstream 类/方法。

---

## 6. 测试方案

### 6.1 Mention filter

**文件**：`tests/channels/test_discord_channel.py`

- `test_mentions_other_bot_only_ignores_message_mentioning_only_other_bot`
  - 构造 `message.mentions = [other_bot]`，无 self mention。
  - 断言 `_handle_message` 未被调用。
- `test_mentions_other_bot_only_accepts_message_mentioning_self_and_other_bot`
  - 构造 mentions self + other bot。
  - 断言消息被处理。
- `test_mentions_other_bot_only_accepts_human_message`
  - 无 bot mention。
  - 断言消息被处理。
- `test_mentions_other_bot_only_checks_raw_mentions_and_content`
  - 测试 `<@BOT_ID>` 和 `<@!BOT_ID>` 在 content 中被识别为 self mention。

### 6.2 Webhook 放行

- `test_webhook_bot_message_is_not_dropped`
  - `message.author.bot = True`，`message.webhook_id = "123"`。
  - 断言消息被处理。
- `test_non_webhook_bot_message_is_dropped_if_guard_exists`
  - 如果实现了 bot-author guard：
    - `message.author.bot = True`，`message.webhook_id = None`。
    - 断言消息被忽略。

### 6.3 语音转写

- `test_audio_attachment_is_transcribed_and_replaces_attachment_marker`
  - Monkeypatch `_transcribe_audio` 返回 `"hello world"`。
  - 断言 content 包含 `"[transcription: hello world]"`。
  - 断言 media_paths 不包含音频文件路径（音频不进入 LLM media）。
- `test_transcription_failure_falls_back_to_attachment_marker`
  - Monkeypatch `_transcribe_audio` 返回 `""`。
  - 断言 marker 为 `"[attachment: foo.mp3 - transcription failed]"`。
- `test_non_audio_attachment_is_not_transcribed`
  - 普通图片附件。
  - 断言走原有路径，`media_paths` 包含文件，`markers` 包含 `[attachment: ...]`。

### 6.4 TTS Service 单元测试

**文件**：`tests/tts/test_service.py`（需新建 `tests/tts/__init__.py`）

- `test_tts_service_does_not_trigger_when_disabled`
  - `TTSConfig(enabled=False)`；断言 `should_trigger(session_tts=True)` 为 False。
- `test_tts_service_triggers_on_session_tts_when_enabled`
  - `TTSConfig(enabled=True)`；断言 `should_trigger(session_tts=True)` 为 True。
- `test_tts_service_triggers_on_sender_name_case_insensitive`
  - `auto_tts_senders=["Peppa"]`，`sender_name="peppa"`；断言 True。
- `test_tts_service_truncates_text_and_returns_path`
  - Monkeypatch `factory.create_provider` 返回 fake provider（记录 text 并写 bytes）。
  - `max_text_length=5`，`await synthesize("abcdef")`。
  - Fake provider 收到 `"abcde"`，返回路径存在。
- `test_tts_factory_rejects_unknown_provider`
  - `TTSConfig(provider="bogus")`；断言 `ValueError`。

### 6.5 `/tts` 命令

- `test_tts_command_sets_session_metadata`
  - 构造 `CommandContext` + mock session。
  - 调用 `cmd_tts(ctx)`，断言 `session.metadata["_session_tts"]` 为 True/False。
- `test_tts_command_returns_status_when_no_args`
  - 无 args，断言返回内容包含当前状态。

### 6.6 Integration（AgentLoop + MessageTool + Outbound）

- `test_session_tts_reaches_outbound_metadata`
  - Mock `AgentLoop._state_respond` 路径：session.metadata 中预置 `_session_tts=True`。
  - 调用 `_process_message`，断言返回的 `OutboundMessage.metadata["_session_tts"]` 为 True。
- `test_session_tts_reaches_message_tool_same_target_send`
  - 设置 session.metadata + 调用 `MessageTool.start_turn()` + `add_default_metadata`。
  - `send_message` same-target，断言 publish 的 `OutboundMessage.metadata["_session_tts"]` 为 True。

### 6.7 DiscordChannel.send TTS 集成

- `test_discord_send_appends_tts_audio_when_session_tts_enabled`
  - Monkeypatch `TTSService.synthesize` 返回 fake path。
  - 构造 `OutboundMessage(metadata={"_session_tts": True})`。
  - 调用 `DiscordChannel.send(msg)`。
  - 断言 `msg.media` 包含 fake path，且原始 send 逻辑仍被调用。
- `test_discord_send_does_not_block_text_on_tts_failure`
  - Monkeypatch `TTSService.synthesize` 抛异常。
  - 调用 `send`，断言文本消息仍被发送，无异常上抛。

---

## 7. 向前兼容性

以下设计决策**依赖 upstream 特定实现细节**，是未来升级（upstream 3.0/4.0）时必须 review 的 point：

| # | 依赖点 | 当前 upstream 实现 | 未来升级风险 |
|---|--------|-------------------|-------------|
| 1 | `AgentLoop._state_respond` 存在且可调 | `AgentLoop` 使用状态机，`RESPOND` 状态由 `_state_respond` 处理 | 若 upstream 改为函数式或取消状态机，TTS metadata 补丁需迁移到新组装点。 |
| 2 | `MessageTool._default_metadata` 是 `ContextVar[dict]` | `start_turn()` 通过 `ContextVar` 传播 metadata | 若 upstream 改用显式 context 对象或依赖注入，需重写传播逻辑。 |
| 3 | `BaseChannel.send` 是 async 实例方法 | `DiscordChannel.send(self, msg)` 签名 | 若 upstream 改为 `send(self, msg, **kwargs)` 或引入中间 transport 层，TTS 注入点需调整。 |
| 4 | `DiscordConfig` 定义在 `discord.py` 而非 schema.py | `DiscordChannel.__init__` 中 `DiscordConfig.model_validate(config)` | 若 upstream 将 channel config 收归 schema.py 统一注册，需同步移动 `tts` 字段。 |
| 5 | `ChannelManager._dispatch_outbound` 直接调用 `channel.send(msg)` | outbound 不经过转换直接投递 | 若 upstream 引入 outbound interceptor/middleware 层，TTS 可迁移到 middleware 中， cleaner。 |
| 6 | `Session.metadata` 是自由 `dict[str, Any]` | 直接读写，无 schema 校验 | 若 upstream 给 metadata 增加 schema 或白名单，`_session_tts` 需注册为合法 key。 |
| 7 | `discord.py` Message 对象的 `mentions`, `raw_mentions`, `webhook_id` | Mention filter 依赖这些属性 | 若 upstream 换用其他 Discord 库（如 `nextcord`/`disnake`），属性名大概率兼容，但需验证。 |

**缓解策略**：
- 所有 TTS 逻辑集中在 `nanobot/tts/` 包和 `DiscordChannel.send` 中，核心改动不超过 5 个文件。
- TTS 模块与 upstream 核心通过 `OutboundMessage.metadata["_session_tts"]` 单一 flag 耦合，契约极简。
- `add_default_metadata` 是正向扩展（新增方法），不破坏现有接口。

---

## 8. 实现顺序

按依赖拓扑排序，保证每一步可独立测试：

1. **Schema + TTS 模块骨架**
   - `nanobot/config/schema.py`：新增 `TTSConfig`，`Config` 加 `tts` 字段。
   - `nanobot/tts/__init__.py`, `base.py`, `factory.py`, `service.py`。
   - `pyproject.toml`：确认 `edge-tts` 在依赖中。
   - 运行 `tests/tts/test_service.py`（此时测试会导入失败或跳过，验证包结构）。

2. **Provider 实现**
   - `nanobot/tts/edge.py`, `nanobot/tts/fish.py`。
   - 运行 TTS service 测试（含 fake provider monkeypatch）。

3. **Mention filter + Webhook 测试**
   - `DiscordChannel._mentions_other_bot_only`。
   - `tests/channels/test_discord_channel.py` 新增测试。
   - 验证现有 Discord 测试不 regress。

4. **语音转写**
   - `_is_audio_attachment`, `_transcribe_audio`，修改 `_download_attachments`。
   - 新增 transcription 相关测试。
   - 运行 `tests/providers/test_transcription.py` + Discord attachment 测试。

5. **`/tts` 命令**
   - `builtin.py` 新增 `cmd_tts` + 注册。
   - 测试命令行为。

6. **session_tts 传递到 outbound + MessageTool**
   - `AgentLoop._state_respond` metadata 补丁。
   - `MessageTool.add_default_metadata` + `AgentLoop._state_build` 调用。
   - 测试 `_process_message` 返回的 outbound 含 flag；测试 MessageTool same-target 发送含 flag。

7. **DiscordChannel.send TTS 注入**
   - `DiscordChannel.__init__` 懒加载 `TTSService`。
   - `DiscordChannel.send` 中注入 TTS 合成逻辑。
   - 端到端测试： outbound → send → media 追加。

8. **集成回归**
   - 运行全部 Discord / agent / channel 测试。
   - 按 plan §9 执行 smoke test。

---

## 9. 关键设计决策总结

1. **Mention filter 放在 `DiscordChannel._handle_discord_message`**，不是 `BaseChannel`。因为 mention 语义平台相关。
2. **语音转写复用 `nanobot/providers/transcription.py`**，不新建 provider。调用链止于 `DiscordChannel._transcribe_audio`。
3. **TTS 是可选插件模块 `nanobot/tts/`，生命周期由 `DiscordChannel` 持有**。不侵入 `AgentLoop` 或 `ChannelManager` 核心流。
4. **`_session_tts` 在 `AgentLoop._state_respond` 中补丁到 outbound metadata**，不改 `_assemble_outbound` 签名，最小化核心 API 侵入。
5. **`MessageTool` 通过新增 `add_default_metadata()` 方法传播 `_session_tts`**，不改 `start_turn()` 签名，不污染 `InboundMessage.metadata`。
6. **TTS 合成在 `DiscordChannel.send` 中执行**，音频以 `OutboundMessage.media` 附加。失败被 swallow，不阻断文本。
7. **`/tts` 走 `CommandRouter` 文本命令层**，不注册 Discord slash command，保持跨 channel 命令语义统一。

## 10. 不确定点

| # | 不确定点 | 建议 |
|---|----------|------|
| 1 | `DiscordConfig` 是否应在 schema.py 中统一注册？ | 当前 upstream 将 `DiscordConfig` 放在 `discord.py`。本 spec 遵循此惯例，在 `DiscordConfig` 中直接加 `tts` 字段。若未来 upstream 统一迁移，同步移动即可。 |
| 2 | `sender_name` 在 outbound 中如何获取？ | **已与 Spec3 对齐**：Spec3 建立 `sender_name` 入口，写入 `InboundMessage.metadata["sender_name"]`。本 spec 在 `AgentLoop._state_respond` 中从 inbound metadata 读取 `sender_name`，结合 `auto_tts_senders` 判断后统一写入 `_session_tts` flag 到 outbound metadata。`DiscordChannel.send` 只读 `_session_tts` 一个 flag。metadata key 名必须与 Spec3 一致：`"sender_name"`。**实现顺序**：先实现 Spec3（建立入口），再实现 Spec2（消费）。 |
| 3 | `edge-tts` 在 upstream 的 pyproject.toml 中是否已存在？ | 需实现时检查。若不存在，加依赖。若存在但版本不同，验证 `edge_tts.Communicate` API 兼容性。 |
| 4 | 音频附件下载后是否应从磁盘清理？ | 当前 `_download_attachments` 不写临时文件，而是写到 `get_media_dir()`。transcription 后的原始音频可保留或删除。为最小改动，保留文件（与图片附件一致）。TTS 生成的音频在 `/tmp/nanobot_tts/` 中，由系统清理。 |
| 5 | `fish.py` 的 `httpx` 超时与重试策略 | 当前 FishTTSProvider 使用 `httpx.AsyncClient(timeout=60.0)`，无重试。若 Fish Audio 不稳定，未来可仿照 `transcription.py` 引入 `_post_with_retry` 模式。本 spec 保持简单，不加重试。 |
