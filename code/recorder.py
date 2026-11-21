#!/usr/bin/env python3
"""Record the files a Claude Code session edits, for review on redpen's page and in Cursor.

Usage: recorder.py pre|post

pre and post run as PreToolUse and PostToolUse hooks on Edit|Write|MultiEdit|NotebookEdit|Bash and read
the hook's JSON on stdin. Each session's record is in ~/.redpen/code/<session>/:

  session.json      {session, title, updated}, rewritten after every edit
  files/<key>.json  {path, new}: a file the session edited; new when the session created it
  base/<key>        the file's baseline: its text before the session's first edit to it, or as the
                    Claude Edits sidebar last kept it; absent for a new file not kept since

<key> is the first 16 hex digits of the SHA-256 of the file's path, as in Claude Code's file history.
pre keeps the file's text in pending/ the first time the session goes to edit the file, and post moves
it into the record once the edit has succeeded, so an edit that is denied or fails records nothing.

Of a shell command that can delete or move files (rm, mv, unlink, trash, find -delete), pre keeps what
git says is deleted and made in the work trees the session works in, of the places the command names
(all of the trees when it names none), and post records what the command changed of it: a tracked file it deleted, with its last committed text as its baseline, and a
file it made, as new. Anything else a shell command does is not recorded.
A failure never blocks an edit: it goes to stderr and the exit status stays 0.
"""
import datetime, hashlib, json, os, pathlib, re, shlex, subprocess, sys, tempfile

home = pathlib.Path.home()
store = home / '.redpen/code'
# A shell command that can delete or move files: an rm, mv, unlink or trash among its words, or find's
# -delete.
moving = re.compile(r'(^|[\s;&|(`])(rm|mv|unlink|trash)(?=\s)|\s-delete(?=\s|;|$)')


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


def stage_shell(session, data, name):
    """Before a shell command that can delete or move files: keeps what git says is deleted and made
    in the work trees the session works in."""
    command = (data.get('tool_input') or {}).get('command') or ''
    if not moving.search(command):
        return
    cwd = data.get('cwd') or '/'
    named = named_paths(command, cwd)
    kept = {}
    for top in work_trees(session, cwd):
        # In a large tree git's status takes seconds: it is asked of the paths the command names in
        # the tree alone, and of a tree it names none in, not at all, unless it names no path.
        paths = [path for path in named if path == top or path.startswith(f'{top}/')]
        if paths or not named:
            kept[top] = {'paths': paths, 'changed': changed(top, paths)}
    if kept:
        write_atomic(store / session / 'pending' / name, json.dumps(kept).encode())


def commit_shell(session, name):
    """After the command: records a tracked file it deleted, with its last committed text as its
    baseline, and a file it made, as new; one the record has stays as it is. Says whether it
    recorded any."""
    staged = store / session / 'pending' / name
    before = read_json(staged)
    staged.unlink(missing_ok=True)
    added = False
    for top, kept in (before or {}).items():
        was = kept['changed']
        for path, kind in changed(top, kept['paths']).items():
            if was.get(path) == kind or (store / session / 'files' / f'{key(path)}.json').exists():
                continue
            if kind == 'gone':
                old = git(top, 'show', f'HEAD:{os.path.relpath(path, top)}')
                if old is None:
                    continue
                record(session, path, old)
            else:
                record(session, path, None)
            added = True
    return added


def work_trees(session, cwd):
    """The tops of the git work trees the session works in: the command's folder's, and its recorded
    files'."""
    folders = {cwd}
    for entry in (store / session / 'files').glob('*.json'):
        path = (read_json(entry) or {}).get('path')
        if isinstance(path, str):
            folders.add(os.path.dirname(path))
    tops = set()
    for folder in folders:
        top = git(folder, 'rev-parse', '--show-toplevel') if os.path.isdir(folder) else None
        if top:
            tops.add(os.fsdecode(top).rstrip('\n'))
    return sorted(tops)


def named_paths(command, cwd):
    """The places a shell command names, read from the command's folder and from each folder a cd in
    it goes to: of a word that is a file, its folder, where a file moved next to it lands too; of one
    that is a folder, the folder; and of one with a / in it, in a folder that is there, the path, a *
    in it matching as in a git pathspec."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    folders = [cwd] + [os.path.normpath(os.path.join(cwd, os.path.expanduser(word)))
                       for before, word in zip([''] + words, words) if before == 'cd']
    paths = set()
    for before, word in zip([''] + words, words):
        # Where a command runs (cd's, git -C's) is no place it changes.
        if word.startswith('-') or before in ('cd', '-C'):
            continue
        for folder in folders:
            path = os.path.normpath(os.path.join(folder, os.path.expanduser(word)))
            if os.path.isdir(path) and not os.path.islink(path):
                paths.add(path)
            elif os.path.lexists(path):
                paths.add(os.path.dirname(path))
            elif '/' in word and os.path.isdir(os.path.dirname(path)):
                paths.add(path)
    return sorted(paths)


def changed(top, paths=()):
    """The files git says are deleted ('gone') and made ('made') in the work tree at top, by their
    paths; of the paths named alone, if any; ignored ones left out."""
    out = git(top, 'status', '--porcelain', '-z', '--no-renames', '--untracked-files=all', '--', *paths)
    found = {}
    for item in (out or b'').split(b'\0'):
        if len(item) < 4:
            continue
        x, y, path = chr(item[0]), chr(item[1]), os.path.join(top, os.fsdecode(item[3:]))
        if 'D' in (x, y) and x != 'A':
            found[path] = 'gone'
        elif x + y == '??' or x == 'A' and y != 'D':
            found[path] = 'made'
    return found


def git(folder, *args):
    """What a git command prints, run in the folder without writing to its repository; None when it
    fails."""
    try:
        ran = subprocess.run(['git', '-C', str(folder), *args], capture_output=True,
                             env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    except OSError:
        return None
    return None if ran.returncode else ran.stdout


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
    if data.get('tool_name') == 'Bash':
        # What pre kept, by the call it was kept for.
        name = f"shell-{key(data.get('tool_use_id') or tool_input.get('command') or '')}.json"
        if sys.argv[1] == 'pre':
            stage_shell(session, data, name)
        elif commit_shell(session, name):
            touch(session, data.get('transcript_path', ''))
        return
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
