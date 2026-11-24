#!/usr/bin/env python3
"""Record the files a Claude Code session edits, for review on redpen's page and in Cursor.

Usage: recorder.py pre|post
       recorder.py prune [DAYS] [--dry-run]   delete the records nothing was done in for DAYS (30)

pre and post run as PreToolUse and PostToolUse hooks on Edit|Write|MultiEdit|NotebookEdit|Bash and read
the hook's JSON on stdin. Each session's record is in ~/.redpen/code/<session>/:

  session.json      {session, title, updated}, rewritten after every edit
  files/<key>.json  {path, new, link?, executable?}: a file the session edited; new when the session
                    created it; link for a change to a link itself; executable: {orig, base},
                    whether it was executable before the session and as accepted
  base/<key>        the file's baseline: its text before the session's first edit to it, or as the
                    review page last accepted it; absent for a new file not accepted since
  after/<key>       the SHA-256 of the file as the session's last edit (or the review's Reject) left
                    it: a file that differs was changed since by someone or something else
  outside/<key>     there when the file was changed so before one of the session's edits: its diff
                    takes in changes not the session's

<key> is the first 16 hex digits of the SHA-256 of the file's path, as in Claude Code's file history.
pre keeps the file's text in pending/ the first time the session goes to edit the file, and post moves
it into the record once the edit has succeeded, so an edit that is denied or fails records nothing.

Of a shell command that may write (any but one made of reading commands alone: ls, cat, grep, git log,
sed without -i, …, with no output into a file), pre keeps what git says is changed, deleted and made
in the work trees the session works in, of the places the command names (all of the trees when it
names none), with each changed file's signature, and post records what the command changed of it: a
tracked file it changed or deleted, with its last committed text as its baseline, and a file it made,
as new; a link as a link. A file that was changed already before the command, or that changed during a
long one, is marked as taking in changes not the session's (outside/); one another session's own edit
left as it is, during the command, is that session's and not recorded. In a work tree whose status
took git long, only a command that can delete or move files (rm, mv, unlink, trash, find -delete) is
watched, for a day. Of a chmod, a file it names that it made executable, or no longer, is recorded
with its text as it is and its executable bit as it was. A failure never blocks an edit: it goes to
stderr and the exit status stays 0.

Once a day post deletes the records, sessions' and git reviews', that nothing was done in for 30 days:
no edit, no action or comment on their page. Their review is the files' history by then, not the
page's; the files themselves are never touched.
"""
import datetime, hashlib, json, os, pathlib, re, shlex, shutil, subprocess, sys, tempfile, time

home = pathlib.Path.home()
store = home / '.redpen/code'
# How a link is kept as a text, before where it points, as review.py keeps it.
link_mark = b'\0symlink\0'
# A shell command that can delete or move files: an rm, mv, unlink or trash among its words, or find's
# -delete.
moving = re.compile(r'(^|[\s;&|(`])(rm|mv|unlink|trash)(?=\s)|\s-delete(?=\s|;|$)')
# One that can make a file executable, or no longer.
chmodding = re.compile(r'(^|[\s;&|(`])chmod(?=\s)')
# A git command that moves HEAD: what a commit took of the session's changes counts as accepted at once.
committing = re.compile(r'\bgit\b[^;&|]*\b(commit|merge|pull|rebase|cherry-pick|revert|am|reset)\b')
# Commands that only read: a shell command made of these alone, writing no output into a file, changes
# no file, and git is not asked around it. sed, find and git read too, but for what they are told.
reading = {'ls', 'cat', 'head', 'tail', 'less', 'more', 'grep', 'egrep', 'fgrep', 'rg', 'ag', 'wc', 'sort', 'uniq',
           'cut', 'tr', 'nl', 'echo', 'printf', 'stat', 'file', 'which', 'type', 'command', 'pwd', 'date', 'diff',
           'cmp', 'comm', 'du', 'df', 'tree', 'jq', 'column', 'basename', 'dirname', 'realpath', 'readlink',
           'printenv', 'whoami', 'id', 'uname', 'hostname', 'ps', 'pgrep', 'lsof', 'true', 'false', 'test', '[',
           'sleep', 'cd', 'pushd', 'popd', 'hexdump', 'xxd', 'od', 'strings', 'shasum', 'sha256sum', 'md5',
           'md5sum', 'awk', 'ping', 'dig', 'host', 'nslookup', 'man', 'time'}
