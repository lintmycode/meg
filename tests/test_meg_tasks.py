#!/usr/bin/env python3
"""
Tests for meg-tasks.py CLI and meg-tasks-dispatch.py logic.

CLI tests run meg-tasks.py as a subprocess with MEG_TASKS_DATA pointing to a
temp file — the live tasks.json is never touched.

Dispatch logic tests import meg-tasks-dispatch.py directly (with env vars
mocked) to unit-test find_due, format_message, and run_command in isolation,
without needing the openclaw binary.

Run with:
    python3 -m pytest tests/test_meg_tasks.py -v
    python3 tests/test_meg_tasks.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE       = Path(__file__).parent.parent
MEG_TASKS  = str(HERE / 'src' / 'meg-tasks.py')
DISPATCH   = HERE / 'src' / 'meg-tasks-dispatch.py'


# ── helpers ───────────────────────────────────────────────────────────────────

def cli(*args, data_file):
    env = os.environ.copy()
    env['MEG_TASKS_DATA'] = str(data_file)
    return subprocess.run(
        [sys.executable, MEG_TASKS, *args],
        capture_output=True, text=True, env=env,
    )


def make_task(id_, name='Test task', status='active', command='echo hello',
              dueAt=None, intervalMinutes=None, recurrence=None,
              timeout=60, shell='/bin/bash', lastRunAt=None,
              lastExitCode=None, completedAt=None, notes=''):
    return {
        'id':              id_,
        'name':            name,
        'command':         command,
        'shell':           shell,
        'timeout':         timeout,
        'createdAt':       '2026-01-01T00:00:00Z',
        'dueAt':           dueAt,
        'status':          status,
        'recurrence':      recurrence,
        'intervalMinutes': intervalMinutes,
        'lastRunAt':       lastRunAt,
        'lastExitCode':    lastExitCode,
        'completedAt':     completedAt,
        'notes':           notes,
    }


def past(minutes=60):
    """Return an ISO timestamp N minutes in the past."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def future(minutes=60):
    """Return an ISO timestamp N minutes in the future."""
    dt = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def load_dispatch():
    """
    Import meg-tasks-dispatch.py with env vars stubbed so the module-level
    os.environ[] access doesn't raise KeyError.
    """
    os.environ.setdefault('REMINDER_CHANNEL', '_test_')
    os.environ.setdefault('REMINDER_TARGET',  '_test_')
    spec = importlib.util.spec_from_file_location('meg_tasks_dispatch', DISPATCH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── base test case ─────────────────────────────────────────────────────────────

class MegTasksTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data = Path(self._tmpdir.name) / 'tasks.json'

    def tearDown(self):
        self._tmpdir.cleanup()

    def write(self, tasks):
        self.data.write_text(json.dumps(tasks, indent=2))

    def read(self):
        return json.loads(self.data.read_text())

    def run_cli(self, *args):
        return cli(*args, data_file=self.data)

    def assertSuccess(self, r):
        self.assertEqual(r.returncode, 0, msg=f'stderr: {r.stderr}')

    def assertFails(self, r):
        self.assertNotEqual(r.returncode, 0, msg=f'stdout: {r.stdout}')


# ── list ─────────────────────────────────────────────────────────────────────

class TestList(MegTasksTestCase):

    def test_missing_file_exits_nonzero(self):
        r = self.run_cli('list')
        self.assertFails(r)
        self.assertIn('not found', r.stderr)

    def test_empty_store(self):
        self.write([])
        r = self.run_cli('list')
        self.assertSuccess(r)
        self.assertIn('No tasks', r.stdout)

    def test_active_tasks_visible(self):
        self.write([make_task('a1', name='Disk report', dueAt=past())])
        r = self.run_cli('list')
        self.assertSuccess(r)
        self.assertIn('Disk report', r.stdout)

    def test_paused_hidden_by_default(self):
        self.write([
            make_task('a1', name='Active task', dueAt=past()),
            make_task('a2', name='Paused task', status='paused'),
        ])
        r = self.run_cli('list')
        self.assertIn('Active task', r.stdout)
        self.assertNotIn('Paused task', r.stdout)
        self.assertIn('paused/done', r.stdout)

    def test_done_hidden_by_default(self):
        self.write([
            make_task('a1', name='Active',  dueAt=past()),
            make_task('a2', name='Archived', status='done'),
        ])
        r = self.run_cli('list')
        self.assertNotIn('Archived', r.stdout)

    def test_all_flag_shows_paused_and_done(self):
        self.write([
            make_task('a1', name='Active',  dueAt=past()),
            make_task('a2', name='Paused',  status='paused'),
            make_task('a3', name='Archived', status='done'),
        ])
        r = self.run_cli('list', '--all')
        self.assertSuccess(r)
        self.assertIn('Paused',   r.stdout)
        self.assertIn('Archived', r.stdout)

    def test_undated_section_label(self):
        self.write([make_task('a1', name='Poller', dueAt=None, intervalMinutes=15)])
        r = self.run_cli('list')
        self.assertIn('no due date', r.stdout)

    def test_dated_before_undated(self):
        self.write([
            make_task('a1', name='Undated task', dueAt=None),
            make_task('a2', name='Dated task',   dueAt=future()),
        ])
        r = self.run_cli('list')
        self.assertLess(r.stdout.index('Dated task'), r.stdout.index('Undated task'))

    def test_sorted_by_due_date(self):
        self.write([
            make_task('a1', name='Later task',   dueAt=future(120)),
            make_task('a2', name='Earlier task', dueAt=future(30)),
        ])
        r = self.run_cli('list')
        self.assertLess(r.stdout.index('Earlier task'), r.stdout.index('Later task'))

    def test_paused_label_in_all(self):
        self.write([make_task('a1', name='On hold', status='paused')])
        r = self.run_cli('list', '--all')
        self.assertIn('[paused]', r.stdout)

    def test_recurrence_displayed(self):
        self.write([make_task('a1', name='Daily job', dueAt=future(), recurrence='daily')])
        r = self.run_cli('list')
        self.assertIn('daily', r.stdout)

    def test_interval_displayed(self):
        self.write([make_task('a1', name='Poller', intervalMinutes=15)])
        r = self.run_cli('list')
        self.assertIn('15m', r.stdout)

    def test_indices_are_one_based(self):
        self.write([make_task('a1', name='Only task', dueAt=future())])
        r = self.run_cli('list')
        self.assertIn('  1', r.stdout)
        self.assertNotIn('  0', r.stdout)


# ── add ───────────────────────────────────────────────────────────────────────

class TestAdd(MegTasksTestCase):

    def setUp(self):
        super().setUp()
        self.write([])

    def test_add_basic(self):
        r = self.run_cli('add', 'Health check', '--command', 'curl -sf https://example.com')
        self.assertSuccess(r)
        tasks = self.read()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['name'],    'Health check')
        self.assertEqual(tasks[0]['command'], 'curl -sf https://example.com')
        self.assertEqual(tasks[0]['status'],  'active')
        self.assertEqual(tasks[0]['source'] if 'source' in tasks[0] else 'cli', 'cli')

    def test_add_requires_command(self):
        r = self.run_cli('add', 'Task without command')
        self.assertFails(r)

    def test_add_with_iso_due(self):
        self.run_cli('add', 'Task', '--command', 'echo hi', '--due', '2026-06-01T09:00:00Z')
        self.assertEqual(self.read()[0]['dueAt'], '2026-06-01T09:00:00Z')

    def test_add_with_relative_due(self):
        self.run_cli('add', 'Task', '--command', 'echo hi', '--due', '+1h')
        due = self.read()[0]['dueAt']
        dt  = datetime.fromisoformat(due.replace('Z', '+00:00'))
        diff = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
        self.assertAlmostEqual(diff, 1, delta=0.05)

    def test_add_undated_by_default(self):
        self.run_cli('add', 'Task', '--command', 'echo hi')
        self.assertIsNone(self.read()[0]['dueAt'])

    def test_add_with_interval(self):
        self.run_cli('add', 'Poller', '--command', 'echo hi', '--interval', '15')
        self.assertEqual(self.read()[0]['intervalMinutes'], 15)

    def test_add_with_recurrence(self):
        self.run_cli('add', 'Daily', '--command', 'echo hi',
                     '--due', '2026-04-09T09:00:00Z', '--recurrence', 'daily')
        self.assertEqual(self.read()[0]['recurrence'], 'daily')

    def test_add_with_hourly_recurrence(self):
        self.run_cli('add', 'Hourly', '--command', 'echo hi',
                     '--due', '2026-04-09T09:00:00Z', '--recurrence', 'hourly')
        self.assertEqual(self.read()[0]['recurrence'], 'hourly')

    def test_add_with_timeout(self):
        self.run_cli('add', 'Task', '--command', 'echo hi', '--timeout', '120')
        self.assertEqual(self.read()[0]['timeout'], 120)

    def test_add_with_shell(self):
        self.run_cli('add', 'Task', '--command', 'echo hi', '--shell', '/bin/sh')
        self.assertEqual(self.read()[0]['shell'], '/bin/sh')

    def test_add_with_notes(self):
        self.run_cli('add', 'Task', '--command', 'echo hi', '--notes', 'context here')
        self.assertEqual(self.read()[0]['notes'], 'context here')

    def test_add_generates_unique_id(self):
        self.run_cli('add', 'First',  '--command', 'echo 1')
        self.run_cli('add', 'Second', '--command', 'echo 2')
        ids = [t['id'] for t in self.read()]
        self.assertEqual(len(set(ids)), 2)

    def test_add_id_contains_slug(self):
        self.run_cli('add', 'Disk Report', '--command', 'df -h')
        self.assertIn('disk-report', self.read()[0]['id'])

    def test_add_invalid_due(self):
        r = self.run_cli('add', 'Task', '--command', 'echo hi', '--due', 'not-a-date')
        self.assertFails(r)
        self.assertEqual(self.read(), [])

    def test_add_appends_to_existing(self):
        self.write([make_task('existing', name='Existing')])
        self.run_cli('add', 'New', '--command', 'echo hi')
        self.assertEqual(len(self.read()), 2)


