"""docdiff's plugins on a scratch repository with a base and a head commit, as a PR's review reads it:
where a comment's quote is in the PR's files (its line range and the text there), and what a push to
GitHub sends, GitHub played by a stand-in. docdiff keeps its state in a scratch DOCDIFF_HOME.

Run them all: python3 -m unittest discover -s ~/.claude/skills/redpen/prose/tests
"""
import atexit, os, pathlib, shutil, subprocess, sys, tempfile, unittest

home = pathlib.Path(os.path.realpath(tempfile.mkdtemp(prefix='docdiff-tests-')))
atexit.register(shutil.rmtree, home, ignore_errors=True)
os.environ['DOCDIFF_HOME'] = str(home / '.docdiff')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import docdiff  # noqa: E402  (after DOCDIFF_HOME: it reads it once)

review = next(m for m in docdiff.PLUGINS if m.__name__.endswith('review'))
github = next(m for m in docdiff.PLUGINS if m.__name__.endswith('github_pr'))

BASE = '# Notes\n\nAlpha beta gamma\ndelta epsilon.\nZeta eta theta.\n\nIota kappa lambda.\n'
HEAD = '# Notes\n\nAlpha beta gamma\ndelta epsilon.\nZeta eta theta.\n\nIota kappa lambda mu.\n'


def git(top, *args):
    return subprocess.run(['git', '-C', str(top), *args], check=True, capture_output=True, text=True).stdout.strip()


def pr_run():
    """A repository with docs/a.md at a base and a head commit, and the run of a review of it as a PR."""
    top = pathlib.Path(tempfile.mkdtemp(dir=home, prefix='repo-'))
    git(top, 'init', '-q')
    git(top, 'config', 'user.email', 'test@example.com')
    git(top, 'config', 'user.name', 'test')
    (top / 'docs').mkdir()
    (top / 'docs/a.md').write_text(BASE)
    git(top, 'add', '-A')
    git(top, 'commit', '-qm', 'base')
    base = git(top, 'rev-parse', 'HEAD')
    (top / 'docs/a.md').write_text(HEAD)
    git(top, 'commit', '-qam', 'head')
    return {'repo': str(top), 'base': base, 'head': git(top, 'rev-parse', 'HEAD'), 'pr': 'owner/repo#1', 'paths': []}


def comment(quote, before='', body='nit'):
    """A comment of the page's on docs/a.md as the head reads it."""
    return {'id': 'c1', 'file': 'docs/a.md', 'side': 'R', 'quote': quote, 'before': before, 'after': '', 'body': body}


class Places(unittest.TestCase):
    def test_a_quote_over_lines_is_placed_at_their_range(self):
        run, c = pr_run(), comment('Alpha beta gamma delta epsilon.')
        review.locate(run, [c])
        g = c['github']
        self.assertEqual((g['path'], g['line'], g['end']), ('docs/a.md', 3, 4))
        self.assertTrue(g['href'].endswith('R3-R4'), g['href'])
        self.assertEqual(g['old'], ['Alpha beta gamma', 'delta epsilon.'])
        self.assertTrue(g['ranged'])

    def test_a_quote_on_one_line_has_no_range(self):
        run, c = pr_run(), comment('Iota kappa lambda mu.')
        review.locate(run, [c])
        self.assertEqual(c['github']['line'], 7)
        self.assertNotIn('end', c['github'])

    def test_a_suggestion_ending_with_the_next_line_takes_it_in(self):
        body = 'nit:\n\n```suggestion\nAlpha beta gamma, delta epsilon.\nZeta eta theta.\n```'
        run, c = pr_run(), comment('Alpha beta gamma delta epsilon.', body=body)
        review.locate(run, [c])
        g = c['github']
        self.assertEqual((g['line'], g['end']), (3, 5))
        self.assertTrue(g['href'].endswith('R3-R5'), g['href'])
        self.assertEqual(g['old'], ['Alpha beta gamma', 'delta epsilon.', 'Zeta eta theta.'])

    def test_file_text_is_the_file_at_the_revision(self):
        run = pr_run()
        self.assertEqual(review.file_text(run['repo'], run['base'], 'docs/a.md')[-2], 'Iota kappa lambda.')
        self.assertEqual(review.file_text(run['repo'], run['head'], 'docs/a.md')[-2], 'Iota kappa lambda mu.')
        self.assertIsNone(review.file_text(run['repo'], run['head'], 'docs/none.md'))


class Pushes(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.call = github.call
        os.environ['GITHUB_TOKEN'] = 'test'

        # GitHub: no pending review yet; the POST starts one, review 7, with the comments it sends.
        def call(method, url, data=None):
            self.sent.append((method, url, data))
            if method == 'POST':
                return {'id': 7, 'commit_id': data['commit_id']}, None
            posted = next((d for m, _, d in self.sent if m == 'POST'), None)
            if url.endswith('/reviews/7/comments') and posted:
                return [{'id': 70 + i, 'path': c['path'], 'body': c['body']} for i, c in enumerate(posted['comments'])], None
            return [], None
        github.call = call

    def tearDown(self):
        github.call = self.call
        os.environ.pop('GITHUB_TOKEN', None)

    def test_a_comment_over_lines_goes_as_a_range_and_one_on_a_line_as_that_line(self):
        run = pr_run()
        ranged, single = comment('Alpha beta gamma delta epsilon.'), {**comment('Iota kappa lambda mu.'), 'id': 'c2'}
        review.locate(run, [ranged, single])
        cs = [{'id': c['id'], 'body': c['body'], 'pushed': None, 'quote': c['quote'], 'where': 'docs/a.md',
               **{k: c['github'].get(k) for k in ('path', 'line', 'end', 'side')}} for c in (ranged, single)]
        got = github.push(run, cs, None)
        self.assertIsNone(got['error'])
        posted = next(data for method, url, data in self.sent if method == 'POST')
        self.assertEqual([{k: c.get(k) for k in ('path', 'start_line', 'line', 'side')} for c in posted['comments']], [
            {'path': 'docs/a.md', 'start_line': 3, 'line': 4, 'side': 'RIGHT'},
            {'path': 'docs/a.md', 'start_line': None, 'line': 7, 'side': 'RIGHT'},
        ])


if __name__ == '__main__':
    unittest.main()
