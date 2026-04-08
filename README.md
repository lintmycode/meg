# meg

Personal assistant that sends scheduled text reminders and runs shell tasks,
delivering output via a configurable channel (Telegram by default).

Two independent stores, one shared `.env`:

| Store | Driven by | What fires |
|-------|-----------|-----------|
| `reminders.json` | `meg-dispatch.py` | Sends the reminder text |
| `tasks.json` | `meg-tasks-dispatch.py` | Runs a shell command, sends its output |

## Files

| File | Purpose |
|------|---------|
| `.env` | Runtime config: channel and target ID (gitignored) |
| `reminders.json` | Reminder store (gitignored) |
| `tasks.json` | Task store (gitignored) |
| `meg.py` | Reminders CLI — list, add, edit, done, remove |
| `meg-dispatch.py` | Reminders cron dispatcher |
| `meg-worker.py` | Reminders diagnostic (prints what's due, no side effects) |
| `meg-tasks.py` | Tasks CLI — list, add, run, pause, resume, done, edit, remove |
| `meg-tasks-dispatch.py` | Tasks cron dispatcher — runs commands, sends output |
| `reminders.example.json` | Template for `reminders.json` |
| `tasks.example.json` | Template for `tasks.json` |
| `.env.example` | Template for `.env` |
| `test_meg.py` | Tests for `meg.py` |
| `test_meg_tasks.py` | Tests for `meg-tasks.py` and `meg-tasks-dispatch.py` logic |

## Setup

```bash
cp .env.example .env
# edit .env: set REMINDER_CHANNEL and REMINDER_TARGET

cp reminders.example.json reminders.json   # for reminders
cp tasks.example.json tasks.json           # for tasks
```

---

## CLI — meg.py

### list

Show pending reminders. Items are sorted: dated (by due date), then undated.

```
meg.py list [--all]
```

```
    #  TEXT                                       DUE                NAG
  ──────────────────────────────────────────────────────────────────────
    1  Call the bank                              2026-04-09 09:00Z   30m
    2  Weekly review                              2026-04-14 09:00Z    —

       (no due date — tracked but never auto-fire)
    3  Research competitors                       —                    —

  (1 done item hidden — use --all to show)
```

`[wife]` appears next to items in wife mode. `--all` also shows done items.

---

### add

```
meg.py add TEXT [--due DATE] [--nag MINUTES] [--recurrence {daily,weekly}]
               [--mode {normal,wife}] [--notes TEXT]
```

| Option | Description |
|--------|-------------|
| `--due DATE` | Due date: ISO 8601, `+30m`, `+2h`, `+1d`, or `none` |
| `--nag MINUTES` | Re-fire interval in minutes once due (default: 15 if `--due` is set) |
| `--recurrence` | Advance `dueAt` after each fire. Only applies when `--nag` is **not** set |
| `--mode wife` | Appends "Still pending. Do it." to the sent message |
| `--notes` | Internal notes — stored in JSON, not sent |

```bash
meg.py add "Call the bank" --due +2h --nag 30
meg.py add "Weekly review" --due 2026-04-14T09:00:00Z --recurrence weekly
meg.py add "Research competitors"           # undated — tracked, never fires
meg.py add "Fix the leak" --mode wife --due +1d --nag 60
```

---

### done

Mark a reminder as done (sets `status: done` and `completedAt`).

```
meg.py done ID
```

```bash
meg.py done 3
meg.py done 2026-04-09   # unique ID prefix
```

---

### edit

Edit one or more fields on an existing reminder.

```
meg.py edit ID [--text TEXT] [--due DATE] [--nag MINUTES]
               [--recurrence {daily,weekly,none}] [--mode {normal,wife}]
               [--notes TEXT] [--status {pending,done}]
```

| Option | Notes |
|--------|-------|
| `--due none` | Clears the due date (item becomes undated) |
| `--nag none` | Clears the nag interval |
| `--recurrence none` | Clears recurrence |
| `--status pending` | Reopens a done item |

```bash
meg.py edit 3 --due +1d --nag 60
meg.py edit 3 --due none --nag none    # make undated
meg.py edit 3 --status pending         # reopen
meg.py edit 3 --mode wife
```

---

### remove

Permanently delete a reminder.

```
meg.py remove ID
```

---

### ID argument

All commands that take `ID` accept:

| Form | Example |
|------|---------|
| 1-based index from `list` | `3` |
| Full reminder ID | `2026-04-09T09:00:00Z-call-the-bank` |
| Unique ID prefix | `2026-04-09` (errors if ambiguous) |

---

## Cron entry point

```bash
python3 /path/to/ops/meg/meg-dispatch.py
```

Cron fires this every N minutes. It checks `reminders.json` for due items, sends a combined message, and updates `lastReminderAt` — but **only after a successful send**.

## Diagnostic

```bash
# See what's currently due (read-only, no messages sent):
python3 /path/to/ops/meg/meg-worker.py
```

## Tests

```bash
python3 -m pytest test_meg.py -v
# or without pytest:
python3 test_meg.py
```

---

## Reminder fields reference

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | `TIMESTAMP-slug`, unique |
| `text` | string | Message body |
| `dueAt` | ISO 8601 \| null | null = undated, never auto-fires |
| `status` | `pending` \| `done` | |
| `nagEveryMinutes` | int \| null | Defaults to 15 at dispatch time if null |
| `recurrence` | `daily` \| `weekly` \| null | Advances `dueAt` after each fire; only used when `nagEveryMinutes` is null |
| `mode` | `normal` \| `wife` | `wife` appends "Still pending. Do it." |
| `lastReminderAt` | ISO 8601 \| null | Updated on each successful send |
| `completedAt` | ISO 8601 \| null | Set by `done` command |
| `source` | `chat` \| `cli` | How the reminder was created |
| `notes` | string | Internal notes, never sent |

## Design notes (reminders)

- `reminders.json` is written atomically (`.tmp` → rename) — safe for concurrent readers.
- Overlapping cron invocations skip via a lockfile (`.meg-dispatch.lock`).
- Dedup: identical message text is suppressed if already sent within the last 24 hours.
- All timestamps are UTC ISO 8601.

---

## Tasks — meg-tasks.py

Tasks run a shell command on a schedule and send its stdout to the channel.
They share the same scheduling model as reminders.

### Scheduling modes

| Config | Behaviour |
|--------|-----------|
| `recurrence` only | Run at `dueAt`, then advance `dueAt` by the recurrence interval |
| `intervalMinutes` only | Run every N minutes after `lastRunAt` (ignores `dueAt` for re-fire timing) |
| neither | One-shot: run once at `dueAt`, then auto-archive (`status=done`) |

### list

```
meg-tasks.py list [--all]
```

```
  #  NAME                    DUE                RECUR    INT    LAST RUN          CODE
───────────────────────────────────────────────────────────────────────────────────────
  1  Daily disk report       2026-04-09 09:00Z  daily      —   2026-04-08 09:01Z  0
  2  Health check            —                  —         15m  2026-04-08 21:00Z  0
  3  Weekly backup           2026-04-14 09:00Z  weekly     —   —                  —
```

`--all` includes paused and done tasks.

### add

```
meg-tasks.py add NAME --command CMD [--due DATE] [--interval MINUTES]
                      [--recurrence {hourly,daily,weekly}] [--timeout SECONDS]
                      [--shell PATH] [--notes TEXT]
```

```bash
meg-tasks.py add "Disk report"   --command "df -h" --due 2026-04-09T09:00:00Z --recurrence daily
meg-tasks.py add "Health check"  --command "curl -sf https://example.com/health" --interval 15
meg-tasks.py add "Cert check"    --command "check-cert.sh" --due +6h --timeout 30
```

### run

Test a task locally — streams output to the terminal, does not send to Telegram,
does not update `lastRunAt`.

```
meg-tasks.py run ID
```

### pause / resume

```
meg-tasks.py pause ID
meg-tasks.py resume ID
```

Paused tasks are skipped by the dispatcher until resumed.

### done

Archive a task (sets `status: done`). The dispatcher auto-archives one-shot tasks after they run.

```
meg-tasks.py done ID
```

### edit

```
meg-tasks.py edit ID [--name NAME] [--command CMD] [--shell PATH] [--timeout SECONDS]
                     [--due DATE] [--interval MINUTES] [--recurrence {hourly,daily,weekly,none}]
                     [--notes TEXT] [--status {active,paused,done}]
```

`--interval none`, `--recurrence none`, `--due none` clear the respective field.

### remove

```
meg-tasks.py remove ID
```

ID accepts the same forms as `meg.py`: 1-based index, full ID, or unique prefix.

### Task fields reference

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | `TIMESTAMP-slug`, unique |
| `name` | string | Human-readable label |
| `command` | string | Shell command or multi-line script |
| `shell` | string | Shell executable (default `/bin/bash`) |
| `timeout` | int | Seconds before the command is killed (default 60) |
| `dueAt` | ISO 8601 \| null | null = fire on next dispatch tick |
| `status` | `active` \| `paused` \| `done` | |
| `recurrence` | `hourly` \| `daily` \| `weekly` \| null | Advances `dueAt` after each run |
| `intervalMinutes` | int \| null | Re-fire interval (takes precedence over recurrence for timing) |
| `lastRunAt` | ISO 8601 \| null | Set after each successful send |
| `lastExitCode` | int \| null | Exit code of last run |
| `completedAt` | ISO 8601 \| null | Set when archived |
| `notes` | string | Internal notes, never sent |

### Cron entry point

```bash
python3 /path/to/ops/meg/meg-tasks-dispatch.py
```

### Message format

```
[Task name]
<stdout of command>
Exit N: <stderr>    ← only on non-zero exit
(no output)         ← only when stdout is empty and exit was 0
```

Output is truncated at 4000 characters to stay within Telegram's limit.

### Design notes (tasks)

- Each due task sends its own message (unlike reminders which batch into one).
- `tasks.json` is written atomically, protected by `.meg-tasks-dispatch.lock`.
- If the Telegram send fails, `lastRunAt` is not updated (task will retry next tick).
- Non-zero exit codes are flagged in the message but do not stop other tasks from running.

## Tests

```bash
python3 test_meg.py           # 66 tests for meg.py
python3 test_meg_tasks.py     # 101 tests for meg-tasks.py + dispatch logic

# or with pytest:
python3 -m pytest test_meg.py test_meg_tasks.py -v
```