# ── run ───────────────────────────────────────────────────────────────────────

class TestRun(MegTasksTestCase):

    def test_run_exits_with_command_exit_code(self):
        self.write([make_task('a1', name='OK task', command='exit 0')])
        r = self.run_cli('run', '1')
        self.assertEqual(r.returncode, 0)

    def test_run_propagates_nonzero_exit(self):
        self.write([make_task('a1', name='Failing task', command='exit 42')])
        r = self.run_cli('run', '1')
        self.assertEqual(r.returncode, 42)

    def test_run_prints_metadata_to_stderr(self):
        self.write([make_task('a1', name='My task', command='echo hi')])
        r = self.run_cli('run', '1')
        self.assertIn('My task', r.stderr)
        self.assertIn('echo hi', r.stderr)

    def test_run_does_not_modify_tasks_json(self):
        self.write([make_task('a1', name='Task', command='echo hi')])
        before = self.read()
        self.run_cli('run', '1')
        after = self.read()
        self.assertEqual(before, after)

    def test_run_by_prefix(self):
        self.write([make_task('2026-05-01T00:00:00Z-unique', name='Task', command='exit 0')])
        r = self.run_cli('run', '2026-05-01')
        self.assertEqual(r.returncode, 0)

    def test_run_unknown_id_errors(self):
        self.write([make_task('a1', name='Task')])
        r = self.run_cli('run', 'no-match')
        self.assertFails(r)


