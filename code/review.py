#!/usr/bin/env python3
"""Review a Claude Code session's edits on a page: each file whole, its changes inline, with Accept and
Reject per change and per file. Or a git work tree's changes against a commit, whoever made them.

Usage: review.py [--session ID] [--port N]   start the server unless it runs; print the page's URL
       review.py --git [REPO] [--base REV]   make or refresh the git review of REPO's work tree (the
                                             current folder's) against REV (HEAD); print its page's URL
       review.py --wait [--session ID]       print the comments nobody took that the pages of the
                                             session and of its git reviews sent, or else wait for
                                             the next ones; Claude keeps it running in the background
       review.py --answer ID                 put Claude's answer, read from stdin, under the comment
                                             on its page
       review.py --prompt-hook               UserPromptSubmit hook: hands the session's comments to
                                             the model, those no waiting command took

The server is redpen's (../base/server.py): it has the pages under /code/, lists the sessions in its
index, and runs this file as it is on disk at each request.

The page reads the record recorder.py keeps in ~/.redpen/code/<session>/: files/<key>.json
names a file the session edited, and base/<key> is its baseline, the text its pending changes are
measured from. Beside them, in the record's review/ folder, the page keeps:

  orig/<key>        the file's text before the session's first edit, apart from the baseline, which
                    moves; orig/<key>.none for a file the session created
  journal.json      what was accepted, rejected and unaccepted here, oldest first, each naming the text
                    it replaced and the text it wrote, so it can be undone and redone
  blobs/<sha256>    those texts
  comments.json     the comments waiting to be sent to Claude
  sent.jsonl        the batches of comments sent, one per line: by the page's Send now, for the
                    command Claude keeps waiting (--wait), or else by the hook, with the session's
                    next message
  handed.jsonl      which of them Claude was handed, and how (see ../base/channel.py)
  answers.jsonl     Claude's answers, each naming the comment it is under, and the threads the user
                    resolved, one per line

So a file has three texts: orig, base and disk (the file itself). Pending changes are base → disk:
Accept moves one into base, Reject writes base's lines back into the file. Accepted changes are
orig → base: Unaccept puts orig's lines back into base, which makes the change pending again.

A change is named by line ranges of the two texts it lies between, counted the way the page's editor
counts lines: a text split at every "\\n", so a text that ends in a newline has an empty last line.

A git review is a record of the same shape that no hook keeps, ~/.redpen/code/git-<name>/: git.json
names the repository, the base commit and the session that made it, in place of session.json, and
each file that differs from the base commit has that commit's text as its orig and as its first
baseline. Its files are found when the command runs and each time its page loads; git is only read.
"""
import argparse, datetime, filecmp, hashlib, importlib.util, json, os, pathlib, re, subprocess, sys
import tempfile, threading, time, urllib.parse

home = pathlib.Path.home()
store = home / '.redpen/code'
history = home / '.claude/file-history'
here = pathlib.Path(__file__).resolve().parent
# Files under these are Claude's own and temporary ones, not the work's: the page lists them apart.
other_roots = (f'{home}/.claude/', '/tmp/', '/private/tmp/', '/var/folders/', '/private/var/folders/')
# A text past this is not sent to the page.
shown_bytes = 16 << 20
# A git review of more changed files than this is refused: the base is not the one meant.
scanned_files = 5000
# A git review nothing was done in for this many seconds is not one the model is told to wait on.
active_seconds = 12 * 3600
# The git reviews whose files are being found now, and the lock that set is read and written under.
scans = set()
scans_lock = threading.Lock()

# What each action reads and writes: the two texts the change lies between, the one written, and
# whether the left text's lines go into the right one (else the right one's into the left).
actions = {
    'accept': ('base', 'disk', 'base', False),
    'reject': ('base', 'disk', 'disk', True),
    'unaccept': ('orig', 'base', 'base', True),
}

# The text a comment's lines are counted in, as the model is told; the file as it is goes unsaid.
sides = {
    'orig': ' (of the file as it was before your edits)',
    'base': ' (of the accepted text, not the file as it is)',
    'disk': '',
}

types = {
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.ttf': 'font/ttf',
}


class Refusal(Exception):
    """A request the server does not carry out: the HTTP status and what to tell the page."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


def key(path):
    """A file's name in a record, as recorder.py makes it."""
    return hashlib.sha256(path.encode()).hexdigest()[:16]


def sha(data):
    return '-' if data is None else hashlib.sha256(data).hexdigest()


def signature(place):
    """Changes whenever the file at place does; '-' while there is none."""
    try:
        stat = place.stat()
    except OSError:
        return '-'
    return f'{stat.st_mtime_ns}:{stat.st_size}' if place.is_file() else '-'


