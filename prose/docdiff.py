#!/usr/bin/env python3
"""Render a repo's uncommitted Markdown changes against a base revision as one inline-diff page.

Usage: docdiff.py [--repo PATH] [--base REV] [--head REV] [--name NAME] [--out DIR] [--new-label TEXT]
                  [--publish CMD] [--reviewed-at REV] [--serve] [--port N] [plugins' options] [paths...]
       docdiff.py --umbrella | --restart | --stop | --install-agent [--port N]

paths are pathspecs relative to the repo root (default: every *.md, and the files plugins make docs of).

Old = the file at REV (default HEAD); new = the file at --head, or without it the working tree,
staged or not, untracked included. Each review is named (--name; by default the repo's name, with --pr
the repo's name and the PR's number) and keeps its page and state in ~/.docdiff/<name>, or in --out, which
~/.docdiff/<name> then links to. One server, the umbrella server, serves every review in ~/.docdiff under
/<name>/ on 127.0.0.1:7878 (--port), with an index of them at /. With --serve, the review is built,
the umbrella server started if it is not running, and "URL: ..." printed. The server stores what the page
sends in the review's dir: accepted/<file>.html, the file as accepted so far, which tells which changes
are still to review; revisit requests in revisit.jsonl. When the head or the base moves, the accepted copy is carried
over by where its changes are, and what changed since the last review is new, with a blue edge (see
accepted_html(), next_round()); --reviewed-at REV makes REV the version last reviewed. It runs docdiff's
code as it is on disk at each request; --restart restarts it (needed only after a change to the server
itself), --stop stops it, and --install-agent prints a LaunchAgent that keeps it running.

The page's header says what is compared (a plugin's title(), or else commits or the uncommitted edits)
and names both sides: a revision with its commit, or the working tree; --new-label TEXT names the new
side instead, for a working tree that stands for something else.

--publish CMD puts "publish accepted" in the header while any file has accepted changes; it publishes
each such file in turn. For each, it writes the accepted copy as Markdown and runs CMD in a shell, {file} replaced by the file's name and {target} by
that Markdown's path; CMD is to publish it and bring the base up to date, after which the accepted copy
starts over from the base. gdocsync's push --target, then its export, is such a command.

Plugins, the modules in plugins/, add their own arguments, docs and buttons (see load_plugins):
respec.py diffs ReSpec specs, gdoc_comments.py shows a Google Doc's comments (--comments), github_pr.py
links changes to a GitHub PR (--pr), review.py keeps review comments and shows revisits' answers.
"""
import argparse, base64, difflib, hashlib, html, http.server, importlib.util, json, mimetypes, os, pathlib, plistlib
import re, shlex, shutil, signal, subprocess, sys, threading, time, traceback, urllib.parse, urllib.request, webbrowser

# Every review's dir, <name>, and the clones of PRs' repos (clones/); the umbrella server's log. DOCDIFF_HOME
# puts them elsewhere, for a test server that must not see the reviews in use.
HOME = pathlib.Path(os.environ.get('DOCDIFF_HOME') or pathlib.Path.home() / '.docdiff').expanduser()
# The umbrella server's port, so a review's address outlives restarts.
PORT = 7878
NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*')
# The LaunchAgent that keeps the umbrella server running (see agent()).
AGENT = 'com.asurkov.docdiff'

# Tags, entities, words and single punctuation marks, each with its trailing space: "nothing," and
# "nothing" share the word.
TOKEN = re.compile(r"<[^>]+>|&#?\w+;\s*|\w+(?:['’]\w+)*\s*|[^\w\s<]\s*|\s+")
BLOCK = re.compile(r'</?(?:p|li|ul|ol|h\d|tr|td|th|table|pre|blockquote|div)\b')
# Edits this few words apart in one block are one change to step to.
MERGE_WORDS = 3
# Unchanged words on each side that go into a change's key, so an accepted change comes back when
# the text right around it is edited, and not when the doc changes elsewhere.
CONTEXT = 4
# Word similarity of an old and a new block: at REWRITE or above the block is word-diffed; below it
# the block shows as rewritten, its old text folded above the new; below PAIR the two are not matched
# and show as one removed block and one added.
REWRITE = 0.5
PAIR = 0.3
SHORT = 6
# Removed or rewritten text longer than this many words starts folded; shorter, it shows open.
FOLD = 200
TAG = re.compile(r'<(/?)([a-zA-Z][\w-]*)[^>]*?(/?)>')
VOID = {'area', 'br', 'col', 'hr', 'img', 'input', 'wbr'}
# Elements that sit in a line of text: bare text between blocks, with these in it, is a block of its own.
INLINE = {'a', 'abbr', 'b', 'bdi', 'bdo', 'br', 'cite', 'code', 'data', 'del', 'dfn', 'em', 'i', 'img', 'ins', 'kbd',
          'mark', 'q', 's', 'samp', 'small', 'span', 'strong', 'sub', 'sup', 'time', 'u', 'var', 'wbr'}
COMMENT = re.compile(r'<!--.*?-->', re.S)
# A ReSpec algorithm's wrapper, which the diff sees through: its steps are items of its list.
ALGORITHM = re.compile(r'<div\s(?:[^>]*?\s)?class="(?:[^"]*\s)?algorithm[\s"]')


def git(repo, *args, check=True):
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if check and r.returncode:
        sys.exit(f'git {" ".join(args)}: {r.stderr.decode().strip()}')
    return r


def side(repo, rev):
    """One side of the diff: the revision in words (a hash short, a name with its short hash) and its
    commit's subject; for the working tree, its words and its path."""
    if not rev:
        return 'working tree', str(repo).replace(str(pathlib.Path.home()), '~', 1)
    h, _, s = git(repo, 'log', '-1', '--format=%h%n%s', rev).stdout.decode().strip().partition('\n')
    return (h if re.fullmatch(r'[0-9a-f]{7,40}', rev) else f'{rev} ({h})'), s


def read(repo, rev, path):
    """A file at rev, or in the working tree with rev None; None where it does not exist."""
    if rev is None:
        p = repo / path
        return p.read_bytes() if p.is_file() else None
    r = git(repo, 'show', f'{rev}:{path}', check=False)
    return r.stdout if r.returncode == 0 else None


def changed_files(repo, base, head, paths):
    """Changed Markdown files and plugins' docs, ReSpec specs say: [(old path or None, new path or None)]."""
    spec = ['--', *(paths or ['*.md', *(p for m in PLUGINS for p in getattr(m, 'PATHS', []))])]
    out = git(repo, 'diff', '--name-status', '--find-renames', base, *([head] if head else []), *spec).stdout.decode()
    names = []
    for line in out.splitlines():
        status, *ns = line.split('\t')
        names.append((None if status == 'A' else ns[0], None if status == 'D' else ns[-1]))
    if not head:
        untracked = git(repo, 'ls-files', '--others', '--exclude-standard', *spec).stdout.decode()
        names += [(None, f) for f in untracked.splitlines()]
    files, docs, memo = [], set(), {}
    for old, new in names:
        name = new or old
        if name.endswith('.md'):
            files.append((old, new))
            continue
        # Any other file stands for the doc a plugin makes of it, if one does.
        doc = next(filter(None, (m.doc(repo, (base, head), name, memo) for m in PLUGINS if hasattr(m, 'doc'))), None)
        if doc and doc not in docs:
            docs.add(doc)
            files.append(tuple(doc if read(repo, rev, doc) is not None else None for rev in (base, head)))
    return files


def render(markdown):
    r = subprocess.run(['pandoc', '-f', 'gfm', '-t', 'html'], input=markdown, capture_output=True)
    if r.returncode:
        sys.exit(f'pandoc: {r.stderr.decode().strip()}')
    return r.stdout.decode()


