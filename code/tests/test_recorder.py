"""recorder.py run as Claude Code runs it, around Edit calls and shell commands in scratch
repositories: what each records, with what baseline, and what it leaves alone."""
import json, os, pathlib, subprocess, sys, time, unittest

import scratch
from scratch import git, hook, recorded, repo, session, shell, write

recorder = scratch.recorder
store = scratch.home / '.redpen/code'


class EditCalls(unittest.TestCase):
    def test_an_edit_keeps_the_text_before_it_once(self):
        top, who = repo({'a.py': 'one\n'}), session()
        for text in ('two\n', 'three\n'):
            hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')})
            write(top, {'a.py': text})
            hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')})
        self.assertEqual(recorded(who), {'a.py': (False, 'one\n', False)})

    def test_a_write_that_makes_a_file_records_it_as_new(self):
        top, who = repo({'a.py': 'a\n'}), session()
        hook('pre', who, top, 'Write', {'file_path': str(top / 'new.py')})
        write(top, {'new.py': 'n\n'})
        hook('post', who, top, 'Write', {'file_path': str(top / 'new.py')})
        self.assertEqual(recorded(who), {'new.py': (True, None, False)})

    def test_a_codex_patch_records_each_file_it_adds_updates_moves_and_deletes(self):
        top, who = repo({'a.py': 'one\n', 'old.py': 'o\n', 'gone.py': 'g\n'}), session()
        index = scratch.home / '.codex/session_index.jsonl'
        index.parent.mkdir(exist_ok=True)
        with open(index, 'a') as f:
            for name in ('First name', 'Renamed'):
                f.write(json.dumps({'id': who, 'thread_name': name}) + '\n')
        patch = ('*** Begin Patch\n*** Add File: new.py\n+n\n*** Update File: a.py\n@@\n-one\n+two\n'
                 '*** Update File: old.py\n*** Move to: moved.py\n@@\n o\n*** Delete File: gone.py\n*** End Patch')
        # As Codex sends it: the patch as the call's command, with a turn_id and no transcript.
        codex = {'turn_id': 'turn', 'transcript_path': None}
        hook('pre', who, top, 'apply_patch', {'command': patch}, **codex)
        write(top, {'new.py': 'n\n', 'a.py': 'two\n', 'old.py': None, 'moved.py': 'o\n', 'gone.py': None})
        hook('post', who, top, 'apply_patch', {'command': patch}, **codex)
        self.assertEqual(recorded(who), {'new.py': (True, None, False), 'a.py': (False, 'one\n', False),
                                         'old.py': (False, 'o\n', False), 'moved.py': (True, None, False),
                                         'gone.py': (False, 'g\n', False)})
        self.assertEqual(json.loads((store / who / 'session.json').read_text())['title'], 'Renamed')