def read_bytes(place):
    try:
        return place.read_bytes()
    except OSError:
        return None


def read_json(place, default=None):
    try:
        return json.loads(place.read_text())
    except (OSError, ValueError):
        return default


def write_atomic(target, data):
    """Readers see the old file or the new one, never a part of it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix='.tmp-')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.replace(tmp, target)


def shown(data):
    """The text the page shows for a file's bytes; None when it is not text the page can show."""
    if data is None:
        return ''
    if len(data) > shown_bytes:
        return None
    try:
        return data.decode()
    except UnicodeDecodeError:
        return None


counts = {}


def compared(a, a_signature, b, b_signature):
    """How the file at b differs from the one at a: None when it does not, else its added and removed
    line counts. Remembered until either file changes."""
    slot = (str(a), a_signature, str(b), b_signature)
    if slot not in counts:
        if len(counts) > 4000:
            counts.clear()
        counts[slot] = count(None if a_signature == '-' else a, None if b_signature == '-' else b)
    return counts[slot]


def count(a, b):
    if a is None and b is None or a and b and filecmp.cmp(a, b, shallow=False):
        return None
    ran = subprocess.run(['git', 'diff', '--no-index', '--numstat', '--no-ext-diff', '--',
                          a or '/dev/null', b or '/dev/null'], capture_output=True, text=True, cwd='/')
    found = re.match(r'(\d+|-)\t(\d+|-)\t', ran.stdout)
    # No line differs, yet the files do: an empty file against none.
    if not found:
        return {'added': 0, 'removed': 0}
    if found.group(1) == '-':
        return {'isBinary': True}
    return {'added': int(found.group(1)), 'removed': int(found.group(2))}


def spliced(left, right, change, is_left_taken):
    """The right text with the change's lines replaced by the left text's, or the other way round."""
    try:
        lefts, rights = (left or b'').decode().split('\n'), (right or b'').decode().split('\n')
    except UnicodeDecodeError:
        raise Refusal(422, 'The file is not text.')
    try:
        spans = [int(change[name]) for name in ('leftStart', 'leftEnd', 'rightStart', 'rightEnd')]
    except (KeyError, TypeError, ValueError):
        raise Refusal(400, 'The change names no line ranges.')
    left_start, left_end, right_start, right_end = spans
    if not (0 <= left_start <= left_end <= len(lefts) and 0 <= right_start <= right_end <= len(rights)):
        raise Refusal(409, 'The change lies outside the file.')
    if is_left_taken:
        rights[right_start:right_end] = lefts[left_start:left_end]
        return '\n'.join(rights).encode()
    lefts[left_start:left_end] = rights[right_start:right_end]
    return '\n'.join(lefts).encode()


