"""review.py on git reviews of scratch repositories: what the page lists, what its actions do to the
files and the journal, moves, generated files, and the overview."""
import os, time, unittest

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

    def test_a_commit_before_the_page_ever_looked_counts_too(self):
        top, who = repo({'a.py': 'a1\n'}), scratch.session()
        self.edit(top, who, 'a.py', 'a2\n')
        git(top, 'commit', '-qam', 'a2')
        rv = review.Review(who)
        self.assertEqual(rv.checkpoint()['files'], 1)
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
        asked = self.rv.describe_by_prompt()
        self.assertIn('opened with changes no overview covers', asked)
        self.assertIn(f'--outline {self.rv.session}', asked)
        # Handed over with the prompt: Claude writes it, and nothing asks again meanwhile.
        self.assertEqual(self.rv.asked_state(), 'taken')
        self.assertFalse(self.rv.needs_overview())
        self.assertEqual(review.shared('channel').waiting(self.rv.sent), [])
        self.outline(('three', [('a.py', 3, 3)]), ('eight', [('a.py', 8, 8)]), ('b', [('b.py', 2, 2)]))
        self.assertIsNone(self.rv.asked_state())
        self.assertFalse(self.rv.needs_overview())
        # A change since that no block covers: asked again, naming the items written before.
        write(self.top, {'a.py': (self.top / 'a.py').read_text().replace('a1\n', 'A1\n')})
        self.assertTrue(self.rv.needs_overview())
        self.assertIn('"three"; "eight"; "b"', self.rv.describe_by_prompt())

    def test_a_comment_on_an_item_the_overview_no_longer_has_is_refused(self):
        self.outline(('three', [('a.py', 3, 3)]))
        with self.assertRaises(review.Refusal) as refused:
            self.rv.comment({'item': 0, 'title': 'eight', 'text': 'Why?'})
        self.assertEqual(refused.exception.status, 409)


if __name__ == '__main__':
    unittest.main()
