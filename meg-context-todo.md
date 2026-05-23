# meg reminder context sync todo

Problem:
- `meg-dispatch.py` currently sends reminders via `openclaw message send`
- Nuno receives the Telegram reminder, but Beth does not get that event in session context/history
- Result: delivery works, conversational continuity does not

Only two acceptable fix directions:

## A) Replace `openclaw message send` with a session send

Goal:
- send the reminder through an OpenClaw session-targeted mechanism instead of plain outbound message delivery
- ideally this both delivers to Nuno and lands in Beth's session context/history

Questions to validate:
- is there a supported CLI/API path to send into the active/main Beth session from `meg-dispatch.py`?
- can that session send still reach the Telegram chat reliably?
- if session send requires a fixed session key, how should that be configured/stored?
- does session delivery preserve the reminder as visible chat output to Nuno?

Desired outcome:
- one send path
- reminder reaches Telegram
- reminder is part of Beth's conversational context automatically

## B) Keep `openclaw message send` and inject a note into the session

Goal:
- preserve the current reliable Telegram delivery path
- add a second side effect after successful send: inject a note/event into Beth's session so she knows the reminder fired

Questions to validate:
- what is the cleanest supported way to inject a system note/event into the current/main session?
- can `meg-dispatch.py` call that directly via OpenClaw CLI/API?
- should the injected note be terse and structured, e.g. `Reminder sent to Nuno: Lead status`?
- should injection happen only after successful Telegram send? (probably yes)

Desired outcome:
- Telegram delivery remains reliable
- Beth sees reminder-send history in session context
- minimal change to existing `meg` delivery flow

## Constraint

Do not pursue other architecture ideas for now.
Only evaluate these two options.

## Current file of interest

- `/home/nuno/.openclaw/workspace/ops/meg/meg-dispatch.py`
