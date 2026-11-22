"""recorder.py run as Claude Code runs it, around Edit calls and shell commands in scratch
repositories: what each records, with what baseline, and what it leaves alone."""
import json, os, unittest

import scratch
from scratch import hook, recorded, repo, session, shell, write

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


if __name__ == '__main__':
    unittest.main()