def inline_images(page, repo, rel_dir, base, from_tree):
    """Embed relative images as data URIs: the page is served from outside the repo."""
    def repl(m):
        src = m.group(2)
        if re.match(r'[a-z]+:|/|#', src):
            return m.group(0)
        path = os.path.normpath(os.path.join(rel_dir, src))
        data = None
        if from_tree and (repo / path).is_file():
            data = (repo / path).read_bytes()
        else:
            r = git(repo, 'show', f'{base}:{path}', check=False)
            data = r.stdout if r.returncode == 0 else None
        if data is None:
            return m.group(0)
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        return f'{m.group(1)}data:{mime};base64,{base64.b64encode(data).decode()}{m.group(3)}'
    return re.sub(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', repl, page)


def is_tag(t):
    return t.startswith('<')


def words(tokens):
    return [t.strip() for t in tokens if not is_tag(t) and t.strip()]


def nesting(page):
    """What a piece of a block does to its nesting: its block tags, less each open tag its close tag
    follows and each close tag an open tag of the same name follows (an item split in two)."""
    out = []
    for m in TAG.finditer(page):
        if not BLOCK.match(m.group(0)):
            continue
        t = (m.group(1), m.group(2).lower())
        if out and out[-1][1] == t[1] and out[-1][0] != t[0]:
            out.pop()
        else:
            out.append(t)
    return out


def diff(old, new, gid):
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    key = lambda t: t if is_tag(t) else t.strip()
    sm = difflib.SequenceMatcher(None, [key(t) for t in a], [key(t) for t in b], autojunk=False)
    out, n, split = [], 0, True
    # Changes with text to show; one that only changes a tag, a link target say, renders as nothing.
    shown = set()
    # Per change: CONTEXT words before it, what it removes and adds, the unchanged words inside it,
    # and CONTEXT words after it. Hashed, that is the key an accept is stored under.
    sig, last = {}, []
    # (group number, old text, new text) per opcode, 0 for unchanged text: what applying one change needs.
    parts = []

    def wrap(tokens, tag, keep_tags):
        run = []

        def flush():
            if ''.join(run).strip():
                out.append(f'<{tag} class="chg" data-g="{gid}-{n}">{"".join(run)}</{tag}>')
                shown.add(n)
            else:
                out.extend(run)
            run.clear()
        for t in tokens:
            if is_tag(t):
                flush()
                if keep_tags:
                    out.append(t)
            else:
                run.append(t)
        flush()

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            seg = b[j1:j2]
            text = words(seg)
            count = sum(1 for t in seg if not is_tag(t) and re.match(r'\w', t))
            split = split or count > MERGE_WORDS or any(BLOCK.match(t) for t in seg)
            if n:
                sig[n] += ['|', *text[:CONTEXT]] if split else ['=', *text]
            last = text
            out.extend(seg)
            parts.append((0, ''.join(a[i1:i2]), ''.join(seg)))
            continue
        if split:
            n, split = n + 1, False
            sig[n] = [*last[-CONTEXT:], '|']
        sig[n] += ['-', *words(a[i1:i2]), '+', *words(b[j1:j2])]
        parts.append((n, ''.join(a[i1:i2]), ''.join(b[j1:j2])))
        if i2 > i1:
            wrap(a[i1:i2], 'del', False)
        if j2 > j1:
            wrap(b[j1:j2], 'ins', True)
    # A change that opens block tags a later one closes (list items folded into a paragraph, say) is one
    # change with it: accepted alone, either would leave the accepted copy's tags unbalanced.
    olds, news, head, merged = {}, {}, None, {}
    for g, x, y in parts:
        if g:
            olds[g], news[g] = olds.get(g, '') + x, news.get(g, '') + y
    acc = ['', '']
    for g in sorted(sig):
        head = head or g
        merged[g] = head
        acc = [acc[0] + olds[g], acc[1] + news[g]]
        if nesting(acc[0]) == nesting(acc[1]):
            head, acc = None, ['', '']
    sigs = {}
    for g in sorted(sig):
        sigs.setdefault(merged[g], []).extend(sig[g])
    keys = {g: hashlib.sha1(' '.join(sigs[merged[g]]).encode()).hexdigest()[:12] for g in sig}
    page = re.sub(rf'data-g="{gid}-(\d+)"',
                  lambda m: f'data-g="{gid}-{merged[int(m.group(1))]}" data-k="{keys[int(m.group(1))]}"',
                  ''.join(out))
    return page, len({merged[g] for g in shown}), [(keys.get(g), x, y) for g, x, y in parts]


class ListTag(str):
    """The opening tag of the list a block is an item of, with the item's number in that list (n), so a
    list that reopens after a change boxed on its own goes on counting from there."""
    def __new__(cls, tag, n):
        self = super().__new__(cls, tag)
        self.n = n
        return self


def split_blocks(page):
    """pandoc HTML as its top-level blocks, each list item a block of its own, and so is bare text between
    blocks, with its inline elements: [(list tag or '', html)]. A ReSpec algorithm (ALGORITHM) is seen through
    and left out, as the render leaves out sections: its steps are items of a list, and text that opens it
    outside a paragraph is bare text. So the pages apply() puts back together have no such wrappers."""
    out, depth, start, lst, n, bare, gap, algorithms = [], 0, None, '', 1, None, 0, 0

    def end_bare(at):
        nonlocal bare
        if bare is not None and COMMENT.sub('', page[bare:at]).strip():
            out.append(('', page[bare:at]))
        bare = None
    for m in TAG.finditer(page):
        closing, name = m.group(1), m.group(2).lower()
        top = 1 if lst else 0
        # Between top-level blocks, text or an inline element starts bare text, and anything else ends it.
        loose = depth == 0 and start is None and not lst
        inline = name in INLINE and not closing
        if loose and bare is None and (inline or COMMENT.sub('', page[gap:m.start()]).strip()):
            bare = gap
        gap = m.end()
        if loose and not inline:
            end_bare(m.start())
            if closing and name == 'div' and algorithms:
                algorithms -= 1
                continue
            if ALGORITHM.match(m.group(0)):
                algorithms += 1
                continue
        if name in VOID or m.group(3):
            if depth == top and start is None and bare is None:
                out.append((lst, m.group(0)))
            continue
        if not closing:
            if depth == 0 and not lst and name in ('ul', 'ol'):
                lst = m.group(0)
                first = re.search(r'\sstart="?(-?\d+)', lst)
                n = int(first.group(1)) if first else 1
            elif depth == top and start is None and bare is None:
                start = m.start()
            depth += 1
            continue
        depth -= 1
        if depth == top and start is not None:
            out.append((ListTag(lst, n) if lst else lst, page[start:m.end()]))
            n += name == 'li'
            start = None
        elif depth == 0 and lst:
            lst = ''
    if depth == 0 and start is None and not lst and bare is None and COMMENT.sub('', page[gap:]).strip():
        bare = gap
    end_bare(len(page))
    return out


def text(block):
    return ' '.join(words(TOKEN.findall(block))) if block else ''


def plain_words(block):
    """A block's words, lowercased, punctuation left out."""
    return [w.lower() for w in text(block).split() if re.match(r'\w', w)]


def similarity(a, b):
    """Share of words two blocks have in common, punctuation left out. A block of a few words, a
    heading say, has to keep most of them to count as similar at all."""
    return likeness(plain_words(a), plain_words(b))


def likeness(wa, wb):
    """similarity() of two blocks' plain_words(); 0 for any share at or below PAIR."""
    if wa == wb:
        return 1.0 if wa else 0
    sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
    # The quick bounds first: most blocks of a long replaced run are nothing alike.
    if sm.real_quick_ratio() <= PAIR or sm.quick_ratio() <= PAIR:
        return 0
    r = sm.ratio()
    return r if r > PAIR and (min(len(wa), len(wb)) >= SHORT or r >= REWRITE) else 0


def pair(olds, news):
    """Match the blocks of a replaced run, keeping their order: [(old index or None, new index or None)].
    Of all the ways to, the one whose matches are the most alike in all, each counting by how far it is
    above PAIR: so a block keeps its own match rather than give it up for a weak one further on, and an
    item inserted in a list leaves the items after it matched with themselves. Between two matches, the
    removed blocks come before the added ones, as the old text came first."""
    wo, wn, n, m = [plain_words(b) for b in olds], [plain_words(b) for b in news], len(olds), len(news)
    # most[i][j]: the most that olds[i:] and news[j:] match for.
    gain, most = {}, [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            r = likeness(wo[i], wn[j])
            if r:
                gain[i, j] = r - PAIR
            most[i][j] = max(most[i + 1][j], most[i][j + 1], most[i + 1][j + 1] + gain[i, j] if r else 0)
    out, gone, added, i, j = [], [], [], 0, 0
    while i < n and j < m:
        if (i, j) in gain and most[i][j] == most[i + 1][j + 1] + gain[i, j]:
            out += gone + added + [(i, j)]
            gone, added, i, j = [], [], i + 1, j + 1
        elif most[i][j] == most[i + 1][j]:
            gone.append((i, None))
            i += 1
        else:
            added.append((None, j))
            j += 1
    return out + gone + [(k, None) for k in range(i, n)] + added + [(None, k) for k in range(j, m)]


def box_of(kinds):
    """The box of whole_blocks() each block of a run goes in, by the blocks' kinds ('del', 'add' or 'rw'):
    consecutive removed blocks share one, and so do consecutive added ones."""
    out, n = [], -1
    for i, kind in enumerate(kinds):
        n += not (i and kind in ('del', 'add') and kinds[i - 1] == kind)
        out.append(n)
    return out


def whole_blocks(run, gid):
    """A run of added, removed or rewritten blocks [(list tag, kind, old, new)] as one change under one
    key. Old text sits under a one-line summary, open when it is short. Consecutive removed blocks share
    one box, and so do consecutive added ones, paragraphs and list items alike."""
    key = hashlib.sha1('|'.join(f'{k}|{text(o)}|{text(w)}' for _, k, o, w in run).encode()).hexdigest()[:12]
    inner = lambda b: re.sub(r'^<li\b[^>]*>|</li>$', '', b)
    merged = []
    for (lst, kind, old, new), box in zip(run, box_of([kind for _, kind, _, _ in run])):
        if box < len(merged):
            merged[-1][2 if kind == 'del' else 3].append((lst, old or new))
        else:
            merged.append((lst, kind, [(lst, old)] if old else [], [(lst, new)] if new else []))
    out = []
    for lst, kind, olds, news in merged:
        # Several blocks go in one box outside any list, so one bar marks them all; so does an item removed
        # from an ordered list, where it would take a number from the items after it.
        lst = '' if len(olds) > 1 or len(news) > 1 or kind == 'del' and lst.startswith('<ol') else lst
        body = lambda bs: inner(bs[0][1]) if lst else wrap_lists(bs)
        was = ''
        if olds:
            lsts = {l for l, _ in olds}
            head = ' '.join(html.unescape(re.sub(r'<[^>]+>', lambda m: ' ' if BLOCK.match(m.group(0)) else '', olds[0][1])).split())
            head = head if len(head) <= 80 else head[:80].rsplit(' ', 1)[0] + '…'
            label = ('was' if kind == 'rw' else 'removed' if len(olds) == 1
                     else f'removed {len(olds)} {"paragraphs" if lsts == {""} else "items" if "" not in lsts else "blocks"}')
            short = len(' '.join(text(o) for _, o in olds).split()) <= FOLD
            was = (f'<details class="was"{" open" if short else ""}><summary>{label}: {html.escape(head)}</summary>'
                   f'<div class="old">{body(olds)}</div></details>')
        now = f'<div class="now">{body(news)}</div>' if news else ''
        tag = 'li' if lst else 'div'
        out.append((lst, f'<{tag} class="chg blk" data-kind="{kind}" data-g="{gid}" data-k="{key}">{was}{now}</{tag}>'))
    return out, key


def block_diff(old, new, gid):
    """Diff block by block (paragraph, heading, list item, table...): unchanged blocks plain, added,
    removed and rewritten ones marked whole, and only a lightly edited one word by word. Also returns
    the pieces apply() takes: per block, its list tag on each side and [(change key or None, old, new)]."""
    a, b = split_blocks(old), split_blocks(new)
    norm = lambda h: re.sub(r'\s+', ' ', h).strip()
    sm = difflib.SequenceMatcher(None, [norm(h) for _, h in a], [norm(h) for _, h in b], autojunk=False)
    # Bare text (see split_blocks()) shows in a paragraph, where the page finds it as a block.
    para = lambda h, body: body if re.match(r'<(?!(?:%s)\b)\w' % '|'.join(INLINE), h) else f'<p>{body}</p>'
    items, pieces, n = [], [], 0
    run, sides = [], []

    def flush():
        nonlocal n
        if run:
            # A change that adds or removes blocks reads as one rewrite: all its old text, then all its new
            # text. Only a change of one-to-one rewrites keeps each old block above its new one.
            if any(k != 'rw' for _, k, _, _ in run):
                both = list(zip(run, sides))
                run[:] = ([(s[0], 'del', r[2], None) for r, s in both if r[2]] +
                          [(s[1], 'add', None, r[3]) for r, s in both if r[3]])
                sides[:] = ([(s[0], '', s[2], '') for r, s in both if r[2]] +
                            [('', s[1], '', s[3]) for r, s in both if r[3]])
            out, key = whole_blocks(run, f'{gid}-b{len(items)}')
            items.extend(out)
            pieces.extend((lo, lw, [(key, o, w)]) for lo, lw, o, w in sides)
            n += 1
            run.clear()
            sides.clear()

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            items += [(lst, para(h, h)) for lst, h in b[j1:j2]]
            pieces += [(x[0], y[0], [(None, x[1], y[1])]) for x, y in zip(a[i1:i2], b[j1:j2])]
            continue
        for i, j in pair([h for _, h in a[i1:i2]], [h for _, h in b[j1:j2]]):
            o = a[i1 + i] if i is not None else None
            w = b[j1 + j] if j is not None else None
            lst = (w or o)[0]
            if o and w and similarity(o[1], w[1]) >= REWRITE:
                flush()
                body, k, parts = diff(o[1], w[1], f'{gid}-b{len(items)}')
                items.append((lst, para(w[1], body)))
                pieces.append((o[0], w[0], parts))
                n += k
            else:
                # A new section starts a new change.
                if run and w and re.match(r'<h\d', w[1]):
                    flush()
                run.append((lst, 'rw' if o and w else 'add' if w else 'del', o and o[1], w and w[1]))
                sides.append((o[0] if o else '', w[0] if w else '', o[1] if o else '', w[1] if w else ''))
        flush()
    return wrap_lists(items), n, pieces


def wrap_lists(items, shown=True):
    """Blocks [(list tag or '', html)] back into one page: consecutive items of one list go back into it.
    Shown (on the page, not compared), an ordered list opened again around a change starts at its first
    item's number, and a line parts two blocks, so that text read across them has a space between."""
    out, open_ = [], ''
    for lst, h in items:
        if not h:
            continue
        if lst != open_:
            out += [f'</{open_[1:3]}>'] if open_ else []
            out += [numbered(lst) if shown else lst] if lst else []
            open_ = lst
        out.append(h)
    out += [f'</{open_[1:3]}>'] if open_ else []
    return ('\n' if shown else '').join(out)


def numbered(lst):
    """An ordered list's opening tag, starting at the number of the item it carries (ListTag)."""
    if not lst.startswith('<ol') or not hasattr(lst, 'n'):
        return lst
    tag = re.sub(r'\sstart="?-?\d+"?', '', lst)
    return tag if lst.n == 1 else f'<ol start="{lst.n}"{tag[3:]}'


def apply(pieces, side, flip):
    """The page that block_diff() compared, with every change from `side` ('old' or 'new') except the
    changes whose keys are in `flip`, which come from the other side."""
    new = lambda k: (side == 'new') != (k in flip)
    items = []
    for lst_old, lst_new, parts in pieces:
        text = []
        for i, (k, o, w) in enumerate(parts):
            t = w if new(k) else o
            # Unchanged text before a change ends in the spacing of whichever side that change takes:
            # "End " before an inserted word, "End" before a deleted one.
            if k is None and i + 1 < len(parts):
                src = w if new(parts[i + 1][0]) else o
                t = t.rstrip() + src[len(src.rstrip()):]
            text.append(t)
        keyed = [k for k, _, _ in parts if k]
        items.append((lst_new if new(keyed[0] if keyed else None) else lst_old, ''.join(text)))
    return wrap_lists(items, shown=False)


def news(last, new_html, k, diffed):
    """What changed since the last review (last, rendered): each change of base to head it touches, with the
    changes of last to now that touch it, {key: [since keys]}; of such a change in several boxes of whole
    blocks (whole_blocks()), the boxes they touch, where not all of them, {key: [box]}; and the blocks of
    last to now that changed, for the page to show what changed in a change since its last review."""
    since = block_diff(last, new_html, f's{k}')
    hit, fresh, touched, spans = [(c, s, e) for c, _, _, s, e in places(since) if c], {}, {}, by_key(places(diffed))
    for c, ss in spans.items():
        got = list(dict.fromkeys(h for h, s, e in hit if meets(ss, [(s, e)])))
        if got:
            fresh[c] = got
    # A change of whole blocks has one part per block, in its run's order; a key the page shows in another
    # number of boxes (two runs alike) keeps its edge on all of them. A box takes what shares text with it
    # (overlaps()): blocks added next to a removed one leave it alone.
    page, _, pieces = diffed
    shown = re.findall(r'class="chg blk"[^>]*data-k="(\w+)"', page)
    for c, got in fresh.items():
        box = box_of(['rw' if o and w else 'del' if o else 'add' for _, _, parts in pieces for g, o, w in parts if g == c])
        if not 1 < shown.count(c) == len(set(box)):
            continue
        on = sorted({b for b, s in zip(box, spans[c]) if overlaps([s], [(x, y) for h, x, y in hit if h in got])})
        if len(on) < len(set(box)):
            touched[c] = on
    return fresh, touched, wrap_lists([b for b in split_blocks(since[0]) if 'data-k="' in b[1]])


def states(diffed, reviewing, reviewed):
    """Where each change of base to head stands: {key: (to review, reviewed)}, the keys of the changes of the
    accepted copy to head at its place (what is still to review there; none once it is reviewed) and of base
    to the copy (what is reviewed). Copy to head is laid over it on head, the side they share; base to copy
    in the copy, the change's text traced there through what copy to head left alone, or else, for removed
    text between two changes still to review, on base, the side those two share."""
    rv, rd, spans, out = places(reviewing), places(reviewed), {}, {}
    for k, o0, o1, n0, n1 in places(diffed):
        if k:
            spans.setdefault(k, []).append(((o0, o1), (n0, n1)))
    for k, ss in spans.items():
        base, head = [b for b, _ in ss], [h for _, h in ss]
        copy = [(across(rv, s), across(rv, e)) for s, e in head]
        on_base = any(None in x for x in copy)
        out[k] = (sorted({r for r, _, _, s, e in rv if r and overlaps(head, [(s, e)])}),
                  sorted({r for r, b0, b1, c0, c1 in rd if r and overlaps(base if on_base else copy, [(b0, b1) if on_base else (c0, c1)])}))
    return out


def places(diffed):
    """Where the parts of a block_diff() are: [(change key or None, old start, old end, new start, new end)],
    in tokens of each side's blocks put together, so two diffs with the same new side (a review round's and
    the PR's) can be laid over each other. A change with nothing to show (a link target) counts as none."""
    page, _, pieces = diffed
    shown, out, o, n = set(re.findall(r'data-k="(\w+)"', page)), [], 0, 0
    for _, _, parts in pieces:
        for k, a, b in parts:
            da, db = len(TOKEN.findall(a)), len(TOKEN.findall(b))
            out.append((k if k in shown else None, o, o + da, n, n + db))
            o, n = o + da, n + db
    return out


def meets(xs, ys):
    """Whether any stretch [start, end) of xs meets one of ys: they overlap, or an empty one (where text was
    removed) is inside the other or at its ends."""
    def one(a, b):
        if a[0] == a[1]:
            return b[0] <= a[0] <= b[1]
        if b[0] == b[1]:
            return a[0] <= b[0] <= a[1]
        return a[0] < b[1] and b[0] < a[1]
    return any(one(x, y) for x in xs for y in ys)


def overlaps(xs, ys):
    """Whether a stretch of xs and one of ys share text, or an empty one is strictly inside the other, or two
    empty ones are at one place: unlike meets(), stretches that only touch at an end do not."""
    def one(a, b):
        e, f = (a, b) if a[0] == a[1] else (b, a)
        if e[0] != e[1]:
            return a[0] < b[1] and b[0] < a[1]
        return e == f if f[0] == f[1] else f[0] < e[0] < f[1]
    return any(one(x, y) for x in xs for y in ys)


def across(ps, x):
    """Where position x of a diff's new side is on its old side, through the text the diff left alone; None
    inside a change."""
    for k, o0, o1, n0, n1 in ps:
        if not k and n0 <= x <= n1:
            return min(o0 + x - n0, o1)
    return None


def by_key(ps, side=3):
    """The stretches of each change of a diff on one side (3 new, 1 old): {key: [(start, end)]}."""
    out = {}
    for p in ps:
        if p[0]:
            out.setdefault(p[0], []).append((p[side], p[side + 1]))
    return out


def carry(copy, seen, old_html, new_html):
    """The accepted copy once the head (or the base) has moved from seen: each change of base to head that
    the move left alone, and that was reviewed at seen, stays reviewed, found by where it is (its text
    traced back through what the move left alone) rather than by its words; one the move touched comes
    back to review, whole, and so does one that was still to review."""
    moved = places(block_diff(seen, new_html, 'x'))
    hit = [(s, e) for k, _, _, s, e in moved if k]
    todo = [(s, e) for k, _, _, s, e in places(block_diff(copy, seen, 'x')) if k]
    diffed = block_diff(old_html, new_html, 'x')
    keep = set()
    for k, ss in by_key(places(diffed)).items():
        then = [(across(moved, s), across(moved, e)) for s, e in ss]
        if not meets(ss, hit) and all(None not in t for t in then) and not meets(then, todo):
            keep.add(k)
    return apply(diffed[2], 'old', keep)


# The actions' words. On your own docs (the working tree, or a Google Doc's export) an accept takes a change
# into the accepted copy and a revisit asks for an edit; on commits (--head: a PR, say) there is nothing to
# edit, so an accept only records that the change was reviewed, and a revisit is a question about it.
WORDS = {
    False: {'accept': '✓ accept', 'unaccept': '↺ unaccept', 'accept_key': 'Accept (a)', 'unaccept_key': 'Unaccept (a)',
            'accept_title': 'Accept the current change (a)', 'unaccept_title': 'Unaccept the current change (a)',
            'accepted': 'accepted', 'Accepted': 'Accepted', 'accept_file': 'accept file', 'unaccept_file': 'unaccept file',
            'revisit': '✎ revisit', 'revisit_key': 'Revisit (r)', 'revisit_title': 'Revisit the selection or the current change (r)',
            'select': 'select text to revisit', 'question': 'What should change?', 'prompt': 'Revisit ', 'sent': 'revisit',
            'revisits': 'revisits'},
    True: {'accept': '✓ looks good', 'unaccept': '↺ not reviewed', 'accept_key': 'Looks good (a)', 'unaccept_key': 'Not reviewed (a)',
           'accept_title': 'Looks good: the current change is reviewed (a)',
           'unaccept_title': 'Not reviewed: the current change goes back to review (a)',
           'accepted': 'reviewed', 'Accepted': 'Reviewed', 'accept_file': 'file looks good', 'unaccept_file': 'file not reviewed',
           'revisit': '✎ ask', 'revisit_key': 'Ask (r)', 'revisit_title': 'Ask Claude about the selection or the current change (r)',
           'select': 'select text to ask about', 'question': 'What do you want to know?', 'prompt': 'Ask about ', 'sent': 'ask',
           'revisits': 'questions',
           # For a change that carries remarks for its author, which a plugin tells (see wordsOf).
           'reviewed': '✓ reviewed', 'reviewed_key': 'Reviewed (a)',
           'reviewed_title': 'Reviewed: you commented on it, so it leaves To review without saying it looks good (a)'},
}

STYLE = '''
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--ins:#dafbe1;--insb:#1a7f37;--del:#ffebe9;--delb:#cf222e;--rw:#bf8700;--code:#f6f8fa;--link:#0969da;--btn:#f6f8fa;--btnh:#eff2f5;--new:#0969da}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;--ins:#033a16;--insb:#3fb950;--del:#4b1113;--delb:#f85149;--rw:#d29922;--code:#161b22;--link:#4493f8;--btn:#212830;--btnh:#262c36;--new:#4493f8}}
body{background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0}
main{max-width:920px;margin:0 auto;padding:16px 16px 40vh;overflow-wrap:anywhere}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 16px;z-index:1;display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:14px}
.what a,.files a{color:var(--fg);font-weight:600} .n{color:var(--muted)}
.what{display:flex;align-items:baseline;gap:10px;padding:10px 16px 0;font-size:13px;white-space:nowrap} .what b{flex:none;font-size:15px}
.what .n{min-width:0;overflow:hidden;text-overflow:ellipsis} .files{padding:2px 16px 8px;font-size:14px} .files:empty{display:none}
button:disabled{opacity:.4;cursor:default}
button{font:inherit;padding:4px 10px;border:1px solid var(--line);background:var(--btn);color:var(--fg);border-radius:6px;cursor:pointer;transition:background-color .1s}
button:not(:disabled):hover{background:var(--btnh)} button:focus-visible{outline:2px solid var(--link);outline-offset:-2px}
.file{margin:48px 0 8px;padding:6px 10px;background:var(--code);border:1px solid var(--line);border-radius:6px;font:600 14px ui-monospace,Menlo,monospace;display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
.fbar{margin-left:auto;display:flex;flex-wrap:wrap;gap:6px}
.state{color:var(--muted);font-weight:400}
.accf{font:12px -apple-system,BlinkMacSystemFont,sans-serif;padding:1px 8px}
ins{background:var(--ins);text-decoration:none;box-shadow:inset 0 -2px var(--insb)}
del{background:var(--del);color:var(--delb);text-decoration:line-through}
.chg{cursor:pointer}
.blk{border-left:3px solid var(--line);padding-left:10px;margin-left:-13px} li.blk{margin-left:0;padding-left:6px}
.blk[data-kind=add]{border-color:var(--insb)} .blk[data-kind=del]{border-color:var(--delb)} .blk[data-kind=rw]{border-color:var(--rw)}
.blk[data-kind=del] .was .old{border-left:0;padding-left:0}
.blk.fresh{border-left-color:var(--new)} .fresh-b{box-shadow:-10px 0 0 var(--bg),-13px 0 0 var(--new)} li.fresh-b,td.fresh-b,th.fresh-b{box-shadow:inset 3px 0 var(--new);padding-left:9px}
#fresh::before,.nw::before{content:'';display:inline-block;width:8px;height:8px;margin-right:6px;border-radius:50%;background:var(--new)}
.nw{margin-left:7px;font-size:12px;font-weight:400;opacity:.8} .nw::before{width:6px;height:6px;margin-right:3px;vertical-align:1px}
.seg button[aria-pressed=true] .nw::before{background:color-mix(in srgb,var(--new) 60%,var(--bg))}
#fresh[aria-pressed=true]{background:color-mix(in srgb,var(--new) 14%,var(--bg));border-color:var(--new);color:var(--new)}
#since{position:absolute;z-index:3;overflow:auto;overscroll-behavior:contain;box-sizing:border-box;background:var(--bg);border:1px solid var(--line);border-left:3px solid var(--new);border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.12);font-size:15px}
#since[hidden]{display:none} #since .sch{position:sticky;top:0;z-index:1;display:flex;align-items:center;gap:8px;padding:6px 12px;background:var(--bg);border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}
#since .sch button{margin-left:auto;padding:0 8px;font-size:13px} #since .scb{padding:2px 12px 8px 16px} #since .chg{cursor:default}
.was summary{color:var(--muted);font-size:13px;cursor:pointer} .was summary~*{margin-top:4px}
.was .old{color:var(--muted);border-left:2px solid var(--delb);padding-left:8px} .old>:first-child,.now>:first-child{margin-top:0}
code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:90%}
pre{background:var(--code);padding:12px;overflow-x:auto}
img{max-width:100%}
table{border-collapse:collapse;display:block;overflow-x:auto;font-size:14px} td,th{border:1px solid var(--line);padding:4px 8px;vertical-align:top}
a{color:var(--link)} .cur{outline:2px solid #bf8700;outline-offset:2px}
body:not(.show-diff) .v-diff,body:not(.show-old) .v-old,body:not(.show-new) .v-new{display:none}
.show-old .step,.show-new .step,body:not(.show-diff) .accf{display:none}
.seg,.pair{display:inline-flex;max-width:100%} .seg button,.pair button{border-radius:0;margin-left:-1px;white-space:nowrap}
.seg button:first-child,.pair button:first-child{border-radius:6px 0 0 6px;margin-left:0} .seg button:last-child,.pair button:last-child{border-radius:0 6px 6px 0}
.seg button[aria-pressed=true]{background:var(--fg);color:var(--bg);border-color:var(--fg)}
#pos{flex:1 1 8em;min-width:0;margin:0 6px;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
header button,.panel button{height:28px;padding:0 12px;line-height:26px;font-size:13px;font-weight:500}
.primary{background:var(--ins);color:var(--insb);border-color:color-mix(in srgb,var(--insb) 40%,transparent)} .primary:not(:disabled):hover{background:color-mix(in srgb,var(--ins),var(--insb) 14%)}
header .step:disabled{opacity:1;color:color-mix(in srgb,var(--muted) 60%,var(--bg));background:var(--bg);border-color:var(--line)}
[data-key]::after{content:attr(data-key);display:inline-block;margin-left:7px;padding:0 4px;min-width:7px;border:1px solid color-mix(in srgb,currentColor 25%,transparent);border-radius:4px;font:11px/15px ui-monospace,SFMono-Regular,Menlo,monospace;color:color-mix(in srgb,currentColor 70%,transparent);text-align:center;vertical-align:1px}
.seg[data-key]::after{align-self:center;color:var(--muted);border-color:var(--line)}
@media (max-width:640px){[data-key]::after,.lbl{display:none} header button{padding:0 8px}}
.panel{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--line);box-shadow:0 -2px 8px rgba(0,0,0,.06);padding:12px max(16px,calc(50% - 444px));z-index:4;font-size:14px;display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:8px}
.panel[hidden]{display:none} #cmx{flex:1 0 100%;white-space:pre-wrap;font:13px ui-monospace,Menlo,monospace} #cmm{flex:1 0 100%;box-sizing:border-box;font:inherit;padding:4px 6px} #pt{flex:1 0 100%;white-space:pre-wrap;max-height:40vh;overflow:auto;font:13px ui-monospace,Menlo,monospace} .panel>blockquote,.panel>textarea,.panel>#rx{flex:1 0 100%;box-sizing:border-box} #rx{font-size:12px} .panel .hint{margin-right:auto;font-size:12px}
#pop{position:absolute;z-index:3;display:flex;background:var(--bg);border:1px solid var(--line);border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.12);overflow:hidden}
#pop[hidden]{display:none} #pop button,#pop a{border:0;border-radius:0;background:none;color:var(--muted);padding:2px 9px;font-size:14px;line-height:1.5;text-decoration:none}
#pop button:hover,#pop a:hover{background:var(--code);color:var(--fg)} #pop button+button,#pop button+a{border-left:1px solid var(--line)}
.panel blockquote{margin:0;padding:2px 10px;border-left:3px solid var(--line);color:var(--muted);font-size:13px;max-height:6em;overflow:auto;white-space:pre-wrap}
.panel textarea{display:block;width:100%;box-sizing:border-box;min-height:4.5em;margin:0;font:inherit;line-height:1.5;resize:vertical;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
.panel textarea:focus{outline:none;border-color:var(--link);box-shadow:0 0 0 3px color-mix(in srgb,var(--link) 20%,transparent)}
'''

SCRIPT = '''
const $=id=>document.getElementById(id),body=document.body,repo=body.dataset.repo,W=JSON.parse($('words').textContent);
// A sessionStorage or localStorage key of this review's: every review's page shares the server's origin.
const sk=k=>k+'@'+location.pathname.replace(/[^/]*$/,'');
// The words for accepting change x (null: none current); a plugin may set its own, per change.
let wordsOf=x=>W;
// Runs before changes xs are accepted (accepting) or taken back, one or a whole file's; a plugin may set
// its own, to act on what is on them.
let onAccept=async(xs,accepting)=>{};
const pos=$('pos'),next=$('next'),prev=$('prev'),acc=$('acc'),rev=$('rev'),rp=$('rp'),rq=$('rq'),rt=$('rt'),pop=$('pop'),pa=$('pa'),pr=$('pr');
// The status line is one line, cut short where narrow: its title holds it whole.
new MutationObserver(()=>pos.title=pos.textContent).observe(pos,{childList:true,characterData:true,subtree:true});
// The changes of base to head, each reviewed (accepted, x.done: class done) or still to review (see states()).
const groups=[],byG=new Map(),dones=new Map([...document.querySelectorAll('script.done')].map(j=>[j.closest('section'),new Set(JSON.parse(j.textContent))]));
for(const e of document.querySelectorAll('.v-diff .chg')){let x=byG.get(e.dataset.g);if(!x){const s=e.closest('section');
x={k:e.dataset.k,file:s.dataset.file,sec:s,view:'diff',done:!!dones.get(s)?.has(e.dataset.k),els:[]};byG.set(e.dataset.g,x);groups.push(x);}
x.els.push(e);if(x.done)e.classList.add('done');}
// What changed since the last review (see news()): per doc, the changes it touched, each with the changes
// of last review to now in it (x.since). Each is new: a blue edge on it, or on the block it is in; of a change
// in several boxes, on the boxes those touched (on).
const rounds=new Map([...document.querySelectorAll('script.round')].map(j=>[j.closest('section'),JSON.parse(j.textContent)]));
const blocks='p,li,dt,dd,td,th,h1,h2,h3,h4,h5,h6,pre,blockquote,figcaption';
for(const x of groups){const r=rounds.get(x.sec),on=r?.boxes?.[x.k];x.since=r?.fresh[x.k]||null;
if(x.since)x.els.forEach((e,i)=>{if(on&&!on.includes(i))return;e.classList.add('fresh');if(!e.classList.contains('blk'))e.closest(blocks)?.classList.add('fresh-b');});}
// The filter: which changes n and p step through and the status counts, all of them, the ones to review or
// the reviewed ones; ● new (onlyNew), pressed, narrows it to the ones in it new since the last review. It
// hides nothing. Each filter's count has its new ones beside it, and ● new counts the ones in the filter on.
const fb=$('fresh'),fresh=groups.some(x=>x.since),lasts=[...new Set([...rounds.values()].map(r=>r.last))],since=lasts.length===1?lasts[0]:'last review';
const fbs=[...document.querySelectorAll('#filt button')],inF={all:x=>true,todo:x=>!x.done,done:x=>x.done},
named={all:'changes',todo:'to review',done:W.accepted},count=(f,n)=>groups.filter(x=>inF[f](x)&&(!n||x.since)).length;
if(fresh)fb.onclick=()=>filter(filt,!onlyNew);else fb.remove();
for(const b of fbs){const f=b.dataset.f,n=count(f),m=fresh&&count(f,true);b.append(' '+n);
if(m){b.append(Object.assign(document.createElement('span'),{className:'nw',textContent:m}));b.title=n+' '+named[f]+', '+m+' of them new since '+since;}
b.onclick=()=>filter(f);}
let cur=body.dataset.view,c=null,anchor=null,mark=null,req=null,filt='todo',onlyNew=false;
const gi=x=>groups.indexOf(x),L=()=>groups.filter(x=>x.view===cur&&inF[filt](x)&&(!onlyNew||x.since)),said=()=>(onlyNew?'new ':'')+named[filt];
function filter(f,o=onlyNew){filt=f;onlyNew=fresh&&o;fbs.forEach(b=>b.setAttribute('aria-pressed',b.dataset.f===f));
if(fresh){const n=count(f),m=count(f,true);fb.textContent=m+' new since '+since;fb.setAttribute('aria-pressed',onlyNew);fb.disabled=!m&&!onlyNew;
fb.title=onlyNew?'Stepping through the '+m+' of the '+n+' '+named[f]+' that are new since '+since+'; click for all '+n:'Step through only the '+m+' of the '+n+' '+named[f]+' that are new since '+since;}
status();}
// A change's removed (-) or added (+) text: an inline <del>/<ins>, or a whole block's old/new part.
const part=(e,s)=>e.classList.contains('blk')?e.querySelector(s==='-'?'.old':'.now')?.textContent||'':e.tagName===(s==='-'?'DEL':'INS')?e.textContent:'';
const txt=(x,s)=>x.els.map(e=>part(e,s)).join(' ').replace(/\\s+/g,' ').trim();
function summary(x){const d=txt(x,'-'),i=txt(x,'+');return [d&&'−'+d,i&&'+'+i].filter(Boolean).join(' ').slice(0,300);}
// n and p step from the current change; with none, from the height clicked last (mark, in page
// coordinates) or else the change current last, while on screen; otherwise from the top of the screen.
function step(d){const l=L(),top=document.querySelector('header').getBoundingClientRect().bottom;
const m=c&&c.view===cur?c:mark===null&&anchor&&anchor.view===cur&&inView(anchor)?anchor:null;
if(m)return (d>0?l.find(x=>gi(x)>gi(m)):l.findLast(x=>gi(x)<gi(m)))||null;
const y=mark!==null&&mark-scrollY>top&&mark-scrollY<innerHeight?mark-scrollY:top,r=x=>x.els.find(e=>e.offsetParent)?.getBoundingClientRect();
return (d>0?l.find(x=>r(x)?.bottom>y):l.findLast(x=>r(x)?.top<y))||null;}
// The doc a change is in, where there are several. With nothing current, the filter's counts say it all.
const where=x=>new Set(groups.map(y=>y.file)).size>1?' · '+x.file:'';
// Accept (a) reads by the current change: accept on one to review, unaccept on a reviewed one.
function status(){const l=L(),i=l.indexOf(c);prev.disabled=!step(-1);next.disabled=!step(1);
const w=wordsOf(c),un=!!c?.done;acc.disabled=!c||cur!=='diff';acc.textContent=un?w.unaccept:w.accept;acc.classList.toggle('primary',!un);
acc.title=un?w.unaccept_title:w.accept_title;
pos.textContent=cur!=='diff'?W.select:i>=0?(i+1)+' of '+l.length+' '+said()+where(c):
c?'change '+(gi(c)+1)+' of '+groups.length+' · '+(c.done?W.accepted:'to review')+(c.since?' · new':'')+where(c):'';}
function focus(x,scroll=true){hidePop();if(c)c.els.forEach(e=>e.classList.remove('cur'));c=x;if(x){anchor=x;mark=null;}
if(c){c.els.forEach(e=>e.classList.add('cur'));if(scroll)c.els.find(e=>e.offsetParent)?.scrollIntoView({block:'center',behavior:'smooth'});}status();}
function go(d){const x=step(d);if(x)focus(x);}
async function post(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
if(!r.ok)throw new Error(url+' '+r.status);return r.json();}
// An accept moves the change into the accepted copy, an unaccept takes it back out; the server rebuilds
// the page and it reloads. It stays where it is: the next change in the filter becomes current only if it
// is already in view; otherwise nothing is current and n goes to it.
async function accept(){if(!c||cur!=='diff')return;const x=c,l=L(),nx=l.find(y=>gi(y)>gi(x))||l.findLast(y=>gi(y)<gi(x))||null;
try{await onAccept([x],!x.done).catch(()=>{});await post(x.done?'unaccept':'accept',{file:x.file,key:x.k});}catch(e){pos.textContent='not saved: '+e.message+' (is the docdiff server running?)';return;}
focus(nx&&inView(nx)?nx:null,false);if(!c)anchor=x;location.reload();}
function inView(x){const top=document.querySelector('header').getBoundingClientRect().bottom;
return x.els.some(e=>{const r=e.getBoundingClientRect();return e.offsetParent&&r.bottom>top&&r.top<innerHeight;});}
document.querySelectorAll('.accf').forEach(b=>b.onclick=async()=>{const s=b.closest('section'),a=b.dataset.a==='accept';
try{await onAccept(groups.filter(x=>x.sec===s&&x.done!==a),a).catch(()=>{});await post(b.dataset.a,{file:s.dataset.file,all:true});
location.reload();}catch(e){pos.textContent='not saved: '+e.message;}});
// Publish runs the --publish command on each file's accepted changes in turn. Its panel asks first, shows
// which file is being published, then what each printed; the page reloads on Done. (No confirm() or
// alert(): an embedded browser may block them, and then nothing would happen.)
const pb=document.getElementById('pub');if(pb){const pp=document.getElementById('pp'),pt=document.getElementById('pt'),
pc=document.getElementById('pc'),ps=document.getElementById('ps'),fs=JSON.parse(pb.dataset.files);
pb.onclick=()=>{pt.textContent='Publish the accepted changes?\\n\\n'+fs.map(([f,n])=>f+': '+n+' change(s)').join('\\n');pp.hidden=false;ps.focus();};
pc.onclick=()=>{pp.hidden=true;};
ps.onclick=async()=>{if(ps.dataset.done){location.reload();return;}pc.hidden=true;ps.disabled=pb.disabled=true;const said=[];
for(const[i,[f]]of fs.entries()){pb.textContent=ps.textContent='publishing '+(i+1)+' of '+fs.length+'…';pt.textContent=[...said,'Publishing '+f+'…'].join('\\n\\n');
try{const r=await fetch('publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:f})});
said.push((r.ok?'✓ Published ':'✗ Not published ')+f+'\\n'+(await r.text()).trim());}catch(e){said.push('✗ Not published '+f+': '+e.message);}}
pt.textContent=said.join('\\n\\n');ps.textContent='Done';ps.disabled=false;ps.dataset.done=1;ps.focus();};}
// Commit accepted, with --commit: its panel lists the files it commits, as they read on disk, and the ones
// still waiting on changes to review; the message is editable (↵ commits); the page reloads on Done.
const cb=document.getElementById('cmt');if(cb){const cp=document.getElementById('cmp'),cx=document.getElementById('cmx'),cm=document.getElementById('cmm'),
cc=document.getElementById('cmc'),cs=document.getElementById('cms'),fs=JSON.parse(cb.dataset.files),ws=JSON.parse(cb.dataset.waiting);
cb.onclick=()=>{cx.textContent='Commit, as they read on disk:\\n'+fs.join('\\n')+(ws.length?'\\n\\nNot these, with changes still to review:\\n'+ws.map(([f,n])=>f+': '+n).join('\\n'):'');
cm.value=cm.value||'Update '+fs.join(', ');cp.hidden=false;cm.focus();};cc.onclick=()=>{cp.hidden=true;};
cs.onclick=async()=>{if(cs.dataset.done){location.reload();return;}cs.disabled=cb.disabled=true;cs.textContent='committing…';let t;
try{const r=await fetch('commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({files:fs,message:cm.value})});t=(r.ok?'✓ ':'✗ ')+(await r.text()).trim();}
catch(e){t='✗ '+e.message;}cx.textContent=t;cs.textContent='Done';cs.disabled=false;cs.dataset.done=1;cc.hidden=true;cs.focus();};
cm.addEventListener('keydown',e=>{e.stopPropagation();if(e.key==='Enter'){e.preventDefault();cs.click();}if(e.key==='Escape')cc.click();});}
function hidePop(){pop.hidden=true;popX=null;}
fb.title='Changed since your last review, at '+lasts.join(', ')+': n and p step through these alone. '+
'Once every new change is reviewed, the next update counts from the version you have now.';
// A new change's diff since its last review: the blocks of last review to now that it holds, in a card under it.
const sc=$('since'),psn=$('psn');
function showSince(x){if(!x?.since)return;const t=x.sec.querySelector('template.since'),bs=[];hidePop();
for(const k of x.since)for(const e of t.content.querySelectorAll('.chg[data-k="'+k+'"]')){let b=e;
while(b.parentNode!==t.content&&!(b.tagName==='LI'&&b.parentNode.parentNode===t.content))b=b.parentNode;if(!bs.includes(b))bs.push(b);}
const h=document.createElement('div'),bd=document.createElement('div'),close=document.createElement('button');let list=null;
h.className='sch';h.textContent='What changed here since your review at '+rounds.get(x.sec).last;close.textContent='×';close.title='Close (esc)';
close.onclick=()=>{sc.hidden=true;};h.append(close);bd.className='scb';
for(const b of bs){if(b.tagName!=='LI'){list=null;bd.append(b.cloneNode(true));continue;}
if(!list||list.from!==b.parentNode){list=b.parentNode.cloneNode(false);list.from=b.parentNode;bd.append(list);}list.append(b.cloneNode(true));}
sc.replaceChildren(h,bd);sc.hidden=false;const r=(x.els.find(e=>e.offsetParent)||x.els[0]).getBoundingClientRect(),w=Math.min(760,innerWidth-16);
sc.style.width=w+'px';sc.style.maxHeight=Math.max(160,innerHeight*.6)+'px';sc.style.left=Math.max(8,Math.min(r.left,innerWidth-w-8))+scrollX+'px';
sc.style.top=r.bottom+scrollY+6+'px';sc.scrollTop=0;sc.scrollIntoView({block:'nearest',behavior:'smooth'});}
document.addEventListener('click',e=>{if(!e.target.closest('#since,#pop'))sc.hidden=true;});
// Plugins' buttons per change (see load_plugins): [label, key, href], href opened in a new tab.
const acts=new Map([...document.querySelectorAll('script.acts')].map(j=>[j.closest('section'),JSON.parse(j.textContent)]));
const actsOf=x=>x&&acts.get(x.sec)?.[x.k]||[];let popX=null;
// Under a clicked change x: accept, revisit and the plugins' buttons. Under the end of a text selection: revisit only.
function showPop(r,withAccept,x){pa.hidden=!withAccept;psn.hidden=!x?.since;if(x?.since){const l=rounds.get(x.sec).last;
psn.textContent='Δ since '+l;psn.title='What changed in it since your review at '+l+' (s)';}pa.textContent=x?.done?'↺':'✓';pa.title=x?.done?wordsOf(x).unaccept_key:wordsOf(x).accept_key;popX=x||null;
pop.querySelectorAll('.act').forEach(b=>b.remove());
for(const[label,key,href]of actsOf(x)){const b=document.createElement('a');b.className='act';b.textContent=label;b.title=label+' ('+key+')';
b.href=href;b.target='_blank';b.rel='noopener';b.onmousedown=e=>e.preventDefault();b.onclick=hidePop;pop.append(b);}
pop.hidden=false;
pop.style.left=Math.max(8,Math.min(r.left,innerWidth-pop.offsetWidth-8))+scrollX+'px';pop.style.top=r.bottom+scrollY+4+'px';}
// A link does not move the page off what is being read. One to the web opens in the default browser (the
// server opens it, as the page may be in an embedded browser), or failing that in a new tab; one to
// docdiff's own pages (127.0.0.1) in a new tab; in the doc, one into the doc or to another doc (links are
// not rewritten) does nothing. With a modifier key the browser follows any link as usual.
document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(!a||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button)return;
const web=/^https?:/.test(a.getAttribute('href'));
if(web&&!['127.0.0.1','localhost'].includes(new URL(a.href).hostname)){e.preventDefault();
fetch('open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:a.href})})
.then(r=>{if(!r.ok)throw new Error(r.status);}).catch(()=>window.open(a.href,'_blank','noopener'));return;}
if(!a.closest('main'))return;e.preventDefault();if(web)window.open(a.href,'_blank','noopener');},true);
// Clicks on the page's controls (plugins mark theirs .ui) are not clicks on the text.
document.addEventListener('click',e=>{if(e.target.closest('header,.what,.files,#pop,.panel,.ui')||!getSelection().isCollapsed)return;
// A removed block's summary only folds and unfolds it.
if(e.target.closest('summary')){hidePop();return;}
const t=e.target.closest('.v-'+cur+' .chg');
if(t){focus(groups.find(x=>x.view===cur&&x.els.includes(t)),false);showPop(t.getBoundingClientRect(),cur==='diff',c);}
else{mark=e.clientY+scrollY;focus(null,false);}});
// Without a current change, where n and p start moves with the screen.
addEventListener('scroll',()=>{if(!c)status();},{passive:true});
// Runs once the click has settled: clicking inside a selection clears it only after the click handler.
document.addEventListener('mouseup',e=>{if(e.target.closest('header,.what,.files,#pop,.panel,.ui'))return;setTimeout(()=>{const s=getSelection();
if(s.isCollapsed||!s.rangeCount){if(!popX)hidePop();return;}
if(!s.anchorNode.parentElement?.closest('main .v'))return;
mark=e.clientY+scrollY;if(!c)status();
const rs=s.getRangeAt(0).getClientRects();if(rs.length)showPop(rs[rs.length-1],false);});});
[pa,pr,psn].forEach(b=>b.onmousedown=e=>e.preventDefault());
pa.onclick=()=>{hidePop();accept();};pr.onclick=()=>{hidePop();openRev(grab());};psn.onclick=()=>showSince(popX);
const vb=[...document.querySelectorAll('.seg button[data-v]')],views=vb.map(b=>b.dataset.v);
function view(v){if(v===cur)return;hidePop();sc.hidden=true;
const secs=[...document.querySelectorAll('section')];let s=secs[0],r=0;
for(const x of secs){const b=x.getBoundingClientRect();if(b.bottom>80){s=x;r=Math.max(0,80-b.top)/Math.max(1,b.height);break;}}
body.classList.replace('show-'+cur,'show-'+v);cur=v;vb.forEach(b=>b.setAttribute('aria-pressed',b.dataset.v===v));
if(c&&c.view!==v)focus(null,false);else status();
if(s){const b=s.getBoundingClientRect();scrollTo(0,scrollY+b.top+r*b.height-80);}}
// The selected text as it now reads: deleted words and folded old blocks left out, unless that is
// all the selection holds.
function quoteOf(s){const f=s.getRangeAt(0).cloneContents();f.querySelectorAll('del,.was').forEach(e=>e.remove());
return f.textContent.replace(/\\s+/g,' ').trim()||s.toString().trim();}
function grab(){const s=getSelection();
if(!s.isCollapsed&&s.rangeCount&&!rp.contains(s.anchorNode))return{quote:quoteOf(s),el:s.anchorNode.parentElement};
return c?{quote:summary(c),el:c.els[0],key:c.k}:null;}
function heading(el){const v=el.closest('.v');if(!v)return '';
const h=[...v.querySelectorAll('h1,h2,h3,h4')].filter(h=>h.compareDocumentPosition(el)&Node.DOCUMENT_POSITION_FOLLOWING).pop();
if(!h)return '';const k=h.cloneNode(true);k.querySelectorAll('del').forEach(d=>d.remove());return k.textContent.trim();}
// g: the text revisited (quote), an element in it (el), its change's key, fields plugins add to the
// request, and words on what it goes on from (context), shown above the quote.
function openRev(g){const s=g&&g.el.closest('section');if(!s){pos.textContent='select text, or step to a change, then press r';return;}
req={file:s.dataset.file,section:heading(g.el),quote:g.quote,view:cur,key:g.key,...g.fields};rq.textContent=g.quote;
$('rx').textContent=g.context||'';$('rx').hidden=!g.context;rp.hidden=false;rt.focus();}
function closeRev(){rp.hidden=true;rt.value='';req=null;}
async function copy(t){try{await navigator.clipboard.writeText(t);return true;}
catch(e){const a=document.createElement('textarea');a.value=t;body.append(a);a.select();const done=document.execCommand('copy');a.remove();return done;}}
// The request as Claude reads it: what it is about (a plugin may lead it its own way: lead), the quote, the ask.
async function send(){const ask=rt.value.trim();if(!ask||!req)return;
const text=(req.lead||W.prompt+repo+'/'+req.file+(req.section?', section "'+req.section+'"':'')+(req.view==='old'?' (old version)':''))+':\\n> '+req.quote.replace(/\\n/g,'\\n> ')+'\\n\\n'+ask;
let queued=true;try{await post('revisit',{...req,prompt:ask,text});}catch(e){queued=false;}
// A watcher touches the file every second while it waits; with none, the clipboard is the way in.
const watched=queued&&await watching();
closeRev();pos.textContent=W.sent+': '+(watched?'sent, Claude is watching':await copy(text)?
'copied; nobody is watching, paste it in the chat':'not sent: nobody is watching and copying failed');}
async function watching(){try{const r=await fetch('watcher',{method:'HEAD'});
return r.ok&&Date.now()-new Date(r.headers.get('Last-Modified'))<5000;}catch(e){return false;}}
rev.onmousedown=e=>e.preventDefault();rev.onclick=()=>openRev(grab());
$('rs').onclick=send;$('rc').onclick=closeRev;
rt.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}if(e.key==='Escape')closeRev();});
next.onclick=()=>go(1);prev.onclick=()=>go(-1);acc.onclick=accept;vb.forEach(b=>b.onclick=()=>view(b.dataset.v));
document.addEventListener('keydown',e=>{if(e.target.closest('input,textarea')||e.metaKey||e.ctrlKey||e.altKey)return;
if(e.key==='Escape'){hidePop();sc.hidden=true;}
if(e.key==='v')view(views[(views.indexOf(cur)+1)%views.length]);
if(e.key==='r'){e.preventDefault();openRev(grab());}
if(cur==='old'||cur==='new')return;
if(e.key==='n'||e.key==='j')go(1);if(e.key==='p'||e.key==='k')go(-1);if(e.key==='a')accept();if(e.key==='s')showSince(c);
const a=actsOf(c).find(a=>a[1]===e.key);if(a)open(a[2],'_blank','noopener');});
// Every reload keeps the view, the filter, the current change and the place in the doc; a rebuild reloads
// the page, unless a panel is open (a revisit, or a plugin's, being typed).
history.scrollRestoration='manual';
function place(){const secs=[...document.querySelectorAll('section')];
for(const x of secs){const b=x.getBoundingClientRect();if(b.bottom>80)return{file:x.dataset.file,r:Math.max(0,80-b.top)/Math.max(1,b.height)};}return null;}
addEventListener('pagehide',()=>{try{sessionStorage.setItem(sk('docdiff'),JSON.stringify({view:cur,filter:filt,fresh:onlyNew,at:place(),c:c&&[c.file,c.k],ci:c?L().indexOf(c):-1,a:anchor&&[anchor.file,anchor.k]}));}catch(e){}});
// The views before the filter, To review, Reviewed and Diff, are its filters now; the filter new before ● new
// was the filter All with ● new.
const was={review:'todo',acc:'done',diff:'all'};
function restore(){let st=null;try{st=JSON.parse(sessionStorage.getItem(sk('docdiff')));}catch(e){}if(!st)return;
const find=p=>p&&groups.find(x=>x.view===cur&&x.file===p[0]&&x.k===p[1])||null;
if(views.includes(st.view)&&st.view!==cur){body.classList.replace('show-'+cur,'show-'+st.view);cur=st.view;vb.forEach(b=>b.setAttribute('aria-pressed',b.dataset.v===cur));}
const f=st.filter==='new'?'all':st.filter||was[st.view];if(inF[f])filt=f;onlyNew=fresh&&(!!st.fresh||st.filter==='new');
anchor=find(st.a);const x=find(st.c)||(st.c&&L()[Math.min(st.ci,L().length-1)]);if(x)focus(x,false);
const back=()=>{const s=st.at&&[...document.querySelectorAll('section')].find(z=>z.dataset.file===st.at.file);
if(s){const b=s.getBoundingClientRect();scrollTo(0,scrollY+b.top+st.at.r*b.height-80);}};
document.readyState==='complete'?back():addEventListener('load',back);}
setInterval(()=>fetch('build.json').then(r=>r.json()).then(b=>{if(b.id!==body.dataset.build&&!document.querySelector('.panel:not([hidden])'))location.reload();}).catch(()=>{}),1500);
restore();filter(filt);
// A link to #file=<name> (from gdocsync's docs list, say) opens the page at that doc, just below the header,
// and &view=<view> in that view, or with the filter that view was before (review, acc, diff).
{const at=()=>{const q=new URLSearchParams(location.hash.slice(1)),want=q.get('file')||'',s=want&&[...document.querySelectorAll('section')].find(s=>s.dataset.file===want);
if(!want)return;history.replaceState(null,'',location.pathname);const v=q.get('view');
if(was[v]){view('diff');filter(was[v],false);}else if(views.includes(v))view(v);
const go=()=>scrollTo(0,s.getBoundingClientRect().top+scrollY-document.querySelector('header').offsetHeight-8);
if(s)document.readyState==='complete'?go():addEventListener('load',go);};at();addEventListener('hashchange',at);}
'''


def htmls(repo, base, head, old, new, out):
    """The file at base and at head (or in the working tree), rendered; '' for a side it does not exist
    on. Also, while a plugin's doc is not rendered yet, what to show for it instead (see load_plugins);
    None for a doc that is."""
    for m in PLUGINS:
        got = hasattr(m, 'htmls') and m.htmls(repo, base, head, old, new, out)
        if got:
            return got
    old_html = inline_images(render(git(repo, 'show', f'{base}:{old}').stdout), repo, os.path.dirname(old), base,
                             False) if old else ''
    new_html = inline_images(render(read(repo, head, new)), repo, os.path.dirname(new), head or base,
                             not head) if new else ''
    return old_html, new_html, None


def accepted_html(repo, base, head, out, old, new, old_html, new_html):
    """The file as accepted so far, and None; or, while a version it needs is not rendered yet, the copy as
    it is and what the file's section shows meanwhile (state, html). The copy is the base until an accept
    moves a change in, and it goes with the head it was last made for, its round's seen (see round_of()).
    When the head or the base has moved since, carry() keeps the accepted changes the move left alone and
    brings back the others, and the round moves on (see next_round()); the copy never keeps text the new
    base dropped. A copy from before rounds were kept moves onto a new base by its changes' keys: those
    that still read the same carry over. Accepts kept as keys in accepted.json, from before the copy
    existed, move in the first time it is needed."""
    name = new or old
    copy, was = out / 'accepted' / f'{name}.html', out / 'accepted' / f'{name}.base'
    if copy.exists():
        then, rnd = was.read_text() if was.exists() else None, round_of(out, name)
        moved = then not in (None, commit(repo, base))
        if 'seen' in rnd:
            seen, wait = version(repo, head, out, name, old, new, rnd['seen'], 'seen')
            if wait or seen == new_html and not moved:
                return copy.read_text(), wait
            if seen is not None:
                return next_round(repo, base, head, out, name, old, new, rnd, seen, old_html, new_html)
            # The working tree it went with was not kept: it goes with this one from now on.
            rnd.pop('seen')
            save_round(out, name, rnd)
        if not moved or git(repo, 'cat-file', '-e', f'{then}^{{commit}}', check=False).returncode:
            see(repo, head, out, name, new_html)
            return copy.read_text(), None
        then_html, _, wait = htmls(repo, then, head, old, new, out)
        if wait:
            return copy.read_text(), None
        keys = {k for _, _, ps in block_diff(then_html, copy.read_text(), 'x')[2] for k, _, _ in ps}
    else:
        legacy = out / 'accepted.json'
        keys = json.loads(legacy.read_text()).get(name, {}) if legacy.exists() else {}
    pieces = block_diff(old_html, new_html, 'x')[2] if keys else []
    found = {k for _, _, ps in pieces for k, _, _ in ps if k in keys}
    text = apply(pieces, 'old', found) if found else old_html
    if found or copy.exists():
        save_copy(repo, base, out, name, text)
        see(repo, head, out, name, new_html)
    return text, None


def next_round(repo, base, head, out, name, old, new, rnd, seen, old_html, new_html):
    """The accepted copy carried over from the head it went with (seen, rendered) to this one, and None;
    the copy and its round as they were go to <out>/backup first. The round's last reviewed version, which
    the page's "new since last review" counts from, moves up to seen once every change that was new there
    had been reviewed (or when there was none yet); otherwise it stays, and what is new adds up."""
    acc, copy = out / 'accepted', (out / 'accepted' / f'{name}.html').read_text()
    if 'last' in rnd:
        last, wait = version(repo, head, out, name, old, new, rnd['last'], 'last')
        if wait:
            return copy, wait
        if last is None or not meets([(s, e) for k, _, _, s, e in places(block_diff(last, seen, 'x')) if k],
                                     [(s, e) for k, _, _, s, e in places(block_diff(copy, seen, 'x')) if k]):
            rnd.pop('last')
    backup(out, name)
    if 'last' not in rnd:
        rnd['last'] = rnd['seen']
        (acc / f'{name}.last.html').write_text(seen)
    text = carry(copy, seen, old_html, new_html)
    save_copy(repo, base, out, name, text)
    rnd['seen'] = {'id': head_id(repo, head, new_html), 'time': now()}
    (acc / f'{name}.seen.html').write_text(new_html)
    save_round(out, name, rnd)
    return text, None


def round_of(out, name):
    """A file's review round, beside its accepted copy in accepted/<file>.round.json: seen, the head the copy
    was last made for, and last, the version last reviewed, which what is new since last review counts
    from; each {id, time}, id a commit or, for the working tree, tree:<hash of its render>. Each version is
    kept rendered beside it too, <file>.seen.html and <file>.last.html."""
    f = out / 'accepted' / f'{name}.round.json'
    return json.loads(f.read_text()) if f.exists() else {}


def save_round(out, name, rnd):
    f = out / 'accepted' / f'{name}.round.json'
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rnd))