git_reading = {'log', 'show', 'diff', 'status', 'rev-parse', 'ls-files', 'ls-tree', 'cat-file', 'blame', 'grep',
               'describe', 'shortlog', 'reflog', 'rev-list', 'for-each-ref', 'merge-base', 'check-attr',
               'check-ignore', 'fetch', 'remote', 'config', 'branch', 'tag', 'worktree', 'help', 'version'}
find_writing = {'-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fprint0', '-fprintf', '-fls'}
# Where an output goes that writes no file.
no_file = {'/dev/null', '/dev/stdout', '/dev/stderr'}
# A command that took this long may have met someone else's edits: what it changed is marked so.
long_seconds = 30
# A work tree whose status took git this long is watched only around commands that delete or move
# files, for slow_hours: {top: when it was found slow}.
slow_seconds = 1.5
slow_hours = 24
# Another session's shell snapshot this old is of a command cut short, whose post hook never came: the
# Bash tool stops a command it waits on after 10 minutes, and one in the background returns at once.
stale_hours = 1
slow_trees = store / 'slow-trees.json'
# A record nothing was done in for this many days goes, looked for once a day; the stamp says when.
idle_days = 30
pruned_stamp = store / '.pruned'


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
    note_head(session, path)


def note_head(session, path):
    """Notes the HEAD of the work tree the file is in, the first time the record has a file there: a
    commit after it takes the session's changes, which the review then counts as accepted."""
    top = tree_of(path)
    place = store / session / 'review' / 'heads.json'
    heads = read_json(place, {})
    if not top or top in heads:
        return
    head = (git(top, 'rev-parse', 'HEAD') or b'').decode().strip()
    if head:
        write_atomic(place, json.dumps({**heads, top: head}, indent=1).encode())