class Review:
    """One session's record, and what the page keeps beside it."""

    def __init__(self, session):
        if not re.fullmatch(r'[\w-]+', session) or not (store / session / 'files').is_dir():
            raise Refusal(404, 'No such session.')
        self.session = session
        self.record = store / session
        # What a git review is of: {repo, base, spec, title, owner, updated}; None for a session.
        self.git = git_info(self.record)
        self.folder = self.record / 'review'
        # The log of the comments sent to Claude, kept by redpen's channel (../base/channel.py).
        self.sent = self.folder / 'sent.jsonl'

    def entries(self):
        for entry in sorted((self.record / 'files').glob('*.json')):
            file = read_json(entry)
            if isinstance(file, dict) and isinstance(file.get('path'), str):
                yield entry.stem, file

    def file(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{16}', key):
            raise Refusal(400, 'No such file.')
        file = read_json(self.record / 'files' / f'{key}.json')
        if not isinstance(file, dict) or not isinstance(file.get('path'), str):
            raise Refusal(404, 'The session did not edit this file.')
        return file

    def places(self, key, file):
        """Where the file's three texts are; a place may hold no file."""
        self.settle(key, file)
        return {
            'orig': self.folder / 'orig' / key,
            'base': self.record / 'base' / key,
            'disk': pathlib.Path(file['path']),
        }

    def settle(self, key, file):
        """Keeps the file's text before the session, once: from Claude Code's file history, else, for a
        file the session did not create, the baseline as it is now."""
        orig = self.folder / 'orig' / key
        none = self.folder / 'orig' / f'{key}.none'
        if orig.exists() or none.exists():
            return
        first = history / self.session / f'{key}@v1'
        base = self.record / 'base' / key
        source = first if first.is_file() else base if base.is_file() and not file.get('new') else None
        if source:
            write_atomic(orig, source.read_bytes())
        else:
            write_atomic(none, b'')

    def listing(self):
        files = []
        for key, file in self.entries():
            places = self.places(key, file)
            signatures = {name: signature(place) for name, place in places.items()}
            files.append({
                'key': key,
                'path': file['path'],
                'isNew': signatures['orig'] == '-',
                'isGone': signatures['disk'] == '-',
                'signature': ' '.join(signatures.values()),
                'pending': compared(places['base'], signatures['base'], places['disk'], signatures['disk']),
                'accepted': compared(places['orig'], signatures['orig'], places['base'], signatures['base']),
                'all': compared(places['orig'], signatures['orig'], places['disk'], signatures['disk']),
                'isOther': not self.git and any(file['path'].startswith(root) for root in other_roots),
            })
        info = self.git or read_json(self.record / 'session.json', {})
        channel = shared('channel')
        return {'session': self.session, 'title': info.get('title') or 'Claude session', 'files': files,
                'comments': self.comments(),
                'threads': self.threads(),
                # Whether Claude's command waits for comments now, and how many sent ones nobody took.
                'isWatched': channel.watching(self.sent) == 'wait',
                # Whether a git review's files are still being found.
                'isScanning': self.session in scans,
                'untaken': sum(len(ask.get('comments') or []) for ask in channel.waiting(self.sent))}

    def opened(self, key):
        file = self.file(key)
        texts = {}
        for name, place in self.places(key, file).items():
            data = read_bytes(place)
            texts[name] = {'sha': sha(data), 'exists': data is not None, 'text': shown(data)}
        return {'key': key, 'path': file['path'], 'texts': texts}

    def apply(self, key, action, change, expected):
        """Carries out accept, reject or unaccept, for one change or, with no change named, the whole
        file, if the two texts are still the ones the page showed."""
        file = self.file(key)
        if action not in actions:
            raise Refusal(400, 'No such action.')
        left_name, right_name, target, is_left_taken = actions[action]
        places = self.places(key, file)
        left, right = read_bytes(places[left_name]), read_bytes(places[right_name])
        if not isinstance(expected, dict) or (sha(left), sha(right)) != (expected.get('left'), expected.get('right')):
            raise Refusal(409, 'The file changed since it was shown.')
        if change is None:
            result = left if is_left_taken else right
        else:
            result = spliced(left, right, change, is_left_taken)
        before = right if target == right_name else left
        entry = {
            'id': f'{time.time_ns():x}',
            'time': now(),
            'action': action,
            'key': key,
            'path': file['path'],
            'change': change,
            'target': target,
            'before': self.kept(before),
            'after': self.kept(result),
            'isUndone': False,
        }
        self.write(key, file, target, result)
        write_atomic(self.folder / 'journal.json', json.dumps(self.journal() + [entry], indent=1).encode())
        return entry

    def flip(self, entry_id):
        """Undoes a journal entry, or redoes an undone one, if the text it wrote is still in place."""
        journal = self.journal()
        entry = next((e for e in journal if e['id'] == entry_id), None)
        if not entry:
            raise Refusal(404, 'No such entry.')
        file = self.file(entry['key'])
        here_name, there_name = ('before', 'after') if entry['isUndone'] else ('after', 'before')
        place = self.places(entry['key'], file)[entry['target']]
        if sha(read_bytes(place)) != entry[here_name]:
            raise Refusal(409, 'The file changed since.')
        there = None if entry[there_name] == '-' else read_bytes(self.folder / 'blobs' / entry[there_name])
        if entry[there_name] != '-' and there is None:
            raise Refusal(410, 'The text to put back is gone.')
        self.write(entry['key'], file, entry['target'], there)
        entry['isUndone'] = not entry['isUndone']
        write_atomic(self.folder / 'journal.json', json.dumps(journal, indent=1).encode())
        return entry

    def journal(self):
        journal = read_json(self.folder / 'journal.json', [])
        return journal if isinstance(journal, list) else []

    def journal_shown(self):
        """The newest entries first, each saying whether it can be undone (or redone) now."""
        hashes = {}
        entries = []
        for entry in reversed(self.journal()[-200:]):
            try:
                place = self.places(entry['key'], self.file(entry['key']))[entry['target']]
            except Refusal:
                entries.append({**entry, 'canFlip': False})
                continue
            if place not in hashes:
                hashes[place] = sha(read_bytes(place))
            entries.append({**entry, 'canFlip': hashes[place] == entry['before' if entry['isUndone'] else 'after']})
        return entries

    def kept(self, data):
        """Keeps the text among the blobs; returns its name there."""
        name = sha(data)
        blob = self.folder / 'blobs' / name
        if data is not None and not blob.exists():
            write_atomic(blob, data)
        return name

    def write(self, key, file, target, data):
        place = self.places(key, file)[target]
        if target == 'disk':
            if data is None:
                # As the Claude Edits pane undoes a file the session created.
                ran = subprocess.run(['/usr/bin/trash', str(place)], capture_output=True, text=True)
                if ran.returncode:
                    raise Refusal(500, f'Could not move the file to the Trash: {ran.stderr.strip()}')
                return
            place.parent.mkdir(parents=True, exist_ok=True)
            # In place: the file keeps its mode and its links.
            with open(place, 'wb') as f:
                f.write(data)
        elif data is None:
            # As the Claude Edits pane keeps a deleted file: no baseline, and new.
            place.unlink(missing_ok=True)
            write_atomic(self.record / 'files' / f'{key}.json', json.dumps({'path': file['path'], 'new': True}).encode())
        else:
            write_atomic(place, data)

    def comments(self):
        comments = read_json(self.folder / 'comments.json', [])
        return comments if isinstance(comments, list) else []

    def comment(self, body):
        file = self.file(body.get('key'))
        text = body.get('text')
        if not isinstance(text, str) or not text.strip():
            raise Refusal(400, 'The comment is empty.')
        try:
            start, end = int(body.get('start')), int(body.get('end'))
        except (TypeError, ValueError):
            raise Refusal(400, 'The comment names no lines.')
        comment = {
            'id': f'{time.time_ns():x}',
            'time': now(),
            'key': body['key'],
            'path': file['path'],
            'side': body['side'] if body.get('side') in sides else 'disk',
            'start': start,
            'end': end,
            'quote': str(body.get('quote') or '')[:4000],
            'text': text.strip()[:4000],
        }
        # A comment on removed lines: the line of the text shown that they stood after, and that text.
        if isinstance(body.get('anchor'), int) and body.get('anchorSide') in sides:
            comment.update(anchor=body['anchor'], anchorSide=body['anchorSide'])
        if body.get('thread'):
            if not any(thread['id'] == body['thread'] for thread in self.threads()):
                raise Refusal(404, 'No such thread.')
            comment['thread'] = body['thread']
        # Under the channel's lock, as the hook and Send now empty the file under it.
        with shared('channel').locked(self.sent):
            write_atomic(self.folder / 'comments.json', json.dumps(self.comments() + [comment], indent=1).encode())
        return comment

    def uncomment(self, comment_id):
        with shared('channel').locked(self.sent):
            left = [c for c in self.comments() if c.get('id') != comment_id]
            write_atomic(self.folder / 'comments.json', json.dumps(left, indent=1).encode())

    def send_now(self):
        """Sends the waiting comments to the command Claude keeps waiting for them (review.py --wait);
        with none waiting, they stay for the session's next message."""
        channel = shared('channel')
        with channel.locked(self.sent):
            comments = self.comments()
            if not comments:
                raise Refusal(409, 'No comment is waiting.')
            if channel.watching(self.sent) != 'wait':
                raise Refusal(409, 'Claude is not waiting for comments now: they go with your next message.')
            channel.append(self.sent, {'comments': comments})
            write_atomic(self.folder / 'comments.json', b'[]')
        return {'sent': len(comments)}

    def threads(self):
        """The comments as the page shows them, each first one with what followed it: the user's
        replies and Claude's answers, the oldest first, and of each of the user's whether Claude has
        it ('draft', 'sent' and not taken, 'taken'). A thread the user resolved is left out."""
        channel = shared('channel')
        handed = {line.get('ask') for line in channel.records(self.folder / 'handed.jsonl')}
        said = [dict(comment, state='taken' if ask.get('id') in handed else 'sent')
                for ask in channel.records(self.sent) for comment in ask.get('comments') or []
                if isinstance(comment, dict)]
        said += [dict(comment, state='draft') for comment in self.comments() if isinstance(comment, dict)]
        notes = channel.records(self.folder / 'answers.jsonl')
        threads, firsts = {}, {}
        for comment in said:
            first = comment.get('thread') if comment.get('thread') in threads else comment.get('id')
            firsts[comment.get('id')] = first
            where = {name: comment.get(name) for name in ('key', 'path', 'side', 'start', 'end', 'quote', 'anchor', 'anchorSide')}
            thread = threads.setdefault(first, {**where, 'id': first, 'entries': []})
            thread['entries'].append({'id': comment.get('id'), 'by': 'user', 'time': comment.get('time'),
                                      'text': comment.get('text'), 'state': comment['state']})
        for note in notes:
            thread = threads.get(firsts.get(note.get('comment')))
            if thread and isinstance(note.get('text'), str):
                thread['entries'].append({'by': 'claude', 'time': note.get('time'), 'text': note['text'],
                                          'change': note.get('change')})
        resolved = {note.get('comment') for note in notes if note.get('resolved')}
        for thread in threads.values():
            thread['entries'].sort(key=lambda entry: str(entry.get('time')))
        return [thread for first, thread in threads.items() if first not in resolved]

    def resolve(self, thread_id):
        """Takes a thread off the page; the log keeps what was said in it."""
        if not any(thread['id'] == thread_id for thread in self.threads()):
            raise Refusal(404, 'No such thread.')
        channel = shared('channel')
        with channel.locked(self.sent):
            channel.add_line(self.folder / 'answers.jsonl', {'comment': thread_id, 'time': now(), 'resolved': True})

    def answer(self, comment_id, text, change=None):
        """Puts Claude's answer under a comment that was sent to it, with the change made in answer,
        {key, path, line}, if one was; says whether the comment is this record's."""
        channel = shared('channel')
        with channel.locked(self.sent):
            sent = [comment.get('id') for ask in channel.records(self.sent) for comment in ask.get('comments') or []
                    if isinstance(comment, dict)]
            if comment_id not in sent:
                return False
            note = {'comment': comment_id, 'time': now(), 'text': text}
            # The file as the record names it, if it does under the path's other spelling.
            for path in (change['path'], os.path.realpath(change['path'])) if change else ():
                if (self.record / 'files' / f'{key(path)}.json').exists():
                    change = dict(change, key=key(path), path=path)
                    break
            channel.add_line(self.folder / 'answers.jsonl', dict(note, change=change) if change else note)
        return True


def fenced(text):
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}\n{text}\n{fence}'


