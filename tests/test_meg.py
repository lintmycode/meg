#!/usr/bin/env python3
"""
Tests for meg.py CLI.

Runs meg.py as a subprocess with MEG_DATA pointing to a temp file so the
live reminders.json is never touched. Each test gets a fresh, isolated store.

Run with:
    python3 -m pytest tests/test_meg.py -v
    python3 tests/test_meg.py          # no pytest required
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

MEG = str(Path(__file__).parent.parent / 'meg.py')


# ── helpers ───────────────────────────────────────────────────────────────────

def cli(*args, data_file):
    """Run meg.py with the given args and return the CompletedProcess."""
    env = os.environ.copy()
    env['MEG_DATA'] = str(data_file)
    return subprocess.run(
        [sys.executable, MEG, *args],
        capture_output=True, text=True, env=env,
    )


def make_item(id_, text='Test item', status='pending', dueAt=None,
              nagEveryMinutes=None, recurrence=None, mode='normal',
              completedAt=None):
    return {
        'id':              id_,
        'text':            text,
        'createdAt':       '2026-01-01T00:00:00Z',
        'dueAt':           dueAt,
        'status':          status,
        'source':          'test',
        'notes':           '',
        'recurrence':      recurrence,
        'nagEveryMinutes': nagEveryMinutes,
        'lastReminderAt':  None,
        'completedAt':     completedAt,
        'mode':            mode,
    }


class MegTestCase(unittest.TestCase):
    """Base class: sets up an isolated temp reminders.json for each test."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data = Path(self._tmpdir.name) / 'reminders.json'

    def tearDown(self):
        self._tmpdir.cleanup()

    def write(self, items):
        self.data.write_text(json.dumps(items, indent=2))

    def read(self):
        return json.loads(self.data.read_text())

    def run_cli(self, *args):
        return cli(*args, data_file=self.data)

    def assertSuccess(self, result):
        self.assertEqual(result.returncode, 0, msg=f'stderr: {result.stderr}')

    def assertFails(self, result):
        self.assertNotEqual(result.returncode, 0, msg=f'stdout: {result.stdout}')


# ── list ─────────────────────────────────────────────────────────────────────

class TestList(MegTestCase):

    def test_missing_file_exits_nonzero(self):
        # data file not created — should error, not crash
        r = self.run_cli('list')
        self.assertFails(r)
        self.assertIn('not found', r.stderr)

    def test_empty_store(self):
        self.write([])
        r = self.run_cli('list')
        self.assertSuccess(r)
        self.assertIn('No reminders', r.stdout)

    def test_pending_items_visible(self):
        self.write([make_item('a1', text='Buy milk', dueAt='2026-01-01T09:00:00Z')])
        r = self.run_cli('list')
        self.assertSuccess(r)
        self.assertIn('Buy milk', r.stdout)

    def test_done_items_hidden_by_default(self):
        self.write([
            make_item('a1', text='Pending', dueAt='2026-01-01T09:00:00Z'),
            make_item('a2', text='Finished', status='done'),
        ])
        r = self.run_cli('list')
        self.assertSuccess(r)
        self.assertIn('Pending', r.stdout)
        self.assertNotIn('Finished', r.stdout)
        self.assertIn('1 done item', r.stdout)

    def test_all_flag_shows_done(self):
        self.write([
            make_item('a1', text='Pending', dueAt='2026-01-01T09:00:00Z'),
            make_item('a2', text='Finished', status='done'),
        ])
        r = self.run_cli('list', '--all')
        self.assertSuccess(r)
        self.assertIn('Pending', r.stdout)
        self.assertIn('Finished', r.stdout)

    def test_undated_section_label(self):
        self.write([make_item('a1', text='Someday task', dueAt=None)])
        r = self.run_cli('list')
        self.assertSuccess(r)
        self.assertIn('no due date', r.stdout)

    def test_dated_before_undated(self):
        self.write([
            make_item('a1', text='undated item', dueAt=None),
            make_item('a2', text='dated item', dueAt='2026-01-01T09:00:00Z'),
        ])
        r = self.run_cli('list')
        self.assertLess(r.stdout.index('dated item'), r.stdout.index('undated item'))

    def test_sorted_by_due_date(self):
        self.write([
            make_item('a1', text='later task',   dueAt='2026-01-03T09:00:00Z'),
            make_item('a2', text='earlier task', dueAt='2026-01-01T09:00:00Z'),
        ])
        r = self.run_cli('list')
        self.assertLess(r.stdout.index('earlier task'), r.stdout.index('later task'))

    def test_wife_mode_label(self):
        self.write([make_item('a1', text='Urgent task', dueAt='2026-01-01T09:00:00Z', mode='wife')])
        r = self.run_cli('list')
        self.assertIn('[wife]', r.stdout)

    def test_nag_displayed(self):
        self.write([make_item('a1', text='Task', dueAt='2026-01-01T09:00:00Z', nagEveryMinutes=60)])
        r = self.run_cli('list')
        self.assertIn('60m', r.stdout)

    def test_indices_are_one_based(self):
        self.write([make_item('a1', text='Only item', dueAt='2026-01-01T09:00:00Z')])
        r = self.run_cli('list')
        self.assertIn('  1', r.stdout)
        self.assertNotIn('  0', r.stdout)