def checkpoint_soon(session):
    """Asks the review server, where it runs, to read the session's file list, which takes what a commit
    took as accepted: in the background, quietly."""
    url = f"http://127.0.0.1:{os.environ.get('REDPEN_PORT', '7878')}/code/api/{session}/files"
    try:
        subprocess.Popen(['curl', '-s', '-m', '15', '-o', '/dev/null', url], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


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
    in from now on. With nothing of the session's pending in it (the text its last edit left is its
    baseline, all accepted, HEAD's, committed, or the one the review page took others' changes at, its
    review/taken/ mark; or its baseline is the text now), those changes are not taken in: the baseline is
    the text now, so the diff holds the coming edit alone."""
    k = key(path)
    entry = read_json(store / session / 'files' / f'{k}.json')
    try:
        after = (store / session / 'after' / k).read_text()
    except OSError:
        return
    if entry is None:
        return
    now = disk_text(path, entry.get('link'))
    if sha(now) == after:
        return
    base = store / session / 'base' / k
    try:
        was = sha(base.read_bytes())
    except OSError:
        was = sha(None)
    if now is not None and is_left_settled(session, path, k, after, was):
        write_atomic(base, now)
    elif now is None or was != sha(now):
        write_atomic(store / session / 'outside' / k, b'')


def is_left_settled(session, path, k, after, was):
    """Whether the text the session's last edit left (its hash, after) has nothing left to review: it is
    the baseline (was, its hash), the one the review page took others' changes at, or HEAD's."""
    if was == after:
        return True
    try:
        if (store / session / 'review' / 'taken' / k).read_text() == after:
            return True
    except OSError:
        pass
    top = tree_of(path)
    head = committed(top, path) if top else None
    return head is not None and sha(head[0]) == after


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
    """Before a shell command that may write: keeps what git says is changed, deleted and made in the
    work trees the session works in, each changed file with its signature, and the time."""
    command = (data.get('tool_input') or {}).get('command') or ''
    cwd = data.get('cwd') or '/'
    trees = {}
    is_moving = bool(moving.search(command))
    if is_moving or not is_reading(command):
        slow = read_json(slow_trees, {})
        # As git spells them: its work trees' tops are real paths.
        named = [os.path.realpath(path) for path in named_paths(command, cwd)]
        # Each path is asked of the work tree it is in: a submodule's, not the one around it, which
        # refuses a path in a submodule and with it the rest.
        owners = {path: tree_of(path) for path in named}
        for top in sorted(set(work_trees(session, cwd)) | {top for top in owners.values() if top}):
            if not is_moving and time.time() - slow.get(top, 0) < slow_hours * 3600:
                continue
            # In a large tree git's status takes seconds: it is asked of the paths the command names
            # in the tree alone, and of a tree it names none in, not at all, unless it names no path.
            paths = [path for path, owner in owners.items() if owner == top]
            if paths or not named:
                began = time.time()
                found = changed(top, paths)
                if time.time() - began > slow_seconds and not paths:
                    write_atomic(slow_trees, json.dumps({**read_json(slow_trees, {}), top: time.time()}).encode())
                trees[top] = {'paths': paths, 'changed': found,
                              'signatures': {path: file_signature(path) for path, kind in found.items() if kind != 'gone'}}
    # Of a chmod, whether each file it names is executable before it: in a git work tree or not.
    modes = {path: is_executable(path) for path in named_files(command, cwd)} if chmodding.search(command) else {}
    if trees or modes:
        write_atomic(store / session / 'pending' / name,
                     json.dumps({'trees': trees, 'modes': modes, 'time': time.time(), 'command': command}).encode())


def commit_shell(session, name, agent=None):
    """After the command: records a tracked file it changed or deleted, with its last committed text
    as its baseline, and a file it made, as new; one the record has stays as it is. Says whether it
    recorded any."""
    staged = store / session / 'pending' / name
    before = read_json(staged) or {}
    staged.unlink(missing_ok=True)
    added = False
    began = before.get('time') or time.time()
    is_long = time.time() - began > long_seconds
    command = before.get('command') or ''
    # What the command changed is the session's own doing, recorded or not before.
    touched = set()
    # Other sessions' shell commands running now, by the work trees they watch: what one of them began
    # before a file changed may be its doing.
    running = others_running(session)
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
        marks = kept.get('signatures') or {}
        for path, kind in changed(top, kept['paths']).items():
            # As it was: in the same state, and, for a file there, as it was written.
            if was.get(path) == kind and (kind == 'gone' or marks.get(path, file_signature(path)) == file_signature(path)):
                continue
            # Another session's own edit, made while the command ran, is that session's.
            if kind != 'gone' and is_others_edit(session, path, began):
                continue
            # Changed while another session's command ran in the tree, by a command of this one that names no
            # path there: that command's, whose own record takes it once it ends; unless this one names the
            # file all the same (a script given on its input, say), and it is this one's.
            if not kept['paths'] and os.path.basename(path) not in command and is_others_running(running, top, path):
                continue
            touched.add(path)
            if (store / session / 'files' / f'{key(path)}.json').exists():
                continue
            if kind == 'made':
                record(session, path, None, os.path.islink(path))
            else:
                old = committed(top, path)
                if old is None:
                    continue
                record(session, path, *old)
            # Changed before the command, or perhaps by someone else while a long one ran, or one the
            # command does not mention (your editor's save while it ran, say): its diff may take in changes
            # not the session's.
            if kind != 'gone' and (path in was or is_long or os.path.basename(path) not in command):
                write_atomic(store / session / 'outside' / key(path), b'')
            added = True
    # A command that edits a file and commits it leaves it clean, so git's status says nothing of it: a file
    # of the record it names whose text is now the committed one is the session's own edit, as it reads.
    if committing.search(command):
        for top in before.get('trees') or {}:
            for entry in (store / session / 'files').glob('*.json'):
                path = (read_json(entry) or {}).get('path') or ''
                if path in touched or not path.startswith(top + os.sep) or os.path.basename(path) not in command:
                    continue
                text, old = disk_text(path), committed(top, path)
                if text is not None and old is not None and old[0] == text:
                    touched.add(path)
    for path in touched:
        keep_after(session, path)
        if agent:
            note_agent(session, path, agent)
    return added


def note_agent(session, path, agent):
    """Notes that a subagent of the session, by its type, edited the file: its record holds the session's
    edits and its subagents', and the review says whose."""
    entry = store / session / 'files' / f'{key(path)}.json'
    kept = read_json(entry)
    if isinstance(kept, dict) and agent not in (kept.get('agents') or []):
        write_atomic(entry, json.dumps({**kept, 'agents': [*(kept.get('agents') or []), agent]}).encode())


