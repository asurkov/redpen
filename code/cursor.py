#!/usr/bin/env python3
"""Show a Claude Code session's edits in Cursor as Cursor's own inline diffs: red and green lines with
Accept and Reject, as if Cursor's agent had made them.

Usage: cursor.py [--session ID] [--workspace ID] [--clear]

For each file the session edited, its baseline in the record recorder.py keeps (its text
before the session's first edit to it or as the Claude Edits sidebar last kept it, or nothing for a file
the session created) goes into Cursor's state database as an inline diff against the file on disk.
Cursor reads these when a window opens, so the window is to be reloaded (Developer: Reload Window) to
show them. Accept keeps the file as it is and
Reject puts the old text back. Each diff goes to the Cursor workspace that holds the file, the one used
last when several do; --workspace ID picks one instead. Skipped: files whose old text equals the disk,
files no longer on disk or outside every workspace, and files Cursor holds a pending diff of its own for.
A diff this script wrote earlier for the same file is replaced.

Files the session edited before the hooks were installed are added to the record first, with their text
from Claude Code's file history.

--session defaults to $CLAUDE_CODE_SESSION_ID. --clear removes the diffs this script wrote in those
workspaces, for every session, and writes nothing. REDPEN_CURSOR_USER_DIR names another Cursor user data
directory (default ~/Library/Application Support/Cursor/User), such as a test profile's.
"""
import argparse, hashlib, json, os, pathlib, re, sqlite3, sys, urllib.parse, uuid

import recorder

HOME = pathlib.Path.home()
# Diff ids this script writes start with this, so it can find its own among Cursor's.
PREFIX = 'claude-'


def transcript_of(session):
    transcript = next(HOME.glob(f'.claude/projects/*/{session}.jsonl'), None)
    if transcript is None:
        sys.exit(f'no transcript for session {session}')
    return transcript


def tracked_files(session):
    """Each file the session edited, with its old text: None for a file the session created."""
    transcript = transcript_of(session)
    first = {}
    with open(transcript) as f:
        for line in f:
            entry = json.loads(line)
            # Snapshots list every tracked file's backup; newer Claude Code adds one delta per backup.
            if entry.get('type') == 'file-history-snapshot':
                backups = entry['snapshot']['trackedFileBackups'].items()
            elif entry.get('type') == 'file-history-delta':
                backups = [(entry['trackingPath'], entry['backup'])]
            else:
                continue
            for path, backup in backups:
                if path not in first or backup['version'] < first[path]['version']:
                    first[path] = backup
    history = HOME / '.claude/file-history' / session
    for path, backup in first.items():
        if backup['version'] == 1 and backup['backupFileName'] is None:
            yield path, None
            continue
        # Version 1 is the file before the session's first edit; it is gone once Claude Code has
        # cleaned the session's history up, and then the old text is unknown.
        v1 = history / f"{hashlib.sha256(path.encode()).hexdigest()[:16]}@v1"
        if v1.exists():
            yield path, v1.read_bytes()
        else:
            print(f'skip {path}: its old text is no longer in the file history', file=sys.stderr)


def workspaces(user_dir):
    """Cursor's workspaces: id, root folders, and when each was last used."""
    for d in (user_dir / 'workspaceStorage').iterdir():
        try:
            ws = json.loads((d / 'workspace.json').read_text())
        except (OSError, ValueError):
            continue
        if 'folder' in ws:
            roots = [uri_path(ws['folder'])]
        elif 'workspace' in ws:
            conf = uri_path(ws['workspace'])
            try:
                folders = json.loads(pathlib.Path(conf).read_text()).get('folders', [])
            except (OSError, ValueError):
                continue
            roots = [os.path.normpath(os.path.join(os.path.dirname(conf), f['path'])) for f in folders if 'path' in f]
        else:
            continue
        state = d / 'state.vscdb'
        yield d.name, [r for r in roots if r], state.stat().st_mtime if state.exists() else 0