# ── add ───────────────────────────────────────────────────────────────────────

class TestAdd(MegTestCase):

    def setUp(self):
        super().setUp()
        self.write([])  # most add tests start with an empty store

    def test_add_basic(self):
        r = self.run_cli('add', 'Buy milk')
        self.assertSuccess(r)
        items = self.read()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['text'], 'Buy milk')
        self.assertEqual(items[0]['status'], 'pending')
        self.assertEqual(items[0]['source'], 'cli')

    def test_add_undated_by_default(self):
        self.run_cli('add', 'Task with no due date')
        self.assertIsNone(self.read()[0]['dueAt'])

    def test_add_with_iso_due(self):
        self.run_cli('add', 'Task', '--due', '2026-06-01T09:00:00Z')
        self.assertEqual(self.read()[0]['dueAt'], '2026-06-01T09:00:00Z')

    def test_add_with_relative_due_minutes(self):
        self.run_cli('add', 'Task', '--due', '+30m')
        due = self.read()[0]['dueAt']
        dt = datetime.fromisoformat(due.replace('Z', '+00:00'))
        diff_minutes = (dt - datetime.now(timezone.utc)).total_seconds() / 60
        self.assertAlmostEqual(diff_minutes, 30, delta=2)

    def test_add_with_relative_due_hours(self):
        self.run_cli('add', 'Task', '--due', '+2h')
        due = self.read()[0]['dueAt']
        dt = datetime.fromisoformat(due.replace('Z', '+00:00'))
        diff_hours = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
        self.assertAlmostEqual(diff_hours, 2, delta=0.05)

    def test_add_with_relative_due_days(self):
        self.run_cli('add', 'Task', '--due', '+1d')
        due = self.read()[0]['dueAt']
        dt = datetime.fromisoformat(due.replace('Z', '+00:00'))
        diff_days = (dt - datetime.now(timezone.utc)).total_seconds() / 86400
        self.assertAlmostEqual(diff_days, 1, delta=0.01)

    def test_add_with_nag(self):
        self.run_cli('add', 'Task', '--due', '+1h', '--nag', '30')
        self.assertEqual(self.read()[0]['nagEveryMinutes'], 30)

    def test_add_with_recurrence(self):
        self.run_cli('add', 'Task', '--due', '2026-04-14T09:00:00Z', '--recurrence', 'weekly')
        self.assertEqual(self.read()[0]['recurrence'], 'weekly')

    def test_add_with_daily_recurrence(self):
        self.run_cli('add', 'Task', '--due', '2026-04-14T09:00:00Z', '--recurrence', 'daily')
        self.assertEqual(self.read()[0]['recurrence'], 'daily')

    def test_add_wife_mode(self):
        self.run_cli('add', 'Fix the leak', '--mode', 'wife')
        self.assertEqual(self.read()[0]['mode'], 'wife')

    def test_add_with_notes(self):
        self.run_cli('add', 'Task', '--notes', 'some internal context')
        self.assertEqual(self.read()[0]['notes'], 'some internal context')

    def test_add_generates_unique_id(self):
        self.run_cli('add', 'First task')
        self.run_cli('add', 'Second task')
        ids = [i['id'] for i in self.read()]
        self.assertEqual(len(set(ids)), 2)

    def test_add_id_contains_slug(self):
        self.run_cli('add', 'Buy Milk Today')
        self.assertIn('buy-milk-today', self.read()[0]['id'])

    def test_add_invalid_due_exits_nonzero(self):
        r = self.run_cli('add', 'Task', '--due', 'not-a-date')
        self.assertFails(r)
        self.assertIn('cannot parse', r.stderr)
        self.assertEqual(self.read(), [])  # file unchanged

    def test_add_appends_to_existing(self):
        self.write([make_item('existing', text='Existing')])
        self.run_cli('add', 'New task')
        self.assertEqual(len(self.read()), 2)

    def test_add_prints_index(self):
        r = self.run_cli('add', 'New task')
        self.assertIn('#', r.stdout)
        self.assertIn('New task', r.stdout)


