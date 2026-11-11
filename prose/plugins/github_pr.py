"""--pr: a change's popup opens the change on GitHub, in the PR's Files changed view at its line, where
review comments go.

A change goes to the line of its first added word in the head, or, when it only removes, of its first
removed word in the base. The change's block (paragraph, list item, heading...) is matched word by word
against the files the PR changes: the file holding the block's longest run of words is the one, and
aligning the block with it puts the change's word on a line. Tags and punctuation do not count, so
ReSpec's rewrites ([=term=], {{IDL}}, |var|) cost a spec's source only the words they change. A Markdown
file's words take their lines from pandoc's source positions. A change with no run of RUN words in a
row anywhere links to its file without a line, or to the PR's files when a spec has several. place()
finds other text of a block the same way, for other plugins: review comments.

remarks() reads the PR's own comments from GitHub, for other plugins too, and puts each line comment
back on the diff the other way round: the words of its line against the blocks of the diff, with me, your
login (--me, or GITHUB_TOKEN's user), so yours count as your review, and whether each thread is resolved.
push() sends your comments, for other plugins, as your pending review on the PR, which stays pending until
you submit it on GitHub, and resolve() resolves or reopens a thread; nothing else is ever posted.
"""
# docdiff, the running docdiff.py, is set by its load_plugins().
import difflib, hashlib, html, json, os, pathlib, re, subprocess, sys, threading, time, urllib.error, urllib.request

LABEL, KEY = 'GitHub ↗', 'g'
WORD = re.compile(r'\w+')
# Words of the change's block matched on each side of the change, at most.
CONTEXT = 40
# The fewest words in a row that place a change.
RUN = 4
# Its name as a source of comments made elsewhere (review.py), which its ↻ names.
SOURCE = 'GitHub'
# DOCDIFF_GITHUB_API sends every call to a stand-in for GitHub's API instead, a test's.
API, AGENT = os.environ.get('DOCDIFF_GITHUB_API') or 'https://api.github.com', 'docdiff (github.com/asurkov/docdiff)'
# A page load asks GitHub for the PR's comments again once they are older than this, in seconds.
FRESH = 300
lock = threading.Lock()


def add_args(p):
    p.add_argument('--pr', help="the GitHub PR that --base..--head is: its URL, owner/repo#N, or N for a PR "
                                "of the origin remote; a change's popup then opens the change on GitHub")
    p.add_argument('--pr-comments', type=lambda f: str(pathlib.Path(f).resolve()),
                   help="the PR's comments from this file instead of GitHub, {review, issue, reviews} as its API "
                        "lists them (offline, or a test)")
    p.add_argument('--me', help="your GitHub login: your comments on the PR count as your review comments "
                                "(default: GITHUB_TOKEN's user, if set)")


def name(run):
    """The review's name: the repo's and the PR's number, as webmonetization-780."""
    if not run.get('pr'):
        return None
    return f"{pathlib.Path(run['repo']).name}-{pr_of(run['repo'], run['pr'])[1]}"


def title(run):
    if not run.get('pr'):
        return None
    slug, n = pr_of(run['repo'], run['pr'])
    return f'GitHub PR · {slug}#{n}', f'https://github.com/{slug}/pull/{n}'


def actions(run):
    if not run.get('pr'):
        return None
    repo, base, head = run['repo'], run['base'], run.get('head')
    if not head:
        sys.exit('--pr needs --head: the PR\'s head commit')
    url = files_url(repo, run['pr'])
    sources, done = {}, {}

    def act(old, new, blocks):
        if (old, new) not in sources:
            sources[old, new] = {side: [f for p in paths if (f := source(repo, rev, p))]
                                 for side, rev, paths in (('L', base, changed(repo, base, head, old, new, 'L')),
                                                          ('R', head, changed(repo, base, head, old, new, 'R')))}
        # Per change and side: the words of its block on that side, and where its first word is (or, for
        # punctuation, the word after it).
        first = {}
        for b in blocks:
            for side, i in (('L', 1), ('R', 2)):
                ws = []
                for part in b:
                    w = WORD.findall(html.unescape(part[i]).lower())
                    if part[0] and part[i].strip() and (part[0], side) not in first:
                        first[part[0], side] = (ws, len(ws))
                    ws.extend(w)
        for k in {k for k, _ in first}:
            if (old, new, k) not in done:
                side = 'R' if (k, 'R') in first else 'L'
                path, line, href = link(url, sources[old, new][side], side, *first[k, side])
                done[old, new, k] = [(label(path, line, side), KEY, href)]
        return {k: done[old, new, k] for k, _ in first}
    return act