def head_id(repo, head, new_html):
    return commit(repo, head) if head else 'tree:' + hashlib.sha1(new_html.encode()).hexdigest()[:12]


def version_name(rec):
    """A version of a round in words: its commit, short, or for the working tree when it was seen."""
    if rec['id'].startswith('tree:'):
        return time.strftime('%b %-d, %H:%M', time.strptime(rec['time'], '%Y-%m-%dT%H:%M:%S'))
    return rec['id'][:7]


def version(repo, head, out, name, old, new, rec, which):
    """A version of the file that its round names (rec, which is 'seen' or 'last'), rendered: as kept beside
    the accepted copy, or else rendered from its commit and kept; None for the working tree's when it was
    not kept, or a commit the repo no longer has. While it is not rendered yet (a spec renders in the page),
    also what the file's section shows meanwhile, (state, html)."""
    f, rev = out / 'accepted' / f'{name}.{which}.html', rec['id']
    if f.exists():
        return f.read_text(), None
    if rev.startswith('tree:') or git(repo, 'cat-file', '-e', f'{rev}^{{commit}}', check=False).returncode:
        return None, None
    got, _, wait = htmls(repo, rev, head, new if new and read(repo, rev, new) is not None else old, new, out)
    if wait:
        return None, (f'rendering {version_name(rec)}, {"last reviewed" if which == "last" else "reviewed"}, with ReSpec…', wait[1])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(got)
    return got, None