class ShellCommands(unittest.TestCase):
    def test_a_command_that_only_reads_is_not_watched(self):
        top, who = repo({'a.py': 'a\n'}), session()
        hook('pre', who, top, 'Bash', {'command': 'cat a.py | wc -l && git log -1'})
        self.assertEqual(list((store / who / 'pending').glob('shell-*')), [])

    def test_a_script_and_sed_changing_clean_files_are_recorded_against_the_commit(self):
        top, who = repo({'a.py': 'a1\n', 'b.py': 'b1\n'}), session()
        shell(who, top, """python3 -c "open('a.py', 'a').write('a2\\n')" """, 'one')
        shell(who, top, "sed -i.bak 's/b1/B1/' b.py && rm b.py.bak", 'two')
        self.assertEqual(recorded(who), {'a.py': (False, 'a1\n', False), 'b.py': (False, 'b1\n', False)})

    def test_a_file_changed_before_the_command_is_marked(self):
        top, who = repo({'a.py': 'a1\n'}), session()
        write(top, {'a.py': 'a1\nsomeone else\n'})
        shell(who, top, "printf 'mine\\n' >> a.py")
        self.assertEqual(recorded(who), {'a.py': (False, 'a1\n', True)})

    def test_rm_and_mv(self):
        top, who = repo({'gone.py': 'g\n', 'old.py': 'o\n'}), session()
        shell(who, top, 'rm gone.py && mv old.py new.py')
        self.assertEqual(recorded(who), {'gone.py': (False, 'g\n', False), 'old.py': (False, 'o\n', False),
                                         'new.py': (True, None, False)})

    def test_another_sessions_edit_during_the_command_is_theirs(self):
        top, who, other = repo({'a.py': 'a\n', 'b.py': 'b\n'}), session(), session()

        def their_edit():
            hook('pre', other, top, 'Edit', {'file_path': str(top / 'b.py')})
            write(top, {'b.py': 'theirs\n'})
            hook('post', other, top, 'Edit', {'file_path': str(top / 'b.py')})

        shell(who, top, "printf 'mine\\n' >> a.py", between=their_edit)
        self.assertEqual(recorded(who), {'a.py': (False, 'a\n', False)})
        self.assertEqual(recorded(other), {'b.py': (False, 'b\n', False)})

    def test_a_change_made_while_another_sessions_command_runs_is_theirs(self):
        top, mine, theirs = repo({'a.py': 'a\n', 'b.py': 'b\n'}), session(), session()
        # My command runs; theirs starts while it does, and changes b.py (a formatter). Each names it by a
        # glob alone.
        hook('pre', mine, top, 'Bash', {'command': 'for f in *.py; do :; done'}, 'm1')
        time.sleep(0.02)
        hook('pre', theirs, top, 'Bash', {'command': 'npx prettier --write "*.py"'}, 't1')
        time.sleep(0.02)
        write(top, {'b.py': 'B\n'})
        # Mine ends first: b.py is not mine.
        hook('post', mine, top, 'Bash', {'command': 'for f in *.py; do :; done'}, 'm1')
        self.assertEqual(recorded(mine), {})
        hook('post', theirs, top, 'Bash', {'command': 'npx prettier --write "*.py"'}, 't1')
        # Theirs, and marked: their command does not name it by its name.
        self.assertEqual(recorded(theirs), {'b.py': (False, 'b\n', True)})

    def test_a_change_my_command_names_is_mine_while_another_sessions_command_runs(self):
        top, mine, theirs = repo({'a.py': 'a\n', 'b.py': 'b\n'}), session(), session()
        # Their long command runs (a test suite, say); mine, a script on its input, writes b.py meanwhile.
        hook('pre', theirs, top, 'Bash', {'command': 'python3 -c pass'}, 't1')
        time.sleep(0.02)
        shell(mine, top, "python3 - <<'EOF'\nopen('b.py', 'w').write('B\\n')\nEOF\n", 'm1')
        self.assertEqual(recorded(mine), {'b.py': (False, 'b\n', False)})
        # Theirs ends after: b.py is mine, as my record left it.
        hook('post', theirs, top, 'Bash', {'command': 'python3 -c pass'}, 't1')
        self.assertEqual(recorded(theirs), {})

    def test_a_change_my_command_names_is_mine_though_another_sessions_began_after(self):
        top, mine, theirs = repo({'b.py': 'b\n'}), session(), session()
        command = "python3 - <<'EOF'\nopen('b.py', 'w').write('B\\n')\nEOF\n"
        hook('pre', mine, top, 'Bash', {'command': command}, 'm1')
        time.sleep(0.02)
        hook('pre', theirs, top, 'Bash', {'command': 'python3 -c pass'}, 't1')
        time.sleep(0.02)
        write(top, {'b.py': 'B\n'})
        hook('post', mine, top, 'Bash', {'command': command}, 'm1')
        self.assertEqual(recorded(mine), {'b.py': (False, 'b\n', False)})

    def test_a_change_my_later_command_makes_while_another_sessions_waits_is_mine(self):
        top, mine, theirs = repo({'b.py': 'b\n'}), session(), session()
        # Their push waits on the network; my reformat, from a list, names no file and starts after it.
        hook('pre', theirs, top, 'Bash', {'command': 'git push origin develop'}, 't1')
        time.sleep(0.02)
        shell(mine, top, 'for f in *.py; do echo B > "$f"; done', 'm1')
        self.assertEqual(recorded(mine), {'b.py': (False, 'b\n', True)})
        hook('post', theirs, top, 'Bash', {'command': 'git push origin develop'}, 't1')
        self.assertEqual(recorded(theirs), {})

    def test_a_file_made_in_a_folder_my_test_run_names_is_the_command_naming_it(self):
        top, mine, theirs = repo({'tests/t.py': 't\n'}), session(), session()
        # My test run names its folder; their command, begun after it, writes a file there by its name.
        hook('pre', mine, top, 'Bash', {'command': 'python3 -m unittest discover tests'}, 'm1')
        time.sleep(0.02)
        hook('pre', theirs, top, 'Bash', {'command': 'F=tests/scratch.tmp; cat > $F'}, 't1')
        time.sleep(0.02)
        write(top, {'tests/scratch.tmp': 'x\n'})
        hook('post', mine, top, 'Bash', {'command': 'python3 -m unittest discover tests'}, 'm1')
        self.assertEqual(recorded(mine), {})
        hook('post', theirs, top, 'Bash', {'command': 'F=tests/scratch.tmp; cat > $F'}, 't1')
        self.assertEqual(recorded(theirs), {'scratch.tmp': (True, None, False)})

    def test_a_file_no_command_names_is_nobodys(self):
        top, mine = repo({'a.py': 'a\n'}), session()
        # A background job writes while my command runs (a push, a test run): mine does not name it.
        shell(mine, top, 'python3 -c pass && sleep 0',
              between=lambda: write(top, {'a.py': 'job\n', 'results.json': '{}\n'}))
        self.assertEqual(recorded(mine), {})

    def test_an_rm_of_a_folder_names_the_files_in_it(self):
        top, mine = repo({'build/a.js': 'a\n', 'build/deep/b.js': 'b\n', 'keep.js': 'k\n'}), session()
        shell(mine, top, 'rm -r build')
        self.assertEqual(recorded(mine), {'a.js': (False, 'a\n', False), 'b.js': (False, 'b\n', False)})

    def test_a_change_another_sessions_command_names_is_theirs_whenever_it_began(self):
        top, mine, theirs = repo({'b.py': 'b\n'}), session(), session()
        hook('pre', theirs, top, 'Bash', {'command': "sed -i.bak 's/b/B/' b.py"}, 't1')
        time.sleep(0.02)
        hook('pre', mine, top, 'Bash', {'command': 'python3 -c pass'}, 'm1')
        time.sleep(0.02)
        write(top, {'b.py': 'B\n'})
        hook('post', mine, top, 'Bash', {'command': 'python3 -c pass'}, 'm1')
        self.assertEqual(recorded(mine), {})
        hook('post', theirs, top, 'Bash', {'command': "sed -i.bak 's/b/B/' b.py"}, 't1')
        self.assertEqual(recorded(theirs), {'b.py': (False, 'b\n', False)})

    def test_a_command_cut_short_in_another_session_runs_no_more(self):
        top, mine, theirs = repo({'a.py': 'a\n'}), session(), session()
        # Their command, which names the file, was cut short an hour and more ago: its post hook never came.
        hook('pre', theirs, top, 'Bash', {'command': 'npx prettier --write a.py'}, 't1')
        snapshot = next((store / theirs / 'pending').glob('shell-*.json'))
        kept = json.loads(snapshot.read_text())
        kept['time'] -= recorder.stale_hours * 3600 + 5
        snapshot.write_text(json.dumps(kept))
        hook('pre', mine, top, 'Bash', {'command': 'for f in *.py; do :; done'}, 'm1')
        write(top, {'a.py': 'mine\n'})
        hook('post', mine, top, 'Bash', {'command': 'for f in *.py; do :; done'}, 'm1')
        self.assertEqual(recorded(mine), {'a.py': (False, 'a\n', True)})
        self.assertFalse(snapshot.exists())

    def test_an_edit_once_all_was_accepted_and_someone_changed_the_file_since_diffs_alone(self):
        top, mine, theirs = repo({'a.py': 'a\n'}), session(), session()
        base = store / mine / 'base' / recorder.key(str(top / 'a.py'))

        def edit(who, text):
            hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')})
            write(top, {'a.py': text})
            hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')})

        edit(mine, 'mine\n')
        # The page accepted all of it: the baseline is the text my edit left.
        base.write_text('mine\n')
        edit(theirs, 'theirs\n')
        edit(mine, 'mine again\n')
        self.assertEqual(recorded(mine), {'a.py': (False, 'theirs\n', False)})
        # The page took their change as the baseline already (review.py's take_others_changes()).
        edit(theirs, 'theirs again\n')
        base.write_text('theirs again\n')
        edit(mine, 'mine at last\n')
        self.assertEqual(recorded(mine), {'a.py': (False, 'theirs again\n', False)})

    def test_an_edit_once_the_page_took_others_changes_diffs_alone_however_often_they_changed(self):
        top, mine, theirs = repo({'a.py': 'a\n'}), session(), session()
        k = recorder.key(str(top / 'a.py'))

        def edit(who, text):
            hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')})
            write(top, {'a.py': text})
            hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')})

        edit(mine, 'mine\n')
        # The page took their change, at the text my edit left; they changed it again since.
        edit(theirs, 'theirs\n')
        (store / mine / 'base' / k).write_text('theirs\n')
        (store / mine / 'review' / 'taken').mkdir(parents=True, exist_ok=True)
        (store / mine / 'review' / 'taken' / k).write_text(recorder.sha(b'mine\n'))
        edit(theirs, 'theirs again\n')
        edit(mine, 'mine again\n')
        self.assertEqual(recorded(mine), {'a.py': (False, 'theirs again\n', False)})

    def test_an_edit_once_mine_was_committed_and_someone_changed_the_file_since_diffs_alone(self):
        top, mine, theirs = repo({'a.py': 'a\n'}), session(), session()

        def edit(who, text):
            hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')})
            write(top, {'a.py': text})
            hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')})

        edit(mine, 'mine\n')
        git(top, 'commit', '-qam', 'mine')
        edit(theirs, 'theirs\n')
        edit(mine, 'mine again\n')
        self.assertEqual(recorded(mine), {'a.py': (False, 'theirs\n', False)})

    def test_a_change_made_before_another_sessions_command_began_stays_mine(self):
        top, mine, theirs = repo({'a.py': 'a\n'}), session(), session()
        hook('pre', mine, top, 'Bash', {'command': 'for f in *.py; do :; done'}, 'm1')
        write(top, {'a.py': 'mine\n'})
        time.sleep(0.02)
        hook('pre', theirs, top, 'Bash', {'command': 'npx prettier --write "*.py"'}, 't1')
        hook('post', mine, top, 'Bash', {'command': 'for f in *.py; do :; done'}, 'm1')
        self.assertEqual(recorded(mine), {'a.py': (False, 'a\n', True)})

    def test_a_file_the_command_does_not_name_is_not_recorded(self):
        top, who = repo({'a.py': 'a\n', 'b.py': 'b\n'}), session()
        # The command writes a.py, which it names; b.py changes meanwhile (an editor's save, say).
        shell(who, top, "printf 'mine\\n' >> a.py", between=lambda: write(top, {'b.py': 'yours\n'}))
        self.assertEqual(recorded(who), {'a.py': (False, 'a\n', False)})

    def test_a_subagents_edits_are_the_sessions_by_its_type(self):
        top, who = repo({'a.py': 'a\n', 'b.py': 'b\n'}), session()
        hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')}, agent_id='x1', agent_type='Explore')
        write(top, {'a.py': 'A\n'})
        hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')}, agent_id='x1', agent_type='Explore')
        hook('pre', who, top, 'Edit', {'file_path': str(top / 'b.py')})
        write(top, {'b.py': 'B\n'})
        hook('post', who, top, 'Edit', {'file_path': str(top / 'b.py')})
        agents = {pathlib.Path(json.loads(f.read_text())['path']).name: json.loads(f.read_text()).get('agents')
                  for f in (store / who / 'files').glob('*.json')}
        self.assertEqual(agents, {'a.py': ['Explore'], 'b.py': None})

    def test_a_command_that_edits_and_commits_a_file_keeps_it_the_sessions(self):
        top, who = repo({'a.py': 'a\n'}), session()
        hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')})
        write(top, {'a.py': 'b\n'})
        hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')})
        shell(who, top, "printf 'c\\n' >> a.py && git commit -qam c")
        # git's status says nothing of a.py now; its mark is still the file as the session left it.
        mark = (store / who / 'after' / recorder.key(str(top / 'a.py'))).read_text()
        self.assertEqual(mark, recorder.sha(b'b\nc\n'))

    def test_what_a_long_command_changed_is_marked(self):
        top, who = repo({'a.py': 'a\n'}), session()

        def long_ago():
            snapshot = next((store / who / 'pending').glob('shell-*.json'))
            kept = json.loads(snapshot.read_text())
            kept['time'] -= recorder.long_seconds + 5
            snapshot.write_text(json.dumps(kept))

        shell(who, top, "printf 'later\\n' >> a.py", between=long_ago)
        self.assertEqual(recorded(who), {'a.py': (False, 'a\n', True)})

    def test_a_file_the_record_has_keeps_its_baseline(self):
        top, who = repo({'a.py': 'one\n'}), session()
        hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')})
        write(top, {'a.py': 'two\n'})
        hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')})
        shell(who, top, "printf 'three\\n' >> a.py")
        self.assertEqual(recorded(who), {'a.py': (False, 'one\n', False)})

    def test_which_commands_only_read(self):
        reading = ['ls -la', 'cat a | grep x | wc -l', 'git log --oneline -3 && git status -s', 'git -C r diff',
                   "sed -n '1,5p' f", 'grep -rn foo . 2>/dev/null', 'find . -name "*.py" | head', 'cd x && ls',
                   'FOO=1 ls', 'ls > /dev/null', 'git -c core.x=y log']
        writing = ["sed -i '' 's/a/b/' f", 'echo hi > out', 'cat a >> b', 'python3 s.py', "python3 - <<'EOF'\nEOF",
                   'git commit -m x', 'git checkout -- a', 'find . -delete', 'npm test', './ca format', 'rm a',
                   'cp a b', 'echo $(date)', 'tee out < in']
        for command in reading:
            self.assertTrue(recorder.is_reading(command), command)
        for command in writing:
            self.assertFalse(recorder.is_reading(command), command)