# ── done ─────────────────────────────────────────────────────────────────────

class TestDone(MegTestCase):

    def test_done_by_index(self):
        self.write([make_item('a1', text='Task', dueAt='2026-01-01T09:00:00Z')])
        r = self.run_cli('done', '1')
        self.assertSuccess(r)
        item = self.read()[0]
        self.assertEqual(item['status'], 'done')
        self.assertIsNotNone(item['completedAt'])

    def test_done_by_full_id(self):
        self.write([make_item('2026-01-01T09:00:00Z-buy-milk', text='Buy milk',
                              dueAt='2026-01-01T09:00:00Z')])
        r = self.run_cli('done', '2026-01-01T09:00:00Z-buy-milk')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['status'], 'done')

    def test_done_by_unique_prefix(self):
        self.write([make_item('2026-05-01T09:00:00Z-unique-task', text='Task',
                              dueAt='2026-05-01T09:00:00Z')])
        r = self.run_cli('done', '2026-05-01')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['status'], 'done')

    def test_done_sets_completed_at(self):
        self.write([make_item('a1', text='Task', dueAt='2026-01-01T09:00:00Z')])
        before = datetime.now(timezone.utc)
        self.run_cli('done', '1')
        after = datetime.now(timezone.utc)
        completed = datetime.fromisoformat(self.read()[0]['completedAt'].replace('Z', '+00:00'))
        self.assertGreaterEqual(completed, before.replace(microsecond=0))
        self.assertLessEqual(completed, after)

    def test_done_idempotent(self):
        self.write([make_item('a1', text='Task', status='done',
                              completedAt='2026-01-01T00:00:00Z')])
        r = self.run_cli('done', '1')
        self.assertSuccess(r)
        # completedAt should not be overwritten
        self.assertEqual(self.read()[0]['completedAt'], '2026-01-01T00:00:00Z')

    def test_done_index_out_of_range(self):
        self.write([])
        r = self.run_cli('done', '99')
        self.assertFails(r)
        self.assertIn('index', r.stderr)

    def test_done_unknown_ref(self):
        self.write([make_item('a1', text='Task')])
        r = self.run_cli('done', 'nonexistent-id')
        self.assertFails(r)
        self.assertIn('no reminder matches', r.stderr)

    def test_done_does_not_affect_other_items(self):
        self.write([
            make_item('a1', text='Task A', dueAt='2026-01-01T09:00:00Z'),
            make_item('a2', text='Task B', dueAt='2026-01-02T09:00:00Z'),
        ])
        self.run_cli('done', '1')
        items = self.read()
        self.assertEqual(items[0]['status'], 'done')
        self.assertEqual(items[1]['status'], 'pending')


# ── edit ─────────────────────────────────────────────────────────────────────

