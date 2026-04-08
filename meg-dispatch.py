#!/usr/bin/env python3
"""
meg-dispatch.py — cron-driven reminder notifier.

Called from cron every N minutes. Reads reminders.json, fires a message for
each item that is due and past its nag interval, then updates reminders.json
and the dispatch state atomically — but only after a successful send.
Nothing is mutated if the message is not sent.

Configuration is read from a .env file next to this script (see .env.example).
All paths are resolved relative to this script.
"""
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE  = Path(__file__).parent
# State lives two levels up from ops/meg/ → workspace root / memory/
STATE = HERE.parent.parent / 'memory' / 'meg-dispatch-state.json'
LOCK  = HERE / '.meg-dispatch.lock'

NAG_DEFAULT  = 15   # minutes, used when nagEveryMinutes is absent
DEDUP_TTL_H  = 24   # hours — identical text is suppressed only within this window


def load_dotenv():
    """Load .env next to this script into os.environ (existing vars take precedence)."""
    env_file = HERE / '.env'
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv()
CHANNEL = os.environ['REMINDER_CHANNEL']
TARGET  = os.environ['REMINDER_TARGET']


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def atomic_write(path: Path, data):
    """Write JSON to a .tmp file then rename — safe against concurrent readers."""
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.rename(path)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Reminder logic
# ---------------------------------------------------------------------------

def recurrence_delta(rec):
    if not rec:
        return None
    if rec == 'daily':
        return timedelta(days=1)
    if rec == 'weekly':
        return timedelta(days=7)
    # monthly and other values are not supported; nag interval handles timing
    return None


def find_due(items, now):
    due = []
    for item in items:
        if item.get('status', 'pending') != 'pending':
            continue

        due_at = parse_dt(item.get('dueAt'))
        # Items without a due date are tracked but never fire automatically.
        if not due_at or due_at > now:
            continue

        try:
            nag_every = int(item.get('nagEveryMinutes') or NAG_DEFAULT)
        except Exception:
            nag_every = NAG_DEFAULT

        last = parse_dt(item.get('lastReminderAt'))
        if last and now < last + timedelta(minutes=nag_every):
            continue

        due.append(item)
    return due


def build_message(item):
    mode = (item.get('mode') or '').lower()
    text = item.get('text', 'Reminder')
    if mode == 'wife':
        return f'Reminder: {text}. Still pending. Do it.'
    return f'Reminder: {text}.'


# ---------------------------------------------------------------------------
# Telegram send
# ---------------------------------------------------------------------------

def send_message(text):
    r = subprocess.run([
        'openclaw', 'message', 'send',
        '--channel', CHANNEL,
        '--target', TARGET,
        '--message', text,
        '--json',
    ], capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(r.returncode)
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Exclusive non-blocking lock so overlapping cron invocations skip cleanly.
    with open(LOCK, 'w') as lock_fh:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.stderr.write('reminder-dispatch: another instance is running, skipping\n')
            return

        reminders_path = HERE / 'reminders.json'
        now   = datetime.now(timezone.utc)
        items = load_json(reminders_path, [])
        due   = find_due(items, now)

        if not due:
            return

        due.sort(key=lambda x: (parse_dt(x.get('dueAt')) or now, x.get('createdAt', '')))
        combined = '\n'.join(build_message(item) for item in due)

        # Dedup: suppress if same text was already sent within DEDUP_TTL_H hours.
        state        = load_json(STATE, {})
        last_text    = state.get('lastSentText')
        last_sent_at = parse_dt(state.get('lastSentAt'))
        if (last_text == combined
                and last_sent_at
                and now - last_sent_at < timedelta(hours=DEDUP_TTL_H)):
            return

        # Send first — only mutate state on success.
        send_message(combined)

        due_ids = {item.get('id') for item in due}
        for row in items:
            if row.get('id') not in due_ids:
                continue
            row['lastReminderAt'] = iso(now)
            delta = recurrence_delta(row.get('recurrence'))
            # Advance dueAt only for pure-recurrence items (no nag interval).
            # When nagEveryMinutes is set, the nag interval governs timing and
            # dueAt is left as the original deadline for reference.
            if delta and not row.get('nagEveryMinutes'):
                row['dueAt'] = iso((parse_dt(row.get('dueAt')) or now) + delta)

        atomic_write(reminders_path, items)

        state['lastSentText'] = combined
        state['lastSentAt']   = iso(now)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(STATE, state)


if __name__ == '__main__':
    main()