def described(comments, git=None):
    """The comments as the model is told them; git is what a git review is of."""
    changes = f"the changes in {git['repo']} against {git['spec']}" if git else 'your changes'
    out = [f'The user left these comments on {changes} on the Claude Edits review page; each quotes the '
           'lines it is on as they saw them.']
    for comment in comments:
        lines = f"line {comment['start']}" if comment['start'] == comment['end'] else f"lines {comment['start']}-{comment['end']}"
        side = sides.get(comment.get('side'), '')
        if git and comment.get('side') == 'orig':
            side = ' (of the file as it was at the base commit)'
        reply = f", a reply in the thread of comment {comment['thread']}" if comment.get('thread') else ''
        out += ['', f"{comment['path']}, {lines}{side} (comment {comment.get('id')}{reply}):"]
        if comment.get('quote'):
            out.append(fenced(comment['quote']))
        out.append(f"Comment: {comment['text']}")
    out += ['', 'Besides what you say or do about each, put a short answer in plain text under it on the page: '
            f"`python3 {here / 'review.py'} --answer <comment id>`, the answer on stdin; after a change made "
            'in answer, add `--change <file>:<line>`, and the answer links to it.']
    return '\n'.join(out)


def prompt_hook():
    """Hands the session's comments to the model with the prompt being submitted: the waiting ones,
    and the sent ones no waiting command took, of the session's own record and of the git reviews
    made from it. A failure never blocks the prompt: it goes to stderr and the exit status stays 0."""
    try:
        session = json.load(sys.stdin).get('session_id', '')
        if not re.fullmatch(r'[\w-]+', session):
            return
        folders = [store / name / 'review' for name in [session] + owned(session)]
        commented = [f for f in folders if (f / 'comments.json').is_file() or (f / 'sent.jsonl').is_file()]
        channel = shared('channel')
        found = []
        for review in [Review(folder.parent.name) for folder in commented]:
            with channel.locked(review.sent):
                comments, asks = review.comments(), channel.waiting(review.sent)
                told = [comment for ask in asks for comment in ask.get('comments') or []] + comments
                if told:
                    found.append((review, comments, asks, described(told, review.git)))
        unwaited = unwaited_reviews(session)
        if not found and not unwaited:
            return
        context = '\n\n'.join([text for *_, text in found] + unwaited)
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': context}}))
        sys.stdout.flush()
        for review, comments, asks, _ in found:
            with channel.locked(review.sent):
                # Kept in the log, so a batch the prompt did not carry after all can be read back.
                if comments:
                    told = {comment.get('id') for comment in comments}
                    asks.append(channel.append(review.sent, {'comments': comments}))
                    left = [comment for comment in review.comments() if comment.get('id') not in told]
                    write_atomic(review.folder / 'comments.json', json.dumps(left, indent=1).encode())
                channel.mark(review.sent, asks, 'prompt')
    except Exception as e:
        print(f'redpen review hook: {e!r}', file=sys.stderr)


