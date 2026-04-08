#!/usr/bin/env python3
"""
meg-worker.py — standalone diagnostic tool.

Prints the reminders that are currently due to stdout, one per line, without
modifying any state. Useful for debugging without triggering a real send.

NOTE: meg-dispatch.py now inlines this logic and is the canonical cron
entry point. This file exists only for manual inspection / debugging.

Usage:
    python3 meg-worker.py
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Resolved relative to this file — works in container and locally.
REMINDERS = Path(__file__).parent / 'reminders.json'

NAG_DEFAULT = 15  # minutes


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def load_items():
    if not REMINDERS.exists():
        return []
    try:
        return json.loads(REMINDERS.read_text())
    except Exception:
        return []


def main():
    now   = datetime.now(timezone.utc)
    items = load_items()
    due   = []

    for item in items:
        if item.get('status', 'pending') != 'pending':
            continue

        due_at = parse_dt(item.get('dueAt'))
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

    if not due:
        print('NO_REPLY')
        return

    due.sort(key=lambda x: (parse_dt(x.get('dueAt')) or now, x.get('createdAt', '')))

    for item in due:
        mode = (item.get('mode') or '').lower()
        text = item.get('text', 'Reminder')
        if mode == 'wife':
            print(f'Reminder: {text}. Still pending. Do it.')
        else:
            print(f'Reminder: {text}.')


if __name__ == '__main__':
    main()