def see(repo, head, out, name, new_html):
    """Notes the head the accepted copy goes with, where none was noted yet (see round_of())."""
    rnd = round_of(out, name)
    if 'seen' not in rnd:
        rnd['seen'] = {'id': head_id(repo, head, new_html), 'time': now()}
        (out / 'accepted' / f'{name}.seen.html').write_text(new_html)
        save_round(out, name, rnd)


# What a file's accepted copy and round are kept in, beside each other in accepted/.
COPY_FILES = ('.html', '.base', '.round.json', '.seen.html', '.last.html')
# Rounds' backups kept, the last ones.
BACKUPS = 20


def backup(out, name):
    """The file's accepted copy and round as they are, kept in <out>/backup/<time>/accepted/ before a new
    round rewrites them; the BACKUPS last are kept."""
    to = out / 'backup' / time.strftime('%Y%m%d-%H%M%S')
    for s in COPY_FILES:
        f = out / 'accepted' / f'{name}{s}'
        if f.exists():
            (to / 'accepted' / f'{name}{s}').parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, to / 'accepted' / f'{name}{s}')
    for d in sorted((out / 'backup').iterdir() if (out / 'backup').is_dir() else [])[:-BACKUPS]:
        shutil.rmtree(d, ignore_errors=True)


def save_copy(repo, base, out, name, text):
    copy = out / 'accepted' / f'{name}.html'
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(text)
    (out / 'accepted' / f'{name}.base').write_text(commit(repo, base))