# ── pause / resume ────────────────────────────────────────────────────────────

class TestPauseResume(MegTasksTestCase):

    def test_pause_active_task(self):
        self.write([make_task('a1', name='Task', status='active')])
        r = self.run_cli('pause', '1')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['status'], 'paused')

    def test_pause_idempotent(self):
        self.write([make_task('a1', name='Task', status='paused')])
        r = self.run_cli('pause', '1')
        self.assertSuccess(r)
        self.assertIn('Already paused', r.stdout)
        self.assertEqual(self.read()[0]['status'], 'paused')

    def test_resume_paused_task(self):
        self.write([make_task('a1', name='Task', status='paused')])
        r = self.run_cli('resume', '1')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['status'], 'active')

    def test_resume_idempotent(self):
        self.write([make_task('a1', name='Task', status='active')])
        r = self.run_cli('resume', '1')
        self.assertSuccess(r)
        self.assertIn('Already active', r.stdout)

    def test_pause_does_not_affect_others(self):
        self.write([
            make_task('a1', name='Task A', dueAt=future()),
            make_task('a2', name='Task B', dueAt=future(120)),
        ])
        self.run_cli('pause', '1')
        tasks = self.read()
        self.assertEqual(tasks[0]['status'], 'paused')
        self.assertEqual(tasks[1]['status'], 'active')


