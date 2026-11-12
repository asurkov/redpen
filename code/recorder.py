#!/usr/bin/env python3
"""Record the files a Claude Code session edits, for review on redpen's page and in Cursor.

Usage: recorder.py pre|post

pre and post run as PreToolUse and PostToolUse hooks on Edit|Write|MultiEdit|NotebookEdit and read the
hook's JSON on stdin. Each session's record is in ~/.redpen/code/<session>/:

  session.json      {session, title, updated}, rewritten after every edit
  files/<key>.json  {path, new}: a file the session edited; new when the session created it
  base/<key>        the file's baseline: its text before the session's first edit to it, or as the
                    Claude Edits sidebar last kept it; absent for a new file not kept since

<key> is the first 16 hex digits of the SHA-256 of the file's path, as in Claude Code's file history.
pre keeps the file's text in pending/ the first time the session goes to edit the file, and post moves
it into the record once the edit has succeeded, so an edit that is denied or fails records nothing.
A failure never blocks an edit: it goes to stderr and the exit status stays 0.
"""
import datetime, hashlib, json, os, pathlib, re, sys, tempfile

home = pathlib.Path.home()
store = home / '.redpen/code'


def key(path):
    return hashlib.sha256(path.encode()).hexdigest()[:16]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


def write_atomic(target, data):
    """Readers see the old file or the new one, never a part of it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix='.tmp-')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.replace(tmp, target)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def record(session, path, old):
    """Adds the file to the session's record with its text before the session (None for a file the
    session created), unless the record has it already."""
    folder = store / session
    entry = folder / 'files' / f'{key(path)}.json'
    if entry.exists():
        return
    if old is not None:
        write_atomic(folder / 'base' / key(path), old)
    write_atomic(entry, json.dumps({'path': path, 'new': old is None}).encode())


def recorded(session):
    """Each file in the session's record with its baseline: None for a new file not kept since."""
    folder = store / session
    for entry in sorted((folder / 'files').glob('*.json')):
        file = json.loads(entry.read_text())
        base = folder / 'base' / entry.stem
        if base.exists():
            yield file['path'], base.read_bytes()
        elif file['new']:
            yield file['path'], None


def stage(session, path):
    """Before an edit: keeps the file's text, unless the record has the file already."""
    folder = store / session
    k = key(path)
    if (folder / 'files' / f'{k}.json').exists():
        return
    # A session that edited the file before these hooks were installed has the file's text before
    # that first edit in Claude Code's file history.
    history = home / '.claude/file-history' / session / f'{k}@v1'
    source = history if history.exists() else pathlib.Path(path)
    new = not source.exists()
    if new:
        (folder / 'pending' / k).unlink(missing_ok=True)
    else:
        write_atomic(folder / 'pending' / k, source.read_bytes())
    write_atomic(folder / 'pending' / f'{k}.json', json.dumps({'path': path, 'new': new}).encode())


def commit(session, path):
    """After a successful edit: moves the text stage() kept into the record."""
    pending = store / session / 'pending'
    k = key(path)
    staged = pending / f'{k}.json'
    if not staged.exists():
        return
    new = json.loads(staged.read_text())['new']
    record(session, path, None if new else (pending / k).read_bytes())
    staged.unlink()
    (pending / k).unlink(missing_ok=True)


def touch(session, transcript):
    """Rewrites session.json with the session's current title and the time now."""
    info = store / session / 'session.json'
    previous = read_json(info, {})
    title = desktop_title(transcript) or previous.get('title') or first_prompt(transcript) or 'Claude session'
    write_atomic(info, json.dumps({
        'session': session,
        'title': title,
        'updated': now(),
    }, indent=1).encode())


def desktop_title(transcript):
    """The title the desktop app shows for the session: its latest custom-title entry, which the app
    repeats every few entries, so the transcript's last megabyte holds it."""
    try:
        with open(transcript, 'rb') as f:
            f.seek(max(0, os.path.getsize(transcript) - (1 << 20)))
            tail = f.read().split(b'\n')
    except OSError:
        return None
    for line in reversed(tail):
        if b'"custom-title"' in line:
            try:
                return json.loads(line).get('customTitle')
            except ValueError:
                continue
    return None


def first_prompt(transcript):
    try:
        with open(transcript) as f:
            for line in f:
                entry = json.loads(line)
                if entry.get('type') != 'user' or entry.get('isMeta'):
                    continue
                content = entry.get('message', {}).get('content')
                texts = [content] if isinstance(content, str) else [
                    block.get('text', '') for block in content or [] if block.get('type') == 'text']
                for text in texts:
                    if text and not text.startswith('<'):
                        return shorten(text)
    except (OSError, ValueError):
        pass
    return None


def shorten(text):
    text = ' '.join(text.split())
    return text if len(text) <= 80 else text[:79] + '…'


def main():
    data = json.load(sys.stdin)
    session = data.get('session_id', '')
    if not re.fullmatch(r'[\w-]+', session):
        return
    tool_input = data.get('tool_input') or {}
    path = tool_input.get('file_path') or tool_input.get('notebook_path')
    if not path:
        return
    path = os.path.normpath(os.path.join(data.get('cwd', '/'), path))
    if sys.argv[1] == 'pre':
        stage(session, path)
        return
    commit(session, path)
    if (store / session / 'files' / f'{key(path)}.json').exists():
        touch(session, data.get('transcript_path', ''))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'redpen recorder: {e!r}', file=sys.stderr)