def commit(repo, rev):
    return git(repo, 'rev-parse', f'{rev}^{{commit}}').stdout.decode().strip()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def load_plugins():
    """The modules in plugins/, each with this module as `docdiff` in it, for git(), read() and the
    like. run below is the run's arguments as .run.json holds them, repo and out as full paths. A plugin
    may define:

    add_args(parser): its arguments, which go into .run.json with the rest.
    name(run): None, or the review's name when --name and --out give none (github_pr.py's names the PR);
      run there has the arguments and repo alone.
    counts(run): None, or words for the review's line in the index of reviews, such as '3 comments'.
    PATHS and doc(repo, (base, head), path, memo): pathspecs of the files it makes docs of, beside *.md,
      and the doc a changed file stands for, None for none; memo lasts one listing of the changes.
    htmls(repo, base, head, old, new, out): None for a doc not its own, else as htmls() returns it: both
      sides rendered, and while they are not, (state, html) for the file's section, words for its bar
      and what gets the page to render it.
    section(run, name): None, or (state, html) to add to a file's section: words for its bar, data for
      the page.
    title(run): None where the plugin does not apply, or else (what the page compares, a link or None)
      for the header's first line, in place of "Uncommitted edits" or "Commits".
    top(run): None, or html to put above the docs, such as gdoc_docs.py's list of them; a page with it
      is served even when no doc changed.
    actions(run): None where the plugin does not apply, or else a function (old path, new path, blocks)
      that returns buttons for the popup of a file's changes, {change key: [(label, key, href)]}; the
      button opens href in a new tab, and so does the key on the current change. blocks is the file's
      diff as text, per block [(change key or None, old text, new text)], as block_diff() pairs them.
    place(run, old path, new path, side, before, after): where text of a file's block is in its source,
      for other plugins: github_pr.py's (see there).
    remarks(run, out, refresh): comments made on the docs elsewhere, for other plugins, one source per
      plugin: github_pr.py's reads the PR's on GitHub, gdoc_comments.py's the Google Docs' (see there).
    STYLE, SCRIPT: CSS and JavaScript for the page, after docdiff's own. An element of class panel holds
      off the reload after a rebuild while it shows, and clicks in it leave the doc alone, as they do in
      an element of class ui (a card or side panel), which does not hold off the reload; openRev(g) opens
      the revisit panel on g.quote, g.fields adding to the request it sends and g.context shown above
      it; wordsOf(x), which a plugin may replace, gives the words for accepting change x, W (WORDS for
      the run's mode) by default; onAccept(xs, accepting), which it may replace too, runs before changes
      xs are accepted or taken back.
    get(run, path, query), post(out, path, req): the server's routes. None for a path not its own, else
      (data, content type) with data None for a 404, or the reply to a post, after which the page
      rebuilds, unless the path is in the plugin's IN_PLACE: posts the page shows in place."""
    mods = []
    for f in sorted((pathlib.Path(__file__).parent / 'plugins').glob('*.py')):
        spec = importlib.util.spec_from_file_location(f'docdiff_{f.stem}', f)
        mods.append(importlib.util.module_from_spec(spec))
        mods[-1].docdiff = sys.modules[__name__]
        spec.loader.exec_module(mods[-1])
    return mods


