"""docdiff's plugins on a scratch repository with a base and a head commit, as a PR's review reads it:
where a comment's quote is in the PR's files (its line range and the text there), what a push to
GitHub sends, GitHub played by a stand-in, and where the page draws a comment's mark, as headless Chrome
renders it from a server of the test's own. docdiff keeps its state in a scratch DOCDIFF_HOME.

Run them all: python3 -m unittest discover -s ~/.claude/skills/redpen/prose/tests
"""
import atexit, json, os, pathlib, re, shutil, socket, subprocess, sys, tempfile, time, unittest, urllib.request

home = pathlib.Path(os.path.realpath(tempfile.mkdtemp(prefix='docdiff-tests-')))
atexit.register(shutil.rmtree, home, ignore_errors=True)
os.environ['DOCDIFF_HOME'] = str(home / '.docdiff')
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import docdiff  # noqa: E402  (after DOCDIFF_HOME: it reads it once)

review = next(m for m in docdiff.PLUGINS if m.__name__.endswith('review'))
github = next(m for m in docdiff.PLUGINS if m.__name__.endswith('github_pr'))

BASE = '# Notes\n\nAlpha beta gamma\ndelta epsilon.\nZeta eta theta.\n\nIota kappa lambda.\n'
HEAD = '# Notes\n\nAlpha beta gamma\ndelta epsilon.\nZeta eta theta.\n\nIota kappa lambda mu.\n'
# A head that adds a paragraph and the list after it, one change of two blocks; the paragraph starts on line 7.
STEPS = BASE.replace('Iota', "To frob a widget, given a gadget,\nperform the following steps:\n\n1. Let knob be the gadget's knob.\n"
                             "2. Turn knob.\n\nIota")
CHROME = next((c for c in ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', shutil.which('google-chrome'),
                           shutil.which('chromium')) if c and os.path.exists(c)), None)


def git(top, *args):
    return subprocess.run(['git', '-C', str(top), *args], check=True, capture_output=True, text=True).stdout.strip()


def pr_run(head=HEAD):
    """A repository with docs/a.md at a base and a head commit (head its text), and the run of a review of
    it as a PR."""
    top = pathlib.Path(tempfile.mkdtemp(dir=home, prefix='repo-'))
    git(top, 'init', '-q')
    git(top, 'config', 'user.email', 'test@example.com')
    git(top, 'config', 'user.name', 'test')
    (top / 'docs').mkdir()
    (top / 'docs/a.md').write_text(BASE)
    git(top, 'add', '-A')
    git(top, 'commit', '-qm', 'base')
    base = git(top, 'rev-parse', 'HEAD')
    (top / 'docs/a.md').write_text(head)
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


class Blocks(unittest.TestCase):
    """block_diff() on two renders of a page. In this file rather than one of its own, which would import
    docdiff before the scratch home above is set."""

    def test_a_block_without_words_that_differs_only_in_a_comment_is_no_change(self):
        # ReSpec writes a random number in a comment of its own all over each render.
        page = '<p>Alpha beta.</p>\n<svg class="respec-visually-hidden"><!---%s%%--><path d="M0 0"/></svg>\n<p>Gamma.</p>'
        old, new = page % '0.467906', page % '0.639278'
        _, n, pieces = docdiff.block_diff(old, new, 'x')
        self.assertEqual(n, 0)
        # Each side keeps its own comment: an accepted copy is the base's page.
        self.assertEqual(docdiff.apply(pieces, 'old', set()), docdiff.apply(docdiff.block_diff(old, old, 'x')[2], 'old', set()))


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


@unittest.skipUnless(CHROME, 'no Chrome to draw the page with')
class Marks(unittest.TestCase):
    def page(self, run, on_github, comments):
        """The page of run's review as a PR, its line comments on_github (as GitHub's API lists them) and
        comments its own, as headless Chrome draws it."""
        name, prs = f"marks-{pathlib.Path(run['repo']).name}", home / 'pr-comments.json'
        prs.write_text(json.dumps({'review': on_github, 'issue': [], 'reviews': []}))
        env, here = {**os.environ, 'DOCDIFF_GITHUB_API': 'http://127.0.0.1:9'}, pathlib.Path(docdiff.__file__).parent
        subprocess.run([sys.executable, '-I', str(here / 'docdiff.py'), '--repo', run['repo'], '--base', run['base'],
                        '--head', run['head'], '--pr', run['pr'], '--pr-comments', str(prs), '--me', 'me', '--name', name],
                       env=env, check=True, capture_output=True)
        (docdiff.HOME / name / 'review.json').write_text(json.dumps({'comments': comments}))
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        server = subprocess.Popen([sys.executable, '-I', str(here.parent / 'base/server.py'), '--serve', '--port', str(port)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        url = f'http://127.0.0.1:{port}/{name}/index.html'
        for _ in range(100):
            try:
                urllib.request.urlopen(url, timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        return subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-first-run', f'--user-data-dir={home / "chrome"}',
                               '--virtual-time-budget=5000', '--dump-dom', url], capture_output=True, text=True, timeout=60).stdout

    def test_a_github_comment_on_the_first_block_of_a_change_is_marked_there_with_a_comment_of_yours(self):
        run = pr_run(STEPS)
        dom = self.page(run, [{'id': 101, 'user': {'login': 'someone'}, 'created_at': '2026-11-23T12:00:00Z',
                               'body': 'Move this out?', 'path': 'docs/a.md', 'side': 'RIGHT', 'line': 7}],
                        [{**comment('To frob a widget, given a gadget, perform the following steps:', body='Rename it?'),
                          'time': '2026-11-23T11:00:00', 'view': 'new', 'key': None, 'section': 'Notes'}])
        diff, new = view(dom, 'diff'), view(dom, 'new')
        # In Diff, on the change: one mark at the end of the paragraph, the GitHub thread's and yours.
        marks = re.findall(r'<span class="rvb[^"]*" title="([^"]*)"', block(diff, 'To frob'))
        self.assertEqual(len(marks), 1, marks)
        self.assertIn('On GitHub: someone', marks[0])
        self.assertIn('Rename it?', marks[0])
        self.assertNotIn('class="rvb', block(diff, 'Let knob'))
        # In Head, where its quote reads.
        self.assertIn('On GitHub: someone', block(new, 'To frob'))
        self.assertNotIn('class="rvb', block(new, 'Let knob'))


def view(dom, v):
    """One view of the page's doc, its markup."""
    i = dom.index(f'<div class="v v-{v}"')
    j = dom.find('<div class="v ', i + 1)
    return dom[i:j if j >= 0 else None]


def block(markup, start):
    """The paragraph or list item whose text starts with start, its markup."""
    return re.search(r'<(p|li)>' + re.escape(start) + r'.*?</\1>', markup, re.S).group(0)


if __name__ == '__main__':
    unittest.main()
