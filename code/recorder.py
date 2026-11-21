#!/usr/bin/env python3
"""Record the files a Claude Code session edits, for review on redpen's page and in Cursor.

Usage: recorder.py pre|post

pre and post run as PreToolUse and PostToolUse hooks on Edit|Write|MultiEdit|NotebookEdit|Bash and read
the hook's JSON on stdin. Each session's record is in ~/.redpen/code/<session>/:

  session.json      {session, title, updated}, rewritten after every edit
  files/<key>.json  {path, new, link?, executable?}: a file the session edited; new when the session
                    created it; link for a change to a link itself; executable: {orig, base},
                    whether it was executable before the session and as accepted
  base/<key>        the file's baseline: its text before the session's first edit to it, or as the
                    Claude Edits sidebar last kept it; absent for a new file not kept since
  after/<key>       the SHA-256 of the file as the session's last edit (or the review's Reject) left
                    it: a file that differs was changed since by someone or something else
  outside/<key>     there when the file was changed so before one of the session's edits: its diff
                    takes in changes not the session's

<key> is the first 16 hex digits of the SHA-256 of the file's path, as in Claude Code's file history.
pre keeps the file's text in pending/ the first time the session goes to edit the file, and post moves
it into the record once the edit has succeeded, so an edit that is denied or fails records nothing.

Of a shell command that can delete or move files (rm, mv, unlink, trash, find -delete), pre keeps what
git says is deleted and made in the work trees the session works in, of the places the command names
(all of the trees when it names none), and post records what the command changed of it: a tracked
file it deleted, with its last committed text as its baseline, and a file it made, as new; a link as a
link. Of a chmod, a file it names that it made executable, or no longer, is recorded with its text as
it is and its executable bit as it was. Anything else a shell command does is not recorded.
A failure never blocks an edit: it goes to stderr and the exit status stays 0.
"""
import datetime, hashlib, json, os, pathlib, re, shlex, subprocess, sys, tempfile

home = pathlib.Path.home()
store = home / '.redpen/code'
# How a link is kept as a text, before where it points, as review.py keeps it.
link_mark = b'\0symlink\0'
# A shell command that can delete or move files: an rm, mv, unlink or trash among its words, or find's
# -delete.
moving = re.compile(r'(^|[\s;&|(`])(rm|mv|unlink|trash)(?=\s)|\s-delete(?=\s|;|$)')
# One that can make a file executable, or no longer.
chmodding = re.compile(r'(^|[\s;&|(`])chmod(?=\s)')


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


def record(session, path, old, is_link=False, executable=None):
    """Adds the file to the session's record with its text before the session (None for a file the
    session created), unless the record has it already; is_link: a change to a link itself, which the
    review reads and writes as a link, not as the file it points to; executable: whether the file was
    executable before the session, None where that is not known."""
    folder = store / session
    entry = folder / 'files' / f'{key(path)}.json'
    if entry.exists():
        return
    if old is not None:
        write_atomic(folder / 'base' / key(path), old)
    kept = {'path': path, 'new': old is None, **({'link': True} if is_link else {})}
    if old is not None and not is_link and executable is not None:
        kept['executable'] = {'orig': executable, 'base': executable}
    write_atomic(entry, json.dumps(kept).encode())


def sha(data):
    return '-' if data is None else hashlib.sha256(data).hexdigest()


def disk_text(path, is_link=False):
    """The file at path as the review reads it: a link's own text for a link the record keeps as one,
    else the bytes; None with nothing there."""
    try:
        if is_link and os.path.islink(path):
            return link_mark + os.fsencode(os.readlink(path))
        return pathlib.Path(path).read_bytes()
    except OSError:
        return None


def keep_after(session, path):
    """Keeps a hash of the file as the session's own edit left it: a file that differs from it was
    changed since by someone or something else."""
    entry = read_json(store / session / 'files' / f'{key(path)}.json')
    if entry is not None:
        write_atomic(store / session / 'after' / key(path), sha(disk_text(path, entry.get('link'))).encode())


def note_outside(session, path):
    """Before the session edits a file it edited before: marks it, for good, as changed outside the
    session if it differs from how the session's last edit left it, as its diff takes those changes
    in from now on."""
    k = key(path)
    entry = read_json(store / session / 'files' / f'{k}.json')
    try:
        after = (store / session / 'after' / k).read_text()
    except OSError:
        return
    if entry is not None and sha(disk_text(path, entry.get('link'))) != after:
        write_atomic(store / session / 'outside' / k, b'')


def is_executable(path):
    """Whether the file at path is executable, by its owner's bit, the one git keeps; None with none."""
    try:
        return bool(os.stat(path).st_mode & 0o100)
    except OSError:
        return None


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
    staged = {'path': path, 'new': new, 'executable': is_executable(path)}
    write_atomic(folder / 'pending' / f'{k}.json', json.dumps(staged).encode())


def commit(session, path):
    """After a successful edit: moves the text stage() kept into the record."""
    pending = store / session / 'pending'
    k = key(path)
    staged = pending / f'{k}.json'
    if not staged.exists():
        return
    kept = json.loads(staged.read_text())
    record(session, path, None if kept['new'] else (pending / k).read_bytes(), executable=kept.get('executable'))
    staged.unlink()
    (pending / k).unlink(missing_ok=True)