# ── done ─────────────────────────────────────────────────────────────────────

class TestDone(MegTasksTestCase):

    def test_done_marks_archived(self):
        self.write([make_task('a1', name='Task')])
        r = self.run_cli('done', '1')
        self.assertSuccess(r)
        item = self.read()[0]
        self.assertEqual(item['status'], 'done')
        self.assertIsNotNone(item['completedAt'])

    def test_done_idempotent(self):
        self.write([make_task('a1', name='Task', status='done',
                              completedAt='2026-01-01T00:00:00Z')])
        r = self.run_cli('done', '1')
        self.assertSuccess(r)
        self.assertEqual(self.read()[0]['completedAt'], '2026-01-01T00:00:00Z')


# ── edit ─────────────────────────────────────────────────────────────────────

class TestEdit(MegTasksTestCase):

    def setUp(self):
        super().setUp()
        self.write([make_task('a1', name='Original', command='echo old',
                              dueAt=future(), recurrence='daily', intervalMinutes=30)])

    def test_edit_name(self):
        self.run_cli('edit', '1', '--name', 'Updated')
        self.assertEqual(self.read()[0]['name'], 'Updated')

    def test_edit_command(self):
        self.run_cli('edit', '1', '--command', 'df -h')
        self.assertEqual(self.read()[0]['command'], 'df -h')

    def test_edit_shell(self):
        self.run_cli('edit', '1', '--shell', '/bin/sh')
        self.assertEqual(self.read()[0]['shell'], '/bin/sh')

    def test_edit_timeout(self):
        self.run_cli('edit', '1', '--timeout', '120')
        self.assertEqual(self.read()[0]['timeout'], 120)

    def test_edit_due_iso(self):
        self.run_cli('edit', '1', '--due', '2026-12-01T10:00:00Z')
        self.assertEqual(self.read()[0]['dueAt'], '2026-12-01T10:00:00Z')

    def test_edit_due_clear(self):
        self.run_cli('edit', '1', '--due', 'none')
        self.assertIsNone(self.read()[0]['dueAt'])

    def test_edit_interval_set(self):
        self.run_cli('edit', '1', '--interval', '60')
        self.assertEqual(self.read()[0]['intervalMinutes'], 60)

    def test_edit_interval_clear(self):
        self.run_cli('edit', '1', '--interval', 'none')
        self.assertIsNone(self.read()[0]['intervalMinutes'])

    def test_edit_recurrence_set(self):
        self.run_cli('edit', '1', '--recurrence', 'weekly')
        self.assertEqual(self.read()[0]['recurrence'], 'weekly')

    def test_edit_recurrence_clear(self):
        self.run_cli('edit', '1', '--recurrence', 'none')
        self.assertIsNone(self.read()[0]['recurrence'])

    def test_edit_status_pause(self):
        self.run_cli('edit', '1', '--status', 'paused')
        self.assertEqual(self.read()[0]['status'], 'paused')

    def test_edit_status_done_sets_completed_at(self):
        self.run_cli('edit', '1', '--status', 'done')
        item = self.read()[0]
        self.assertEqual(item['status'], 'done')
        self.assertIsNotNone(item['completedAt'])

    def test_edit_status_reactivate_clears_completed_at(self):
        self.write([make_task('a1', name='Task', status='done',
                              completedAt='2026-01-01T00:00:00Z')])
        self.run_cli('edit', '1', '--status', 'active')
        item = self.read()[0]
        self.assertEqual(item['status'], 'active')
        self.assertIsNone(item['completedAt'])

    def test_edit_notes(self):
        self.run_cli('edit', '1', '--notes', 'updated notes')
        self.assertEqual(self.read()[0]['notes'], 'updated notes')

    def test_edit_nothing_changed(self):
        r = self.run_cli('edit', '1')
        self.assertSuccess(r)
        self.assertIn('Nothing changed', r.stdout)

    def test_edit_multiple_fields(self):
        self.run_cli('edit', '1', '--name', 'New', '--command', 'ls -la', '--timeout', '45')
        item = self.read()[0]
        self.assertEqual(item['name'],    'New')
        self.assertEqual(item['command'], 'ls -la')
        self.assertEqual(item['timeout'], 45)

    def test_edit_invalid_interval(self):
        r = self.run_cli('edit', '1', '--interval', 'banana')
        self.assertFails(r)

    def test_edit_does_not_affect_others(self):
        self.write([
            make_task('a1', name='Task A', dueAt=future()),
            make_task('a2', name='Task B', dueAt=future(120)),
        ])
        self.run_cli('edit', '1', '--name', 'Task A modified')
        tasks = self.read()
        self.assertEqual(tasks[0]['name'], 'Task A modified')
        self.assertEqual(tasks[1]['name'], 'Task B')