PLUGINS = load_plugins()


def plugin_actions(actors, old, new, pieces):
    """The buttons plugins give a file's changes, from the pieces of its block_diff()."""
    acts = {}
    for act in actors:
        got = act(old, new, [[(key, text(o), text(w)) for key, o, w in parts] for _, _, parts in pieces])
        for key, a in got.items():
            acts.setdefault(key, []).extend(a)
    return acts


def build(repo, base, head, files, build_id, out, keep=(), run=None, new_label=None, publish=False, actors=()):
    sections, nav, words, pubs, commits, waiting, counts = [], [], WORDS[bool(head)], [], [], [], [0, 0]
    waiting_for = lambda k, name, wait: (f'<section id="f{k}" data-file="{html.escape(name)}"><div class="file">{html.escape(name)}'
                                         f' <span class=state>· {html.escape(wait[0])}</span></div>{wait[1]}</section>')
    for k, (old, new) in enumerate(files):
        name = new or old
        old_html, new_html, wait = htmls(repo, base, head, old, new, out)
        if wait:
            sections.append(waiting_for(k, name, wait))
            continue
        diffed = block_diff(old_html, new_html, f'f{k}')
        body, n, pieces = diffed
        if not n and name not in keep:
            continue
        copy, wait = accepted_html(repo, base, head, out, old, new, old_html, new_html)
        rnd = round_of(out, name)
        last, wait = version(repo, head, out, name, old, new, rnd['last'], 'last') if 'last' in rnd and not wait else (None, wait)
        if wait:
            sections.append(waiting_for(k, name, wait))
            continue
        # Each change of base to head is still to review where the accepted copy to head has a change, and
        # reviewed (accepted) where it has none.
        stand = states(diffed, block_diff(copy, new_html, 'x'), block_diff(old_html, copy, 'x'))
        done = sorted(c for c, (t, _) in stand.items() if not t)
        todo, accepted = len(stand) - len(done), len(done)
        acts = plugin_actions(actors, old, new, pieces)
        fresh, boxes, since = news(last, new_html, k, diffed) if last is not None else ({}, {}, '')
        counts[0] += todo
        counts[1] += accepted
        state = 'new file' if not old else 'deleted' if not new else f'renamed from {old}' if old != new else ''
        sid = f'f{k}'
        label = html.escape(name)
        # A doc shown only because it was named, with nothing changed, stays out of the docs at the top.
        if n:
            nav.append((sid, label, f'{todo} to review · {accepted} {words["accepted"]}'))
        plain = lambda h, missing: re.sub(r'\sid="[^"]*"', '', h) or f'<p class="n">({missing})</p>'
        bar = ((f'<button class="accf" data-a="accept">{words["accept_file"]}</button>' if todo else '') +
               (f'<button class="accf" data-a="unaccept">{words["unaccept_file"]}</button>' if accepted else ''))
        if publish and accepted:
            pubs.append([name, accepted])
        # With --commit, a file whose changes are all accepted can be committed as it reads on disk.
        if run and run.get('commit') and not head and accepted:
            (waiting.append([name, todo]) if todo else commits.append(name))
        view = lambda v, h: f'<div class="v v-{v}" data-view="{v}">{h}</div>'
        # What plugins add to the file: words for its bar, data for the page.
        extra = [x for x in (m.section(run, name) for m in PLUGINS if hasattr(m, 'section')) if x]
        state = ' · '.join(filter(None, [state, *(s for s, _ in extra)]))
        data = ''.join(h for _, h in extra) + (('<script type="application/json" class="acts">' +
                                                json.dumps(acts, ensure_ascii=False).replace('</', '<\\/') + '</script>') if acts else '')
        data += f'<script type="application/json" class="done">{json.dumps(done)}</script>'
        # What changed since the last review, for its blue edges and the changes' own diffs since then.
        if fresh:
            data += ('<script type="application/json" class="round">' + json.dumps(
                {'last': version_name(rnd['last']), 'time': rnd['last']['time'], 'fresh': fresh, 'boxes': boxes}).replace('</', '<\\/') +
                '</script>' + f'<template class="since">{since}</template>')
        sections.append(f'<section id="{sid}" data-file="{label}">{data}<div class="file">{label}'
                        f'{f" <span class=state>· {html.escape(state)}</span>" if state else ""}'
                        f'{f"<span class=fbar>{bar}</span>" if bar else ""}</div>'
                        f'{view("diff", body)}{view("old", plain(old_html, "new file"))}{view("new", plain(new_html, "deleted"))}'
                        f'</section>')
    what, href = compared(run)
    shown = f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{html.escape(what)}</a>' if href else html.escape(what)
    # The two sides in short (the old one's subject and the working tree's path only in the title).
    (old_name, old_subject), (new_name, new_subject) = side(repo, base), side(repo, head)
    sides = f'{old_name} → {new_label or (f"{new_name} {new_subject}" if head else new_name)}'
    sides_title = f'Old: {old_name} {old_subject} — New: {new_label or f"{new_name} {new_subject}"}'
    # Each doc's counts, where there are several docs: of one, the filter's counts say as much. The span
    # stays, for what plugins add to a doc's line.
    files = ' · '.join(f'<a href="#{sid}">{label}</a> <span class="n">{counted if len(nav) > 1 else ""}</span>'
                       for sid, label, counted in nav)
    title = f'docdiff · {what}'
    # One button publishes every file with accepted changes, each in turn.
    pub = (f'<button id="pub" data-files="{html.escape(json.dumps(pubs, ensure_ascii=False))}" '
           f'title="Publish the accepted changes of {len(pubs)} file(s)">⇪ publish accepted ({sum(n for _, n in pubs)})</button>'
           if pubs else '')
    # And one commits every file whose changes are all accepted; the ones with changes left wait.
    cmt = (f'<button id="cmt" data-files="{html.escape(json.dumps(commits, ensure_ascii=False))}" '
           f'data-waiting="{html.escape(json.dumps(waiting, ensure_ascii=False))}" '
           f'title="Commit the {len(commits)} file(s) whose changes are all accepted, as they read on disk">⇪ commit accepted ({len(commits)})</button>'
           if commits else '')
    buttons = [('diff', 'Diff'), ('old', 'Old'), ('new', 'New')]
    seg = ''.join(f'<button data-v="{v}" aria-pressed="{str(v == "diff").lower()}">{label}</button>' for v, label in buttons)
    # The filter: which changes n and p step through, and the status counts; ● new narrows it to the new ones.
    filters = [('all', 'All'), ('todo', 'To review'), ('done', words['Accepted'])]
    filt = ''.join(f'<button data-f="{f}" aria-pressed="false">{label}</button>' for f, label in filters)
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{STYLE}{"".join(getattr(m, 'STYLE', '') for m in PLUGINS)}</style></head><body class="show-diff" data-view="diff" data-repo="{html.escape(str(repo))}" data-build="{build_id}">
<div class="what"><b>{shown}</b><span class="n" title="{html.escape(sides_title)}">{html.escape(sides)}</span></div>
<div class="files">{files}</div>
<header><span id="filt" class="seg step">{filt}</span><button id="fresh" class="step" aria-pressed="false"></button><span class="pair step"><button id="prev" class="step" data-key="p" title="Previous change (p or k)">↑<span class="lbl"> prev</span></button><button id="next" class="step" data-key="n" title="Next change (n or j)"><span class="lbl">next change </span>↓</button></span>
<span id="pos" class="n"></span><button id="acc" class="step primary" data-key="a">{words['accept']}</button><button id="rev" data-key="r" title="{words['revisit_title']}">{words['revisit']}</button>{pub}{cmt}
<span id="views" class="seg" data-key="v" title="v cycles the views: the diff, and the old and new versions clean">{seg}</span></header>
<main>{page_top(run)}{"".join(sections)}</main>
<div id="rp" class="panel" hidden><div id="rx" class="n" hidden></div><blockquote id="rq"></blockquote><textarea id="rt" placeholder="{words['question']}"></textarea>
<span class="hint n">⇧↵ adds a line</span><button id="rc" data-key="esc">Cancel</button><button id="rs" class="primary" data-key="↵">Send</button></div>
{'<div id="pp" class="panel" hidden><div id="pt"></div><button id="pc">Cancel</button><button id="ps" class="primary">Publish</button></div>' if pub else ''}
{'<div id="cmp" class="panel" hidden><div id="cmx"></div><input id="cmm" placeholder="Commit message"><button id="cmc">Cancel</button><button id="cms" class="primary">Commit</button></div>' if cmt else ''}
<div id="pop" hidden><button id="pa">✓</button><button id="pr" title="{words['revisit_key']}">✎</button><button id="psn" hidden></button></div>
<div id="since" class="ui" hidden></div>
<script type="application/json" id="words">{json.dumps(words, ensure_ascii=False)}</script>
<script>{SCRIPT}{"".join(getattr(m, 'SCRIPT', '') for m in PLUGINS)}</script></body></html>''', len(sections), counts


def compared(run):
    """What a run compares, for the page's header and the index of reviews: a plugin's words (a PR, a Google
    Doc) with a link or None, or else commits or the uncommitted edits."""
    repo = pathlib.Path(run['repo'])
    return next((t for t in (m.title(run) for m in PLUGINS if hasattr(m, 'title')) if t), None) or (
        (f'Commits · {repo.name} · {run["base"]}..{run["head"]}' if run.get('head') else f'Uncommitted edits · {repo.name}'), None)


def page_top(run):
    """What plugins put above the docs (their top())."""
    return ''.join(t for t in (m.top(run) for m in PLUGINS if run and hasattr(m, 'top')) if t)


def files_for(repo, base, head, paths):
    """Changed files [(old, new)], plus files named in paths that have no changes, so they can be
    revised as edits land, files named in paths in their order; and the names of those."""
    files = changed_files(repo, base, head, paths)
    named = [p for p in paths if (repo / p).is_file() and p not in {new for _, new in files}]
    place = {p: i for i, p in enumerate(paths)}
    return sorted(files + [(p, p) for p in named], key=lambda f: place.get(f[1] or f[0], len(place))), named


def rebuild(out):
    """The page for the run recorded in out, then build.json, so an open page reloads into it."""
    run = json.loads((out / '.run.json').read_text())
    repo = pathlib.Path(run['repo'])
    files, named = files_for(repo, run['base'], run.get('head'), run['paths'])
    build_id = str(time.time_ns())
    actors = [f for f in (m.actions(run) for m in PLUGINS if hasattr(m, 'actions')) if f]
    page, shown, (todo, done) = build(repo, run['base'], run.get('head'), files, build_id, out, named, run,
                                      run.get('new_label'), bool(run.get('publish')), actors)
    # Each written whole and then swapped in, so a page that reloads meanwhile never gets half of one. The
    # counts are for the index of reviews.
    for name, text in (('index.html', page), ('build.json', json.dumps({'id': build_id, 'from': fingerprint(run),
                                                                        'todo': todo, 'done': done}))):
        (out / f'{name}.tmp').write_text(text)
        (out / f'{name}.tmp').replace(out / name)
    return repo, run['base'], shown


def fingerprint(run):
    """What a build was made from: the revisions, and the working tree's changed files with their sizes and
    times, so the server can tell when the repo has moved on (a commit, an edit, a new export)."""
    repo = pathlib.Path(run['repo'])
    revs = [git(repo, 'rev-parse', '--verify', '--quiet', r, check=False).stdout.decode().strip()
            for r in filter(None, (run['base'], run.get('head')))]
    if run.get('head'):
        return ' '.join(revs)
    marks = []
    for line in git(repo, 'status', '--porcelain', '-uall', check=False).stdout.decode().splitlines():
        try:
            s = (repo / line[3:].split(' -> ')[-1]).stat()
            marks.append(f'{line}:{s.st_size}:{s.st_mtime_ns}')
        except OSError:
            marks.append(line)
    return ' '.join(revs) + '|' + '|'.join(marks)


def move(out, req, accept):
    """Accept one change, or the whole file, into the accepted copy, or take it back out; then rebuild."""
    run = json.loads((out / '.run.json').read_text())
    repo, base, head, name = pathlib.Path(run['repo']), run['base'], run.get('head'), req['file']
    old, new = next((f for f in files_for(repo, base, head, run['paths'])[0] if (f[1] or f[0]) == name), (None, None))
    old_html, new_html, wait = htmls(repo, base, head, old, new, out)
    if wait:
        raise LookupError('that file is not rendered yet; reload the page')
    copy, wait = accepted_html(repo, base, head, out, old, new, old_html, new_html)
    if wait:
        raise LookupError('that file is not rendered yet; reload the page')
    if req.get('all'):
        text = new_html if accept else old_html
    else:
        # The key is a change's of base to head: the changes of the accepted copy at its place move (see
        # states()). A page from before keys a change of copy to head, or of base to copy, itself.
        reviewing, reviewed = block_diff(copy, new_html, 'x'), block_diff(old_html, copy, 'x')
        todo, done = states(block_diff(old_html, new_html, 'x'), reviewing, reviewed).get(req['key'], ([req['key']],) * 2)
        keys, pieces = set(todo if accept else done), (reviewing if accept else reviewed)[2]
        if not keys or not keys <= {k for _, _, ps in pieces for k, _, _ in ps}:
            raise LookupError('that change is gone; reload the page')
        text = apply(pieces, 'old' if accept else 'new', keys)
    save_copy(repo, base, out, name, text)
    see(repo, head, out, name, new_html)
    rebuild(out)


def reviewed_at(repo, base, head, out, paths, rev):
    """Makes rev the version each changed file was last reviewed at, as if you had reviewed it there (on
    GitHub, say): what changed since then is new since your last review. What is reviewed stays as it is;
    a round there was goes to <out>/backup first."""
    if git(repo, 'rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}', check=False).returncode:
        sys.exit(f'unknown revision: {rev}')
    for old, new in files_for(repo, base, head, paths)[0]:
        name, rnd = new or old, round_of(out, new or old)
        if rnd:
            backup(out, name)
        rnd['last'] = {'id': commit(repo, rev), 'time': now()}
        (out / 'accepted' / f'{name}.last.html').unlink(missing_ok=True)
        save_round(out, name, rnd)


def publish(out, name):
    """Runs the --publish command on the file's accepted copy, written as Markdown, and returns what it
    printed; once it succeeds, the accepted copy and its round start over from the base, which the command
    updated."""
    run = json.loads((out / '.run.json').read_text())
    copy, target = out / 'accepted' / f'{name}.html', out / 'accepted' / f'{name}.md'
    if name not in {new or old for old, new in files_for(pathlib.Path(run['repo']), run['base'], run.get('head'), run['paths'])[0]}:
        raise LookupError('no such file in this diff')
    if not copy.exists():
        raise LookupError('nothing accepted in that file')
    # Links are the render's own (bare addresses), not the text's, and would come back as Markdown links.
    page = re.sub(r'</?a\b[^>]*>', '', copy.read_text())
    r = subprocess.run(['pandoc', '-f', 'html', '-t', 'gfm', '--wrap=none'], input=page, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f'pandoc: {r.stderr.strip()}')
    target.write_text(r.stdout)
    cmd = run['publish'].replace('{file}', shlex.quote(name)).replace('{target}', shlex.quote(str(target)))
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    printed = (r.stdout + r.stderr).strip()
    if r.returncode:
        raise RuntimeError(printed or f'{cmd}: exit {r.returncode}')
    for f in (target, *(out / 'accepted' / f'{name}{s}' for s in COPY_FILES)):
        f.unlink(missing_ok=True)
    rebuild(out)
    return printed


def commit_accepted(out, req):
    """Commits the files req['files'] names, each as it reads on disk, once every change in it is accepted,
    with req['message'] (or one naming them). With --commit-into PATH (this page is an edit session's
    worktree), the commit then lands on that checkout too, cherry-picked; if it does not apply there, it
    stays on the session's branch and the reply says so. Then runs --after-commit in the background, if
    given (an export, so a page of the Google Docs shows the commit). Returns what happened."""
    run = json.loads((out / '.run.json').read_text())
    repo, base, head = pathlib.Path(run['repo']), run['base'], run.get('head')
    if not run.get('commit') or head:
        raise LookupError('this page does not commit')
    names = [str(n) for n in req.get('files') or []]
    files = {new or old: (old, new) for old, new in files_for(repo, base, head, run['paths'])[0]}
    for n in names:
        if n not in files:
            raise LookupError(f'{n}: not in this diff')
        old, new = files[n]
        old_html, new_html, wait = htmls(repo, base, head, old, new, out)
        copy, wait = (None, wait) if wait else accepted_html(repo, base, head, out, old, new, old_html, new_html)
        if wait or block_diff(copy, new_html, 'c')[1] or not block_diff(old_html, copy, 'c')[1]:
            raise LookupError(f'{n}: not every change in it is accepted')
    if not names:
        raise LookupError('nothing to commit')
    message = str(req.get('message') or '').strip() or 'Update ' + ', '.join(names)
    for args in (['add', '--', *names], ['commit', '-q', '-m', message, '--', *names]):
        r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError((r.stdout + r.stderr).strip() or f'git {args[0]} failed')
    done = f"committed {git(repo, 'log', '-1', '--format=%h %s').stdout.decode().strip()}"
    if run.get('commit_into'):
        into, sha = run['commit_into'], git(repo, 'rev-parse', 'HEAD').stdout.decode().strip()
        r = subprocess.run(['git', '-C', into, 'cherry-pick', sha], capture_output=True, text=True)
        if r.returncode:
            subprocess.run(['git', '-C', into, 'cherry-pick', '--abort'], capture_output=True)
            why = [l for l in (r.stdout + r.stderr).splitlines() if l.startswith(('CONFLICT', 'error', 'fatal'))]
            done += f'; not landed on {into}, it stays on this session\'s branch: ' + (why[0] if why else 'git cherry-pick failed')
        else:
            done += f"; landed on {into} as {git(pathlib.Path(into), 'log', '-1', '--format=%h').stdout.decode().strip()}"
    if run.get('after_commit'):
        with open(out / 'after-commit.log', 'w') as log:
            subprocess.Popen(run['after_commit'], shell=True, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    rebuild(out)
    return done


def respond(h, method):
    """A request to the umbrella server (h, an Umbrella), in docdiff's code as it is now. / is the index of
    the reviews; /<name>/… is a review's: the files in its dir, with caching off, so a reload after a rebuild
    shows the new page, and its routes (see review_get(), review_post()). It answers on 127.0.0.1, and on
    localhost, where specs render (respec.py), with a review's sources alone, so a spec's own scripts read
    no review's state; to any other name for this address (a page elsewhere that points a name of its own
    at it, the port being fixed), nothing. Only the page posts: a spec's render frame is on localhost,
    another origin, though the same server."""
    port, url = h.server.server_address[1], urllib.parse.urlsplit(h.path)
    m = re.fullmatch(r'/([^/]*)(/.*)?', url.path)
    frame = method != 'POST' and m and (m.group(2) or '').startswith('/src/')
    if h.headers.get('Host') != f'127.0.0.1:{port}' and not (frame and h.headers.get('Host') == f'localhost:{port}'):
        return h.send_error(403)
    if method == 'POST' and h.headers.get('Origin') != f'http://127.0.0.1:{port}':
        return h.send_error(403)
    if url.path in ('/', '/index.html'):
        return send(h, 200, index().encode(), 'text/html; charset=utf-8', method)
    # Every page asks for one; none, rather than a 404 in the log each time.
    if url.path == '/favicon.ico':
        return send(h, 204, b'', 'image/x-icon', method)
    # What running() asks for.
    if url.path == '/.docdiff.json':
        return send(h, 200, json.dumps({'pid': os.getpid(), 'port': port}).encode(), 'application/json', method)
    out = HOME / m.group(1) if m and NAME.fullmatch(m.group(1)) else None
    if not out or not (out / '.run.json').is_file():
        return h.send_error(404, 'no such review')
    if m.group(2) is None:
        h.send_response(301)
        h.send_header('Location', f'{url.path}/' + (f'?{url.query}' if url.query else ''))
        h.send_header('Content-Length', '0')
        return h.end_headers()
    h.path, h.directory = m.group(2) + (f'?{url.query}' if url.query else ''), str(out)
    # Rebuilds and posts take turns, per review.
    lock = h.server.locks.setdefault(out.name, threading.Lock())
    if method == 'HEAD':
        return http.server.SimpleHTTPRequestHandler.do_HEAD(h)
    (review_post if method == 'POST' else review_get)(h, out, lock)


def review_get(h, out, lock):
    """A GET in a review: build.json, rebuilt first if the repo has moved on; a plugin's route (see
    load_plugins); or else a file in its dir."""
    url = urllib.parse.urlsplit(h.path)
    run = json.loads((out / '.run.json').read_text())
    # An open page asks for build.json every second or two: when the repo has moved on since the
    # build, rebuild first, so the page reloads into what the repo now holds.
    if url.path == '/build.json':
        with lock:
            if json.loads((out / 'build.json').read_text()).get('from') != fingerprint(run):
                rebuild(out)
    got = next(filter(None, (m.get(run, urllib.parse.unquote(url.path), url.query)
                             for m in PLUGINS if hasattr(m, 'get'))), None)
    if not got:
        return http.server.SimpleHTTPRequestHandler.do_GET(h)
    data, mime = got
    if data is None:
        return h.send_error(404)
    send(h, 200, data, mime)


def review_post(h, out, lock):
    """The page's POSTs in a review: /accept and /unaccept move a change into or out of the accepted copy
    and rebuild the page; /revisit appends to revisit.jsonl; /commit and /publish commit or publish the
    accepted changes (see commit_accepted(), publish()); /open opens a link. Plugins add routes of their
    own (see load_plugins)."""
    req = json.loads(h.rfile.read(int(h.headers['Content-Length'])))
    # A link to the web, opened in the default browser rather than in the page's own, which may be an
    # embedded browser's tab.
    if h.path == '/open':
        url = str(req.get('url', ''))
        if not re.match(r'https?://', url) or urllib.parse.urlsplit(url).hostname in ('127.0.0.1', 'localhost'):
            return h.send_error(400)
        return send(h, 200, json.dumps({'opened': webbrowser.open(url)}).encode(), 'application/json')
    with lock:
        if h.path in ('/accept', '/unaccept'):
            try:
                move(out, req, h.path == '/accept')
            except LookupError as e:
                return h.send_error(409, str(e))
            reply = {}
        elif h.path == '/revisit':
            with open(out / 'revisit.jsonl', 'a') as f:
                f.write(json.dumps({'time': time.strftime('%Y-%m-%dT%H:%M:%S'), **req}, ensure_ascii=False) + '\n')
            reply = {}
        elif h.path == '/commit':
            try:
                data, code = commit_accepted(out, req).encode(), 200
            except (LookupError, RuntimeError) as e:
                data, code = str(e).encode(), 409 if isinstance(e, LookupError) else 500
            return send(h, code, data, 'text/plain; charset=utf-8')
        elif h.path == '/publish':
            try:
                data, code = publish(out, req['file']).encode(), 200
            except (LookupError, RuntimeError) as e:
                data, code = str(e).encode(), 409 if isinstance(e, LookupError) else 500
            return send(h, code, data, 'text/plain; charset=utf-8')
        else:
            reply = next(filter(lambda r: r is not None, (m.post(out, h.path, req)
                                                          for m in PLUGINS if hasattr(m, 'post'))), None)
            if reply is None:
                return h.send_error(404)
            if h.path not in {p for m in PLUGINS for p in getattr(m, 'IN_PLACE', ())}:
                rebuild(out)
    send(h, 200, json.dumps(reply).encode(), 'application/json')


def send(h, code, data, mime, method='GET'):
    h.send_response(code)
    h.send_header('Content-Type', mime)
    h.send_header('Content-Length', str(len(data)))
    h.end_headers()
    if method != 'HEAD':
        h.wfile.write(data)


INDEX_STYLE = '''
main{padding-bottom:16px} h1{font-size:22px} .reviews{list-style:none;padding:0;margin:0}
.reviews li{padding:12px 0;border-bottom:1px solid var(--line)} .reviews .name{font-weight:600} .reviews .n{font-size:13px}
'''


def index():
    """The index of reviews, the last active first: each one's name, linked to its page, what it compares
    and from where, its counts (to review and reviewed, and the plugins' counts()), and when it was last
    active (run, accepted, commented, asked or answered)."""
    rows, e = [], html.escape
    for d in HOME.iterdir() if HOME.is_dir() else []:
        try:
            run = json.loads((d / '.run.json').read_text())
        except (OSError, ValueError):
            continue
        # A review whose dir is elsewhere (--out) shows once, by the name its link in HOME has.
        if not NAME.fullmatch(d.name) or run.get('name', d.name) != d.name:
            continue
        # One review that cannot be read (its repo gone, say) leaves the others listed.
        try:
            what, href = compared(run)
            b = json.loads((d / 'build.json').read_text()) if (d / 'build.json').exists() else {}
            counts = ([f'{b["todo"]} to review', f'{b["done"]} {WORDS[bool(run.get("head"))]["accepted"]}'] if 'todo' in b else []) + \
                     [w for m in PLUGINS if hasattr(m, 'counts') for w in m.counts(run) or []]
        except (Exception, SystemExit) as x:
            what, href, counts = f'not readable: {x}', None, []
        when = max(f.stat().st_mtime for f in [d / '.run.json', *(d / n for n in ('review.json', 'revisit.jsonl', 'answers.jsonl')),
                                                 *d.glob('accepted/*')] if f.exists())
        where = ' · '.join([str(run.get('repo', '')).replace(str(pathlib.Path.home()), '~', 1), *run.get('paths', [])])
        what = f'<a href="{e(href)}" target="_blank" rel="noopener">{e(what)}</a>' if href else e(what)
        rows.append((when, f'<li><a class="name" href="{e(d.name)}/index.html">{e(d.name)}</a> · {what}<div class="n">'
                           f'{e(" · ".join([*counts, time.strftime("active %b %-d, %H:%M", time.localtime(when))]))}</div>'
                           f'<div class="n">{e(where)}</div></li>'))
    listed = ''.join(r for _, r in sorted(rows, reverse=True)) or '<li class="n">No reviews yet.</li>'
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>docdiff · reviews</title><style>{STYLE}{INDEX_STYLE}</style></head><body><main><h1>Reviews</h1>
<p class="n">Every review in {e(str(HOME).replace(str(pathlib.Path.home()), '~', 1))}, the last active first.</p>
<ul class="reviews">{listed}</ul></main></body></html>'''