def unwaited_reviews(session):
    """What tells the model to start its waiting command: said while a git review of the session's
    that something was done in lately has no command waiting for its comments."""
    channel = shared('channel')
    titles = []
    for name in owned(session):
        folder = store / name / 'review'
        marks = [store / name / 'git.json', folder / 'journal.json', folder / 'comments.json',
                 folder / 'sent.jsonl', folder / 'answers.jsonl']
        last = max((mark.stat().st_mtime for mark in marks if mark.exists()), default=0)
        if time.time() - last < active_seconds and channel.watching(folder / 'sent.jsonl') != 'wait':
            titles.append(git_info(store / name)['title'])
    if not titles:
        return []
    return [f"No command of yours waits for the comments of the review page of {'; '.join(titles)}. Run "
            f"`python3 {here / 'review.py'} --wait` in the background now, and again each time it ends: a "
            'comment the user sends from the page then reaches you at once.']


def waited_reviews(session):
    """The records one waiting command of the session's watches: its own and its git reviews'."""
    found = []
    for name in dict.fromkeys([session] + (owned(session) if session else [])):
        try:
            found.append(Review(name))
        except Refusal:
            pass
    return found


def wait(session):
    """Prints the comments nobody took that the pages of the session and of its git reviews sent, or
    else waits for the next ones, and ends: Claude keeps it running in the background while the user
    reviews, and starts it again each time. A git review made while it waits is waited on too."""
    if not waited_reviews(session):
        sys.exit(f'No record of session {session!r}: it edited no file yet, or --session names no session.')
    channel = shared('channel')
    while True:
        handed = 0
        for review in waited_reviews(session):
            review.folder.mkdir(parents=True, exist_ok=True)
            channel.beat(review.sent)
            handed += channel.hand_over(review.sent, 'wait',
                                        lambda ask: print(described(ask.get('comments') or [], review.git)))
        if handed:
            return
        time.sleep(1)