def place(run, old, new, side, before, after):
    """For other plugins, review comments say: where the text `after` starts, `before` being the text of
    its block before it, on that side of the file ('L' the base, 'R' the head), found in the PR's files
    as a change's button is: (path, line, link) as link() returns them; None without --pr."""
    if not run.get('pr') or not run.get('head'):
        return None
    repo, rev = run['repo'], run['base'] if side == 'L' else run['head']
    files = [f for p in changed(repo, run['base'], run['head'], old, new, side) if (f := source(repo, rev, p))]
    ws = WORD.findall(before.lower())
    return link(files_url(repo, run['pr']), files, side, ws + WORD.findall(after.lower()), len(ws))


def label(path, line, side):
    """The button names the file and line, as GitHub highlights the line but does not scroll to it."""
    if not path:
        return LABEL
    return f'{LABEL} {os.path.basename(path)}' + (f':{line}' + (' (old)' if side == 'L' else '') if line else '')


def link(url, files, side, ws, s):
    """Where the word ws[s] comes from, in the file that holds the longest run of the words around it:
    (path, line, link), the line None where no run places the word, and the path too when the spec has
    several files."""
    lo = max(0, s - CONTEXT)
    win, s = ws[lo:s + CONTEXT], s - lo
    best, at = None, (0, 0, 0)
    for f in files:
        f['sm'].set_seq1(win)
        m = f['sm'].find_longest_match()
        if m.size > at[2]:
            best, at = f, m
    if not best or at[2] < min(RUN, len(win)):
        if len(files) != 1:
            return None, None, url
        return files[0]['path'], None, f'{url}#diff-{anchor(files[0]["path"])}'
    # Aligned near the run: the word matched nearest the change's own.
    i, j, _ = at
    a0 = max(0, j - 2 * i - 20)
    ms = [m for m in difflib.SequenceMatcher(None, win, best['words'][a0:j + 2 * (len(win) - i) + 20],
                                             autojunk=False).get_matching_blocks() if m.size]
    a, b, n = min(ms, key=lambda m: max(m.a - s, s - (m.a + m.size - 1), 0))
    line = best['lines'][a0 + b + min(max(s - a, 0), n - 1)]
    return best['path'], line, f'{url}#diff-{anchor(best["path"])}{side}{line}'


def anchor(path):
    return hashlib.sha256(path.encode()).hexdigest()


def files_url(repo, pr):
    slug, n = pr_of(repo, pr)
    return f'https://github.com/{slug}/pull/{n}/files'


def pr_of(repo, pr):
    """The PR's owner/repo and number."""
    m = re.fullmatch(r'(?:https://github\.com/)?(?:([\w.-]+/[\w.-]+)(?:/pull/|#))?#?(\d+)(?:/.*)?', pr.strip())
    if not m:
        sys.exit(f'--pr {pr}: not a PR URL, owner/repo#N or N')
    slug = m.group(1)
    if not slug:
        origin = git(repo, 'remote', 'get-url', 'origin')
        o = re.search(r'github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$', origin)
        if not o:
            sys.exit(f'--pr {pr}: the origin remote is not on GitHub ({origin or "none"}); use owner/repo#N')
        slug = o.group(1)
    return slug, m.group(2)


