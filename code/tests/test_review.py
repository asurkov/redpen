"""review.py on git reviews of scratch repositories: what the page lists, what its actions do to the
files and the journal, moves, generated files, and the overview."""
import json, os, time, unittest

import scratch
from scratch import git, listed, repo, reviewed, write

review = scratch.review


def counts(file, view='pending'):
    count = file[view]
    return None if count is None else (count['added'], count['removed'])


class Listing(unittest.TestCase):
    def test_a_changed_a_made_and_a_deleted_file(self):
        top = repo({'a.py': 'one\ntwo\n', 'gone.py': 'x\n'})
        write(top, {'a.py': 'one\nTWO\nthree\n', 'gone.py': None, 'new.py': 'n\n'})
        files = listed(reviewed(top))
        self.assertEqual(counts(files['a.py']), (2, 1))
        self.assertTrue(files['gone.py']['isGone'])
        self.assertTrue(files['new.py']['isNew'])
        self.assertIsNone(files['a.py']['accepted'])

    def test_a_same_size_edit_is_seen(self):
        # The change time and the bytes tell it, not the size and the modification time alone.
        top = repo({'a.py': 'abc\n'})
        rv = reviewed(top)
        self.assertEqual(listed(rv), {})
        stat = os.stat(top / 'a.py')
        write(top, {'a.py': 'xyz\n'})
        os.utime(top / 'a.py', ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(counts(listed(reviewed(top))['a.py']), (1, 1))


class Actions(unittest.TestCase):
    def test_accept_a_change_reject_the_rest_and_undo(self):
        top = repo({'a.py': 'l1\nl2\nl3\nl4\nl5\n'})
        write(top, {'a.py': 'L1\nl2\nl3\nl4\nL5\n'})
        rv = reviewed(top)
        key = listed(rv)['a.py']['key']
        first = {'leftStart': 0, 'leftEnd': 1, 'rightStart': 0, 'rightEnd': 1}
        rv.apply(key, 'accept', first, scratch.expected(rv, key, 'accept'))
        file = listed(rv)['a.py']
        self.assertEqual(counts(file), (1, 1))
        self.assertEqual(counts(file, 'accepted'), (1, 1))
        rejected = rv.apply(key, 'reject', None, scratch.expected(rv, key, 'reject'))
        self.assertEqual((top / 'a.py').read_text(), 'L1\nl2\nl3\nl4\nl5\n')
        self.assertIsNone(listed(rv)['a.py']['pending'])
        rv.flip(rejected['id'])
        self.assertEqual((top / 'a.py').read_text(), 'L1\nl2\nl3\nl4\nL5\n')

    def test_an_action_on_a_file_changed_since_is_refused(self):
        top = repo({'a.py': 'one\n'})
        write(top, {'a.py': 'two\n'})
        rv = reviewed(top)
        key = listed(rv)['a.py']['key']
        shown = scratch.expected(rv, key, 'accept')
        write(top, {'a.py': 'three\n'})
        with self.assertRaises(review.Refusal) as refused:
            rv.apply(key, 'accept', None, shown)
        self.assertEqual(refused.exception.status, 409)

    def test_rejecting_a_made_file_takes_it_and_its_emptied_folder_away(self):
        top = repo({'a.py': 'a\n'})
        write(top, {'deep/er/new.py': 'n\n'})
        rv = reviewed(top)
        key = listed(rv)['deep/er/new.py']['key']
        rv.apply(key, 'reject', None, scratch.expected(rv, key, 'reject'))
        self.assertFalse((top / 'deep').exists())

    def test_a_folder_action_is_one_entry_undone_at_once(self):
        top = repo({'src/a.py': 'a\n', 'src/b.py': 'b\n', 'other.py': 'o\n'})
        write(top, {'src/a.py': 'A\n', 'src/b.py': 'B\n', 'other.py': 'O\n'})
        rv = reviewed(top)
        files = listed(rv)
        inside = [files['src/a.py'], files['src/b.py']]
        entry = rv.apply_files([file['key'] for file in inside], 'accept',
                               {file['key']: file['signature'] for file in inside}, f'{top}/src/')
        self.assertEqual(entry['files'], 2)
        files = listed(rv)
        self.assertIsNone(files['src/a.py']['pending'])
        self.assertIsNotNone(files['other.py']['pending'])
        rv.flip(entry['id'])
        self.assertIsNotNone(listed(rv)['src/b.py']['pending'])


class FolderComments(unittest.TestCase):
    def test_a_comment_on_a_folder_lists_its_changed_files_and_is_answered_under_it(self):
        top = repo({'src/a.py': 'a\n', 'src/same.py': 's\n', 'src/gone.py': 'g\n', 'other.py': 'o\n'})
        write(top, {'src/a.py': 'A\n', 'src/gone.py': None, 'src/deep/new.py': 'n\n', 'other.py': 'O\n'})
        rv = reviewed(top)
        said = rv.comment({'folder': f'{top}/src/', 'text': 'Put these under lib/.'})
        self.assertEqual(said['quote'], 'a.py\ndeep/new.py (new)\ngone.py (deleted)')
        thread = next(thread for thread in rv.threads() if thread['folder'] == f'{top}/src/')
        self.assertEqual((thread['key'], thread['entries'][0]['text']), (None, 'Put these under lib/.'))
        told = review.described(rv.comments(), rv.git, rv.session)
        self.assertIn(f'The folder {top}/src/, its changed files (comment {said["id"]}):', told)
        self.assertIn('gone.py (deleted)', told)
        rv.post(review.shared('channel'))
        self.assertTrue(rv.answer(said['id'], 'Moved.'))
        time.sleep(0.01)
        rv.comment({'folder': f'{top}/src/', 'text': 'Thanks.', 'thread': said['id']})
        thread = next(thread for thread in rv.threads() if thread['folder'] == f'{top}/src/')
        self.assertEqual([entry['by'] for entry in thread['entries']], ['user', 'claude', 'user'])

    def test_a_folder_with_no_changes_or_no_folder_is_refused(self):
        top = repo({'src/a.py': 'a\n', 'b.py': 'b\n'})
        write(top, {'b.py': 'B\n'})
        rv = reviewed(top)
        for folder, status in ((f'{top}/src/', 409), (f'{top}/src', 400), ('src/', 400)):
            with self.assertRaises(review.Refusal) as refused:
                rv.comment({'folder': folder, 'text': 'Why?'})
            self.assertEqual(refused.exception.status, status, folder)


class Commits(unittest.TestCase):
    def edit(self, top, who, name, text):
        scratch.hook('pre', who, top, 'Edit', {'file_path': str(top / name)})
        write(top, {name: text})
        scratch.hook('post', who, top, 'Edit', {'file_path': str(top / name)})

    def test_a_commit_makes_what_it_took_accepted_and_undo_takes_it_back(self):
        top, who = repo({'a.py': 'a1\n', 'b.py': 'b1\n'}), scratch.session()
        self.edit(top, who, 'a.py', 'a2\n')
        rv = review.Review(who)
        # The first look notes HEAD and accepts nothing.
        self.assertIsNone(rv.checkpoint())
        self.assertEqual(counts(listed(rv)['a.py']), (1, 1))
        git(top, 'commit', '-qam', 'a2')
        entry = rv.checkpoint()
        self.assertEqual((entry['files'], entry['commit']), (1, git(top, 'rev-parse', 'HEAD')[:10]))
        self.assertIsNone(listed(rv)['a.py']['pending'])
        self.assertEqual(counts(listed(rv)['a.py'], 'accepted'), (1, 1))
        # The next edit is all that is pending; a look with HEAD where it was does nothing.
        self.edit(top, who, 'a.py', 'a3\n')
        self.assertIsNone(rv.checkpoint())
        self.assertEqual(counts(listed(rv)['a.py']), (1, 1))
        self.assertEqual(rv.opened(listed(rv)['a.py']['key'])['texts']['base']['sha'], review.sha(b'a2\n'))
        # Undo: the committed change is pending again, with the one after it.
        rv.flip(entry['id'])
        self.assertEqual(rv.opened(listed(rv)['a.py']['key'])['texts']['base']['sha'], review.sha(b'a1\n'))

    def test_files_committed_before_head_was_kept_are_settled_once(self):
        top, who = repo({'a.py': 'a1\n', 'b.py': 'b1\n'}), scratch.session()
        self.edit(top, who, 'a.py', 'a2\n')
        self.edit(top, who, 'b.py', 'b2\n')
        git(top, 'commit', '-qm', 'a2', 'a.py')
        # A record kept before HEAD was: its HEAD seen first after the commit, nothing settled yet.
        rv = review.Review(who)
        (rv.folder / 'heads.json').write_text(json.dumps({str(top): git(top, 'rev-parse', 'HEAD').strip()}))
        entry = rv.checkpoint()
        self.assertEqual(entry['files'], 1)
        self.assertIsNone(listed(rv)['a.py']['pending'])
        self.assertEqual(counts(listed(rv)['b.py']), (1, 1))
        # Once: undone, it stays undone.
        rv.flip(entry['id'])
        self.assertIsNone(rv.checkpoint())
        self.assertEqual(counts(listed(rv)['a.py']), (1, 1))

    def test_a_commit_before_the_page_ever_looked_counts_too(self):
        top, who = repo({'a.py': 'a1\n'}), scratch.session()
        self.edit(top, who, 'a.py', 'a2\n')
        git(top, 'commit', '-qam', 'a2')
        rv = review.Review(who)
        self.assertEqual(rv.checkpoint()['files'], 1)
        self.assertIsNone(listed(rv)['a.py']['pending'])

    def test_a_file_another_session_changes_once_all_of_mine_was_accepted_is_theirs(self):
        top, mine, theirs = repo({'a.py': 'a1\n', 'b.py': 'b1\n'}), scratch.session(), scratch.session()
        self.edit(top, mine, 'a.py', 'a2\n')
        self.edit(top, mine, 'b.py', 'b2\n')
        rv = review.Review(mine)
        rv.checkpoint()
        # a.py all accepted by its commit; b.py's change pending still.
        git(top, 'add', 'a.py')
        git(top, 'commit', '-qm', 'a2')
        rv.checkpoint()
        self.edit(top, theirs, 'a.py', 'a3\n')
        self.edit(top, theirs, 'b.py', 'b3\n')
        rv.take_others_changes()
        files = listed(rv)
        # a.py: theirs alone, its baseline now, and said so.
        self.assertIsNone(files['a.py']['pending'])
        self.assertTrue(files['a.py']['isEditedElsewhere'])
        # b.py: mine pending, theirs in it, as before.
        self.assertEqual(counts(files['b.py']), (1, 1))
        self.assertEqual(rv.opened(files['b.py']['key'])['texts']['base']['sha'], review.sha(b'b1\n'))
        # They change it again: taken again, as I have not edited it since.
        self.edit(top, theirs, 'a.py', 'a3 again\n')
        rv.take_others_changes()
        self.assertIsNone(listed(rv)['a.py']['pending'])
        # My next edit of a.py is all of its diff.
        self.edit(top, mine, 'a.py', 'a4\n')
        files = listed(rv)
        self.assertEqual(rv.opened(files['a.py']['key'])['texts']['base']['sha'], review.sha(b'a3 again\n'))
        self.assertFalse(files['a.py']['isEditedElsewhere'])

    def test_a_file_all_accepted_takes_others_changes_each_time_and_writes_nothing_between(self):
        top, mine, theirs = repo({'a.py': 'a1\n'}), scratch.session(), scratch.session()
        self.edit(top, mine, 'a.py', 'a2\n')
        rv = review.Review(mine)
        rv.accept_all()
        # Accepted, not committed: they change it twice, and both are theirs.
        self.edit(top, theirs, 'a.py', 'a3\n')
        rv.take_others_changes()
        self.edit(top, theirs, 'a.py', 'a4\n')
        rv.take_others_changes()
        self.assertIsNone(listed(rv)['a.py']['pending'])
        # With nothing new, the baseline stays as it is: no write at each look.
        base = rv.record / 'base' / listed(rv)['a.py']['key']
        written = base.stat().st_mtime_ns
        rv.take_others_changes()
        self.assertEqual(base.stat().st_mtime_ns, written)

    def test_a_file_whose_text_i_left_is_committed_takes_others_changes_whatever_its_baseline(self):
        top, mine, theirs = repo({'a.py': 'a1\n'}), scratch.session(), scratch.session()
        self.edit(top, mine, 'a.py', 'a2\n')
        git(top, 'commit', '-qam', 'a2')
        self.edit(top, theirs, 'a.py', 'a3\n')
        rv = review.Review(mine)
        # A part of theirs accepted on the page left a baseline that is neither; HEAD holds what I left.
        (rv.record / 'base' / listed(rv)['a.py']['key']).write_text('a2, and a part of a3\n')
        rv.take_others_changes()
        self.assertIsNone(listed(rv)['a.py']['pending'])

    def test_a_commit_of_other_files_leaves_the_record_alone(self):
        top, who = repo({'a.py': 'a1\n', 'b.py': 'b1\n'}), scratch.session()
        self.edit(top, who, 'a.py', 'a2\n')
        rv = review.Review(who)
        rv.checkpoint()
        write(top, {'b.py': 'b2\n'})
        git(top, 'add', 'b.py')
        git(top, 'commit', '-qm', 'b2')
        self.assertIsNone(rv.checkpoint())
        self.assertEqual(counts(listed(rv)['a.py']), (1, 1))


class AcceptAll(unittest.TestCase):
    def test_everything_pending_is_accepted_as_one_entry_and_undone_at_once(self):
        top = repo({'a.py': 'a\n', 'src/b.py': 'b\n'})
        write(top, {'a.py': 'A\n', 'src/b.py': 'B\n', 'new.py': 'n\n'})
        rv = reviewed(top)
        shown = {file['key']: file['signature'] for file in rv.listing()['files'] if file['pending']}
        entry = rv.accept_all(shown)
        self.assertEqual((entry['all'], entry['files']), (True, 3))
        self.assertTrue(all(file['pending'] is None for file in listed(rv).values()))
        self.assertEqual([e['id'] for e in rv.journal_shown()], [entry['id']])
        rv.flip(entry['id'])
        self.assertTrue(all(file['pending'] for file in listed(rv).values()))

    def test_a_file_changed_since_the_page_showed_it_or_nothing_pending_is_refused(self):
        top = repo({'a.py': 'a\n'})
        write(top, {'a.py': 'A\n'})
        rv = reviewed(top)
        shown = {file['key']: file['signature'] for file in rv.listing()['files'] if file['pending']}
        write(top, {'a.py': 'AA\n'})
        with self.assertRaises(review.Refusal) as refused:
            rv.accept_all(shown)
        self.assertEqual(refused.exception.status, 409)
        rv.accept_all()
        with self.assertRaises(review.Refusal):
            rv.accept_all()


class Moves(unittest.TestCase):
    def test_a_renamed_file_is_one_row_and_reject_puts_it_back(self):
        top = repo({'a.py': 'one\ntwo\nthree\n'})
        git(top, 'mv', 'a.py', 'b.py')
        rv = reviewed(top)
        files = listed(rv)
        self.assertEqual(set(files), {'b.py'})
        self.assertEqual(files['b.py']['movedFrom'], f'{top}/a.py')
        key = files['b.py']['key']
        rv.apply(key, 'reject', None, scratch.expected(rv, key, 'reject'))
        self.assertEqual(sorted(os.listdir(top)), ['.git', 'a.py'])

    def test_a_rename_by_case_alone_is_put_back_under_its_old_name(self):
        top = repo({'Foo.py': 'foo\n'})
        if not os.path.exists(top / 'foo.py'):
            self.skipTest('the file system tells case apart')
        git(top, 'mv', 'Foo.py', 'foo.py')
        rv = reviewed(top)
        files = listed(rv)
        self.assertEqual(files['foo.py']['movedFrom'], f'{top}/Foo.py')
        key = files['foo.py']['key']
        rv.apply(key, 'reject', None, scratch.expected(rv, key, 'reject'))
        self.assertIn('Foo.py', os.listdir(top))
        self.assertNotIn('foo.py', os.listdir(top))


class Generated(unittest.TestCase):
    def test_marked_and_default_generated_files(self):
        files = {'.gitattributes': 'dist/** linguist-generated\nCargo.lock -linguist-generated\n',
                 'app.js': 'a\n', 'dist/b.js': 'b\n', 'yarn.lock': 'y\n', 'Cargo.lock': 'c\n',
                 'x.pb.go': '// Code generated by protoc-gen-go. DO NOT EDIT.\npackage x\n',
                 'notes.txt': 'notes\nDO NOT EDIT below\n'}
        top = repo(files)
        write(top, {name: text + 'more\n' for name, text in files.items() if name != '.gitattributes'})
        rv = reviewed(top)
        marked = {name for name, file in listed(rv).items() if file['isGenerated']}
        self.assertEqual(marked, {'dist/b.js', 'yarn.lock', 'x.pb.go'})
        generated = [file for name, file in listed(rv).items() if name in marked]
        entry = rv.apply_files([file['key'] for file in generated], 'accept',
                               {file['key']: file['signature'] for file in generated}, None, True)
        self.assertEqual((entry['generated'], entry['files']), (True, 3))
        left = {name for name, file in listed(rv).items() if file['pending']}
        self.assertEqual(left, {'app.js', 'Cargo.lock', 'notes.txt'})

    def test_banners(self):
        banners = [
            '// Code generated by protoc-gen-go. DO NOT EDIT.\npackage x',
            '# Generated by the protocol buffer compiler.  DO NOT EDIT!',
            '#\n# Autogenerated by Thrift Compiler (0.9.3)\n#\n# DO NOT EDIT UNLESS YOU ARE SURE',
            '/* eslint-disable */\n// THIS IS A GENERATED FILE, DO NOT EDIT',
            '/* !!! This is code generated by Prisma. Do not edit directly. !!! */',
            '#\n# This file is autogenerated by pip-compile with Python 3.11',
            '# This file is automatically @generated by Cargo.',
            '// Automatically generated file. Do not modify.',
        ]
        plain = ['line\nDO NOT EDIT below\n', '# DO NOT EDIT: managed by Terraform', '# Generated reports go to /tmp',
                 '#!/bin/sh\n# makes the generated files\nmake gen', 'import os\n']
        for text in banners:
            self.assertTrue(review.is_generated_banner(text), text)
        for text in plain:
            self.assertFalse(review.is_generated_banner(text), text)


class Overview(unittest.TestCase):
    def setUp(self):
        self.top = repo({'a.py': ''.join(f'a{n}\n' for n in range(1, 11)), 'b.py': 'b1\nb2\n'})
        write(self.top, {'a.py': ''.join(f'A{n}\n' if n in (3, 8) else f'a{n}\n' for n in range(1, 11)),
                         'b.py': 'b1\nB2\n'})
        self.rv = reviewed(self.top)

    def outline(self, *items):
        return self.rv.set_outline({'items': [{'title': title, 'blocks': [
            {'path': f'{self.top}/{path}', 'start': start, 'end': end, 'note': title} for path, start, end in blocks]}
            for title, blocks in items]})

    def states(self, shown):
        return [[(block['start'], block['state']) for block in item['blocks']] for item in shown['items']]

    def test_a_path_or_lines_the_review_has_not_are_refused(self):
        with self.assertRaises(review.Refusal) as refused:
            self.outline(('x', [('nope.py', 1, 1), ('b.py', 5, 9)]))
        self.assertIn('nope.py is not among', str(refused.exception))
        self.assertIn('has no lines 5-9', str(refused.exception))

    def test_blocks_follow_their_lines_and_leave_the_rest_uncovered(self):
        self.outline(('three', [('a.py', 3, 3)]), ('eight', [('a.py', 8, 8)]))
        write(self.top, {'a.py': 'new1\nnew2\n' + (self.top / 'a.py').read_text()})
        shown = self.rv.outline_shown()
        self.assertEqual(self.states(shown), [[(5, 'pending')], [(10, 'pending')]])
        uncovered = {(os.path.basename(change['path']), change['start']) for change in shown['uncovered']}
        self.assertEqual(uncovered, {('a.py', 1), ('b.py', 2)})

    def test_an_items_accept_takes_its_blocks_alone_and_undoes_at_once(self):
        shown = self.outline(('three', [('a.py', 3, 3)]), ('eight', [('a.py', 8, 8)]))
        signatures = {file['key']: file['signature'] for file in self.rv.listing()['files']}
        entry = self.rv.apply_item(0, shown['time'], signatures)
        self.assertEqual(entry['item'], 'three')
        self.assertEqual(self.states(self.rv.outline_shown()), [[(3, 'accepted')], [(8, 'pending')]])
        self.rv.flip(entry['id'])
        self.assertEqual(self.states(self.rv.outline_shown()), [[(3, 'pending')], [(8, 'pending')]])

    def test_a_block_whose_lines_changed_is_stale(self):
        self.outline(('eight', [('a.py', 8, 8)]))
        write(self.top, {'a.py': (self.top / 'a.py').read_text().replace('A8', 'A8 again')})
        shown = self.rv.outline_shown()
        self.assertEqual(self.states(shown), [[(8, 'stale')]])
        self.assertIn(8, [change['start'] for change in shown['uncovered']])

    def test_the_ask_goes_as_a_comment_and_an_overview_answers_it(self):
        self.rv.describe()
        self.assertEqual(self.rv.asked_state(), 'draft')
        listing = self.rv.listing()
        self.assertEqual((listing['comments'], listing['threads']), ([], []))
        told = review.described(self.rv.comments(), self.rv.git, self.rv.session)
        self.assertIn(f'--outline {self.rv.session}', told)
        self.outline(('three', [('a.py', 3, 3)]))
        self.assertIsNone(self.rv.asked_state())
        self.assertEqual(self.rv.comments(), [])

    def test_a_comment_on_an_item_quotes_it_and_is_answered_under_it(self):
        self.outline(('three', [('a.py', 3, 3)]), ('eight', [('a.py', 8, 8)]))
        said = self.rv.comment({'item': 1, 'title': 'eight', 'text': 'Why this one?'})
        thread = next(thread for thread in self.rv.threads() if thread['item'] == 'eight')
        self.assertEqual((thread['key'], thread['entries'][0]['text']), (None, 'Why this one?'))
        told = review.described(self.rv.comments(), self.rv.git, self.rv.session)
        self.assertIn('The overview\'s item "eight"', told)
        self.assertIn(f'{self.top}/a.py:8-8 — eight', told)
        self.rv.post(review.shared('channel'))
        self.assertTrue(self.rv.answer(said['id'], 'Because.'))
        # A reply comes later than the answer it replies to: the thread is in the order of its times.
        time.sleep(0.01)
        self.rv.comment({'item': 1, 'title': 'eight', 'text': 'And?', 'thread': said['id']})
        thread = next(thread for thread in self.rv.threads() if thread['item'] == 'eight')
        self.assertEqual([entry['by'] for entry in thread['entries']], ['user', 'claude', 'user'])

    def test_the_page_asks_for_an_overview_while_changes_have_none(self):
        self.assertTrue(self.rv.needs_overview())
        asked = self.rv.describe_by_fork()
        # A question a fork with no tools answers: the JSON alone, the changes numbered in it.
        self.assertIn('opened with changes no overview covers', asked)
        self.assertIn('Answer with it as JSON alone', asked)
        self.assertNotIn(' --outline', asked)
        self.assertIn(f'=== {self.top}/a.py\n@@\n  1 | a1\n  2 | a2\n- (3) | a3\n+ 3 | A3\n', asked)
        self.assertIn(f'=== {self.top}/b.py\n@@\n  1 | b1\n- (2) | b2\n+ 2 | B2', asked)
        # Handed over with the question: Claude writes it, and nothing asks again meanwhile.
        self.assertEqual(self.rv.asked_state(), 'taken')
        self.assertFalse(self.rv.needs_overview())
        self.assertEqual(review.shared('channel').waiting(self.rv.sent), [])
        self.outline(('three', [('a.py', 3, 3)]), ('eight', [('a.py', 8, 8)]), ('b', [('b.py', 2, 2)]))
        self.assertIsNone(self.rv.asked_state())
        self.assertFalse(self.rv.needs_overview())
        # A change since that no block covers: asked again, naming the items written before.
        write(self.top, {'a.py': (self.top / 'a.py').read_text().replace('a1\n', 'A1\n')})
        self.assertTrue(self.rv.needs_overview())
        self.assertIn('"three"; "eight"; "b"', self.rv.describe_by_fork())

    def test_the_pages_describe_waiting_for_the_next_message_is_the_forks_to_answer(self):
        self.outline(('three', [('a.py', 3, 3)]), ('eight', [('a.py', 8, 8)]), ('b', [('b.py', 2, 2)]))
        self.rv.describe()
        self.assertEqual(self.rv.asked_state(), 'draft')
        asked = self.rv.describe_by_fork()
        # The user's ask, the items written before named, and gone from what waits for the next message.
        self.assertIn('The user asked on the redpen review page', asked)
        self.assertIn('"three"; "eight"; "b"', asked)
        self.assertEqual(self.rv.comments(), [])
        self.assertEqual(self.rv.asked_state(), 'taken')

    def test_an_ask_the_fork_brought_no_overview_for_is_asked_again(self):
        self.rv.describe_by_fork()
        self.assertEqual(self.rv.asked_state(), 'taken')
        self.rv.drop_ask()
        self.assertIsNone(self.rv.asked_state())
        self.assertTrue(self.rv.needs_overview())

    def test_an_ask_taken_long_ago_with_no_overview_is_asked_again(self):
        self.rv.describe_by_fork()
        self.assertEqual(self.rv.asked_state(), 'taken')
        # Its turn was cut short: Claude took it taken_minutes and more ago and wrote none.
        handed = self.rv.folder / 'handed.jsonl'
        then = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - review.taken_minutes * 60 - 5))
        handed.write_text(''.join(json.dumps({**json.loads(line), 'time': then}) + '\n'
                                  for line in handed.read_text().splitlines()))
        self.assertIsNone(self.rv.asked_state())
        self.assertTrue(self.rv.needs_overview())

    def test_a_comment_on_an_item_the_overview_no_longer_has_is_refused(self):
        self.outline(('three', [('a.py', 3, 3)]))
        with self.assertRaises(review.Refusal) as refused:
            self.rv.comment({'item': 0, 'title': 'eight', 'text': 'Why?'})
        self.assertEqual(refused.exception.status, 409)