def uri_path(uri):
    parsed = urllib.parse.urlparse(uri)
    return urllib.parse.unquote(parsed.path) if parsed.scheme == 'file' else None


def lines(data):
    """The text's lines as Cursor's editor splits them: a trailing newline ends in an empty line."""
    return re.split(r'\r\n|\r|\n', data.decode())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--session', default=os.environ.get('CLAUDE_CODE_SESSION_ID'))
    ap.add_argument('--workspace')
    ap.add_argument('--clear', action='store_true')
    args = ap.parse_args()
    user_dir = pathlib.Path(os.environ.get('REDPEN_CURSOR_USER_DIR', HOME / 'Library/Application Support/Cursor/User'))
    all_ws = list(workspaces(user_dir))
    db = sqlite3.connect(user_dir / 'globalStorage/state.vscdb', timeout=30)

    if args.clear:
        for ws_id, roots, _ in all_ws:
            if args.workspace in (None, ws_id):
                n = db.execute('DELETE FROM cursorDiskKV WHERE key LIKE ?', (f'inlineDiff:{ws_id}:{PREFIX}%',)).rowcount
                if n:
                    print(f'removed {n} diffs from {", ".join(roots)}')
        db.commit()
        return

    if not args.session:
        sys.exit('no --session and no $CLAUDE_CODE_SESSION_ID')
    for path, old in tracked_files(args.session):
        recorder.record(args.session, path, old)
    recorder.touch(args.session, transcript_of(args.session))
    files = list(recorder.recorded(args.session))
    written = {}
    for path, old in files:
        try:
            new = pathlib.Path(path).read_bytes()
        except OSError:
            continue
        if old == new:
            continue
        holders = [(used, ws_id, roots) for ws_id, roots, used in all_ws
                   if args.workspace in (None, ws_id) and any(path.startswith(r.rstrip('/') + '/') for r in roots)]
        if not holders:
            print(f'skip {path}: no Cursor workspace holds it', file=sys.stderr)
            continue
        _, ws_id, roots = max(holders)
        try:
            new.decode()
            old_lines = [] if old is None else lines(old)
        except UnicodeDecodeError:
            print(f'skip {path}: not text', file=sys.stderr)
            continue
        prefix = f'inlineDiff:{ws_id}:'
        stored = db.execute('SELECT key, value FROM cursorDiskKV WHERE key LIKE ?', (prefix + '%',)).fetchall()
        same_file = [(key, json.loads(value)['diffId']) for key, value in stored if json.loads(value)['uri']['path'] == path]
        if any(not diff_id.startswith(PREFIX) for _, diff_id in same_file):
            print(f'skip {path}: Cursor has a pending diff of its own for it', file=sys.stderr)
            continue
        for key, _ in same_file:
            db.execute('DELETE FROM cursorDiskKV WHERE key = ?', (key,))
        diff_id = PREFIX + str(uuid.uuid4())
        record = {
            'diffId': diff_id,
            'uri': {'$mid': 1, 'external': pathlib.Path(path).as_uri(), 'path': path, 'scheme': 'file'},
            'originalTextLines': old_lines,
            'generationUUID': str(uuid.uuid4()),
            # Cursor reads the fields of this even when the diff belongs to no agent chat.
            'composerMetadata': {},
            'hideDecorations': False,
        }
        db.execute('INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)', (prefix + diff_id, json.dumps(record)))
        written.setdefault((ws_id, tuple(roots)), []).append(path + (' (new)' if old is None else ''))
    db.commit()

    for (_, roots), paths in written.items():
        print(f'{len(paths)} diffs in the Cursor workspace {", ".join(roots)}:')
        for p in paths:
            print(f'  {p}')
    if written:
        print('Reload that Cursor window (Developer: Reload Window) to see them.')
    else:
        print('nothing to show: the session changed no file that is still different on disk')


if __name__ == '__main__':
    main()