def remarks(run, out, refresh=None):
    """For other plugins (review.py): the PR's comments on GitHub, read only, {fetched, checked, error, me,
    comments, source, open, refresh, posting, push, resolve} (the last six: GitHub's name, its link's words,
    that ↻ can ask GitHub again, that your 💬 may be posted there, and that push() sends them and resolve()
    resolves threads: only with GITHUB_TOKEN, so without one the page reads GitHub and writes nothing), me the user's login (--me, or GITHUB_TOKEN's user): its line comments, each placed on the diff where it can be (see place_lines()), its own
    comments and its reviews' bodies. A thread's first comment has its thread, {thread (its id), resolved,
    resolved_by}, where GitHub said, which takes a token. Kept in <out>/github.json, which refresh 'auto' (a page load) asks
    GitHub to bring up to date when it is older than FRESH, and 'now' (a button) always; each page is
    asked for with its last ETag, so a list that did not change comes back empty (304), though without a
    token GitHub still counts it against its 60 requests an hour. A token only from GITHUB_TOKEN, never
    kept. None without --pr."""
    if not run.get('pr') or not run.get('head'):
        return None
    f = pathlib.Path(out) / 'github.json'
    with lock:
        cache = json.loads(f.read_text()) if f.exists() else {}
        was = json.dumps(cache, sort_keys=True)
        if run.get('pr_comments'):
            cache = {**cache, 'data': json.loads(pathlib.Path(run['pr_comments']).read_text()), 'error': None,
                     'fetched': cache.get('fetched') or time.time()}
        elif refresh == 'now' or refresh == 'auto' and time.time() - cache.get('checked', 0) > FRESH:
            cache = fetch(run, cache)
            if not run.get('me') and not cache.get('me') and os.environ.get('GITHUB_TOKEN'):
                cache['me'] = whoami()
        # Placed again when the comments, the base or the head are others.
        digest = hashlib.sha1(json.dumps([run['base'], run['head'], cache.get('data')], sort_keys=True).encode()).hexdigest()
        if cache.get('placed_for') != digest and 'data' in cache:
            cs, done = comments(run, out, cache['data'])
            cache.update(comments=cs, placed_for=digest if done else None)
        if json.dumps(cache, sort_keys=True) != was:
            f.with_suffix('.tmp').write_text(json.dumps(cache, ensure_ascii=False))
            f.with_suffix('.tmp').replace(f)
    writes = bool(os.environ.get('GITHUB_TOKEN'))
    # Threads, resolved or not, are kept apart from the comments, so a resolve places nothing again.
    threads = {t['root']: t for t in cache.get('threads') or []}
    cs = [{**c, 'thread': t['id'], 'resolved': t['resolved'], 'resolved_by': t['by']} if c['kind'] == 'line' and (t := threads.get(c['id']))
          else c for c in cache.get('comments', [])]
    return {k: cache.get(k) for k in ('fetched', 'checked', 'error')} | {'me': run.get('me') or cache.get('me'), 'comments': cs,
                                                                         'source': SOURCE, 'open': 'Open on GitHub ↗', 'refresh': True,
                                                                         'posting': True, 'push': writes, 'resolve': writes}


def whoami():
    """GITHUB_TOKEN's user (GET /user), or None when GitHub does not say; kept by remarks(), the token never."""
    ask = {'User-Agent': AGENT, 'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {os.environ["GITHUB_TOKEN"]}'}
    try:
        with urllib.request.urlopen(urllib.request.Request(f'{API}/user', headers=ask), timeout=10) as r:
            return json.load(r).get('login')
    except (urllib.error.URLError, OSError, ValueError):
        return None


def fetch(run, cache):
    """Asks GitHub for the PR's review comments, comments and reviews, page by page, each with its last
    ETag; offline or rate limited, keeps what it had and says why. With a token, also its review threads
    (threads()), whose resolved state the REST API does not give; without one, none."""
    slug, n = pr_of(run['repo'], run['pr'])
    token, pages, data, now = os.environ.get('GITHUB_TOKEN'), dict(cache.get('pages', {})), {}, time.time()
    try:
        for kind, path in (('review', f'pulls/{n}/comments'), ('issue', f'issues/{n}/comments'), ('reviews', f'pulls/{n}/reviews')):
            url, data[kind] = f'{API}/repos/{slug}/{path}?per_page=100', []
            while url:
                was = pages.get(url)
                ask = {'User-Agent': AGENT, 'Accept': 'application/vnd.github+json',
                       **({'If-None-Match': was['etag']} if was and was.get('etag') else {}),
                       **({'Authorization': f'Bearer {token}'} if token else {})}
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers=ask), timeout=10) as r:
                        got = pages[url] = {'etag': r.headers.get('ETag'), 'items': json.load(r),
                                            'next': next(iter(re.findall(r'<([^>]+)>;\s*rel="next"', r.headers.get('Link') or '')), None)}
                except urllib.error.HTTPError as e:
                    if e.code != 304 or not was:
                        raise
                    got = was
                data[kind] += got['items']
                url = got['next']
    except urllib.error.HTTPError as e:
        reset = e.headers.get('X-RateLimit-Reset')
        why = (f'rate limited until {time.strftime("%H:%M", time.localtime(int(reset)))}' if e.headers.get('X-RateLimit-Remaining') == '0'
               and reset else f'GitHub answered {e.code}')
        return {**cache, 'checked': now, 'error': why}
    except (urllib.error.URLError, OSError, ValueError):
        return {**cache, 'checked': now, 'error': 'GitHub not reached (offline?)'}
    ts, why = None, None
    if token:
        try:
            ts = threads(slug, n)
        except Refused as e:
            ts, why = cache.get('threads'), f'resolved threads not read: {e}'
    return {**cache, 'pages': pages, 'data': data, 'threads': ts, 'fetched': now, 'checked': now, 'error': why}