def answer(session, comment_id, where=None):
    """Puts the answer on stdin under the comment on its page: Claude's, to a comment it was handed.
    where, PATH or PATH:LINE, is the change made in answer. The session's own records are looked in
    first."""
    text = sys.stdin.read().strip()[:8000]
    if not text:
        sys.exit('No answer on stdin.')
    change = None
    if where:
        path, _, line = where.rpartition(':') if re.search(r':\d+$', where) else (where, '', '')
        path = os.path.abspath(path)
        change = {'key': key(path), 'path': path, 'line': int(line) if line else None}
    names = [review.session for review in waited_reviews(session)] + [s['session'] for s in sessions()]
    for name in dict.fromkeys(names):
        try:
            if Review(name).answer(comment_id, text, change):
                return print(f'Answered under comment {comment_id} on the page of {name}.')
        except Refusal:
            pass
    sys.exit(f'No comment {comment_id!r} was sent from a review page.')


def git(repo, *args):
    """What a git command prints, run in the repository without writing there (no index refresh);
    None when it fails."""
    try:
        ran = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                             env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    except OSError:
        return None
    return None if ran.returncode else ran.stdout


def git_info(record):
    """What the git review in the record is of; None for a session's record."""
    info = read_json(record / 'git.json')
    return info if isinstance(info, dict) and all(isinstance(info.get(n), str) for n in ('repo', 'base', 'spec')) else None


def git_review(folder, spec, owner):
    """Makes the git review of the folder's repository against the commit its work tree and spec both
    come from, or refreshes the one there is, and makes owner the session its comments reach.
    Returns the record's name."""
    top = git(folder, 'rev-parse', '--show-toplevel')
    if top is None:
        raise Refusal(404, f'{folder} is in no git work tree.')
    top = pathlib.Path(os.fsdecode(top).rstrip('\n'))
    base = git(top, 'merge-base', spec, 'HEAD')
    if base is None:
        raise Refusal(404, f'{spec} names no commit that HEAD comes from.')
    base = base.decode().strip()
    branch = (git(top, 'rev-parse', '--abbrev-ref', 'HEAD') or b'').decode().strip()
    name = f"git-{re.sub(r'[^A-Za-z0-9_-]', '_', top.name)}-{sha(str(top).encode())[:6]}-{base[:10]}"
    info = {
        'repo': str(top),
        'base': base,
        'spec': spec,
        'title': f'{top.name}: {branch} against {spec} ({base[:10]})',
        'owner': owner,
        'updated': now(),
    }
    scanned(store / name, info)
    (store / name / 'files').mkdir(parents=True, exist_ok=True)
    write_atomic(store / name / 'git.json', json.dumps(info, indent=1).encode())
    return name