def committed(top, path):
    """The file's last committed text, as checking it out writes it (the repository's line endings and
    filters, Git LFS's say, applied, else as git keeps it), whether git keeps it as a link, whether as
    executable: as record() takes them. None when HEAD has no such file."""
    name = os.path.relpath(path, top)
    listed = git(top, 'ls-tree', 'HEAD', '--', name) or b''
    is_link = listed.startswith(b'120000 ')
    old = None if is_link else git(top, 'cat-file', '--filters', f'HEAD:{name}')
    old = old if old is not None else git(top, 'cat-file', 'blob', f'HEAD:{name}')
    if old is None:
        return None
    # A link is kept as a link's text: the review puts it back as a link.
    return (link_mark + old if is_link else old), is_link, listed.startswith(b'100755 ')


def file_signature(path):
    """Changes whenever the file does: its modification and change times and its size; '-' with none."""
    try:
        stat = os.lstat(path)
    except OSError:
        return '-'
    return f'{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}'


def others_running(session):
    """The shell commands of other sessions running now: [(when each began, the work trees it watches)].
    A stale snapshot goes: its command ended long ago."""
    found = []
    for staged in store.glob('*/pending/shell-*.json'):
        if staged.parent.parent.name == session:
            continue
        kept = read_json(staged) or {}
        if time.time() - (kept.get('time') or 0) > stale_hours * 3600:
            staged.unlink(missing_ok=True)
            continue
        if kept.get('trees'):
            found.append((kept.get('time') or 0, set(kept['trees'])))
    return found


def is_others_running(running, top, path):
    """Whether one of the commands running in the work tree began before the file last changed (for a file
    gone, began at all): the change may be that command's."""
    try:
        changed_at = os.lstat(path).st_mtime
    except OSError:
        changed_at = None
    return any(top in trees and (changed_at is None or began <= changed_at) for began, trees in running)


def is_others_edit(session, path, since):
    """Whether another session's own edit, made since the time given, left the file as it is."""
    found = None
    for after in store.glob(f'*/after/{key(path)}'):
        if after.parent.parent.name == session:
            continue
        try:
            if after.stat().st_mtime < since:
                continue
            found = found or sha(disk_text(path))
            if after.read_text() == found:
                return True
        except OSError:
            continue
    return False


def is_reading(command):
    """Whether a shell command only reads: each of its commands one of the reading ones, none writing
    its output into a file. A heredoc or a command substitution runs what is not read here: no."""
    if '<<' in command or '`' in command or '$(' in command:
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    words = []
    for token in tokens + [';']:
        if token in ('&&', '||', ';', ';;', '|', '&', '(', ')', '|&'):
            if words and not is_reading_one(words):
                return False
            words = []
        else:
            words.append(token)
    return True