def comments(run, out, data):
    """The PR's comments as the page shows them, and whether every doc could be looked at (a spec waiting
    for its render could not): line comments (outdated ones too, with their old line), with where they
    are when place_lines() finds it, then the PR's own comments and its reviews with a body. Not your
    pending review, nor its comments: GitHub shows them to you alone, so they are not posted yet."""
    who = lambda c: (c.get('user') or {}).get('login') or '?'
    pending = {r['id'] for r in data.get('reviews', []) if r.get('state') == 'PENDING'}
    cs = [{'id': c['id'], 'kind': 'line', 'author': who(c), 'time': c.get('created_at'), 'body': c.get('body') or '',
           'url': c.get('html_url'), 'reply_to': c.get('in_reply_to_id'), 'path': c.get('path'),
           'side': 'L' if c.get('side') == 'LEFT' else 'R', 'line': c.get('line') or c.get('original_line'),
           'outdated': c.get('line') is None} for c in data.get('review', []) if c.get('pull_request_review_id') not in pending]
    cs += [{'id': c['id'], 'kind': 'pr', 'author': who(c), 'time': c.get('created_at'), 'body': c.get('body') or '',
            'url': c.get('html_url')} for c in data.get('issue', [])]
    cs += [{'id': r['id'], 'kind': 'review', 'author': who(r), 'time': r.get('submitted_at'), 'body': r.get('body') or '',
            'url': r.get('html_url'), 'state': r.get('state')} for r in data.get('reviews', [])
           if (r.get('body') or '').strip() and r['id'] not in pending]
    done = place_lines(run, out, [c for c in cs if c['kind'] == 'line' and not c['outdated'] and not c['reply_to'] and c['line']])
    return cs, done


def place_lines(run, out, cs):
    """Puts line comments on the diff, as link() puts a change on a line the other way round: the words of
    the comment's line, with CONTEXT words on each side, are matched against each block of the file's
    diff on that side, and the block whose run of them passes through the line's first word is the
    longest holds it (or, with none, the block with the longest run); aligning the two gives that word
    there. A comment gets its doc (file), that word's change (key, None in unchanged
    text), and, to be found by in any view, the block's text from that word on (quote) and before it.
    False when a doc could not be looked at yet."""
    repo, done = pathlib.Path(run['repo']), True
    for old, new in docdiff.files_for(repo, run['base'], run['head'], run['paths'])[0]:
        o, n, wait = docdiff.htmls(repo, run['base'], run['head'], old, new, pathlib.Path(out))
        if wait:
            done = False
            continue
        pieces = docdiff.block_diff(o, n, 'x')[2]
        for side, rev in (('L', run['base']), ('R', run['head'])):
            srcs = {p: f for p in changed(str(repo), run['base'], run['head'], old, new, side) if (f := source(str(repo), rev, p))}
            mine = [c for c in cs if c['side'] == side and c['path'] in srcs and 'file' not in c]
            blocks = [block_words(b, side) for b in pieces] if mine else []
            for c in mine:
                f = srcs[c['path']]
                at = next((i for i, line in enumerate(f['lines']) if line >= c['line']), None)
                if at is None:
                    continue
                lo = max(0, at - CONTEXT)
                win, s = f['words'][lo:at + CONTEXT], at - lo
                best, score, ms = None, (0, 0), None
                for b in blocks:
                    ys = [y for y in difflib.SequenceMatcher(None, win, b['words'], autojunk=False).get_matching_blocks() if y.size]
                    x = (max((y.size for y in ys if y.a <= s < y.a + y.size), default=0), max((y.size for y in ys), default=0))
                    if x > score:
                        best, score, ms = b, x, ys
                if not best or max(score) < min(RUN, len(win)):
                    continue
                a, j, size = min(ms, key=lambda y: max(y.a - s, s - (y.a + y.size - 1), 0))
                start, part = best['at'][j + min(max(s - a, 0), size - 1)]
                c.update(file=new or old, key=best['keys'][part], quote=' '.join(best['text'][start:].split()[:8]),
                         before=' '.join(best['text'][max(0, start - 300):start].split()))
    return done