def scanned(record, info):
    """Adds to a git review's record each file that differs from the base commit now, tracked or not
    (ignored ones apart), with that commit's text as its orig and its baseline. A file the record has
    stays as it is, so what was accepted stays accepted. Returns how many it added."""
    top = pathlib.Path(info['repo'])
    changed = git(top, 'diff', '--name-only', '-z', '--no-renames', '--ignore-submodules', info['base'], '--')
    untracked = git(top, 'ls-files', '--others', '--exclude-standard', '-z')
    if changed is None or untracked is None:
        raise Refusal(404, f'git could not compare {top} with {info["base"][:10]}.')
    names = sorted({os.fsdecode(name) for name in (changed + untracked).split(b'\0') if name})
    if len(names) > scanned_files:
        raise Refusal(413, f'{len(names)} files differ from {info["spec"]}: not a change to review file by file.')
    added = 0
    for name in names:
        place = top / name
        k = key(str(place))
        entry = record / 'files' / f'{k}.json'
        if entry.exists() or place.is_symlink() or place.is_dir():
            continue
        old = git(top, 'cat-file', 'blob', f'{info["base"]}:{name}')
        if old is None and not place.is_file():
            continue
        if old is None:
            write_atomic(record / 'review/orig' / f'{k}.none', b'')
        else:
            write_atomic(record / 'review/orig' / k, old)
            write_atomic(record / 'base' / k, old)
        write_atomic(entry, json.dumps({'path': str(place), 'new': old is None}).encode())
        added += 1
    return added


def rescan(review):
    """Finds a git review's files anew behind the page, one search at a time; a failure leaves those
    it has."""
    def run():
        try:
            scanned(review.record, review.git)
        except Refusal:
            pass
        finally:
            with scans_lock:
                scans.discard(review.session)

    with scans_lock:
        if review.session in scans:
            return
        scans.add(review.session)
    threading.Thread(target=run, daemon=True).start()


def owned(session):
    """The git reviews made from the session: their comments reach it as its own do."""
    return [info.parent.name for info in sorted(store.glob('git-*/git.json'))
            if (git_info(info.parent) or {}).get('owner') == session]


def sessions():
    """Every session and git review with a record, the one edited last first."""
    found = []
    for info in [*store.glob('*/session.json'), *store.glob('git-*/git.json')]:
        data = read_json(info, {})
        if (info.parent / 'files').is_dir():
            found.append({'session': info.parent.name, 'title': data.get('title') or 'Claude session',
                          'updated': data.get('updated') or '', 'files': len(list((info.parent / 'files').glob('*.json')))})
    return sorted(found, key=lambda s: s['updated'], reverse=True)


def listed_reviews():
    """The sessions and git reviews with a record, for the server's index: each one's title, where
    its page is, how many files it changed, and when it was last active (edited, reviewed or
    commented)."""
    rows = []
    for s in sessions():
        folder = store / s['session']
        marks = [folder / 'session.json', folder / 'git.json', folder / 'review/journal.json',
                 folder / 'review/comments.json', folder / 'review/sent.jsonl']
        rows.append({'name': s['title'], 'href': f's/{s["session"]}/',
                     'counts': [f'{s["files"]} file{"" if s["files"] == 1 else "s"}'],
                     'when': max((f.stat().st_mtime for f in marks if f.exists()), default=0)})
    return {'title': "Claude's edits", 'rows': rows,
            'note': 'Every session and git review with a record in ~/.redpen/code, the last active first.'}


