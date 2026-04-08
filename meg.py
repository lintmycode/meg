#!/usr/bin/env python3
"""
meg.py — manage reminders from the command line.

Commands:
    list              Show pending reminders (--all to include done)
    add TEXT          Add a new reminder
    done ID           Mark a reminder as done
    edit ID           Edit fields on an existing reminder
    remove ID         Delete a reminder permanently

ID can be a 1-based index from `list`, a full reminder ID, or a unique prefix.

The reminders file defaults to reminders.json next to this script.
Override with the MEG_DATA environment variable (used by tests).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from textwrap import shorten

HERE      = Path(__file__).parent
REMINDERS = Path(os.environ.get('MEG_DATA', HERE / 'reminders.json'))
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


def atomic_write(path: Path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.rename(path)


def load_items():
    if not REMINDERS.exists():
        sys.exit(f'error: {REMINDERS} not found — copy reminders.example.json to get started')
    try:
        return json.loads(REMINDERS.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f'error: reminders.json is invalid JSON: {e}')


def save_items(items):
    atomic_write(REMINDERS, items)


# ── ordering and ID resolution ────────────────────────────────────────────────

def display_order(items, include_done=False):
    """
    Return items in a stable display order:
      pending, dated (sorted by dueAt) → pending, undated → done (if include_done)
    """
    pending_dated   = sorted(
        [i for i in items if i.get('status', 'pending') == 'pending' and i.get('dueAt')],
        key=lambda x: (parse_dt(x.get('dueAt')) or FAR_FUTURE, x.get('createdAt', ''))
    )
    pending_undated = [i for i in items if i.get('status', 'pending') == 'pending' and not i.get('dueAt')]
    done            = [i for i in items if i.get('status') == 'done'] if include_done else []
    return pending_dated + pending_undated + done


def resolve(items, ref):
    """
    Resolve a user-supplied ref to an item dict.
    Accepts: 1-based index (from list output), full ID, or unique ID prefix.
    """
    ordered = display_order(items, include_done=True)

    if re.fullmatch(r'\d+', ref):
        idx = int(ref) - 1
        if 0 <= idx < len(ordered):
            return ordered[idx]
        sys.exit(f'error: no item at index {ref} (list has {len(ordered)} items)')

    for item in items:
        if item.get('id') == ref:
            return item

    matches = [i for i in items if i.get('id', '').startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sys.exit(f'error: "{ref}" is ambiguous — matches {len(matches)} items; use a longer prefix or the full ID')

    sys.exit(f'error: no reminder matches "{ref}"')


# ── due-date parsing ──────────────────────────────────────────────────────────

def parse_due(value, now):
    """
    Parse a due-date argument:
      none / -           → clear (returns None)
      +30m / +2h / +1d   → relative offset from now
      ISO 8601 string    → parsed directly (Z or +HH:MM offset; naive treated as UTC)
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


# ── table display ─────────────────────────────────────────────────────────────

TEXT_W = 50
DUE_W  = 16
NAG_W  = 6


def fmt_due(due_str):
    dt = parse_dt(due_str)
    return dt.strftime('%Y-%m-%d %H:%MZ') if dt else '—'


def fmt_nag(nag):
    return f'{nag}m' if nag is not None else '—'


def print_section(indexed_items):
    for n, item in indexed_items:
        text = shorten(item.get('text', ''), width=TEXT_W, placeholder='…')
        if (item.get('mode') or '') == 'wife':
            text = shorten(item.get('text', ''), width=TEXT_W - 7, placeholder='…') + ' [wife]'
        due = fmt_due(item.get('dueAt'))
        nag = fmt_nag(item.get('nagEveryMinutes'))
        print(f'  {n:>3}  {text:<{TEXT_W}}  {due:<{DUE_W}}  {nag:>{NAG_W}}')


def print_header():
    print(f"  {'#':>3}  {'TEXT':<{TEXT_W}}  {'DUE':<{DUE_W}}  {'NAG':>{NAG_W}}")
    print('  ' + '─' * (5 + TEXT_W + 2 + DUE_W + 2 + NAG_W))


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_list(args):
    items    = load_items()
    show_all = getattr(args, 'all', False)

    # Indices are always over the full set so refs are stable across runs
    all_ordered = display_order(items, include_done=True)
    index_of    = {id(item): n + 1 for n, item in enumerate(all_ordered)}

    def indexed(subset):
        return [(index_of[id(i)], i) for i in subset]

    pending_dated   = [i for i in all_ordered if i.get('status', 'pending') == 'pending' and i.get('dueAt')]
    pending_undated = [i for i in all_ordered if i.get('status', 'pending') == 'pending' and not i.get('dueAt')]
    done_items      = [i for i in all_ordered if i.get('status') == 'done']

    nothing = not pending_dated and not pending_undated and (not done_items or not show_all)
    if nothing:
        print('No reminders.')
        return

    printed_header = False

    if pending_dated:
        print_header()
        printed_header = True
        print_section(indexed(pending_dated))

    if pending_undated:
        if not printed_header:
            print_header()
            printed_header = True
        else:
            print()
        print(f"  {'':>3}  (no due date — tracked but never auto-fire)")
        print_section(indexed(pending_undated))

    if show_all and done_items:
        if not printed_header:
            print_header()
        else:
            print()
        print(f"  {'':>3}  Done:")
        print_section(indexed(done_items))

    if not show_all and done_items:
        n = len(done_items)
        print(f'\n  ({n} done item{"s" if n > 1 else ""} hidden — use --all to show)')