class TestEdit(MegTestCase):

    def setUp(self):
        super().setUp()
        self.write([make_item('a1', text='Original text', dueAt='2026-01-01T09:00:00Z',
                              nagEveryMinutes=30, recurrence='daily')])

    def test_edit_text(self):
        r = self.run_cli('edit', '1', '--text', 'Updated text')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['text'], 'Updated text')

    def test_edit_due_iso(self):
        r = self.run_cli('edit', '1', '--due', '2026-06-15T12:00:00Z')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['dueAt'], '2026-06-15T12:00:00Z')

    def test_edit_due_relative(self):
        r = self.run_cli('edit', '1', '--due', '+1d')
        self.assertSuccess(r)
        due = self.read()[0]['dueAt']
        dt = datetime.fromisoformat(due.replace('Z', '+00:00'))
        diff_days = (dt - datetime.now(timezone.utc)).total_seconds() / 86400
        self.assertAlmostEqual(diff_days, 1, delta=0.02)

    def test_edit_due_clear(self):
        r = self.run_cli('edit', '1', '--due', 'none')
        self.assertSuccess(r)
        self.assertIsNone(self.read()[0]['dueAt'])

    def test_edit_nag_set(self):
        r = self.run_cli('edit', '1', '--nag', '45')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['nagEveryMinutes'], 45)

    def test_edit_nag_clear(self):
        r = self.run_cli('edit', '1', '--nag', 'none')
        self.assertSuccess(r)
        self.assertIsNone(self.read()[0]['nagEveryMinutes'])

    def test_edit_recurrence_set(self):
        self.write([make_item('a1', text='Task')])
        r = self.run_cli('edit', '1', '--recurrence', 'weekly')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['recurrence'], 'weekly')

    def test_edit_recurrence_clear(self):
        r = self.run_cli('edit', '1', '--recurrence', 'none')
        self.assertSuccess(r)
        self.assertIsNone(self.read()[0]['recurrence'])

    def test_edit_mode(self):
        r = self.run_cli('edit', '1', '--mode', 'wife')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['mode'], 'wife')

    def test_edit_notes(self):
        r = self.run_cli('edit', '1', '--notes', 'some context')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['notes'], 'some context')

    def test_edit_status_to_done(self):
        r = self.run_cli('edit', '1', '--status', 'done')
        self.assertSuccess(r)
        item = self.read()[0]
        self.assertEqual(item['status'], 'done')
        self.assertIsNotNone(item['completedAt'])

    def test_edit_status_reopen(self):
        self.write([make_item('a1', text='Task', status='done',
                              completedAt='2026-01-01T00:00:00Z')])
        r = self.run_cli('edit', '1', '--status', 'pending')
        self.assertSuccess(r)
        item = self.read()[0]
        self.assertEqual(item['status'], 'pending')
        self.assertIsNone(item['completedAt'])

    def test_edit_nothing_changed_reports(self):
        r = self.run_cli('edit', '1')
        self.assertSuccess(r)
        self.assertIn('Nothing changed', r.stdout)

    def test_edit_multiple_fields_at_once(self):
        r = self.run_cli('edit', '1', '--text', 'New text', '--nag', '60', '--mode', 'wife')
        self.assertSuccess(r)
        item = self.read()[0]
        self.assertEqual(item['text'], 'New text')
        self.assertEqual(item['nagEveryMinutes'], 60)
        self.assertEqual(item['mode'], 'wife')

    def test_edit_invalid_nag(self):
        r = self.run_cli('edit', '1', '--nag', 'banana')
        self.assertFails(r)
        self.assertIn('--nag', r.stderr)

    def test_edit_does_not_affect_other_items(self):
        self.write([
            make_item('a1', text='Task A', dueAt='2026-01-01T09:00:00Z'),
            make_item('a2', text='Task B', dueAt='2026-01-02T09:00:00Z'),
        ])
        self.run_cli('edit', '1', '--text', 'Task A modified')
        items = self.read()
        self.assertEqual(items[0]['text'], 'Task A modified')
        self.assertEqual(items[1]['text'], 'Task B')


# ── remove ────────────────────────────────────────────────────────────────────