def shared(name):
    """A module of redpen's base, ../base/<name>.py, loaded by its path, as python -I keeps this
    script's folder off the path; the server's own copy, when it is the server that runs this code."""
    module = sys.modules.get(f'redpen_base_{name}')
    if not module:
        spec = importlib.util.spec_from_file_location(f'redpen_base_{name}', here.parent / 'base' / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[spec.name] = module
    return module


def kept_lock(h):
    """The lock the records' reads and writes take turns under: the server keeps it, as this code
    comes and goes. Its key is no review's name in the server's other part."""
    return h.server.locks.setdefault('/code', threading.Lock())


def send(h, status, body, kind='text/plain; charset=utf-8', cache='no-store'):
    h.send_response(status)
    h.send_header('Content-Type', kind)
    h.send_header('Content-Length', str(len(body)))
    h.send_header('Cache-Control', cache)
    h.end_headers()
    if h.command != 'HEAD':
        h.wfile.write(body)


def send_json(h, value, status=200):
    send(h, status, json.dumps(value).encode(), 'application/json')


def send_file(h, root, name, cache='no-store'):
    target = (root / name).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        raise Refusal(404, 'Not found.')
    send(h, 200, target.read_bytes(), types.get(target.suffix, 'application/octet-stream'), cache)


def respond(h, method):
    """Answers a request redpen's server (../base/server.py) hands on, its path the one within /code.
    That a page of the server's own asks, the server has checked."""
    path = urllib.parse.unquote(urllib.parse.urlsplit(h.path).path)
    try:
        if method != 'POST':
            return get(h, path)
        length = int(h.headers.get('Content-Length') or 0)
        if not 0 < length <= 1 << 20:
            raise Refusal(400, 'No request body.')
        try:
            body = json.loads(h.rfile.read(length))
        except ValueError:
            raise Refusal(400, 'The request body is not JSON.')
        if not isinstance(body, dict):
            raise Refusal(400, 'The request body is not an object.')
        with kept_lock(h):
            return post(h, path, body)
    except Refusal as e:
        send_json(h, {'error': str(e)}, e.status)


def get(h, path):
    if path.startswith('/page/'):
        return send_file(h, here / 'page', path[6:])
    if path.startswith('/vs/'):
        return send_file(h, here / 'vendor/monaco', path[1:], 'public, max-age=86400')
    found = re.fullmatch(r'/s/([\w-]+)(/?)', path)
    if found:
        review = Review(found.group(1))
        if not found.group(2):
            h.send_response(301)
            h.send_header('Location', f'{found.group(1)}/')
            h.send_header('Content-Length', '0')
            h.end_headers()
            return
        if review.git:
            # A git review's files are found anew as its page loads: the page shows those it has
            # at once, and the others as they are found.
            rescan(review)
        return send_file(h, here / 'page', 'page.html')
    found = re.fullmatch(r'/api/([\w-]+)/(files|journal|file/([0-9a-f]{16}))', path)
    if not found:
        raise Refusal(404, 'Not found.')
    review = Review(found.group(1))
    with kept_lock(h):
        if found.group(2) == 'files':
            return send_json(h, review.listing())
        if found.group(2) == 'journal':
            return send_json(h, review.journal_shown())
        return send_json(h, review.opened(found.group(3)))


def post(h, path, body):
    found = re.fullmatch(r'/api/([\w-]+)/(apply|flip|comment|uncomment|send|resolve)', path)
    if not found:
        raise Refusal(404, 'Not found.')
    review = Review(found.group(1))
    if found.group(2) == 'apply':
        return send_json(h, review.apply(body.get('key'), body.get('action'), body.get('change'), body.get('expected')))
    if found.group(2) == 'flip':
        return send_json(h, review.flip(body.get('id')))
    if found.group(2) == 'comment':
        return send_json(h, review.comment(body))
    if found.group(2) == 'send':
        return send_json(h, review.send_now())
    if found.group(2) == 'resolve':
        review.resolve(body.get('id'))
        return send_json(h, {})
    review.uncomment(body.get('id'))
    send_json(h, {})


def main():
    parser = argparse.ArgumentParser(description='Review a Claude Code session\'s edits on a page.')
    parser.add_argument('--session', default=os.environ.get('CLAUDE_CODE_SESSION_ID', ''))
    parser.add_argument('--port', type=int)
    parser.add_argument('--prompt-hook', action='store_true')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--answer', metavar='ID')
    parser.add_argument('--change', metavar='PATH[:LINE]')
    parser.add_argument('--git', nargs='?', const='.', metavar='REPO')
    parser.add_argument('--base', default='HEAD', metavar='REV')
    args = parser.parse_args()
    # The hook reads the record alone, and no change to the server's code can fail it.
    if args.prompt_hook:
        return prompt_hook()
    if args.wait:
        return wait(args.session)
    if args.answer:
        return answer(args.session, args.answer, args.change)
    server = shared('server')
    port = args.port or server.default_port
    if not server.running(port):
        server.start(port)
    session = args.session or next((s['session'] for s in sessions()), '')
    if args.git:
        try:
            session = git_review(args.git, args.base, args.session)
        except Refusal as e:
            sys.exit(str(e))
    if session and (store / session / 'files').is_dir():
        print(f'http://127.0.0.1:{port}{server.mounts["code"]}/s/{session}/')
    else:
        if session:
            print(f'session {session} has edited no file yet', file=sys.stderr)
        print(f'http://127.0.0.1:{port}/')


if __name__ == '__main__':
    main()