def block_words(piece, side):
    """A block of a diff (as block_diff() makes its pieces) on one side: its text as it reads, its words,
    and per word where it starts in the text and in which part, whose key keys has."""
    text, words, at, keys = '', [], [], []
    for i, (k, o, w) in enumerate(piece[2]):
        t = html.unescape(re.sub(r'<!--.*?-->|<[^>]+>', '', o if side == 'L' else w, flags=re.S))
        for m in WORD.finditer(t):
            words.append(m.group(0).lower())
            at.append((len(text) + m.start(), i))
        text += t
        keys.append(k)
    return {'text': text, 'words': words, 'at': at, 'keys': keys}


def changed(repo, base, head, old, new, side):
    """The files a change on that side can come from: the Markdown file itself, or what the PR changes
    in a spec's directory."""
    path = old if side == 'L' else new
    if not path or path.endswith('.md'):
        return [path] if path else []
    d = os.path.dirname(path) or '.'
    return git(repo, 'diff', '--name-only', '--no-renames', base, head, '--', d).splitlines()


def source(repo, rev, path):
    """A file at rev as its words and their lines, and a matcher with it as the second sequence;
    None where it does not exist or is not text."""
    r = subprocess.run(['git', '-C', repo, 'show', f'{rev}:{path}'], capture_output=True)
    if r.returncode or b'\0' in r.stdout:
        return None
    page, md = r.stdout.decode(errors='replace'), path.endswith('.md')
    if md:
        page = subprocess.run(['pandoc', '-f', 'gfm+sourcepos', '-t', 'html', '--wrap=none'], input=page,
                              capture_output=True, text=True).stdout
    words, lines, line = [], [], 1

    def add(t, line):
        t = html.unescape(t)
        for w in WORD.finditer(t):
            words.append(w.group(0).lower())
            lines.append(line + t.count('\n', 0, w.start()))
    for m in re.finditer(r'<!--.*?-->|<[^>]*>|[^<]+', page, re.S):
        t = m.group(0)
        if t[0] != '<':
            add(t, line)
        elif md:
            pos = re.search(r'\bdata-pos="(\d+):', t)
            line = int(pos.group(1)) if pos else line
        else:
            # ReSpec shows a note's or an example's title as its caption.
            for a in re.finditer(r'\stitle="([^"]*)"', t):
                add(a.group(1), line + t.count('\n', 0, a.start()))
        if not md:
            line += t.count('\n')
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq2(words)
    return {'path': path, 'words': words, 'lines': lines, 'sm': sm}


def git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True).stdout.strip()


# Adds a thread to a pending review, which GitHub's REST API cannot do: it only makes a review with its comments.
THREAD = ('mutation($r:ID!,$p:String!,$l:Int!,$s:DiffSide!,$b:String!){addPullRequestReviewThread(input:'
          '{pullRequestReviewId:$r,path:$p,line:$l,side:$s,body:$b}){thread{comments(first:1){nodes{fullDatabaseId}}}}}')


class Refused(Exception):
    """GitHub said no, in its own words, or was not reached."""


