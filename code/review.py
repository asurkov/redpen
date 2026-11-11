#!/usr/bin/env python3
"""Review a Claude Code session's edits on a page: each file whole, its changes inline, with Accept and
Reject per change and per file.

Usage: review.py [--session ID] [--port N]   start the server unless it runs; print the page's URL
       review.py --prompt-hook               UserPromptSubmit hook: hands the session's waiting
                                             comments to the model

The server is redpen's (../base/server.py): it has the pages under /code/, lists the sessions in its
index, and runs this file as it is on disk at each request.

The page reads the record recorder.py keeps in ~/.claude/cursordiff/<session>/: files/<key>.json
names a file the session edited, and base/<key> is its baseline, the text its pending changes are
measured from. Beside them, in the record's review/ folder, the page keeps:

  orig/<key>        the file's text before the session's first edit, apart from the baseline, which
                    moves; orig/<key>.none for a file the session created
  journal.json      what was accepted, rejected and unaccepted here, oldest first, each naming the text
                    it replaced and the text it wrote, so it can be undone and redone
  blobs/<sha256>    those texts
  comments.json     the comments waiting for the session's next message
  sent.json         the batches of comments the hook handed over, the last 50

So a file has three texts: orig, base and disk (the file itself). Pending changes are base → disk:
Accept moves one into base, Reject writes base's lines back into the file. Accepted changes are
orig → base: Unaccept puts orig's lines back into base, which makes the change pending again.

A change is named by line ranges of the two texts it lies between, counted the way the page's editor
counts lines: a text split at every "\\n", so a text that ends in a newline has an empty last line.
"""
import argparse, datetime, filecmp, hashlib, importlib.util, json, os, pathlib, re, subprocess, sys
import tempfile, threading, time, urllib.parse

home = pathlib.Path.home()
store = home / '.claude/cursordiff'
history = home / '.claude/file-history'
here = pathlib.Path(__file__).resolve().parent
# Files under these are Claude's own and temporary ones, not the work's: the page lists them apart.
other_roots = (f'{home}/.claude/', '/tmp/', '/private/tmp/', '/var/folders/', '/private/var/folders/')
# A text past this is not sent to the page.
shown_bytes = 16 << 20

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
        self.folder = self.record / 'review'

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
                'isOther': any(file['path'].startswith(root) for root in other_roots),
            })
        info = read_json(self.record / 'session.json', {})
        return {'session': self.session, 'title': info.get('title') or 'Claude session', 'files': files,
                'comments': self.comments()}

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
        write_atomic(self.folder / 'comments.json', json.dumps(self.comments() + [comment], indent=1).encode())
        return comment

    def uncomment(self, comment_id):
        left = [c for c in self.comments() if c.get('id') != comment_id]
        write_atomic(self.folder / 'comments.json', json.dumps(left, indent=1).encode())


def fenced(text):
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}\n{text}\n{fence}'


def described(comments):
    out = ['The user left these comments on your changes on the Claude Edits review page; each quotes the '
           'lines it is on as they saw them.']
    for comment in comments:
        lines = f"line {comment['start']}" if comment['start'] == comment['end'] else f"lines {comment['start']}-{comment['end']}"
        out += ['', f"{comment['path']}, {lines}{sides.get(comment.get('side'), '')}:"]
        if comment.get('quote'):
            out.append(fenced(comment['quote']))
        out.append(f"Comment: {comment['text']}")
    return '\n'.join(out)


def prompt_hook():
    """Hands the session's waiting comments to the model with the prompt being submitted, and clears
    them. A failure never blocks the prompt: it goes to stderr and the exit status stays 0."""
    try:
        session = json.load(sys.stdin).get('session_id', '')
        if not re.fullmatch(r'[\w-]+', session) or not (store / session / 'review' / 'comments.json').is_file():
            return
        review = Review(session)
        waiting = review.comments()
        if not waiting:
            return
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': described(waiting)}}))
        sys.stdout.flush()
        # Kept, so a batch the prompt did not carry after all can be read back.
        sent = read_json(review.folder / 'sent.json', []) + [{'time': now(), 'comments': waiting}]
        write_atomic(review.folder / 'sent.json', json.dumps(sent[-50:], indent=1).encode())
        write_atomic(review.folder / 'comments.json', b'[]')
    except Exception as e:
        print(f'redpen review hook: {e!r}', file=sys.stderr)


def sessions():
    """Every session with a record, the one edited last first."""
    found = []
    for info in store.glob('*/session.json'):
        data = read_json(info, {})
        if (info.parent / 'files').is_dir():
            found.append({'session': info.parent.name, 'title': data.get('title') or 'Claude session',
                          'updated': data.get('updated') or '', 'files': len(list((info.parent / 'files').glob('*.json')))})
    return sorted(found, key=lambda s: s['updated'], reverse=True)


def listed_reviews():
    """The sessions with a record, for the server's index: each one's title, where its page is, how
    many files it edited, and when it was last active (edited, reviewed or commented)."""
    rows = []
    for s in sessions():
        folder = store / s['session']
        marks = [folder / 'session.json', folder / 'review/journal.json', folder / 'review/comments.json']
        rows.append({'name': s['title'], 'href': f's/{s["session"]}/',
                     'counts': [f'{s["files"]} file{"" if s["files"] == 1 else "s"}'],
                     'when': max((f.stat().st_mtime for f in marks if f.exists()), default=0)})
    return {'title': "Claude's edits", 'rows': rows,
            'note': 'Every session with a record in ~/.claude/cursordiff, the last active first.'}


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
        Review(found.group(1))
        if not found.group(2):
            h.send_response(301)
            h.send_header('Location', f'{found.group(1)}/')
            h.send_header('Content-Length', '0')
            h.end_headers()
            return
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
    found = re.fullmatch(r'/api/([\w-]+)/(apply|flip|comment|uncomment)', path)
    if not found:
        raise Refusal(404, 'Not found.')
    review = Review(found.group(1))
    if found.group(2) == 'apply':
        return send_json(h, review.apply(body.get('key'), body.get('action'), body.get('change'), body.get('expected')))
    if found.group(2) == 'flip':
        return send_json(h, review.flip(body.get('id')))
    if found.group(2) == 'comment':
        return send_json(h, review.comment(body))
    review.uncomment(body.get('id'))
    send_json(h, {})


def main():
    parser = argparse.ArgumentParser(description='Review a Claude Code session\'s edits on a page.')
    parser.add_argument('--session', default=os.environ.get('CLAUDE_CODE_SESSION_ID', ''))
    parser.add_argument('--port', type=int)
    parser.add_argument('--prompt-hook', action='store_true')
    args = parser.parse_args()
    # The hook reads the record alone, and no change to the server's code can fail it.
    if args.prompt_hook:
        return prompt_hook()
    server = shared('server')
    port = args.port or server.default_port
    if not server.running(port):
        server.start(port)
    session = args.session or next((s['session'] for s in sessions()), '')
    if session and (store / session / 'files').is_dir():
        print(f'http://127.0.0.1:{port}{server.mounts["code"]}/s/{session}/')
    else:
        if session:
            print(f'session {session} has edited no file yet', file=sys.stderr)
        print(f'http://127.0.0.1:{port}/')


if __name__ == '__main__':
    main()