def cmd_add(args):
    now   = datetime.now(timezone.utc)
    items = load_items()

    slug    = re.sub(r'[^a-z0-9]+', '-', args.text.lower()).strip('-')[:40]
    item_id = f'{iso(now)}-{slug}'
    due     = parse_due(args.due, now) if args.due else None

    item = {
        'id':              item_id,
        'text':            args.text,
        'createdAt':       iso(now),
        'dueAt':           due,
        'status':          'pending',
        'source':          'cli',
        'notes':           args.notes or '',
        'recurrence':      args.recurrence or None,
        'nagEveryMinutes': args.nag or None,
        'lastReminderAt':  None,
        'completedAt':     None,
        'mode':            args.mode or 'normal',
    }
    items.append(item)
    save_items(items)

    all_ordered = display_order(items, include_done=True)
    n = next((i + 1 for i, x in enumerate(all_ordered) if x.get('id') == item_id), '?')
    print(f'Added #{n}: {args.text}')
    if due:
        print(f'  Due:  {fmt_due(due)}')
    if args.nag:
        print(f'  Nag:  every {args.nag}m')
    if args.recurrence:
        print(f'  Recurrence: {args.recurrence}')


def cmd_done(args):
    now   = datetime.now(timezone.utc)
    items = load_items()
    item  = resolve(items, args.id)

    if item.get('status') == 'done':
        print(f'Already done: {item["text"]}')
        return

    item['status']      = 'done'
    item['completedAt'] = iso(now)
    save_items(items)
    print(f'Done: {item["text"]}')


def cmd_edit(args):
    now   = datetime.now(timezone.utc)
    items = load_items()
    item  = resolve(items, args.id)
    changed = []

    if args.text is not None:
        item['text'] = args.text
        changed.append('text')

    if args.due is not None:
        item['dueAt'] = parse_due(args.due, now)
        changed.append('dueAt')

    if args.nag is not None:
        if args.nag.lower() in ('none', '-', '0'):
            item['nagEveryMinutes'] = None
        else:
            try:
                item['nagEveryMinutes'] = int(args.nag)
            except ValueError:
                sys.exit('error: --nag must be a positive integer or "none"')
        changed.append('nagEveryMinutes')

    if args.recurrence is not None:
        item['recurrence'] = None if args.recurrence == 'none' else args.recurrence
        changed.append('recurrence')

    if args.mode is not None:
        item['mode'] = args.mode
        changed.append('mode')

    if args.notes is not None:
        item['notes'] = args.notes
        changed.append('notes')

    if args.status is not None:
        item['status'] = args.status
        if args.status == 'done' and not item.get('completedAt'):
            item['completedAt'] = iso(now)
        elif args.status == 'pending':
            item['completedAt'] = None
        changed.append('status')

    if not changed:
        print('Nothing changed — specify at least one field to update.')
        return

    save_items(items)
    print(f'Updated: {item["text"]}')
    print(f'  Changed: {", ".join(changed)}')


def cmd_remove(args):
    items  = load_items()
    item   = resolve(items, args.id)
    pruned = [i for i in items if i.get('id') != item.get('id')]
    save_items(pruned)
    print(f'Removed: {item["text"]}')


# ── argument parser ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='meg.py',
        description='Manage reminders from the command line.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  %(prog)s list
  %(prog)s list --all
  %(prog)s add "Call the bank" --due +2h --nag 30
  %(prog)s add "Weekly review" --due 2026-04-14T09:00:00Z --recurrence weekly
  %(prog)s done 3
  %(prog)s edit 3 --due +1d --nag 60
  %(prog)s edit 3 --due none --nag none     # clear due date and nag
  %(prog)s edit 3 --status pending          # reopen a done item
  %(prog)s remove 3
""",
    )
    sub = parser.add_subparsers(dest='command', metavar='COMMAND')

    # list
    p = sub.add_parser('list', help='show pending reminders')
    p.add_argument('--all', action='store_true', help='include done items')
    p.set_defaults(func=cmd_list)

    # add
    p = sub.add_parser('add', help='add a new reminder')
    p.add_argument('text', help='reminder text')
    p.add_argument('--due', metavar='DATE',
                   help='due date: ISO 8601, +30m, +2h, +1d, or "none"')
    p.add_argument('--nag', type=int, metavar='MINUTES',
                   help='re-fire interval in minutes (default: 15 when --due is set)')
    p.add_argument('--recurrence', choices=['daily', 'weekly'],
                   help='advance due date after each fire (only applies when --nag is not set)')
    p.add_argument('--mode', choices=['normal', 'wife'], default='normal',
                   help='"wife" appends "Still pending. Do it." to the message')
    p.add_argument('--notes', metavar='TEXT', default='', help='internal notes (not sent)')
    p.set_defaults(func=cmd_add)

    # done
    p = sub.add_parser('done', help='mark a reminder as done')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.set_defaults(func=cmd_done)

    # edit
    p = sub.add_parser('edit', help='edit one or more fields on a reminder')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.add_argument('--text',        metavar='TEXT',  help='new reminder text')
    p.add_argument('--due',         metavar='DATE',
                   help='new due date: ISO 8601, +30m, +2h, +1d, or "none" to clear')
    p.add_argument('--nag',         metavar='MINUTES',
                   help='nag interval in minutes, or "none" to clear')
    p.add_argument('--recurrence',  choices=['daily', 'weekly', 'none'],
                   help='"none" clears the recurrence')
    p.add_argument('--mode',        choices=['normal', 'wife'])
    p.add_argument('--notes',       metavar='TEXT')
    p.add_argument('--status',      choices=['pending', 'done'],
                   help='reopen ("pending") or close ("done") without a timestamp message')
    p.set_defaults(func=cmd_edit)

    # remove
    p = sub.add_parser('remove', help='permanently delete a reminder')
    p.add_argument('id', help='index, full ID, or unique ID prefix')
    p.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == '__main__':
    main()