class Umbrella(http.server.SimpleHTTPRequestHandler):
    """The umbrella server's requests, each answered by docdiff's code as it is on disk now (see live())."""

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        live().respond(self, 'GET')

    def do_HEAD(self):
        live().respond(self, 'HEAD')

    def do_POST(self):
        live().respond(self, 'POST')

    def log_request(self, code='-', size='-'):
        # Open pages ask every second or two: only failures go to the log (log_error()).
        pass


# The code the umbrella server's requests run (see live()), and what it was loaded from.
LIVE, LIVE_LOCK = {}, threading.Lock()


def sources():
    """docdiff.py and its plugins, each with its time: when these change, live() loads the code again."""
    me = pathlib.Path(__file__).resolve()
    return [(str(f), f.stat().st_mtime_ns) for f in [me, *sorted((me.parent / 'plugins').glob('*.py'))]]


def live():
    """docdiff as it is on disk now: this module, loaded again once docdiff.py or a plugin has changed, or a
    plugin came or went, so new code runs without a restart. Code that does not load goes to the log, and
    the code before it goes on."""
    now = sources()
    with LIVE_LOCK:
        if LIVE['sources'] != now:
            LIVE['sources'] = now
            spec = importlib.util.spec_from_file_location('docdiff_live', now[0][0])
            mod = importlib.util.module_from_spec(spec)
            # Its plugins find it there (see load_plugins).
            sys.modules[spec.name] = mod
            try:
                spec.loader.exec_module(mod)
                LIVE['mod'] = mod
            except Exception:
                traceback.print_exc()
        return LIVE['mod']