def stage_shell(session, data, name):
    """Before a shell command that can delete or move files: keeps what git says is deleted and made
    in the work trees the session works in."""
    command = (data.get('tool_input') or {}).get('command') or ''
    cwd = data.get('cwd') or '/'
    trees = {}
    if moving.search(command):
        # As git spells them: its work trees' tops are real paths.
        named = [os.path.realpath(path) for path in named_paths(command, cwd)]
        # Each path is asked of the work tree it is in: a submodule's, not the one around it, which
        # refuses a path in a submodule and with it the rest.
        owners = {path: tree_of(path) for path in named}
        for top in sorted(set(work_trees(session, cwd)) | {top for top in owners.values() if top}):
            # In a large tree git's status takes seconds: it is asked of the paths the command names
            # in the tree alone, and of a tree it names none in, not at all, unless it names no path.
            paths = [path for path, owner in owners.items() if owner == top]
            if paths or not named:
                trees[top] = {'paths': paths, 'changed': changed(top, paths)}
    # Of a chmod, whether each file it names is executable before it: in a git work tree or not.
    modes = {path: is_executable(path) for path in named_files(command, cwd)} if chmodding.search(command) else {}
    if trees or modes:
        write_atomic(store / session / 'pending' / name, json.dumps({'trees': trees, 'modes': modes}).encode())


def commit_shell(session, name):
    """After the command: records a tracked file it deleted, with its last committed text as its
    baseline, and a file it made, as new; one the record has stays as it is. Says whether it
    recorded any."""
    staged = store / session / 'pending' / name
    before = read_json(staged) or {}
    staged.unlink(missing_ok=True)
    added = False
    # What the command changed is the session's own doing, recorded or not before.
    touched = set()
    # A file a chmod made executable, or no longer: its text as it is, and its bit as it was.
    for path, was in (before.get('modes') or {}).items():
        if was is None or is_executable(path) in (None, was):
            continue
        touched.add(path)
        if not (store / session / 'files' / f'{key(path)}.json').exists():
            record(session, path, pathlib.Path(path).read_bytes(), executable=was)
            added = True
    for top, kept in (before.get('trees') or {}).items():
        was = kept['changed']
        for path, kind in changed(top, kept['paths']).items():
            if was.get(path) == kind:
                continue
            touched.add(path)
            if (store / session / 'files' / f'{key(path)}.json').exists():
                continue
            if kind == 'gone':
                name = os.path.relpath(path, top)
                listed = git(top, 'ls-tree', 'HEAD', '--', name) or b''
                is_link = listed.startswith(b'120000 ')
                # As checking it out writes it, the repository's line endings and filters (Git LFS's
                # say) applied, else as git keeps it.
                old = None if is_link else git(top, 'cat-file', '--filters', f'HEAD:{name}')
                old = old if old is not None else git(top, 'cat-file', 'blob', f'HEAD:{name}')
                if old is None:
                    continue
                # A link is kept as a link's text: the review puts it back as a link; a file as
                # executable as git kept it.
                record(session, path, link_mark + old if is_link else old, is_link, listed.startswith(b'100755 '))
            else:
                record(session, path, None, os.path.islink(path))
            added = True
    for path in touched:
        keep_after(session, path)
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
    that is a folder, the folder; and of one with a / in it, or given to an mv, that is not there yet
    but its folder is, the path (where a folder moved lands), a * in it matching as in a git pathspec."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    folders = [cwd] + [os.path.normpath(os.path.join(cwd, os.path.expanduser(word)))
                       for before, word in zip([''] + words, words) if before == 'cd']
    paths = set()
    # Whether the words are an mv's (git mv's too), until the command ends.
    is_moving = False
    for before, word in zip([''] + words, words):
        if word in ('&&', '||', ';', '|', '&'):
            is_moving = False
            continue
        if word == 'mv':
            is_moving = True
            continue
        is_last = word.endswith(';')
        word = word.rstrip(';')
        # Where a command runs (cd's, git -C's) is no place it changes.
        if word and not word.startswith('-') and before not in ('cd', '-C'):
            for folder in folders:
                path = os.path.normpath(os.path.join(folder, os.path.expanduser(word)))
                if os.path.isdir(path) and not os.path.islink(path):
                    paths.add(path)
                elif os.path.lexists(path):
                    paths.add(os.path.dirname(path))
                elif ('/' in word or is_moving) and os.path.isdir(os.path.dirname(path)):
                    paths.add(path)
        if is_last:
            is_moving = False
    return sorted(paths)


def tree_of(path):
    """The top of the git work tree a path is in, the innermost (a submodule's); None outside one."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    while folder != '/' and not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    top = git(folder, 'rev-parse', '--show-toplevel')
    return os.fsdecode(top).rstrip('\n') if top else None


def named_files(command, cwd):
    """The files, not links nor folders, a shell command names, read from the command's folder and
    from each folder a cd in it goes to."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    folders = [cwd] + [os.path.normpath(os.path.join(cwd, os.path.expanduser(word)))
                       for before, word in zip([''] + words, words) if before == 'cd']
    paths = {os.path.normpath(os.path.join(folder, os.path.expanduser(word)))
             for word in words if not word.startswith(('-', '+')) for folder in folders}
    return sorted(path for path in paths if os.path.isfile(path) and not os.path.islink(path))


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
        note_outside(session, path)
        stage(session, path)
        return
    commit(session, path)
    if (store / session / 'files' / f'{key(path)}.json').exists():
        keep_after(session, path)
        touch(session, data.get('transcript_path', ''))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'redpen recorder: {e!r}', file=sys.stderr)