# ── remove ────────────────────────────────────────────────────────────────────

class TestRemove(MegTasksTestCase):

    def test_remove_by_index(self):
        self.write([
            make_task('a1', name='Keep',   dueAt=future(120)),
            make_task('a2', name='Delete', dueAt=future()),
        ])
        # a2 has earlier due date → index 1
        self.run_cli('remove', '1')
        tasks = self.read()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['name'], 'Keep')

    def test_remove_last_item(self):
        self.write([make_task('a1', name='Only task')])
        r = self.run_cli('remove', '1')
        self.assertSuccess(r)
        self.assertEqual(self.read(), [])

    def test_remove_by_full_id(self):
        self.write([make_task('2026-01-01T09:00:00Z-task', name='Task', dueAt=future())])
        r = self.run_cli('remove', '2026-01-01T09:00:00Z-task')
        self.assertSuccess(r)
        self.assertEqual(self.read(), [])

    def test_remove_unknown_errors(self):
        self.write([make_task('a1', name='Task')])
        r = self.run_cli('remove', 'no-match')
        self.assertFails(r)
        self.assertEqual(len(self.read()), 1)


# ── ID resolution ─────────────────────────────────────────────────────────────

class TestIDResolution(MegTasksTestCase):

    def test_ambiguous_prefix_errors(self):
        self.write([
            make_task('2026-01-01T09:00:00Z-first',  name='First',  dueAt=future()),
            make_task('2026-01-01T10:00:00Z-second', name='Second', dueAt=future(120)),
        ])
        r = self.run_cli('done', '2026-01-01')
        self.assertFails(r)
        self.assertIn('ambiguous', r.stderr)

    def test_unique_prefix_resolves(self):
        self.write([make_task('2026-07-04T09:00:00Z-fireworks', name='Fireworks', dueAt=future())])
        r = self.run_cli('done', '2026-07-04')
        self.assertSuccess(r)

    def test_index_zero_errors(self):
        self.write([make_task('a1', name='Task')])
        r = self.run_cli('done', '0')
        self.assertFails(r)

    def test_index_out_of_range_errors(self):
        self.write([make_task('a1', name='Task')])
        r = self.run_cli('done', '999')
        self.assertFails(r)

    def test_paused_and_done_reachable_by_index(self):
        self.write([make_task('a1', name='Paused task', status='paused')])
        r = self.run_cli('resume', '1')
        self.assertSuccess(r)

    def test_nonexistent_string_errors(self):
        self.write([make_task('a1', name='Task')])
        r = self.run_cli('done', 'no-match-anywhere')
        self.assertFails(r)
        self.assertIn('no task matches', r.stderr)