def push(run, cs, last, dry=False):
    """For other plugins (review.py): your comments cs as your pending review on the PR, never submitted:
    you submit it on GitHub. With dry, only what it would do, after reading your pending review.
    cs [{id, body, path, line, side, where, quote, pushed}]: where it is in the PR's files (side L or R),
    or for one without a line where it is in the docs and its quote, and pushed what an earlier push made
    of it, {review, id, body}; last what it made the review, {review, body}.

    A comment without a line goes in the review's own text. Your pending review is your review in state
    PENDING, which GitHub shows its author alone, one per PR. Without one, a push starts it (POST .../reviews
    with no event) with every comment; with one, a comment new to it is added to it, one already in it and
    edited here since is updated, and the review's text rewritten, unless edited on GitHub since. One in a
    review you submitted is left out. Returns {plan: [{id, act, why}], pushed: {comment id: pushed}, review:
    {review, body} once there is one, url: where to submit it, error}, act one of add, update, text (in the
    review's text), same (there already, as it reads here) and leave (why says why)."""
    slug, n = pr_of(run['repo'], run['pr'])
    norm, side = lambda t: ' '.join((t or '').split()), {'L': 'LEFT', 'R': 'RIGHT'}
    out = {'plan': [], 'pushed': {}, 'review': None, 'url': files_url(run['repo'], run['pr']), 'error': None}
    if not os.environ.get('GITHUB_TOKEN'):
        return {**out, 'error': 'the docdiff server has no GITHUB_TOKEN, which a push needs'}
    try:
        reviews = pages(f'/repos/{slug}/pulls/{n}/reviews')
        states, pending = {r['id']: r['state'] for r in reviews}, next((r for r in reviews if r['state'] == 'PENDING'), None)
        there = {c['id']: c.get('body') or '' for c in pages(f'/repos/{slug}/pulls/{n}/reviews/{pending["id"]}/comments')} if pending else {}
    except Refused as e:
        return {**out, 'error': str(e)}
    last = last if pending and (last or {}).get('review') == pending['id'] else {}
    adds, edits, texts = [], [], []
    for c in cs:
        p = c.get('pushed') or {}
        act, why = 'add', None
        if states.get(p.get('review'), 'PENDING') != 'PENDING':
            act, why = 'leave', 'in a review you submitted on GitHub: edit it there'
        elif not c.get('path') or not c.get('line'):
            texts.append(c)
            continue
        elif pending and p.get('review') == pending['id'] and p.get('id') in there:
            act, why = (('same', None) if c['body'] == p['body'] else ('update', None) if norm(there[p['id']]) == norm(p['body'])
                        else ('leave', 'edited on GitHub since it was pushed: left as it reads there'))
        elif pending and pending.get('commit_id') != run['head']:
            act, why = 'leave', (f'your pending review is on {pending["commit_id"][:7]}, this page on {run["head"][:7]}: '
                                 'submit or discard it on GitHub, then push again')
        (adds if act == 'add' else edits if act == 'update' else []).append(c)
        out['plan'].append({'id': c['id'], 'act': act, 'why': why})
    # The comments without a line, as the review's text: each its place in the docs, the quoted text and the
    # comment. It is rewritten while it reads as the last push left it.
    text = '\n\n---\n\n'.join(f"**{c['where']}**\n\n" + (f"> {q}\n\n" if (q := short(c.get('quote'))) else '') + c['body'] for c in texts)
    now = norm((pending or {}).get('body'))
    act, why = (('same', None) if pending and now == norm(text) else ('text', None) if not now or now == norm(last.get('body'))
                else ('leave', "the review's text was edited on GitHub: left as it reads there"))
    out['plan'] += [{'id': c['id'], 'act': act, 'why': why} for c in texts]
    writing = bool(texts) and act == 'text'
    if pending:
        out['review'] = {'review': pending['id'], 'body': last.get('body')}
    if dry or not adds and not edits and not writing:
        return out
    try:
        if not pending:
            pending = ask('POST', f'/repos/{slug}/pulls/{n}/reviews', {'commit_id': run['head'], **({'body': text} if writing else {}),
                          'comments': [{'path': c['path'], 'line': c['line'], 'side': side[c['side']], 'body': c['body']} for c in adds]})
            out['review'] = {'review': pending['id'], 'body': text if writing else None}
            got = pages(f'/repos/{slug}/pulls/{n}/reviews/{pending["id"]}/comments')
            for c in adds:
                g = next((g for g in got if g.get('path') == c['path'] and norm(g.get('body')) == norm(c['body'])), None)
                got.remove(g) if g else None
                out['pushed'][c['id']] = {'review': pending['id'], 'id': g and g['id'], 'body': c['body']}
        else:
            for c in edits:
                ask('PATCH', f'/repos/{slug}/pulls/comments/{c["pushed"]["id"]}', {'body': c['body']})
                out['pushed'][c['id']] = {**c['pushed'], 'body': c['body']}
            for c in adds:
                t = ask('POST', '/graphql', {'query': THREAD, 'variables': {'r': pending['node_id'], 'p': c['path'], 'l': c['line'],
                                                                            's': side[c['side']], 'b': c['body']}})
                out['pushed'][c['id']] = {'review': pending['id'], 'body': c['body'], 'id': int(
                    t['data']['addPullRequestReviewThread']['thread']['comments']['nodes'][0]['fullDatabaseId'])}
            if writing:
                ask('PUT', f'/repos/{slug}/pulls/{n}/reviews/{pending["id"]}', {'body': text})
                out['review']['body'] = text
        if act in ('text', 'same'):
            out['pushed'].update({c['id']: {'review': pending['id'], 'body': c['body']} for c in texts})
    except Refused as e:
        out['error'] = str(e)
    return out


