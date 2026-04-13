#!/usr/bin/env python3
"""
meg-tasks.py — manage shell tasks from the command line.

Tasks are like reminders but instead of a text message, the dispatcher runs a
shell command and sends its output to the configured channel.

Commands:
    list                Show active tasks (--all to include paused/done)
    add NAME            Add a new task
    run ID              Run a task locally and stream output (no Telegram send)
    pause ID            Suspend a task until resumed
    resume ID           Re-activate a paused task
    done ID             Archive a completed task
    edit ID             Edit fields on an existing task
    remove ID           Delete a task permanently

ID can be a 1-based index from `list`, a full task ID, or a unique prefix.

Override the tasks file path with MEG_TASKS_DATA (used by tests).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from textwrap import shorten

HERE  = Path(__file__).parent
TASKS = Path(os.environ.get('MEG_TASKS_DATA', HERE.parent / 'data' / 'tasks.json'))
FAR_FUTURE = datetime(9999, 12, 31, tzinfo=timezone.utc)


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


def load_tasks():
    if not TASKS.exists():
        sys.exit(f'error: {TASKS} not found — copy tasks.example.json to get started')
    try:
        return json.loads(TASKS.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f'error: tasks.json is invalid JSON: {e}')


def save_tasks(tasks):
    atomic_write(TASKS, tasks)


# ── ordering and ID resolution ────────────────────────────────────────────────

def display_order(tasks, include_inactive=False):
    """
    Stable display order:
      active, dated (by dueAt) → active, undated → paused → done (if include_inactive)
    """
    active_dated = sorted(
        [t for t in tasks if t.get('status', 'active') == 'active' and t.get('dueAt')],
        key=lambda x: (parse_dt(x.get('dueAt')) or FAR_FUTURE, x.get('createdAt', ''))
    )
    active_undated = [t for t in tasks if t.get('status', 'active') == 'active' and not t.get('dueAt')]
    paused = [t for t in tasks if t.get('status') == 'paused'] if include_inactive else []
    done   = [t for t in tasks if t.get('status') == 'done']   if include_inactive else []
    return active_dated + active_undated + paused + done


def resolve(tasks, ref):
    """
    Resolve a user ref to a task dict.
    Accepts: 1-based index (from list), full ID, or unique ID prefix.
    """
    ordered = display_order(tasks, include_inactive=True)

    if re.fullmatch(r'\d+', ref):
        idx = int(ref) - 1
        if 0 <= idx < len(ordered):
            return ordered[idx]
        sys.exit(f'error: no item at index {ref} (list has {len(ordered)} items)')

    for task in tasks:
        if task.get('id') == ref:
            return task

    matches = [t for t in tasks if t.get('id', '').startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sys.exit(f'error: "{ref}" is ambiguous — matches {len(matches)} items; use a longer prefix or full ID')

    sys.exit(f'error: no task matches "{ref}"')


# ── due-date parsing ──────────────────────────────────────────────────────────

def parse_due(value, now):
    """
    Parse a due value:
      none / -           → None (clear / run immediately)
      +30m / +2h / +1d   → relative offset from now
      ISO 8601           → parsed directly
    """
    if not value or value.lower() in ('none', '-'):
        return None
    m = re.fullmatch(r'\+(\d+)(m|h|d)', value.lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {'m': timedelta(minutes=n), 'h': timedelta(hours=n), 'd': timedelta(days=n)}[unit]
        return iso(now + delta)
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return iso(dt)
    except ValueError:
        sys.exit(
            f'error: cannot parse due date "{value}"\n'
            '  Accepted formats: 2026-04-09T09:00:00Z  |  +30m  +2h  +1d  |  none'
        )


# ── display ───────────────────────────────────────────────────────────────────

NAME_W = 34
DUE_W  = 16
REC_W  = 7
INT_W  = 6
LAST_W = 16


def fmt_due(due_str):
    dt = parse_dt(due_str)
    return dt.strftime('%Y-%m-%d %H:%MZ') if dt else '—'


def fmt_last(last_str, exit_code):
    dt = parse_dt(last_str)
    if not dt:
        return '—', '—'
    code_str = '0' if exit_code == 0 else f'ERR({exit_code})'
    return dt.strftime('%Y-%m-%d %H:%MZ'), code_str


def print_header():
    print(f"  {'#':>3}  {'NAME':<{NAME_W}}  {'DUE':<{DUE_W}}  {'RECUR':<{REC_W}}  {'INT':>{INT_W}}  {'LAST RUN':<{LAST_W}}  CODE")
    print('  ' + '─' * (5 + NAME_W + 2 + DUE_W + 2 + REC_W + 2 + INT_W + 2 + LAST_W + 6))


def print_section(indexed_tasks):
    for n, task in indexed_tasks:
        name = shorten(task.get('name', ''), width=NAME_W, placeholder='…')
        if task.get('status') == 'paused':
            name = shorten(task.get('name', ''), width=NAME_W - 9, placeholder='…') + ' [paused]'
        due   = fmt_due(task.get('dueAt'))
        rec   = task.get('recurrence') or '—'
        intvl = f"{task['intervalMinutes']}m" if task.get('intervalMinutes') else '—'
        last, code = fmt_last(task.get('lastRunAt'), task.get('lastExitCode'))
        print(f"  {n:>3}  {name:<{NAME_W}}  {due:<{DUE_W}}  {rec:<{REC_W}}  {intvl:>{INT_W}}  {last:<{LAST_W}}  {code}")


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_list(args):
    tasks = load_tasks()
    include_all = getattr(args, 'all', False)

    all_ordered = display_order(tasks, include_inactive=True)
    index_of    = {id(t): n + 1 for n, t in enumerate(all_ordered)}

    def indexed(subset):
        return [(index_of[id(t)], t) for t in subset]

    active_dated   = [t for t in all_ordered if t.get('status', 'active') == 'active' and t.get('dueAt')]
    active_undated = [t for t in all_ordered if t.get('status', 'active') == 'active' and not t.get('dueAt')]
    paused_tasks   = [t for t in all_ordered if t.get('status') == 'paused']
    done_tasks     = [t for t in all_ordered if t.get('status') == 'done']

    nothing = not active_dated and not active_undated and \
              (not paused_tasks or not include_all) and \
              (not done_tasks or not include_all)
    if nothing:
        print('No tasks.')
        return

    printed = False

    if active_dated:
        print_header()
        printed = True
        print_section(indexed(active_dated))

    if active_undated:
        if not printed:
            print_header()
            printed = True
        else:
            print()
        print(f"  {'':>3}  (no due date — runs on every dispatch tick)")
        print_section(indexed(active_undated))

    if include_all and paused_tasks:
        if not printed:
            print_header()
            printed = True
        else:
            print()
        print(f"  {'':>3}  Paused:")
        print_section(indexed(paused_tasks))

    if include_all and done_tasks:
        if not printed:
            print_header()
        else:
            print()
        print(f"  {'':>3}  Done:")
        print_section(indexed(done_tasks))

    if not include_all:
        n_inactive = len(paused_tasks) + len(done_tasks)
        if n_inactive:
            print(f'\n  ({n_inactive} paused/done task{"s" if n_inactive > 1 else ""} hidden — use --all to show)')


def cmd_add(args):
    now   = datetime.now(timezone.utc)
    tasks = load_tasks()

    slug    = re.sub(r'[^a-z0-9]+', '-', args.name.lower()).strip('-')[:40]
    task_id = f'{iso(now)}-{slug}'
    due     = parse_due(args.due, now) if args.due else None

    task = {
        'id':              task_id,
        'name':            args.name,
        'command':         args.command,
        'shell':           args.shell or '/bin/bash',
        'timeout':         args.timeout or 60,
        'createdAt':       iso(now),
        'dueAt':           due,
        'status':          'active',
        'recurrence':      args.recurrence or None,
        'intervalMinutes': args.interval or None,
        'lastRunAt':       None,
        'lastExitCode':    None,
        'completedAt':     None,
        'notes':           args.notes or '',
    }
    tasks.append(task)
    save_tasks(tasks)

    all_ordered = display_order(tasks, include_inactive=True)
    n = next((i + 1 for i, t in enumerate(all_ordered) if t.get('id') == task_id), '?')
    print(f'Added #{n}: {args.name}')
    print(f'  Command: {args.command}')
    if due:
        print(f'  Due:     {fmt_due(due)}')
    if args.recurrence:
        print(f'  Recur:   {args.recurrence}')
    if args.interval:
        print(f'  Interval: every {args.interval}m')


def cmd_run(args):
    """Run a task locally: streams output to the terminal, does not send to Telegram."""
    tasks = load_tasks()
    task  = resolve(tasks, args.id)

    command = task.get('command', '')
    shell   = task.get('shell', '/bin/bash')
    timeout = task.get('timeout', 60)

    # Metadata on stderr so it doesn't mix with command stdout
    print(f'Task:    {task["name"]}', file=sys.stderr)
    print(f'Command: {command}',       file=sys.stderr)
    print('─' * 40,                    file=sys.stderr)

    try:
        result = subprocess.run(command, shell=True, executable=shell, timeout=timeout)
        sys.exit(result.returncode)
    except subprocess.TimeoutExpired:
        print(f'\nerror: timed out after {timeout}s', file=sys.stderr)
        sys.exit(1)


def cmd_pause(args):
    tasks = load_tasks()
    task  = resolve(tasks, args.id)
    if task.get('status') == 'paused':
        print(f'Already paused: {task["name"]}')
        return
    task['status'] = 'paused'
    save_tasks(tasks)
    print(f'Paused: {task["name"]}')


def cmd_resume(args):
    tasks = load_tasks()
    task  = resolve(tasks, args.id)
    if task.get('status') == 'active':
        print(f'Already active: {task["name"]}')
        return
    task['status'] = 'active'
    save_tasks(tasks)
    print(f'Resumed: {task["name"]}')


def cmd_done(args):
    now   = datetime.now(timezone.utc)
    tasks = load_tasks()
    task  = resolve(tasks, args.id)
    if task.get('status') == 'done':
        print(f'Already done: {task["name"]}')
        return
    task['status']      = 'done'
    task['completedAt'] = iso(now)
    save_tasks(tasks)
    print(f'Done: {task["name"]}')


def cmd_edit(args):
    now   = datetime.now(timezone.utc)
    tasks = load_tasks()
    task  = resolve(tasks, args.id)
    changed = []

    if args.name is not None:
        task['name'] = args.name
        changed.append('name')

    if args.command is not None:
        task['command'] = args.command
        changed.append('command')

    if args.shell is not None:
        task['shell'] = args.shell
        changed.append('shell')

    if args.timeout is not None:
        task['timeout'] = args.timeout
        changed.append('timeout')

    if args.due is not None:
        task['dueAt'] = parse_due(args.due, now)
        changed.append('dueAt')

    if args.interval is not None:
        if args.interval.lower() in ('none', '-', '0'):
            task['intervalMinutes'] = None
        else:
            try:
                task['intervalMinutes'] = int(args.interval)
            except ValueError:
                sys.exit('error: --interval must be a positive integer or "none"')
        changed.append('intervalMinutes')

    if args.recurrence is not None:
        task['recurrence'] = None if args.recurrence == 'none' else args.recurrence
        changed.append('recurrence')

    if args.notes is not None:
        task['notes'] = args.notes
        changed.append('notes')

    if args.status is not None:
        task['status'] = args.status
        if args.status == 'done' and not task.get('completedAt'):
            task['completedAt'] = iso(now)
        elif args.status in ('active', 'paused'):
            task['completedAt'] = None
        changed.append('status')

    if not changed:
        print('Nothing changed — specify at least one field to update.')
        return

    save_tasks(tasks)
    print(f'Updated: {task["name"]}')
    print(f'  Changed: {", ".join(changed)}')


def cmd_remove(args):
    tasks  = load_tasks()
    task   = resolve(tasks, args.id)
    pruned = [t for t in tasks if t.get('id') != task.get('id')]
    save_tasks(pruned)
    print(f'Removed: {task["name"]}')


# ── argument parser ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='meg-tasks.py',
        description='Manage shell tasks from the command line.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
scheduling modes:
  --recurrence only       runs at dueAt, then advances dueAt by the recurrence interval
  --interval only         runs every N minutes regardless of dueAt (re-fires after lastRunAt)
  neither                 one-shot: runs once at dueAt, then marked done automatically

examples:
  %(prog)s list
  %(prog)s list --all
  %(prog)s add "Disk report" --command "df -h" --due 2026-04-09T09:00:00Z --recurrence daily
  %(prog)s add "Health ping" --command "curl -sf https://example.com/health" --interval 15
  %(prog)s add "Cert check" --command "check-cert.sh" --due +1h --timeout 30
  %(prog)s run 1                          # test locally before deploying
  %(prog)s pause 2
  %(prog)s resume 2
  %(prog)s edit 1 --command "df -h /" --timeout 30
  %(prog)s edit 1 --interval none --recurrence daily
  %(prog)s done 3
  %(prog)s remove 3
""",
    )
    sub = parser.add_subparsers(dest='subcommand', metavar='COMMAND')

    # list
    p = sub.add_parser('list', help='show active tasks')
    p.add_argument('--all', action='store_true', help='include paused and done tasks')
    p.set_defaults(func=cmd_list)

    # add
    p = sub.add_parser('add', help='add a new task')
    p.add_argument('name', help='task name (human readable)')
    p.add_argument('--command', required=True, metavar='CMD',
                   help='shell command or script to run')
    p.add_argument('--due', metavar='DATE',
                   help='first run time: ISO 8601, +30m, +2h, +1d (omit to run immediately)')
    p.add_argument('--interval', type=int, metavar='MINUTES',
                   help='re-run every N minutes after lastRunAt (takes precedence over --recurrence for timing)')
    p.add_argument('--recurrence', choices=['hourly', 'daily', 'weekly'],
                   help='advance dueAt after each run (only when --interval is not set)')
    p.add_argument('--timeout', type=int, metavar='SECONDS', default=60,
                   help='command timeout in seconds (default: 60)')
    p.add_argument('--shell', metavar='PATH', default='/bin/bash',
                   help='shell executable (default: /bin/bash)')
    p.add_argument('--notes', metavar='TEXT', default='', help='internal notes')
    p.set_defaults(func=cmd_add)

    # run
    p = sub.add_parser('run', help='run a task locally (streams to terminal, no Telegram)')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.set_defaults(func=cmd_run)

    # pause
    p = sub.add_parser('pause', help='suspend a task until resumed')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.set_defaults(func=cmd_pause)

    # resume
    p = sub.add_parser('resume', help='re-activate a paused task')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.set_defaults(func=cmd_resume)

    # done
    p = sub.add_parser('done', help='archive a task')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.set_defaults(func=cmd_done)

    # edit
    p = sub.add_parser('edit', help='edit one or more fields on a task')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.add_argument('--name',       metavar='NAME')
    p.add_argument('--command',    metavar='CMD')
    p.add_argument('--shell',      metavar='PATH')
    p.add_argument('--timeout',    type=int, metavar='SECONDS')
    p.add_argument('--due',        metavar='DATE',
                   help='ISO 8601, +30m, +2h, +1d, or "none" to clear')
    p.add_argument('--interval',   metavar='MINUTES',
                   help='minutes between runs, or "none" to clear')
    p.add_argument('--recurrence', choices=['hourly', 'daily', 'weekly', 'none'])
    p.add_argument('--notes',      metavar='TEXT')
    p.add_argument('--status',     choices=['active', 'paused', 'done'])
    p.set_defaults(func=cmd_edit)

    # remove
    p = sub.add_parser('remove', help='permanently delete a task')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == '__main__':
    main()