# ── general CLI ───────────────────────────────────────────────────────────────

class TestGeneralCLI(MegTasksTestCase):

    def test_no_command_exits_nonzero(self):
        self.write([])
        r = cli(data_file=self.data)
        self.assertNotEqual(r.returncode, 0)

    def test_help_flag(self):
        r = cli('--help', data_file=self.data)
        self.assertEqual(r.returncode, 0)
        self.assertIn('COMMAND', r.stdout)

    def test_subcommand_help(self):
        for cmd in ('list', 'add', 'run', 'pause', 'resume', 'done', 'edit', 'remove'):
            with self.subTest(cmd=cmd):
                r = cli(cmd, '--help', data_file=self.data)
                self.assertEqual(r.returncode, 0)

    def test_atomic_write_leaves_no_tmp(self):
        self.write([])
        self.run_cli('add', 'Task', '--command', 'echo hi')
        self.assertFalse(self.data.with_suffix('.tmp').exists())

    def test_json_valid_after_each_mutation(self):
        self.write([])
        mutations = [
            ('add', 'Task A', '--command', 'echo a', '--due', '+1h', '--recurrence', 'daily'),
            ('add', 'Task B', '--command', 'echo b', '--interval', '15'),
            ('pause', '1'),
            ('resume', '1'),
            ('edit', '2', '--command', 'echo updated', '--timeout', '30'),
            ('done', '1'),
            ('remove', '2'),
        ]
        for cmd in mutations:
            self.run_cli(*cmd)
            try:
                json.loads(self.data.read_text())
            except json.JSONDecodeError as e:
                self.fail(f'Invalid JSON after {cmd}: {e}')


# ── dispatch logic (unit tests, no subprocess, no openclaw) ───────────────────