def is_reading_one(words):
    """Whether one command, its words as the shell splits them, only reads."""
    for at, word in enumerate(words):
        if word in ('>', '>>', '&>', '&>>', '>|') and (at + 1 == len(words) or words[at + 1] not in no_file):
            return False
    # Its name, past the variables set for it.
    while words and re.fullmatch(r'\w+=.*', words[0]):
        words = words[1:]
    if not words:
        return True
    name, rest = os.path.basename(words[0]), words[1:]
    if name == 'sed':
        return not any(word.startswith(('-i', '--in-place')) for word in rest)
    if name == 'find':
        return not find_writing & set(rest)
    if name == 'git':
        # Past git's own options, -C and -c taking a value.
        at = 0
        while at < len(rest) and rest[at].startswith('-'):
            at += 2 if rest[at] in ('-C', '-c') else 1
        return at < len(rest) and rest[at] in git_reading
    return name in reading


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
    """The files git says are deleted ('gone'), made ('made') and otherwise changed ('changed') in the
    work tree at top, by their paths; of the paths named alone, if any; ignored ones left out."""
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
        elif {'M', 'T'} & {x, y}:
            found[path] = 'changed'
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


def last_activity(folder):
    """When anything was last done in a record: an edit recorded, or an action, a comment, an answer or
    an overview on its page, each of which writes the record's folder or its review/ folder."""
    marks = [folder, *folder.iterdir()]
    if (folder / 'review').is_dir():
        marks += list((folder / 'review').iterdir())
    return max(os.lstat(mark).st_mtime for mark in marks)


def prune(days=idle_days, is_dry=False, kept=()):
    """Deletes the records, sessions' and git reviews', nothing was done in for days, but those named in
    kept; with is_dry, only says which. Returns [(name, bytes)]."""
    gone = []
    for folder in sorted(store.iterdir()) if store.is_dir() else []:
        is_record = (folder / 'files').is_dir() or (folder / 'session.json').exists() or (folder / 'git.json').exists()
        if not folder.is_dir() or folder.is_symlink() or not is_record or folder.name in kept:
            continue
        try:
            if time.time() - last_activity(folder) < days * 86400:
                continue
            size = sum(f.lstat().st_size for f in folder.rglob('*') if f.is_file())
        except OSError:
            continue
        if not is_dry:
            shutil.rmtree(folder, ignore_errors=True)
        gone.append((folder.name, size))
    return gone


def prune_daily(session):
    """Prunes the store once a day, the session's own record aside."""
    try:
        if time.time() - pruned_stamp.stat().st_mtime < 86400:
            return
    except OSError:
        pass
    # The stamp first: the other sessions' hooks meanwhile leave it to this one.
    write_atomic(pruned_stamp, b'')
    prune(kept={session})


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
    if sys.argv[1] == 'prune':
        days = next((int(arg) for arg in sys.argv[2:] if arg.isdigit()), idle_days)
        is_dry = '--dry-run' in sys.argv
        gone = prune(days, is_dry)
        for name, size in gone:
            print(f'{name}  {size / 1e6:.1f} MB')
        print(f"{'Would delete' if is_dry else 'Deleted'} {len(gone)} records nothing was done in for {days} days, "
              f'{sum(size for _, size in gone) / 1e6:.1f} MB.')
        return
    data = json.load(sys.stdin)
    session = data.get('session_id', '')
    if not re.fullmatch(r'[\w-]+', session):
        return
    tool_input = data.get('tool_input') or {}
    # A subagent's call (the hook says which, agent_id) is recorded in the session's record, by its type.
    agent = (data.get('agent_type') or 'subagent') if data.get('agent_id') else None
    if sys.argv[1] == 'post':
        prune_daily(session)
    if data.get('tool_name') == 'Bash':
        # What pre kept, by the call it was kept for.
        name = f"shell-{key(data.get('tool_use_id') or tool_input.get('command') or '')}.json"
        if sys.argv[1] == 'pre':
            stage_shell(session, data, name)
        elif commit_shell(session, name, agent):
            touch(session, data.get('transcript_path', ''))
        if sys.argv[1] == 'post' and committing.search(tool_input.get('command') or ''):
            checkpoint_soon(session)
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
        if agent:
            note_agent(session, path, agent)
        touch(session, data.get('transcript_path', ''))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'redpen recorder: {e!r}', file=sys.stderr)