class TestRemove(MegTestCase):

    def test_remove_by_index(self):
        self.write([
            make_item('a1', text='Keep me',    dueAt='2026-01-02T09:00:00Z'),
            make_item('a2', text='Delete me',  dueAt='2026-01-01T09:00:00Z'),
        ])
        # a2 has the earlier due date so it is #1 in display order
        r = self.run_cli('remove', '1')
        self.assertSuccess(r)
        items = self.read()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['text'], 'Keep me')

    def test_remove_last_item(self):
        self.write([make_item('a1', text='Only item')])
        r = self.run_cli('remove', '1')
        self.assertSuccess(r)
        self.assertEqual(self.read(), [])

    def test_remove_by_full_id(self):
        self.write([make_item('2026-01-01T09:00:00Z-task', text='Task',
                              dueAt='2026-01-01T09:00:00Z')])
        r = self.run_cli('remove', '2026-01-01T09:00:00Z-task')
        self.assertSuccess(r)
        self.assertEqual(self.read(), [])

    def test_remove_unknown_ref(self):
        self.write([make_item('a1', text='Task')])
        r = self.run_cli('remove', 'does-not-exist')
        self.assertFails(r)
        self.assertEqual(len(self.read()), 1)  # unchanged


# ── ID resolution ─────────────────────────────────────────────────────────────

class TestIDResolution(MegTestCase):

    def test_ambiguous_prefix_errors(self):
        self.write([
            make_item('2026-01-01T09:00:00Z-first',  text='First',  dueAt='2026-01-01T09:00:00Z'),
            make_item('2026-01-01T10:00:00Z-second', text='Second', dueAt='2026-01-01T10:00:00Z'),
        ])
        r = self.run_cli('done', '2026-01-01')
        self.assertFails(r)
        self.assertIn('ambiguous', r.stderr)

    def test_unique_prefix_resolves(self):
        self.write([make_item('2026-05-15T09:00:00Z-unique', text='Task',
                              dueAt='2026-05-15T09:00:00Z')])
        r = self.run_cli('done', '2026-05-15')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['status'], 'done')

    def test_index_zero_errors(self):
        self.write([make_item('a1', text='Task')])
        r = self.run_cli('done', '0')
        self.assertFails(r)

    def test_index_out_of_range_errors(self):
        self.write([make_item('a1', text='Task')])
        r = self.run_cli('done', '999')
        self.assertFails(r)

    def test_nonexistent_string_errors(self):
        self.write([make_item('a1', text='Task')])
        r = self.run_cli('done', 'no-match-anywhere')
        self.assertFails(r)
        self.assertIn('no reminder matches', r.stderr)

    def test_done_items_reachable_by_index(self):
        # Done items are included in the index even when --all is not passed to list,
        # so they can always be referenced by index for edit/remove.
        self.write([make_item('a1', text='Done task', status='done',
                              completedAt='2026-01-01T00:00:00Z')])
        r = self.run_cli('remove', '1')
        self.assertSuccess(r)
        self.assertEqual(self.read(), [])


# ── general ───────────────────────────────────────────────────────────────────

class TestGeneral(MegTestCase):

    def test_no_command_exits_nonzero(self):
        self.write([])
        r = cli(data_file=self.data)
        self.assertNotEqual(r.returncode, 0)

    def test_help_flag(self):
        r = cli('--help', data_file=self.data)
        self.assertEqual(r.returncode, 0)
        self.assertIn('COMMAND', r.stdout)

    def test_subcommand_help(self):
        for cmd in ('list', 'add', 'done', 'edit', 'remove'):
            with self.subTest(cmd=cmd):
                r = cli(cmd, '--help', data_file=self.data)
                self.assertEqual(r.returncode, 0)

    def test_atomic_write_leaves_no_tmp(self):
        self.write([])
        self.run_cli('add', 'Task')
        tmp = self.data.with_suffix('.tmp')
        self.assertFalse(tmp.exists(), '.tmp file should not persist after write')

    def test_json_is_valid_after_each_command(self):
        self.write([])
        cmds = [
            ('add', 'Task one', '--due', '+1h', '--nag', '15'),
            ('add', 'Task two'),
            ('done', '1'),
            ('edit', '2', '--text', 'Task two edited'),
            ('remove', '1'),
        ]
        for cmd in cmds:
            self.run_cli(*cmd)
            try:
                json.loads(self.data.read_text())
            except json.JSONDecodeError as e:
                self.fail(f'Invalid JSON after {cmd}: {e}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