# The PR's review threads, page by page, and resolving or reopening one: GraphQL alone has threads.
THREADS = ('query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){'
           'reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor}nodes{id isResolved resolvedBy{login}'
           'comments(first:1){nodes{fullDatabaseId}}}}}}}')
SETTLE = 'mutation($t:ID!){%s(input:{threadId:$t}){thread{isResolved resolvedBy{login}}}}'


def threads(slug, n):
    """The PR's review threads, [{id, root, resolved, by}]: root the id of its first comment, as the REST
    API gives it, and by who resolved it."""
    o, r = slug.split('/')
    out, after = [], None
    while True:
        got = ask('POST', '/graphql', {'query': THREADS, 'variables': {'o': o, 'r': r, 'n': int(n), 'c': after}})
        page = got['data']['repository']['pullRequest']['reviewThreads']
        out += [{'id': t['id'], 'root': int(t['comments']['nodes'][0]['fullDatabaseId']), 'resolved': t['isResolved'],
                 'by': (t.get('resolvedBy') or {}).get('login')} for t in page['nodes'] if t['comments']['nodes']]
        if not page['pageInfo']['hasNextPage']:
            return out
        after = page['pageInfo']['endCursor']


def resolve(run, out, thread, resolved):
    """For other plugins (review.py): resolves the PR's review thread on GitHub, as its Resolve conversation
    does, or with resolved false reopens it, on the user's click alone; the copy in github.json then says
    what GitHub answered. None, or what went wrong."""
    if not os.environ.get('GITHUB_TOKEN'):
        return 'the docdiff server has no GITHUB_TOKEN, which resolving needs'
    verb = 'resolveReviewThread' if resolved else 'unresolveReviewThread'
    try:
        t = ask('POST', '/graphql', {'query': SETTLE % verb, 'variables': {'t': thread}})['data'][verb]['thread']
    except Refused as e:
        return str(e)
    f = pathlib.Path(out) / 'github.json'
    with lock:
        cache = json.loads(f.read_text()) if f.exists() else {}
        for x in cache.get('threads') or []:
            if x['id'] == thread:
                x.update(resolved=t['isResolved'], by=(t.get('resolvedBy') or {}).get('login'))
        f.with_suffix('.tmp').write_text(json.dumps(cache, ensure_ascii=False))
        f.with_suffix('.tmp').replace(f)
    return None


def short(q):
    """A quote of at most 300 characters, cut at a word."""
    q = ' '.join((q or '').split())
    return q[:300].rsplit(' ', 1)[0] + '…' if len(q) > 300 else q


def ask(method, path, data=None):
    """GitHub's API with GITHUB_TOKEN: what it answers, as JSON; Refused, in GitHub's words, when it says no."""
    return call(method, API + path, data)[0]


def pages(path):
    """Every item of a list GitHub gives page by page."""
    items, url = [], f'{API}{path}?per_page=100'
    while url:
        got, url = call('GET', url)
        items += got
    return items


def call(method, url, data=None):
    """What GitHub answers, as JSON, and the link to its next page, if any."""
    r = urllib.request.Request(url, method=method, data=None if data is None else json.dumps(data).encode(),
                               headers={'User-Agent': AGENT, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                                        'Authorization': f'Bearer {os.environ["GITHUB_TOKEN"]}'})
    try:
        with urllib.request.urlopen(r, timeout=20) as r:
            got, more = json.load(r), next(iter(re.findall(r'<([^>]+)>;\s*rel="next"', r.headers.get('Link') or '')), None)
    except urllib.error.HTTPError as e:
        try:
            m = json.load(e)
            why = '; '.join(filter(None, [m.get('message')] + [x if isinstance(x, str) else x.get('message') or json.dumps(x)
                                                                for x in m.get('errors') or []]))
        except ValueError:
            why = ''
        raise Refused(f'GitHub answered {e.code}' + (f': {why}' if why else ''))
    except (urllib.error.URLError, OSError, ValueError):
        raise Refused('GitHub not reached (offline?)')
    # GraphQL answers 200, with what went wrong in errors.
    if isinstance(got, dict) and got.get('errors'):
        raise Refused('GitHub answered: ' + '; '.join(x.get('message') or json.dumps(x) for x in got['errors']))
    return got, more
