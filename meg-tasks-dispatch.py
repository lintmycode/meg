#!/usr/bin/env python3
"""
meg-tasks-dispatch.py — cron-driven task runner.

Called from cron every N minutes. For each active task that is due and past
its run interval, runs the shell command, sends its output to the configured
channel, then updates tasks.json atomically — only after a successful send.

Scheduling modes (same logic as meg-dispatch.py):
  recurrence only   → advance dueAt by interval after each run
  intervalMinutes   → re-fire every N minutes after lastRunAt
  neither           → one-shot: run once, then mark status=done

Configuration is read from .env next to this script (see .env.example).
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
TASKS = HERE / 'tasks.json'
LOCK  = HERE / '.meg-tasks-dispatch.lock'

# Telegram hard limit is 4096; leave headroom for the truncation notice
TELEGRAM_MAX = 4000


def load_dotenv():
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


# ── helpers ───────────────────────────────────────────────────────────────────

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def atomic_write(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.rename(path)


def recurrence_delta(rec):
    if rec == 'hourly':
        return timedelta(hours=1)
    if rec == 'daily':
        return timedelta(days=1)
    if rec == 'weekly':
        return timedelta(weeks=1)
    return None


# ── scheduling ────────────────────────────────────────────────────────────────

def find_due(tasks, now):
    """Return active tasks whose next run time has passed."""
    due = []
    for task in tasks:
        if task.get('status', 'active') != 'active':
            continue

        due_at = parse_dt(task.get('dueAt'))
        # Tasks without dueAt run on every dispatch tick (intervalMinutes governs throttle)
        if due_at and due_at > now:
            continue

        interval = task.get('intervalMinutes')
        if interval:
            last_run = parse_dt(task.get('lastRunAt'))
            if last_run and now < last_run + timedelta(minutes=interval):
                continue

        due.append(task)
    return due


# ── execution ─────────────────────────────────────────────────────────────────

def run_command(task):
    """
    Run the task's command in a subprocess.
    Returns (stdout, stderr, exit_code).
    exit_code=-1 on timeout.
    """
    command = task.get('command', '')
    shell   = task.get('shell', '/bin/bash')
    timeout = task.get('timeout', 60)

    try:
        r = subprocess.run(
            command,
            shell=True,
            executable=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return '', f'Command timed out after {timeout}s', -1


def format_message(task, stdout, stderr, exit_code):
    """
    Build the Telegram message for a task run.

    Format:
      [Task name]
      <stdout>
      Exit N: <stderr>   ← only on failure
      (no output)        ← only when stdout is empty and exit was 0
    """
    name   = task.get('name', task.get('id', 'Task'))
    header = f'[{name}]'
    body   = stdout.strip()
    footer = None

    if exit_code != 0:
        err_detail = stderr.strip()
        footer = f'Exit {exit_code}' + (f': {err_detail}' if err_detail else '')

    parts = [header]
    if body:
        parts.append(body)
    elif not footer:
        parts.append('(no output)')
    if footer:
        parts.append(footer)

    msg = '\n'.join(parts)
    if len(msg) > TELEGRAM_MAX:
        msg = msg[:TELEGRAM_MAX - 14] + '\n…(truncated)'
    return msg


# ── send ──────────────────────────────────────────────────────────────────────

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
        return False
    return True


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    with open(LOCK, 'w') as lock_fh:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.stderr.write('meg-tasks-dispatch: already running, skipping\n')
            return

        if not TASKS.exists():
            return

        try:
            tasks = json.loads(TASKS.read_text())
        except json.JSONDecodeError as e:
            sys.stderr.write(f'meg-tasks-dispatch: invalid JSON: {e}\n')
            return

        now     = datetime.now(timezone.utc)
        due     = find_due(tasks, now)
        changed = False

        for task in due:
            stdout, stderr, exit_code = run_command(task)
            msg  = format_message(task, stdout, stderr, exit_code)
            sent = send_message(msg)

            if not sent:
                # Do not update state if the message failed to send
                sys.stderr.write(f'meg-tasks-dispatch: send failed for task {task.get("id")}\n')
                continue

            for row in tasks:
                if row.get('id') != task.get('id'):
                    continue

                row['lastRunAt']    = iso(now)
                row['lastExitCode'] = exit_code
                delta    = recurrence_delta(row.get('recurrence'))
                interval = row.get('intervalMinutes')

                if delta and not interval:
                    # Recurrence only: advance dueAt to next occurrence
                    row['dueAt'] = iso((parse_dt(row.get('dueAt')) or now) + delta)
                elif not delta and not interval:
                    # One-shot: archive after the single run
                    row['status']      = 'done'
                    row['completedAt'] = iso(now)
                # intervalMinutes: keep dueAt as-is; interval governs re-fire timing

                changed = True
                break

        if changed:
            atomic_write(TASKS, tasks)


if __name__ == '__main__':
    main()