def umbrella(port):
    """Serves every review in HOME on port (see respond()) until stopped. A port in use is waited for: the
    LaunchAgent's server, while one started by --serve still runs, takes over when that one stops."""
    LIVE.update(mod=sys.modules[__name__], sources=sources())
    waiting = False
    while True:
        try:
            server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Umbrella)
            break
        except OSError as e:
            if not waiting:
                print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} port {port}: {e.strerror}; waiting for it', flush=True)
            waiting = True
            time.sleep(5)
    server.locks = {}
    print(f'{time.strftime("%Y-%m-%d %H:%M:%S")} pid {os.getpid()} serves {HOME} on http://127.0.0.1:{port}/', flush=True)
    server.serve_forever()


def running(port):
    """The umbrella server on port, {pid, port}; None if none answers."""
    try:
        return json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/.docdiff.json', timeout=2).read())
    except (OSError, ValueError):
        return None


def start(port):
    """Starts the umbrella server apart from this process, so it outlives the command and the shell that
    ran it, its output in HOME/server.log; returns once it answers."""
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / 'server.log', 'a') as log:
        p = subprocess.Popen([sys.executable, '-I', os.path.abspath(__file__), '--umbrella', '--port', str(port)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        got = running(port)
        if got:
            # Another one came up first (the LaunchAgent's, another run's): this one is not needed.
            if got['pid'] != p.pid:
                p.terminate()
            return
        if p.poll() is not None:
            break
        time.sleep(0.1)
    p.terminate()
    sys.exit(f'no docdiff server came up on port {port} (is something else on it?): see {HOME / "server.log"}')


def stop(port):
    """Stops the umbrella server on port, if one runs (under the LaunchAgent, launchd starts it again);
    returns what ran."""
    got = running(port)
    if got:
        os.kill(got['pid'], signal.SIGTERM)
        for _ in range(50):
            if (running(port) or {}).get('pid') != got['pid']:
                break
            time.sleep(0.1)
    return got


def restart(port):
    """Restarts the umbrella server: launchd does, under the LaunchAgent; else it is stopped and started.
    Needed only after a change to the server itself (umbrella(), Umbrella, live()): the rest of docdiff's
    code is loaded again on its own."""
    was = running(port)
    if port == PORT and not subprocess.run(['launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/{AGENT}'],
                                           capture_output=True).returncode:
        for _ in range(100):
            if (running(port) or was or {}).get('pid') != (was or {}).get('pid'):
                return
            time.sleep(0.1)
        sys.exit(f'launchd restarted {AGENT}, and nothing new answers on port {port}: see {HOME / "server.log"}')
    stop(port)
    start(port)


def agent():
    """The LaunchAgent that keeps the umbrella server running, as a plist: started at login, started again
    when it stops, its output in HOME/server.log. Its PATH has pandoc's and git's dirs, as launchd's own
    has no Homebrew."""
    tools = [os.path.dirname(t) for t in map(shutil.which, ('pandoc', 'git')) if t]
    return plistlib.dumps({
        'Label': AGENT,
        'ProgramArguments': [shutil.which('python3') or sys.executable, '-I', os.path.abspath(__file__), '--umbrella'],
        'EnvironmentVariables': {'PATH': ':'.join(dict.fromkeys([*tools, '/usr/bin', '/bin', '/usr/sbin', '/sbin']))},
        'RunAtLoad': True, 'KeepAlive': True,
        'StandardOutPath': str(HOME / 'server.log'), 'StandardErrorPath': str(HOME / 'server.log'),
    }).decode()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', default='.')
    p.add_argument('--base', default='HEAD')
    p.add_argument('--head')
    p.add_argument('--name', help="the review's name: its dir is ~/.docdiff/<name> and its page /<name>/ (default: "
                                  "--out's dir name, or else the repo's name, with --pr and the PR's number)")
    p.add_argument('--out', help="the review's dir, elsewhere than ~/.docdiff/<name>, which then links to it")
    p.add_argument('--new-label')
    p.add_argument('--publish')
    p.add_argument('--commit', action='store_true')
    p.add_argument('--after-commit')
    p.add_argument('--commit-into')
    p.add_argument('--reviewed-at', metavar='REV',
                   help='count what changed since REV as new since your last review (once: it is not kept for reruns)')
    p.add_argument('--serve', action='store_true')
    p.add_argument('--port', type=int, default=PORT, help="the umbrella server's port")
    p.add_argument('--umbrella', action='store_true', help='run the umbrella server here (what --serve starts)')
    p.add_argument('--restart', action='store_true', help='restart the umbrella server')
    p.add_argument('--stop', action='store_true', help='stop the umbrella server')
    p.add_argument('--install-agent', action='store_true',
                   help='print a LaunchAgent that keeps the umbrella server running, to install by hand')
    p.add_argument('paths', nargs='*')
    for m in PLUGINS:
        if hasattr(m, 'add_args'):
            m.add_args(p)
    a = p.parse_args()
    if a.umbrella:
        return umbrella(a.port)
    if a.restart:
        restart(a.port)
        return print(f'restarted: http://127.0.0.1:{a.port}/')
    if a.stop:
        got = stop(a.port)
        return print(f'stopped pid {got["pid"]}' if got else f'no docdiff server on port {a.port}')
    if a.install_agent:
        print(agent(), end='')
        plist, me = f'~/Library/LaunchAgents/{AGENT}.plist', f'python3 -I {shlex.quote(os.path.abspath(__file__))}'
        return print(f'\nTo install: save it as {plist}, stop the server started by --serve, and load it:\n'
                     f'  {me} --install-agent > {plist}\n'
                     f'  {me} --stop\n'
                     f'  launchctl bootstrap gui/$(id -u) {plist}\n'
                     f'To remove: launchctl bootout gui/$(id -u)/{AGENT} && rm {plist}', file=sys.stderr)
    if not shutil.which('pandoc'):
        sys.exit('pandoc not found: brew install pandoc')
    top = git(pathlib.Path(a.repo), 'rev-parse', '--show-toplevel').stdout.decode().strip()
    repo = pathlib.Path(top)
    for rev in filter(None, (a.base, a.head)):
        if git(repo, 'rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}', check=False).returncode:
            sys.exit(f'unknown revision: {rev}')
    if a.name and (not NAME.fullmatch(a.name) or a.name == 'clones'):
        sys.exit(f'--name {a.name}: letters, digits, _, . and -, starting with a letter, digit or _; not clones')
    name = a.name or re.sub(r'[^A-Za-z0-9_.-]+', '-', pathlib.Path(a.out).resolve().name if a.out else next(
        filter(None, (m.name({**vars(a), 'repo': str(repo)}) for m in PLUGINS if hasattr(m, 'name'))), None) or repo.name).lstrip('.-')
    out = pathlib.Path(a.out) if a.out else HOME / name
    was = json.loads((out / '.run.json').read_text()).get('repo') if (out / '.run.json').exists() else None
    if was and was != str(repo):
        sys.exit(f'{out} is the review of {was}: name this one with --name')
    out.mkdir(parents=True, exist_ok=True)
    # A dir elsewhere is served by a link to it in HOME, by the review's name.
    link = HOME / name
    if link.resolve() != out.resolve():
        if link.is_symlink() and not (link / '.run.json').exists():
            link.unlink()
        if link.exists():
            sys.exit(f'{link} is another review\'s: name this one with --name')
        HOME.mkdir(parents=True, exist_ok=True)
        link.symlink_to(out.resolve())
    # The server rebuilds from this after an accept; plugins read their arguments from it.
    run = {**{k: v for k, v in vars(a).items() if k != 'reviewed_at'}, 'name': name, 'repo': str(repo), 'out': str(out.resolve())}
    (out / '.run.json').write_text(json.dumps(run))
    if a.reviewed_at:
        reviewed_at(repo, a.base, a.head, out, a.paths, a.reviewed_at)
    _, _, shown = rebuild(out)
    print(f'{shown} changed doc(s) in {repo} vs {a.base}{f"..{a.head}" if a.head else ""} → {out / "index.html"}',
          flush=True)
    if not shown and not page_top(run):
        return
    if a.serve:
        if not running(a.port):
            start(a.port)
        print(f'URL: http://127.0.0.1:{a.port}/{name}/index.html', flush=True)


if __name__ == '__main__':
    main()