class TestDispatchFindDue(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = load_dispatch()

    def now(self):
        return datetime.now(timezone.utc)

    def test_active_past_due_is_returned(self):
        tasks = [make_task('a1', status='active', dueAt=past())]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(len(due), 1)

    def test_active_future_due_is_skipped(self):
        tasks = [make_task('a1', status='active', dueAt=future())]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(due, [])

    def test_paused_task_is_skipped(self):
        tasks = [make_task('a1', status='paused', dueAt=past())]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(due, [])

    def test_done_task_is_skipped(self):
        tasks = [make_task('a1', status='done', dueAt=past())]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(due, [])

    def test_no_due_at_fires_immediately(self):
        # Tasks with no dueAt fire on every tick (intervalMinutes governs throttle)
        tasks = [make_task('a1', status='active', dueAt=None, intervalMinutes=60,
                           lastRunAt=past(120))]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(len(due), 1)

    def test_interval_not_elapsed_is_skipped(self):
        tasks = [make_task('a1', status='active', dueAt=past(),
                           intervalMinutes=60, lastRunAt=past(30))]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(due, [])

    def test_interval_elapsed_fires(self):
        tasks = [make_task('a1', status='active', dueAt=past(),
                           intervalMinutes=15, lastRunAt=past(20))]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(len(due), 1)

    def test_no_last_run_at_with_interval_fires(self):
        # First run: no lastRunAt means it's always due
        tasks = [make_task('a1', status='active', dueAt=past(), intervalMinutes=15)]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(len(due), 1)

    def test_multiple_due_tasks_all_returned(self):
        tasks = [
            make_task('a1', status='active', dueAt=past()),
            make_task('a2', status='active', dueAt=past(30)),
            make_task('a3', status='paused', dueAt=past()),
        ]
        due = self.d.find_due(tasks, self.now())
        self.assertEqual(len(due), 2)


class TestDispatchFormatMessage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = load_dispatch()

    def task(self, name='My task'):
        return make_task('a1', name=name)

    def test_header_contains_task_name(self):
        msg = self.d.format_message(self.task('Disk report'), 'output here', '', 0)
        self.assertIn('[Disk report]', msg)

    def test_stdout_included(self):
        msg = self.d.format_message(self.task(), 'hello world', '', 0)
        self.assertIn('hello world', msg)

    def test_empty_output_placeholder(self):
        msg = self.d.format_message(self.task(), '', '', 0)
        self.assertIn('no output', msg)

    def test_nonzero_exit_shows_exit_code(self):
        msg = self.d.format_message(self.task(), '', 'something went wrong', 1)
        self.assertIn('Exit 1', msg)
        self.assertIn('something went wrong', msg)

    def test_nonzero_exit_no_stderr(self):
        msg = self.d.format_message(self.task(), 'partial output', '', 2)
        self.assertIn('Exit 2', msg)
        self.assertIn('partial output', msg)

    def test_timeout_exit_code(self):
        msg = self.d.format_message(self.task(), '', 'Command timed out after 60s', -1)
        self.assertIn('Exit -1', msg)
        self.assertIn('timed out', msg)

    def test_long_output_truncated(self):
        big_output = 'x' * 5000
        msg = self.d.format_message(self.task(), big_output, '', 0)
        self.assertLessEqual(len(msg), self.d.TELEGRAM_MAX + 20)
        self.assertIn('truncated', msg)

    def test_output_within_limit_not_truncated(self):
        short_output = 'line\n' * 10
        msg = self.d.format_message(self.task(), short_output, '', 0)
        self.assertNotIn('truncated', msg)


class TestDispatchRunCommand(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = load_dispatch()

    def test_successful_command(self):
        task = make_task('a1', command='echo hello')
        stdout, stderr, code = self.d.run_command(task)
        self.assertEqual(code, 0)
        self.assertIn('hello', stdout)

    def test_failing_command(self):
        task = make_task('a1', command='exit 3', shell='/bin/bash')
        stdout, stderr, code = self.d.run_command(task)
        self.assertEqual(code, 3)

    def test_stderr_captured(self):
        task = make_task('a1', command='echo error >&2', shell='/bin/bash')
        stdout, stderr, code = self.d.run_command(task)
        self.assertIn('error', stderr)

    def test_timeout_returns_minus_one(self):
        task = make_task('a1', command='sleep 60', timeout=1)
        stdout, stderr, code = self.d.run_command(task)
        self.assertEqual(code, -1)
        self.assertIn('timed out', stderr)

    def test_multiline_script(self):
        task = make_task('a1', command='echo line1\necho line2', shell='/bin/bash')
        stdout, stderr, code = self.d.run_command(task)
        self.assertEqual(code, 0)
        self.assertIn('line1', stdout)
        self.assertIn('line2', stdout)


class TestDispatchRecurrenceDelta(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = load_dispatch()

    def test_hourly(self):
        self.assertEqual(self.d.recurrence_delta('hourly'), timedelta(hours=1))

    def test_daily(self):
        self.assertEqual(self.d.recurrence_delta('daily'), timedelta(days=1))

    def test_weekly(self):
        self.assertEqual(self.d.recurrence_delta('weekly'), timedelta(weeks=1))

    def test_none_returns_none(self):
        self.assertIsNone(self.d.recurrence_delta(None))

    def test_unknown_returns_none(self):
        self.assertIsNone(self.d.recurrence_delta('monthly'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