def made_idle(name, days):
    """Puts a record's last activity days back: its folder, its entries and its review's."""
    folder = store / name
    then = time.time() - days * 86400
    for place in [folder, *folder.iterdir(), *((folder / 'review').iterdir() if (folder / 'review').is_dir() else [])]:
        os.utime(place, (then, then), follow_symlinks=False)


def edited(top, who):
    """A record of the session's, from one edit."""
    hook('pre', who, top, 'Edit', {'file_path': str(top / 'a.py')})
    write(top, {'a.py': f'{who}\n'})
    hook('post', who, top, 'Edit', {'file_path': str(top / 'a.py')})


class Pruning(unittest.TestCase):
    def setUp(self):
        self.top = repo({'a.py': 'a\n'})
        self.old, self.fresh = session(), session()
        edited(self.top, self.old)
        edited(self.top, self.fresh)
        made_idle(self.old, recorder.idle_days + 1)

    def test_an_idle_record_goes_and_a_fresh_one_stays(self):
        pruned = subprocess.run([sys.executable, str(scratch.code / 'recorder.py'), 'prune', '--dry-run'],
                                capture_output=True, text=True, env={**os.environ, 'HOME': str(scratch.home)}).stdout
        self.assertIn(self.old, pruned)
        self.assertNotIn(self.fresh, pruned)
        self.assertTrue((store / self.old).exists())
        subprocess.run([sys.executable, str(scratch.code / 'recorder.py'), 'prune'], check=True,
                       capture_output=True, env={**os.environ, 'HOME': str(scratch.home)})
        self.assertFalse((store / self.old).exists())
        self.assertTrue((store / self.fresh).exists())
        self.assertEqual((self.top / 'a.py').read_text(), f'{self.fresh}\n')

    def test_the_hook_prunes_once_a_day_and_never_its_own_session(self):
        (store / '.pruned').unlink(missing_ok=True)
        made_idle(self.fresh, recorder.idle_days + 1)
        # An idle session runs a command that only reads, which leaves its record idle: its post hook
        # prunes the other idle record, not its own.
        hook('post', self.fresh, self.top, 'Bash', {'command': 'ls'})
        self.assertFalse((store / self.old).exists())
        self.assertTrue((store / self.fresh).exists())
        again = session()
        edited(self.top, again)
        made_idle(again, recorder.idle_days + 1)
        edited(self.top, self.fresh)
        self.assertTrue((store / again).exists())

    def test_a_folder_that_is_no_record_stays(self):
        (store / 'notes').mkdir(exist_ok=True)
        made_idle('notes', recorder.idle_days + 1)
        recorder.prune()
        self.assertTrue((store / 'notes').exists())


if __name__ == '__main__':
    unittest.main()
