@AGENTS.md

## Raw user message ledger

```text
nanobot/agent/loop.py             # Gates human turns before commands, queues, models, or tools.
nanobot/session/manager.py        # Mutable model context; `/new` and compaction may clear it.
nanobot/session/raw_ledger.py     # Append-only raw human messages in UTF-8 JSONL.
tests/agent/test_loop_raw_ledger.py
tests/session/test_raw_ledger.py
```

`SessionManager` owns replayable model context, not an audit log. `RawMessageLedger` owns the authoritative record of human messages received after enablement and never feeds model context. Records live at `<workspace>/messages/raw-user-messages.jsonl`; each append is flushed and fsynced before processing continues, and a failed append is rolled back to its starting offset. System, cron, subagent, local-trigger, Dream, heartbeat, and internal-continuation turns are excluded. Queue re-publication preserves an internal `InboundMessage` recorded flag so one delivery is not duplicated without leaking that state into channel metadata; identical distinct messages remain distinct records.