class SessionOverview(unittest.TestCase):
    """The overview of a session's review against the session's edits since: one they made old is not
    shown, and the next turn's end asks for one anew."""

    def setUp(self):
        self.top, self.who = repo({'a.py': 'a1\na2\n', 'b.py': 'b1\n'}), scratch.session()
        self.edit('a.py', 'A1\na2\n')
        self.edit('b.py', 'B1\n')
        self.rv = review.Review(self.who)

    def edit(self, name, text):
        scratch.hook('pre', self.who, self.top, 'Edit', {'file_path': str(self.top / name)})
        write(self.top, {name: text})
        scratch.hook('post', self.who, self.top, 'Edit', {'file_path': str(self.top / name)})

    def outline(self):
        # Blocks on the whole files: every change to them counts as covered.
        return self.rv.set_outline({'items': [{'title': 'both', 'blocks': [
            {'path': f'{self.top}/a.py'}, {'path': f'{self.top}/b.py'}]}]})

    def test_an_overview_the_sessions_edits_made_old_is_not_shown_and_asked_for_anew(self):
        self.assertFalse(self.outline()['isOld'])
        self.assertFalse(self.rv.needs_overview())
        self.edit('a.py', 'A1\nA2\n')
        shown = self.rv.outline_shown()
        self.assertEqual((shown['isOld'], shown['items']), (True, []))
        self.assertTrue(self.rv.needs_overview())
        # Asked anew, naming the old one's items; its answer is shown.
        self.assertIn('"both"', self.rv.describe_by_fork())
        self.assertFalse(self.outline()['isOld'])

    def test_a_change_its_writer_left_out_is_not_asked_for_again(self):
        self.rv.set_outline({'items': [{'title': 'a', 'blocks': [{'path': f'{self.top}/a.py'}]}]})
        shown = self.rv.outline_shown()
        self.assertEqual([os.path.basename(change['path']) for change in shown['uncovered']], ['b.py'])
        self.assertFalse(self.rv.needs_overview())

    def test_the_reviews_reject_and_a_commit_leave_it_current(self):
        self.outline()
        key = listed(self.rv)['b.py']['key']
        self.rv.apply(key, 'reject', None, scratch.expected(self.rv, key, 'reject'))
        self.assertEqual((self.top / 'b.py').read_text(), 'b1\n')
        scratch.shell(self.who, self.top, 'git commit -qam a.py')
        shown = self.rv.outline_shown()
        self.assertEqual((shown['isOld'], len(shown['items'])), (False, 1))

    def test_an_edit_while_the_fork_writes_makes_its_overview_old(self):
        self.rv.describe_by_fork()
        self.edit('a.py', 'A1\nA2\n')
        self.assertTrue(self.outline()['isOld'])
        self.assertTrue(self.rv.needs_overview())

    def test_one_kept_before_edited_was_is_old_by_the_times_of_the_edits(self):
        place = self.rv.folder / 'outline.json'
        self.outline()
        kept = json.loads(place.read_text())
        del kept['edited']
        for when, is_old in (('2000-01-01T00:00:00.000+00:00', True), ('2999-01-01T00:00:00.000+00:00', False)):
            place.write_text(json.dumps({**kept, 'time': when}))
            self.assertEqual(self.rv.outline_shown()['isOld'], is_old)


if __name__ == '__main__':
    unittest.main()
