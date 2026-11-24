#!/usr/bin/env python3
"""Review a Claude Code session's edits on a page: each file whole, its changes inline, with Accept and
Reject per change and per file. Or a git work tree's changes against a commit, whoever made them.

Usage: review.py [--session ID] [--port N]   start the server unless it runs; print the page's URL
       review.py --git [REPO] [--base REV]   make or refresh the git review of REPO's work tree (the
                                             current folder's) against REV (the branch's upstream,
                                             else HEAD); print its page's URL
       review.py --wait [--session ID]       print the comments nobody took that the pages of the
                                             session and of its git reviews sent, or else wait for
                                             the next ones; Claude keeps it running in the background
       review.py --answer ID                 put Claude's answer, read from stdin, under the comment
                                             on its page
       review.py --generated PATH...         print those of the files their projects generate
       review.py --outline [REVIEW]          keep the overview of the changes, JSON read from stdin,
                                             for the page of REVIEW (the session's); say the changes
                                             it leaves out
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
  comments.json     the comments made while Claude's command did not wait: not sent yet
  sent.jsonl        the batches of comments sent, one per line: by the page as each is made, while
                    the command Claude keeps waiting (--wait) runs; by that command as it starts;
                    or else by the hook, with the session's next message
  handed.jsonl      which of them Claude was handed, and how (see ../base/channel.py)
  answers.jsonl     Claude's answers, each naming the comment it is under, and the threads the user
                    resolved, one per line
  outline.json      the overview Claude wrote when asked (an ask among the comments, {describe}):
                    items by purpose, each with the blocks of lines it is made of, each block with
                    those lines as they were, to find them again; and the record's edited as it was
                    when asked, so an overview the session's edits since made old is not shown
  asked-edited      the record's edited when a fork was asked for an overview, till it answers

So a file has three texts: orig, base and disk (the file itself). Pending changes are base → disk:
Accept moves one into base, Reject writes base's lines back into the file. Accepted changes are
orig → base: Unaccept puts orig's lines back into base, which makes the change pending again.

A change is named by line ranges of the two texts it lies between, counted the way the page's editor
counts lines: a text split at every "\\n", so a text that ends in a newline has an empty last line.

A git review is a record of the same shape that no hook keeps, ~/.redpen/code/git-<name>/: git.json
names the repository, the base commit and the session that made it, in place of session.json, and
each file that differs from the base commit has that commit's text as its orig and as its first
baseline. Its files are found when the command runs and each time its page loads; git is only read.
The review of a branch against another commit than HEAD follows its base: when the branch was rebased,
the record is carried over to the new base commit then too (carried()), under the name it has. One
against HEAD keeps its base through commits made on top, and is carried over to HEAD once an amend or
a rebase rewrote the branch (is_rewritten()).
"""
import argparse, base64, datetime, difflib, functools, hashlib, importlib.util, json, os, pathlib, re, subprocess, sys
import tempfile, threading, time, urllib.error, urllib.parse, urllib.request

home = pathlib.Path.home()
store = home / '.redpen/code'
history = home / '.claude/file-history'
here = pathlib.Path(__file__).resolve().parent
# Files under these are Claude's own and temporary ones, not the work's: the page lists them apart;
# ~/.claude first, as is_other() makes an exception there.
other_roots = (f'{home}/.claude/', '/tmp/', '/private/tmp/', '/var/folders/', '/private/var/folders/')
# A text past this is not sent to the page.
shown_bytes = 16 << 20
# What takes a file away: to the Trash, where it can be got back from.
trash_command = ['/usr/bin/trash']
# How a symbolic link is kept as a text, before where it points: no text the page reads starts with
# it, so one that does is a link to make, not bytes to write. recorder.py keeps links the same way.
link_mark = b'\0symlink\0'
# The modes git keeps a link, an executable file and a submodule in.
link_mode = b'120000'
executable_mode = b'100755'
submodule_mode = b'160000'
# A git review of more changed files than this is refused: the base is not the one meant.
scanned_files = 5000
# The most files of one folder the page's full tree lists.
listed_files = 2000
# Whether git ignores a path, by repository: {path: (whether, when git said it)}; said again after
# this many seconds, so an edited .gitignore shows.
ignore_verdicts = {}
ignore_seconds = 60
# Files a project generates, which the page folds away: those its .gitattributes marks
# linguist-generated, as GitHub reads it; where it says nothing, these lockfiles, and a file whose
# first lines carry a generator's banner: Meta's @generated (Cargo.lock's too); "generated" with "do
# not edit" (Go's "Code generated … DO NOT EDIT.", protoc's, Thrift's, GraphQL codegen's, Prisma's);
# or "this file is autogenerated" (pip-compile's). A "DO NOT EDIT" alone, or a "generated" alone, is
# no banner.
generated_names = {'package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lockb',
                   'Cargo.lock', 'poetry.lock', 'Pipfile.lock', 'uv.lock', 'Gemfile.lock', 'composer.lock',
                   'go.sum', 'flake.lock'}
generated_banners = [
    re.compile(r'@generated\b'),
    re.compile(r'\b(?:this|the) file (?:is|was|has been) (?:auto-?generated|automatically generated|generated)\b', re.I),
]
generated_word = re.compile(r'\b(?:auto-?generated|automatically generated|generated)\b', re.I)
do_not_edit = re.compile(r'\bdo not (?:edit|modify)\b', re.I)
generated_head_lines = 5
# What a project's .gitattributes says of a path being generated, by repository: {path: (True, False
# or None for nothing, when git said it)}, asked again after ignore_seconds, as ignore_verdicts.
generated_verdicts = {}
# Whether a file's first lines say it is generated, by its path: (its signature then, whether).
generated_heads = {}
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
    """Changes whenever the file at place does; '-' while there is none: its modification time, its
    change time, which every write moves and no tool can set back as one can the other, and its size,
    last."""
    try:
        stat = place.stat()
    except OSError:
        return '-'
    return f'{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}' if place.is_file() else '-'


def read_bytes(place):
    try:
        return place.read_bytes()
    except OSError:
        return None


def is_executable(place):
    """Whether the file at place is executable, by its owner's bit, the one git keeps; None with no
    file there."""
    try:
        return bool(os.stat(place).st_mode & 0o100)
    except OSError:
        return None


def set_executable(place, is_on):
    """Makes the file at place executable, for whoever can read it, or not executable for anyone."""
    mode = os.stat(place).st_mode
    os.chmod(place, mode | (mode & 0o444) >> 2 if is_on else mode & ~0o111)


def read_link_place(place):
    """What is at a link's place: the link itself, as a link's text; else the file there, or None."""
    try:
        return link_mark + os.fsencode(os.readlink(place)) if os.path.islink(place) else read_bytes(pathlib.Path(place))
    except OSError:
        return None


def write_link_place(place, data):
    """Puts at a link's place the link or the file the text is, in place of what is there; with no
    text, takes away what is there: a link, as a link is no more than its text, a file to the Trash."""
    is_link = data is not None and data.startswith(link_mark)
    if os.path.islink(place):
        os.unlink(place)
    elif os.path.lexists(place) and (data is None or is_link):
        ran = subprocess.run([*trash_command, str(place)], capture_output=True, text=True)
        if ran.returncode:
            raise Refusal(500, f'Could not move the file to the Trash: {ran.stderr.strip()}')
    if data is None:
        return
    pathlib.Path(place).parent.mkdir(parents=True, exist_ok=True)
    if is_link:
        os.symlink(os.fsdecode(data[len(link_mark):]), place)
    else:
        with open(place, 'wb') as f:
            f.write(data)


def link_signature(place):
    """Changes whenever what is at a link's place does; '-' while there is nothing."""
    try:
        return f'link:{os.readlink(place)}' if os.path.islink(place) else signature(pathlib.Path(place))
    except OSError:
        return '-'


def link_counts(a, b):
    """How a link's text, or the file in its place, differs from another: whole, a line for a link."""
    if a == b:
        return None
    lines = lambda data: 0 if not data else 1 if data.startswith(link_mark) else data.count(b'\n') + (not data.endswith(b'\n'))
    return {'added': lines(b), 'removed': lines(a)}


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
    if data.startswith(link_mark):
        return f'symbolic link → {os.fsdecode(data[len(link_mark):])}'
    if len(data) > shown_bytes:
        return None
    try:
        return data.decode()
    except UnicodeDecodeError:
        return None


def image_type(data):
    """The media type of the image the bytes are, by how they start; None for any other kind."""
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[4:12] in (b'ftypavif', b'ftypavis'):
        return 'image/avif'
    if data[:2] == b'BM' and len(data) > 26:
        return 'image/bmp'
    if data[:4] == b'\x00\x00\x01\x00':
        return 'image/x-icon'
    return None


counts = {}
likenesses = {}
# The hash of a file as it is, by its place and signature: remembered until the file changes.
disk_hashes = {}
# How many answers each of those keeps before it starts anew: enough for reviews of thousands of
# files, three texts each, as one listing asks of them all.
cached_answers = 200_000
# A text past this is counted by git, whose diff is quicker on it than Python's.
counted_bytes = 256 << 10
# The moves found, by the files gone and made as they were; and the most pairs of them compared
# to find moves, a refactor of thousands of files say: past it, those of one extension, else none.
moves_found = {}
moved_pairs = 100_000
# The most an overview holds: items, blocks in all, and changes it leaves out that the page lists.
outline_items = 40
outline_blocks = 400
uncovered_shown = 300
# An ask for an overview Claude took this long ago with none written was dropped (its turn cut short by
# Esc or an error): it waits no more, so it can be asked again.
taken_minutes = 20
# The most of the changes, in characters, the question for an overview shows a model with no tools, and
# of each line of them.
shown_change_chars = 200_000
shown_line_chars = 400
# How alike a file gone from its place and a new file must be to be one file moved, as git's rename
# threshold has it.
moved_likeness = 0.5


def likeness(a, a_signature, b, b_signature):
    """How alike the texts at a and b are, from 0 to 1. Remembered until either file changes."""
    slot = (str(a), a_signature, str(b), b_signature)
    if slot not in likenesses:
        if len(likenesses) > cached_answers:
            likenesses.clear()
        likenesses[slot] = alike(read_bytes(a), read_bytes(b))
    return likenesses[slot]


def alike(a, b):
    """The share of two texts' lines they have in common; 0 for an empty text, or two of sizes too far
    apart, or too long to compare but the same."""
    if not a or not b or not a.strip() or not b.strip() or min(len(a), len(b)) < moved_likeness * max(len(a), len(b)):
        return 0
    if a == b:
        return 1
    lefts, rights = a.splitlines(), b.splitlines()
    if max(len(lefts), len(rights)) > 20000:
        return 0
    matcher = difflib.SequenceMatcher(None, lefts, rights, autojunk=False)
    return matcher.ratio() if matcher.quick_ratio() >= moved_likeness else 0


def counted(left, right):
    """How the text right differs from left, as git's numstat says it: None for the same; else lines
    added and removed, a line's ending part of it; binary for a text with a NUL in its first 8000
    bytes, as git tells one."""
    if left == right:
        return None
    if any(text and b'\0' in text[:8000] for text in (left, right)):
        return {'isBinary': True}
    lefts, rights = (left or b'').splitlines(keepends=True), (right or b'').splitlines(keepends=True)
    # The lines both start and end with are not looked at again.
    head = 0
    while head < min(len(lefts), len(rights)) and lefts[head] == rights[head]:
        head += 1
    tail = 0
    while tail < min(len(lefts), len(rights)) - head and lefts[-1 - tail] == rights[-1 - tail]:
        tail += 1
    lefts, rights = lefts[head:len(lefts) - tail], rights[head:len(rights) - tail]
    added = removed = 0
    if lefts and rights:
        for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(None, lefts, rights, autojunk=False).get_opcodes():
            if kind != 'equal':
                removed += i2 - i1
                added += j2 - j1
    else:
        added, removed = len(rights), len(lefts)
    return {'added': added, 'removed': removed}


def hashed(place, place_signature):
    """The hash of the file at place. Remembered until the file changes."""
    slot = (str(place), place_signature, False)
    if slot not in disk_hashes:
        if len(disk_hashes) > cached_answers:
            disk_hashes.clear()
        disk_hashes[slot] = sha(read_bytes(place))
    return disk_hashes[slot]


def compared(a, a_signature, b, b_signature):
    """How the file at b differs from the one at a: None when it does not, else its added and removed
    line counts. Remembered until either file changes."""
    slot = (str(a), a_signature, str(b), b_signature)
    if slot not in counts:
        if len(counts) > cached_answers:
            counts.clear()
        counts[slot] = count(None if a_signature == '-' else a, None if b_signature == '-' else b)
    return counts[slot]


def count(a, b):
    # The bytes themselves: filecmp would answer from its own memory of a file's size and mtime, which
    # a tool can put back over a change.
    texts = [None if place is None else read_bytes(place) for place in (a, b)]
    if texts[0] == texts[1]:
        return None
    # An ordinary text is counted here, as git would; a git process for each file would take seconds
    # over a review of thousands.
    if all(text is None or len(text) <= counted_bytes for text in texts):
        return counted(*texts)
    ran = subprocess.run(['git', 'diff', '--no-index', '--numstat', '--no-ext-diff', '--',
                          a or '/dev/null', b or '/dev/null'], capture_output=True, text=True, cwd='/')
    found = re.match(r'(\d+|-)\t(\d+|-)\t', ran.stdout)
    # No line differs, yet the files do: an empty file against none.
    if not found:
        return {'added': 0, 'removed': 0}
    if found.group(1) == '-':
        return {'isBinary': True}
    return {'added': int(found.group(1)), 'removed': int(found.group(2))}


def shown_entry(entry, members):
    """A journal entry as the page shows it: of an action on a folder's files, at the folder, saying
    how many; of one on a moved file, at its new place, saying the old one; of an overview item's
    Accept, by the item's title, saying how many files."""
    if entry.get('item') or entry.get('generated') or entry.get('commit') or entry.get('all'):
        return {**entry, 'change': None}
    if entry.get('folder'):
        return {**entry, 'path': entry['folder'], 'change': None}
    if len(members) < 2:
        return entry
    return {**entry, 'path': members[-1]['path'], 'movedFrom': members[0]['path']}


def spliced(left, right, change, is_left_taken):
    """The right text with the change's lines replaced by the left text's, or the other way round.
    A list of changes, the parts of them the user selected, is taken as one."""
    try:
        lefts, rights = (left or b'').decode().split('\n'), (right or b'').decode().split('\n')
    except UnicodeDecodeError:
        raise Refusal(422, 'The file is not text.')
    try:
        parts = change if isinstance(change, list) else [change]
        spans = sorted([int(part[name]) for name in ('leftStart', 'leftEnd', 'rightStart', 'rightEnd')]
                       for part in parts)
    except (KeyError, TypeError, ValueError):
        raise Refusal(400, 'The change names no line ranges.')
    if not spans:
        raise Refusal(400, 'The change names no line ranges.')
    left_floor = right_floor = 0
    for left_start, left_end, right_start, right_end in spans:
        if not (left_floor <= left_start <= left_end <= len(lefts)
                and right_floor <= right_start <= right_end <= len(rights)):
            raise Refusal(409, 'The change lies outside the file.')
        left_floor, right_floor = left_end, right_end
    # From the last one up: the lines of those above stay where they were.
    for left_start, left_end, right_start, right_end in reversed(spans):
        if is_left_taken:
            rights[right_start:right_end] = lefts[left_start:left_end]
        else:
            lefts[left_start:left_end] = rights[right_start:right_end]
    return '\n'.join(rights if is_left_taken else lefts).encode()


def changes_between(a, b):
    """The changes from lines a to lines b, as difflib's opcodes, the equal stretches left out:
    (tag, a_start, a_end, b_start, b_end), each span counted from 0, its end left out."""
    return [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != 'equal']


def shown_line(line):
    """A line of the changes as the question for an overview shows it: cut past shown_line_chars."""
    return line if len(line) <= shown_line_chars else line[:shown_line_chars] + '…'


def touches(span, start, end):
    """Whether lines start..end of a text (from 1, both in) touch a change's span [a, b) of it: overlap
    it or, where the change only took lines away (an empty span), stand right beside it."""
    a, b = span
    return a < end and start - 1 < b if a < b else start - 1 <= a <= end


def located(lines, block):
    """Where an overview block's lines are in the text now, (start, end) from 1: its own place while
    they are still there, else the nearest place they are; None when they are nowhere."""
    quote = block['quote'].split('\n')
    start, end = block['start'], block['end']
    if lines[start - 1:end] == quote:
        return start, end
    found = [at + 1 for at in range(len(lines) - len(quote) + 1)
             if lines[at] == quote[0] and lines[at:at + len(quote)] == quote]
    nearest = min(found, key=lambda at: abs(at - start), default=None)
    return None if nearest is None else (nearest, nearest + len(quote) - 1)


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
        self.twins = None

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

    def rows(self):
        """Each file of the record: (key, file, the places of its three texts, their signatures)."""
        rows = []
        for key, file in self.entries():
            places = self.places(key, file)
            signatures = {name: signature(place) for name, place in places.items()}
            if file.get('link'):
                signatures['disk'] = link_signature(places['disk'])
            elif signatures['disk'] != '-' and not self.is_there(places['disk']):
                signatures['disk'] = '-'
            rows.append((key, file, places, signatures))
        return rows

    def case_twins(self):
        """The record's paths another of its paths differs from by case alone: the two names of a file
        renamed by its case, on a file system that keeps case but ignores it, as macOS's does."""
        if self.twins is None:
            paths = [file['path'] for _, file in self.entries()]
            folded = {}
            for path in paths:
                folded[path.casefold()] = folded.get(path.casefold(), 0) + 1
            self.twins = {path for path in paths if folded[path.casefold()] > 1}
        return self.twins

    def is_there(self, place):
        """Whether a file of the record is at its place: for one of case twins, by its name's very case,
        as the file system finds the other name's file there too."""
        if str(place) not in self.case_twins():
            return True
        folder, name = os.path.split(str(place))
        try:
            return name in os.listdir(folder)
        except OSError:
            return False

    @staticmethod
    def modes(orig_file, base_file, disk):
        """Whether the file is executable as it was, as accepted and as it is: the first two as its
        record keeps them, the last from the file at disk; None where that is not known, or for a
        link."""
        if orig_file.get('link'):
            return {'orig': None, 'base': None, 'disk': None}
        kept = lambda file, name: (file.get('executable') or {}).get(name)
        return {'orig': kept(orig_file, 'orig'), 'base': kept(base_file, 'base'), 'disk': is_executable(disk)}

    def set_mode(self, key, target, is_on):
        """Makes the file executable, or not, as accepted (target 'base') or as it is ('disk')."""
        file = self.file(key)
        if target == 'disk':
            set_executable(file['path'], is_on)
        else:
            kept = {**file, 'executable': {**(file.get('executable') or {}), target: is_on}}
            write_atomic(self.record / 'files' / f'{key}.json', json.dumps(kept).encode())

    def is_edited_elsewhere(self, key, file, disk, disk_signature):
        """Whether a session's file was changed by someone or something else since the session's last
        edit to it: now, or before an edit of the session's since (marked so by the recorder). A git
        review's changes are anyone's."""
        if self.git:
            return False
        if (self.record / 'outside' / key).exists():
            return True
        try:
            after = (self.record / 'after' / key).read_text()
        except OSError:
            return False
        return self.disk_hash(file, disk, disk_signature) != after

    def disk_hash(self, file, disk, disk_signature):
        """The hash of the file's text on disk, a link's its own: remembered until it changes."""
        slot = (str(disk), disk_signature, bool(file.get('link')))
        if slot not in disk_hashes:
            if len(disk_hashes) > cached_answers:
                disk_hashes.clear()
            disk_hashes[slot] = sha(self.read(file, 'disk', disk))
        return disk_hashes[slot]

    def take_others_changes(self):
        """Of a session's review, a file with nothing of the session's left to review: the text its last
        edit left (after/) is its baseline (all accepted), HEAD's (committed), or the one it was when
        others' changes were taken before (review/taken/), the session not having edited it since. A change
        since is someone else's (another session's, the user's), each time: the file's text now becomes its
        baseline. Not in History, as nothing of the session's is to undo; it says edited elsewhere."""
        if self.git:
            return
        heads = {}
        for key, file, places, signatures in self.rows():
            try:
                after = (self.record / 'after' / key).read_text()
            except OSError:
                continue
            disk = self.disk_hash(file, places['disk'], signatures['disk'])
            base = hashed(places['base'], signatures['base'])
            if disk == after or base == disk:
                continue
            taken = self.folder / 'taken' / key
            if base == after or (read_bytes(taken) or b'').decode() == after or self.head_hash(file, heads) == after:
                self.write(key, file, 'base', self.read(file, 'disk', places['disk']))
                write_atomic(taken, after.encode())

    def head_hash(self, file, heads):
        """The hash of the file's text at HEAD, a link's as a link's; None outside a work tree or with no such
        file. heads: the HEAD of each work tree asked, kept for the caller's round."""
        top = repository_top(os.path.dirname(file['path']) or '/')
        if not top:
            return None
        if top not in heads:
            heads[top] = (git(top, 'rev-parse', 'HEAD') or b'').decode().strip()
        return committed_hash(top, heads[top], os.path.relpath(file['path'], top)) if heads[top] else None

    def read(self, file, name, place):
        """One of the file's texts; of a link, at its own place, the link itself, not what it points to;
        none at the place of a name a case rename took away."""
        if name == 'disk' and file.get('link'):
            return read_link_place(place)
        if name == 'disk' and not self.is_there(place):
            return None
        return read_bytes(place)

    def moves(self, rows):
        """The files moved, as git finds renames: a file gone from its place paired with a new file
        whose text is mostly its old one, the likest pairs first; {new file's key: old file's key}."""
        # A link moved is a link gone and one made, as git sees it.
        gone = [(key, places['orig'], signatures['orig'], file['path']) for key, file, places, signatures in rows
                if signatures['orig'] != '-' and signatures['disk'] == '-' and not file.get('link')]
        made = [(key, places['disk'], signatures['disk'], file['path']) for key, file, places, signatures in rows
                if signatures['orig'] == '-' and signatures['disk'] != '-' and not file.get('link')]
        # Found once for the files as they are: every listing asks.
        slot = (str(self.record), tuple((key, sig) for key, _, sig, _ in gone), tuple((key, sig) for key, _, sig, _ in made))
        if slot in moves_found:
            return moves_found[slot]
        size = lambda sig: int(sig.rsplit(':', 1)[1])
        moves = {}
        # As git finds renames: first the files moved as they were, by their texts' hashes, the
        # likeliest one of the same name; an empty file is no one file.
        by_hash = {}
        for old in gone:
            if size(old[2]):
                by_hash.setdefault(hashed(old[1], old[2]), []).append(old)
        for new in made:
            olds = by_hash.get(hashed(new[1], new[2])) if size(new[2]) else None
            if olds:
                old = next((old for old in olds if os.path.basename(old[3]) == os.path.basename(new[3])), olds[0])
                olds.remove(old)
                moves[new[0]] = old[0]
        # Then, of those left, the ones of the same name; then any two, or past so many pairs those
        # of the same extension, else no more. A pair of sizes too far apart is not one file.
        def pair_up(group):
            """Pairs the files left of each group, if there are few enough pairs; says whether."""
            olds, news = {}, {}
            for old in gone:
                if old[0] not in moves.values():
                    olds.setdefault(group(old), []).append(old)
            for new in made:
                if new[0] not in moves:
                    news.setdefault(group(new), []).append(new)
            if sum(len(found) * len(news.get(each, ())) for each, found in olds.items()) > moved_pairs:
                return False
            scored = sorted(((likeness(old[1], old[2], new[1], new[2]), new[0], old[0])
                             for each, found in olds.items() for old in found for new in news.get(each, ())
                             if min(size(old[2]), size(new[2])) >= moved_likeness * max(size(old[2]), size(new[2])) > 0),
                            reverse=True)
            for score, new_key, old_key in scored:
                if score >= moved_likeness and new_key not in moves and old_key not in moves.values():
                    moves[new_key] = old_key
            return True
        pair_up(lambda found: os.path.basename(found[3]))
        if not pair_up(lambda found: ''):
            pair_up(lambda found: os.path.splitext(found[3])[1])
        if len(moves_found) > 100:
            moves_found.clear()
        moves_found[slot] = moves
        return moves

    def move_of(self, key):
        """The file the file at key was moved from, (key, file); None when it was not moved."""
        old = self.moves(self.rows()).get(key)
        return (old, self.file(old)) if old else None

    @staticmethod
    def moved_places(new, old):
        """A moved file's three texts: its old text, at its old place; what was accepted of it, at its
        new place once the move was, else at its old place; and the file at its new place."""
        return {'orig': old['orig'], 'base': new['base'] if new['base'].exists() else old['base'], 'disk': new['disk']}

    def listing(self):
        files = []
        rows = self.rows()
        moves = self.moves(rows)
        by_key = {row[0]: row for row in rows}
        for key, file, places, signatures in rows:
            # A file moved away is listed at its new place.
            if key in moves.values():
                continue
            moved_from = by_key[moves[key]] if key in moves else None
            if moved_from:
                places = self.moved_places(places, moved_from[2])
                signatures = {name: signature(place) for name, place in places.items()}
            pending = compared(places['base'], signatures['base'], places['disk'], signatures['disk'])
            accepted = compared(places['orig'], signatures['orig'], places['base'], signatures['base'])
            files.append({
                'key': key,
                'path': file['path'],
                'movedFrom': moved_from[1]['path'] if moved_from else None,
                'isNew': signatures['orig'] == '-',
                'isGone': signatures['disk'] == '-',
                'signature': ' '.join(signatures.values()),
                'pending': pending,
                'accepted': accepted,
                # With nothing accepted, all of it is what is pending; with nothing pending, what was
                # accepted: a third comparison only for a file with both.
                'all': pending if accepted is None else accepted if pending is None else
                       compared(places['orig'], signatures['orig'], places['disk'], signatures['disk']),
                'isOther': not self.git and is_other(file['path']),
                # The session's subagents that edited it, by their types.
                'agents': file.get('agents') or [],
            })
            # Made executable, or no longer: a change of its own, its text the same or not.
            is_new_base = moved_from and places['base'] != moved_from[2]['base']
            mode = self.modes(moved_from[1] if moved_from else file, file if not moved_from or is_new_base else moved_from[1], places['disk'])
            files[-1]['mode'] = mode
            for view, (a, b) in {'pending': ('base', 'disk'), 'accepted': ('orig', 'base'), 'all': ('orig', 'disk')}.items():
                if None not in (mode[a], mode[b]) and mode[a] != mode[b]:
                    files[-1][view] = files[-1][view] or {'added': 0, 'removed': 0}
            if file.get('link'):
                # A link, or a file a link took the place of or gave its place to: whole.
                texts = {name: self.read(file, name, place) for name, place in places.items()}
                files[-1].update(isLink=True, pending=link_counts(texts['base'], texts['disk']),
                                 accepted=link_counts(texts['orig'], texts['base']), all=link_counts(texts['orig'], texts['disk']))
            if moved_from:
                # The move is a change of its own, its text the same or not: pending until accepted,
                # which keeps a baseline at the new place.
                is_kept = places['base'] != moved_from[2]['base']
                unchanged = {'added': 0, 'removed': 0}
                files[-1]['pending'] = files[-1]['pending'] or (None if is_kept else unchanged)
                files[-1]['accepted'] = files[-1]['accepted'] or (unchanged if is_kept else None)
                files[-1]['all'] = files[-1]['all'] or unchanged
            files[-1]['isEditedElsewhere'] = self.is_edited_elsewhere(key, file, places['disk'], signatures['disk'])
            # What was accepted in it was lost to a rebase, and something of it is to look at.
            is_uncarried = file.get('uncarried') or moved_from and moved_from[1].get('uncarried')
            files[-1]['isUncarried'] = bool(is_uncarried) and files[-1]['pending'] is not None
        # Generated files, which the page folds away; Claude's own and temporary ones are folded already.
        marked = generated_paths([listed['path'] for listed in files if not listed['isOther']])
        for listed in files:
            listed['isGenerated'] = listed['path'] in marked
        info = self.git or read_json(self.record / 'session.json', {})
        channel = shared('channel')
        # The folders a deleted file was in that are gone with it, its own and those up from it.
        gone_folders = set()
        for listed in files:
            folder = os.path.dirname(listed['path']) if listed['isGone'] else ''
            while folder not in ('', '/') and folder not in gone_folders and not os.path.isdir(folder):
                gone_folders.add(folder)
                folder = os.path.dirname(folder)
        return {'session': self.session, 'title': info.get('title') or 'Claude session', 'files': files,
                'goneFolders': sorted(gone_folders),
                'comments': [comment for comment in self.comments() if not comment.get('describe')],
                'threads': self.threads(),
                # The overview kept, by its file's signature, where an ask for one is, and when the session's
                # edits last changed the work, which may make it old.
                'outline': {'signature': signature(self.folder / 'outline.json'), 'asked': self.asked_state(),
                            'edited': self.edited()},
                # Whether Claude's command waits for comments now, and how many sent ones nobody took.
                'isWatched': channel.watching(self.sent) == 'wait',
                # Whether a git review's files are still being found.
                'isScanning': self.session in scans,
                # The last move of a git review to another base commit: {from, to, time}.
                'rebased': (self.git or {}).get('rebased'),
                # Where the page's full tree starts.
                'top': self.top(),
                # What the page's own code is now: a page made of other code loads itself again.
                'page': page_code(),
                'untaken': sum(len(ask.get('comments') or []) for ask in channel.waiting(self.sent))}

    def opened(self, key, is_bare=False):
        """The file's three texts as the page shows them; is_bare: their hashes and sizes alone, for a
        file the page does not compare."""
        file = self.file(key)
        places = self.places(key, file)
        moved = self.move_of(key)
        if moved:
            places = self.moved_places(places, self.places(*moved))
        texts = {}
        for name, place in places.items():
            data = self.read(file, name, place)
            texts[name] = {'sha': sha(data), 'exists': data is not None, 'text': None if is_bare else shown(data),
                           'size': None if data is None else len(data)}
            if is_bare:
                continue
            # An image the page cannot show as text it shows as the picture it is.
            kind = image_type(data) if data is not None and texts[name]['text'] is None and len(data) <= shown_bytes else None
            if kind:
                texts[name]['image'] = f'data:{kind};base64,{base64.b64encode(data).decode()}'
        return {'key': key, 'path': file['path'], 'movedFrom': moved[1]['path'] if moved else None,
                'isLink': bool(file.get('link')), 'texts': texts}

    def top(self):
        """The folder the page's full tree starts from, with its closing slash: a git review's work
        tree; for a session, the repository the work's files are in, else the folder they all are in.
        None with no such files."""
        if self.git:
            return self.git['repo'].rstrip('/') + '/'
        paths = [file['path'] for _, file in self.entries() if not is_other(file['path'])]
        if not paths:
            return None
        common = os.path.commonpath([os.path.dirname(path) for path in paths])
        top = repository_top(common)
        return (top if top and f'{common}/'.startswith(f'{top}/') else common).rstrip('/') + '/'

    def apply(self, key, action, change, expected):
        """Carries out accept, reject or unaccept, for one change, for the selected parts of some (a
        list) or, with no change named, the whole file, if the two texts are still the ones the page
        showed."""
        file = self.file(key)
        if action not in actions:
            raise Refusal(400, 'No such action.')
        left_name, right_name = actions[action][:2]
        moved = self.move_of(key)
        if moved:
            return self.apply_move(key, file, *moved, action, change, expected)
        if file.get('link') and change is not None:
            raise Refusal(400, 'A link is accepted or rejected whole.')
        places = self.places(key, file)
        left, right = self.read(file, left_name, places[left_name]), self.read(file, right_name, places[right_name])
        if not isinstance(expected, dict) or (sha(left), sha(right)) != (expected.get('left'), expected.get('right')):
            raise Refusal(409, 'The file changed since it was shown.')
        entry = self.applied(key, file, action, change, left, right)
        if file.get('uncarried') and read_bytes(places['base']) == self.read(file, 'disk', places['disk']):
            # Looked at again, to the end: the mark a rebase left goes.
            kept = {name: value for name, value in self.file(key).items() if name != 'uncarried'}
            write_atomic(self.record / 'files' / f'{key}.json', json.dumps(kept).encode())
        return entry

    def apply_move(self, key, file, old_key, old_file, action, change, expected):
        """Carries out an action on a moved file, whole: on its old place and its new one, as one
        entry of the journal (a group). Accept keeps the move, Reject puts the file back where it was,
        with what was accepted of it, and Unaccept makes the move pending again."""
        if change is not None:
            raise Refusal(400, 'A moved file is accepted or rejected whole.')
        left_name, right_name = actions[action][:2]
        places = self.moved_places(self.places(key, file), self.places(old_key, old_file))
        left, right = read_bytes(places[left_name]), read_bytes(places[right_name])
        if not isinstance(expected, dict) or (sha(left), sha(right)) != (expected.get('left'), expected.get('right')):
            raise Refusal(409, 'The file changed since it was shown.')
        entries = self.moved_applied(key, file, old_key, old_file, action, f'move-{time.time_ns():x}')
        if not entries:
            raise Refusal(409, 'Nothing to do: the file changed since it was shown.')
        return {**entries[0], 'path': file['path'], 'movedFrom': old_file['path']}

    def moved_applied(self, key, file, old_key, old_file, action, group, marks=None):
        """Carries out the action on a moved file's old place and its new one, each an entry of the
        journal in group; returns the entries."""
        left_name, right_name = actions[action][:2]
        entries = []
        # The old place first: a file put back is there before the new one goes. But for a file renamed
        # by its case the two places are one to the file system: the new name goes first, or putting
        # the old one back would write into it, and taking it away then would take the file.
        members = [(old_key, old_file), (key, file)]
        if old_file['path'].casefold() == file['path'].casefold():
            members.reverse()
        for member_key, member in members:
            member_places = self.places(member_key, member)
            member_left = self.read(member, left_name, member_places[left_name])
            member_right = self.read(member, right_name, member_places[right_name])
            if member_left != member_right:
                entries.append(self.applied(member_key, member, action, None, member_left, member_right, group, marks))
        return entries

    def accept_all(self, signatures=None):
        """Accepts what is pending of every file of the review, as one entry of History, which one Undo takes
        back: a rubber stamp. With signatures (the page's), only while each file is as the page listed it.
        Returns that entry as the page shows it."""
        pending = {file['key']: file for file in self.listing()['files'] if file['pending']}
        if not pending:
            raise Refusal(409, 'Nothing is pending.')
        if signatures is not None and any((signatures or {}).get(key) != file['signature'] for key, file in pending.items()):
            raise Refusal(409, 'A file changed since the page showed it.')
        return self.apply_files(list(pending), 'accept', {key: file['signature'] for key, file in pending.items()},
                                None, is_all=True)

    def apply_files(self, keys, action, signatures, folder, is_generated=False, is_all=False):
        """Carries out a whole-file action on each of the files, a folder's or the generated ones,
        that has something in the action's view, as one entry of the journal, if each is still as the
        page listed it. Returns that entry as the page shows it, its id the one Undo takes."""
        if action not in actions:
            raise Refusal(400, 'No such action.')
        if not isinstance(keys, list) or not keys or not isinstance(signatures, dict) or not (isinstance(folder, str) or is_generated or is_all):
            raise Refusal(400, 'No files named.')
        listed = {listed['key']: listed for listed in self.listing()['files']}
        if any(key not in listed or listed[key]['signature'] != signatures.get(key) for key in keys):
            raise Refusal(409, 'A file changed since the page showed it.')
        view = {'accept': 'pending', 'reject': 'pending', 'unaccept': 'accepted'}[action]
        todo = [key for key in keys if listed[key][view]]
        if not todo:
            raise Refusal(409, 'Nothing to do.')
        left_name, right_name = actions[action][:2]
        group = f'folder-{time.time_ns():x}'
        marks = {'generated': True, 'files': len(todo)} if is_generated else {'all': True, 'files': len(todo)} if is_all \
            else {'folder': folder, 'files': len(todo)}
        entries = []
        for key in todo:
            file = self.file(key)
            moved = self.move_of(key)
            if moved:
                entries += self.moved_applied(key, file, *moved, action, group, marks)
                continue
            places = self.places(key, file)
            left, right = self.read(file, left_name, places[left_name]), self.read(file, right_name, places[right_name])
            entries.append(self.applied(key, file, action, None, left, right, group, marks))
        return shown_entry(entries[0], entries)

    def applied(self, key, file, action, change, left, right, group=None, marks=None):
        """Writes what the action makes of the two texts, and adds it to the journal; group ties the
        entries of one action on a moved file, or a folder's files; marks go with each, the folder say."""
        right_name, target, is_left_taken = actions[action][1:]
        result = (left if is_left_taken else right) if change is None else spliced(left, right, change, is_left_taken)
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
            **({'group': group} if group else {}),
            **(marks or {}),
        }
        self.write(key, file, target, result)
        # The whole file's action takes its executable bit along, as the text: [before, after].
        if change is None and not file.get('link'):
            current = self.file(key)
            mode = self.modes(current, current, current['path'])
            source = {'accept': 'disk', 'reject': 'base', 'unaccept': 'orig'}[action]
            if None not in (mode[target], mode[source]) and mode[target] != mode[source]:
                self.set_mode(key, target, mode[source])
                entry['executable'] = [mode[target], mode[source]]
        write_atomic(self.folder / 'journal.json', json.dumps(self.journal() + [entry], indent=1).encode())
        return entry

    def flip(self, entry_id):
        """Undoes a journal entry, or redoes an undone one, if the text it wrote is still in place; an
        entry of a moved file's action with the rest of its group, the last first when undone."""
        journal = self.journal()
        entry = next((e for e in journal if e['id'] == entry_id), None)
        if not entry:
            raise Refusal(404, 'No such entry.')
        members = [e for e in journal if e.get('group') == entry['group']] if entry.get('group') else [entry]
        here_name, there_name = ('before', 'after') if entry['isUndone'] else ('after', 'before')
        flips = []
        for member in members if entry['isUndone'] else reversed(members):
            file = self.file(member['key'])
            place = self.places(member['key'], file)[member['target']]
            if sha(self.read(file, member['target'], place)) != member[here_name]:
                raise Refusal(409, 'The file changed since.')
            there = None if member[there_name] == '-' else read_bytes(self.folder / 'blobs' / member[there_name])
            if member[there_name] != '-' and there is None:
                raise Refusal(410, 'The text to put back is gone.')
            flips.append((member, file, there))
        for member, file, there in flips:
            self.write(member['key'], file, member['target'], there)
            # The executable bit it moved goes back too, where there is a file to have it.
            if member.get('executable') and (member['target'] != 'disk' or there is not None):
                self.set_mode(member['key'], member['target'], member['executable'][0 if not member['isUndone'] else 1])
            member['isUndone'] = not member['isUndone']
        write_atomic(self.folder / 'journal.json', json.dumps(journal, indent=1).encode())
        return shown_entry(entry, members)

    def journal(self):
        journal = read_json(self.folder / 'journal.json', [])
        return journal if isinstance(journal, list) else []

    def journal_shown(self):
        """The newest entries first, each saying whether it can be undone (or redone) now; the entries
        of one action on a moved file as one."""
        hashes = {}
        journal = self.journal()[-200:]
        groups = {}
        for entry in journal:
            if entry.get('group'):
                groups.setdefault(entry['group'], []).append(entry)

        def can_flip(entry):
            try:
                file = self.file(entry['key'])
                place = self.places(entry['key'], file)[entry['target']]
            except Refusal:
                return False
            if place not in hashes:
                hashes[place] = sha(self.read(file, entry['target'], place))
            return hashes[place] == entry['before' if entry['isUndone'] else 'after']

        entries = []
        for entry in reversed(journal):
            members = groups.get(entry.get('group')) or [entry]
            if entry is members[0]:
                entries.append({**shown_entry(entry, members), 'canFlip': all(can_flip(member) for member in members)})
        return entries

    def checkpoint(self):
        """Of a session's review, what a commit took of the session's changes counts as accepted: for each
        work tree the record's files are in, the HEAD seen last is kept (review/heads.json), and where it
        moved since, each file of the record the new commits touched gets the committed text as its
        baseline, as one History entry, which Undo takes back. The first look only notes HEAD, so a part
        accepted is never taken back to an older commit. Returns that entry, or None."""
        if self.git:
            return None
        place = self.folder / 'heads.json'
        seen = read_json(place, {})
        trees = {}
        for key, file in self.entries():
            top = None if file.get('link') else repository_top(os.path.dirname(file['path']) or '/')
            if top:
                trees.setdefault(top, []).append((key, file))
        heads, todo = {}, []
        for top, files in trees.items():
            head = (git(top, 'rev-parse', 'HEAD') or b'').decode().strip()
            if not head:
                continue
            heads[top] = head
            if not seen.get(top) or seen[top] == head:
                continue
            touched = set((git(top, 'diff', '--name-only', '-z', seen[top], head) or b'').decode().split('\0'))
            for key, file in files:
                name = os.path.relpath(file['path'], top)
                if name not in touched:
                    continue
                base = read_bytes(self.places(key, file)['base'])
                now_text = committed_text(top, head, name)
                if now_text != base:
                    todo.append((key, file, base, now_text, head))
        entries = []
        if todo:
            group = f'commit-{time.time_ns():x}'
            for key, file, base, now_text, head in todo:
                entries.append(self.applied(key, file, 'accept', None, base, now_text, group,
                                            {'commit': head[:10], 'files': len(todo)}))
        if any(seen.get(top) != head for top, head in heads.items()):
            write_atomic(place, json.dumps({**seen, **heads}, indent=1).encode())
        return shown_entry(entries[0], entries) if entries else None

    def last_commit(self):
        """The newest commit's entry in History, as the page shows it, not undone; None with none: the
        page says so when it comes, whoever had it taken."""
        journal = self.journal()
        last = next((entry for entry in reversed(journal) if entry.get('commit') and not entry['isUndone']), None)
        if not last:
            return None
        return shown_entry(last, [entry for entry in journal if entry.get('group') == last.get('group')])

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
            self.write_disk(file, place, data)
            if data is None:
                self.remove_emptied(place)
            # What the review writes is not someone else's change: the session's hash follows it.
            if not self.git and (self.record / 'after' / key).exists():
                write_atomic(self.record / 'after' / key, sha(self.read(file, 'disk', place)).encode())
        elif data is None:
            # A deleted file kept as deleted: no baseline, and new.
            place.unlink(missing_ok=True)
            kept = {'path': file['path'], 'new': True, **{name: file[name] for name in ('link', 'executable') if name in file}}
            write_atomic(self.record / 'files' / f'{key}.json', json.dumps(kept).encode())
        else:
            write_atomic(place, data)

    def remove_emptied(self, place):
        """Takes away the folders a file taken away leaves empty, a moved folder's say, from its own
        up, never the review's top nor above it; a folder with anything in it stays."""
        top = self.top()
        folder = os.path.dirname(str(place))
        while top and folder.startswith(top) and f'{folder}/' != top:
            try:
                os.rmdir(folder)
            except OSError:
                return
            folder = os.path.dirname(folder)

    @staticmethod
    def write_disk(file, place, data):
        """Puts the text in the file itself, or with none, takes the file away."""
        if file.get('link'):
            return write_link_place(place, data)
        if data is None:
            # A file taken away goes to the Trash, not out of reach.
            ran = subprocess.run([*trash_command, str(place)], capture_output=True, text=True)
            if ran.returncode:
                raise Refusal(500, f'Could not move the file to the Trash: {ran.stderr.strip()}')
            return
        place.parent.mkdir(parents=True, exist_ok=True)
        # In place: the file keeps its mode and its links.
        with open(place, 'wb') as f:
            f.write(data)

    def comments(self):
        comments = read_json(self.folder / 'comments.json', [])
        return comments if isinstance(comments, list) else []

    def comment(self, body):
        text = body.get('text')
        if not isinstance(text, str) or not text.strip():
            raise Refusal(400, 'The comment is empty.')
        if body.get('item') is not None:
            return self.kept_comment(self.item_comment(body, text.strip()[:4000]), body)
        if body.get('folder') is not None:
            return self.kept_comment(self.folder_comment(body, text.strip()[:4000]), body)
        file = self.file(body.get('key'))
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
        # A comment on the whole file, its lines all of the text it was made on.
        if body.get('isFile') is True:
            comment['isFile'] = True
        return self.kept_comment(comment, body)

    def item_comment(self, body, text):
        """A comment on an item of the overview, as the page showed it: by its place and its title. It
        quotes the item, its text and its blocks."""
        outline = read_json(self.folder / 'outline.json', {})
        items = outline.get('items') if isinstance(outline, dict) else None
        index = body.get('item')
        if not isinstance(items, list) or not isinstance(index, int) or not 0 <= index < len(items) \
                or items[index].get('title') != body.get('title'):
            raise Refusal(409, 'The overview changed since the page showed it.')
        item = items[index]
        blocks = []
        for block in item.get('blocks') or []:
            lines = '' if block.get('start') is None else ':%s-%s' % (block['start'], block['end'])
            side = ' (as it was)' if block.get('side') == 'orig' else ''
            note = ' — ' + block['note'] if block.get('note') else ''
            blocks.append(block['path'] + lines + side + note)
        return {'id': f'{time.time_ns():x}', 'time': now(), 'item': item['title'],
                'quote': '\n'.join([item.get('text') or '', *blocks]).strip()[:4000], 'text': text}

    def folder_comment(self, body, text):
        """A comment on a folder of the list, by its path with the closing slash. It quotes the
        review's changed files under it as they were then, each by its path from the folder, said new,
        deleted or moved."""
        folder = body.get('folder')
        if not isinstance(folder, str) or not folder.startswith('/') or not folder.endswith('/'):
            raise Refusal(400, 'The comment names no folder.')
        lines = []
        for listed in sorted(self.listing()['files'], key=lambda listed: listed['path']):
            if not listed['path'].startswith(folder) or not listed['all']:
                continue
            marks = ['new'] if listed['isNew'] else ['deleted'] if listed['isGone'] else []
            if listed['movedFrom']:
                marks.append(f"moved from {listed['movedFrom']}")
            lines.append(listed['path'][len(folder):] + (f" ({', '.join(marks)})" if marks else ''))
        if not lines:
            raise Refusal(409, 'The folder has no changes now.')
        return {'id': f'{time.time_ns():x}', 'time': now(), 'folder': folder, 'quote': '\n'.join(lines)[:4000],
                'text': text}

    def kept_comment(self, comment, body):
        """Keeps a comment to be sent, in the thread it replies in if any; sends it at once while
        Claude's command waits."""
        if body.get('thread'):
            if not any(thread['id'] == body['thread'] for thread in self.threads()):
                raise Refusal(404, 'No such thread.')
            comment['thread'] = body['thread']
        # Under the channel's lock, as the hook and the waiting command empty the file under it.
        channel = shared('channel')
        with channel.locked(self.sent):
            write_atomic(self.folder / 'comments.json', json.dumps(self.comments() + [comment], indent=1).encode())
            # Claude's command waits for comments now: this one is sent, with any made before it.
            if channel.watching(self.sent) == 'wait':
                self.post(channel)
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
            self.post(channel)
        return {'sent': len(comments)}

    def post(self, channel):
        """Sends the comments not sent yet, as one ask; returns how many. Under the channel's lock,
        which the caller holds."""
        comments = self.comments()
        if comments:
            channel.append(self.sent, {'comments': comments})
            write_atomic(self.folder / 'comments.json', b'[]')
        return len(comments)

    def threads(self):
        """The comments as the page shows them, each first one with what followed it: the user's
        replies and Claude's answers, the oldest first, and of each of the user's whether Claude has
        it ('draft', 'sent' and not taken, 'taken'). A thread the user resolved is left out."""
        channel = shared('channel')
        handed = {line.get('ask') for line in channel.records(self.folder / 'handed.jsonl')}
        said = [dict(comment, state='taken' if ask.get('id') in handed else 'sent')
                for ask in channel.records(self.sent) for comment in ask.get('comments') or []
                if isinstance(comment, dict) and not comment.get('describe')]
        # The ask for an overview goes the comments' way, and is no thread.
        said += [dict(comment, state='draft') for comment in self.comments()
                 if isinstance(comment, dict) and not comment.get('describe')]
        notes = channel.records(self.folder / 'answers.jsonl')
        threads, firsts = {}, {}
        for comment in said:
            first = comment.get('thread') if comment.get('thread') in threads else comment.get('id')
            firsts[comment.get('id')] = first
            where = {name: comment.get(name) for name in ('key', 'path', 'side', 'start', 'end', 'quote', 'anchor', 'anchorSide', 'isFile', 'item', 'folder')}
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

    # The overview Claude writes of the changes when the user asks: items by purpose, each with the
    # blocks of code it is made of. A block is lines of a file on one side (disk: the file as it is;
    # orig: as it was before), kept with the lines it named, so they are found again when lines above
    # them move, and marked stale when they are gone.

    def describe(self):
        """Asks Claude for the overview: an ask among the comments, sent at once while Claude's
        command waits, else with the session's next message."""
        channel = shared('channel')
        with channel.locked(self.sent):
            if not any(comment.get('describe') for comment in self.comments()):
                ask = {'id': f'{time.time_ns():x}', 'time': now(), 'describe': True}
                write_atomic(self.folder / 'comments.json', json.dumps(self.comments() + [ask], indent=1).encode())
            if channel.watching(self.sent) == 'wait':
                self.post(channel)
        return {'asked': self.asked_state()}

    def needs_overview(self):
        """Whether the work's changes have a part no overview covers: none written yet while the work has
        changes, or changes no block of it touches; never while an ask for one waits."""
        if self.asked_state():
            return False
        shown = self.outline_shown()
        if shown['time'] is None:
            return any(file['all'] and not file['isOther'] and not file['isGenerated'] for file in self.listing()['files'])
        return shown['isOld'] or bool(shown['uncovered'])

    def edited(self):
        """When the session's own edits last changed its work, as the recorder keeps it; '' before any."""
        return (read_bytes(self.record / 'edited') or b'').decode()

    def is_outline_old(self, outline):
        """Whether the session's edits changed its work since the overview was asked for: the record's
        edited is not the one kept with it. One kept before edited was: by the times of the session's
        edits (after/), which the review's Reject moves too. A git review's never is."""
        if self.git:
            return False
        if 'edited' in outline:
            return outline['edited'] != self.edited()
        try:
            written = datetime.datetime.fromisoformat(str(outline.get('time'))).timestamp()
        except ValueError:
            return False
        return any(after.stat().st_mtime > written for after in (self.record / 'after').glob('*'))

    def describe_by_fork(self):
        """Asks for the overview by a question over the session's own conversation, which the Claude Code
        mod asks as a fork with no tools: the ask, the page's Describe where one waits, is logged as sent and
        handed over at once, so the page says Claude writes it and nothing hands it over again. Returns the
        question: how to write it, the items of the one written before, to keep those that still hold, and
        the changes, numbered."""
        channel = shared('channel')
        with channel.locked(self.sent):
            # The page's Describe, waiting to go with the next message: the fork answers it now.
            comments = self.comments()
            drafted = [comment for comment in comments if comment.get('describe')]
            if drafted:
                write_atomic(self.folder / 'comments.json',
                             json.dumps([comment for comment in comments if not comment.get('describe')], indent=1).encode())
            ask = channel.append(self.sent, {'comments': drafted or [{'id': f'{time.time_ns():x}', 'time': now(), 'describe': True}]})
            channel.mark(self.sent, [ask], 'fork')
            # The work the fork describes: an edit of the session's while it writes makes its overview old.
            write_atomic(self.folder / 'asked-edited', self.edited().encode())
        outline = read_json(self.folder / 'outline.json', {})
        titles = [item.get('title') for item in outline.get('items') or []] if isinstance(outline, dict) else []
        changes = f"the changes in {self.git['repo']} against {self.git['spec']}" if self.git else 'your changes'
        return (outline_asked(changes, self.git, self.session, titles, is_forked=True, is_users=bool(drafted)) + '\n\n' +
                self.changes_shown())

    def drop_ask(self):
        """The last ask for an overview, taken by a fork that brought none: it waits no more."""
        channel = shared('channel')
        asks = [ask for ask in channel.records(self.sent) for comment in ask.get('comments') or []
                if isinstance(comment, dict) and comment.get('describe')]
        if asks:
            channel.add_line(self.folder / 'handed.jsonl', {'ask': asks[-1].get('id'), 'time': channel.stamp(), 'dropped': True})
        (self.folder / 'asked-edited').unlink(missing_ok=True)

    def changes_shown(self):
        """The work's changes as a model with no tools reads them: by file, each changed stretch with the
        lines around it, each line marked + (added), - (taken away) or neither, and numbered in the file as
        it is now, or, in brackets, as it was; past shown_change_chars, the files left are only named."""
        out, left = [], []
        size = 0
        for file in self.listing()['files']:
            if not file['all'] or file['isOther'] or file['isGenerated']:
                continue
            if size > shown_change_chars:
                left.append(file['path'])
                continue
            moved = f" (moved from {file['movedFrom']})" if file.get('movedFrom') else ''
            lines = self.lines_of(file['key'])
            if lines['orig'] is None or lines['disk'] is None:
                part = [f"=== {file['path']}{moved}: no text to show by lines (a link, binary, or too long)"]
            else:
                part = [f"=== {file['path']}{moved}" + (' (new)' if file['isNew'] else ' (deleted)' if file['isGone'] else '')]
                matcher = difflib.SequenceMatcher(None, lines['orig'], lines['disk'], autojunk=False)
                for group in matcher.get_grouped_opcodes(3):
                    part.append('@@')
                    for tag, a1, a2, b1, b2 in group:
                        if tag == 'equal':
                            part += [f'  {b + 1} | {shown_line(lines["disk"][b])}' for b in range(b1, b2)]
                            continue
                        part += [f'- ({a + 1}) | {shown_line(lines["orig"][a])}' for a in range(a1, a2)]
                        part += [f'+ {b + 1} | {shown_line(lines["disk"][b])}' for b in range(b1, b2)]
            size += sum(len(line) + 1 for line in part)
            out += part
        if left:
            out.append('=== Not shown, past what the question holds: ' + ', '.join(left))
        return '\n'.join(out)

    def asked_state(self):
        """Where the last ask for an overview is: 'draft', to go with the next message; 'sent', not
        taken yet; 'taken', Claude writes it; None, answered, dropped or never asked."""
        if any(comment.get('describe') for comment in self.comments()):
            return 'draft'
        channel = shared('channel')
        asks = [(ask, comment) for ask in channel.records(self.sent) for comment in ask.get('comments') or []
                if isinstance(comment, dict) and comment.get('describe')]
        if not asks:
            return None
        # By the ask's own time, which now() wrote, as it writes the overview's; the log's is local.
        last, asked = asks[-1]
        outline = read_json(self.folder / 'outline.json', {})
        if isinstance(outline, dict) and str(outline.get('time') or '') >= str(asked.get('time') or ''):
            return None
        handed = [line for line in channel.records(self.folder / 'handed.jsonl') if line.get('ask') == last.get('id')]
        if any(line.get('dropped') for line in handed):
            return None
        taken = handed[0] if handed else None
        if not taken:
            return 'sent'
        try:
            taken_at = time.mktime(time.strptime(str(taken.get('time')), '%Y-%m-%dT%H:%M:%S'))
        except ValueError:
            return 'taken'
        return 'taken' if time.time() - taken_at < taken_minutes * 60 else None

    def lines_of(self, key):
        """The file's three texts as lines, split as spliced() splits them: a missing text has none of
        its own, [''], and one that is no text (a link, binary, or past what is counted) is None. A moved
        file's at its new place, its orig the old place's."""
        file = self.file(key)
        places = self.places(key, file)
        moved = self.move_of(key)
        if moved:
            places = self.moved_places(places, self.places(*moved))
        lines = {}
        for name, place in places.items():
            data = None if file.get('link') else self.read(file, name, place)
            try:
                is_text = not file.get('link') and (data is None or len(data) <= counted_bytes)
                lines[name] = (data or b'').decode().split('\n') if is_text else None
            except UnicodeDecodeError:
                lines[name] = None
        return lines

    def set_outline(self, data):
        """Keeps Claude's overview, each block's lines read from its file as it is now; refuses one that
        names a file the review does not have, or lines its file does not have, saying which."""
        items = data.get('items') if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise Refusal(400, 'The overview has no items: {"items": [{"title", "text", "blocks": [...]}]}.')
        if len(items) > outline_items:
            raise Refusal(400, f'The overview has {len(items)} items: at most {outline_items}.')
        problems, kept_items = [], []
        for n, item in enumerate(items, 1):
            if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not item['title'].strip():
                problems.append(f'Item {n} has no title.')
                continue
            blocks = []
            for m, block in enumerate(item.get('blocks') or [], 1):
                where = f'Item {n}, block {m}'
                if not isinstance(block, dict) or not isinstance(block.get('path'), str):
                    problems.append(f'{where} names no path.')
                    continue
                path = os.path.abspath(block['path'])
                found = next((key(p) for p in (path, os.path.realpath(path))
                              if (self.record / 'files' / f'{key(p)}.json').exists()), None)
                side = block.get('side') or 'disk'
                if not found:
                    problems.append(f"{where}: {path} is not among the review's files.")
                    continue
                if side not in ('disk', 'orig'):
                    problems.append(f'{where}: side is "disk" (the file as it is) or "orig" (as it was).')
                    continue
                kept = {'key': found, 'path': self.file(found)['path'], 'side': side,
                        'note': str(block.get('note') or '').strip()[:300]}
                if block.get('start') is not None or block.get('end') is not None:
                    try:
                        start, end = int(block['start']), int(block['end'] if block.get('end') is not None else block['start'])
                    except (KeyError, TypeError, ValueError):
                        problems.append(f'{where}: start and end are line numbers.')
                        continue
                    lines = self.lines_of(found)[side]
                    if lines is None:
                        problems.append(f'{where}: {path} is no text the page shows by lines; leave out start and end.')
                        continue
                    if not 1 <= start <= end <= len(lines):
                        problems.append(f'{where}: {path} has no lines {start}-{end}{" as it was" if side == "orig" else ""}: it has {len(lines)}.')
                        continue
                    kept.update(start=start, end=end, quote='\n'.join(lines[start - 1:end]))
                blocks.append(kept)
            kept_items.append({'title': item['title'].strip()[:160], 'text': str(item.get('text') or '').strip()[:1200],
                               'blocks': blocks})
        count = sum(len(item['blocks']) for item in kept_items)
        if count > outline_blocks:
            problems.append(f'The overview has {count} blocks: at most {outline_blocks}.')
        if problems:
            raise Refusal(400, 'The overview was not kept:\n' + '\n'.join(problems))
        channel = shared('channel')
        with channel.locked(self.sent):
            # The work as it was when the fork was asked, else as it is.
            asked_edited = self.folder / 'asked-edited'
            edited = read_bytes(asked_edited)
            write_atomic(self.folder / 'outline.json', json.dumps({
                'time': now(), 'edited': self.edited() if edited is None else edited.decode(), 'items': kept_items,
            }, indent=1).encode())
            asked_edited.unlink(missing_ok=True)
            # An ask for it still waiting is answered: it goes, and is not told again.
            left = [comment for comment in self.comments() if not comment.get('describe')]
            if len(left) != len(self.comments()):
                write_atomic(self.folder / 'comments.json', json.dumps(left, indent=1).encode())
            answered = [ask for ask in channel.waiting(self.sent) if ask.get('comments')
                        and all(isinstance(comment, dict) and comment.get('describe') for comment in ask['comments'])]
            if answered:
                channel.mark(self.sent, answered, 'outline')
        return self.outline_shown()

    def outline_shown(self):
        """The overview as the page shows it: each block where its lines are now and what is left of
        it to review ('pending', 'accepted', 'unchanged', 'stale': its lines are gone), each item's
        pending blocks counted; the changes no block touches; and where an ask for it is. One the session's
        edits made old has no items: it would pass for one of the changes since (isOld)."""
        shown = {'asked': self.asked_state(), 'time': None, 'isOld': False, 'items': [], 'uncovered': []}
        outline = read_json(self.folder / 'outline.json', None)
        if not isinstance(outline, dict):
            return shown
        if self.is_outline_old(outline):
            return {**shown, 'time': outline.get('time'), 'isOld': True}
        listed = {file['key']: file for file in self.listing()['files']}
        # A moved file's blocks may name its old place: they are the file's at its new one.
        owner = {key(file['movedFrom']): file['key'] for file in listed.values() if file.get('movedFrom')}
        facts = {}

        def facts_of(file_key):
            if file_key not in facts:
                try:
                    lines = self.lines_of(file_key)
                except Refusal:
                    lines = {'orig': None, 'base': None, 'disk': None}
                compared = lambda a, b: changes_between(lines[a], lines[b]) if None not in (lines[a], lines[b]) else None
                facts[file_key] = (lines, compared('base', 'disk'), compared('orig', 'disk'))
            return facts[file_key]

        def state_of(block, file_key):
            file = listed.get(file_key)
            whole = 'pending' if file and file['pending'] else 'accepted' if file and file['all'] else 'unchanged'
            if block.get('start') is None:
                return whole, None
            lines, pending, changed = facts_of(file_key)
            span = located(lines[block['side']], block) if lines[block['side']] is not None else None
            if span is None:
                return 'stale', None
            # Lines taken away: the file's state; the file as it is: its own lines'.
            if block['side'] == 'orig' or pending is None or changed is None:
                return whole, span
            if any(touches((b1, b2), *span) for _, _, _, b1, b2 in pending):
                return 'pending', span
            if any(touches((b1, b2), *span) for _, _, _, b1, b2 in changed):
                return 'accepted', span
            return 'unchanged', span

        covers = {}
        for item in outline.get('items') or []:
            blocks = []
            for block in item.get('blocks') or []:
                file_key = owner.get(block['key'], block['key'])
                state, span = state_of(block, file_key)
                blocks.append({'key': file_key, 'path': block['path'], 'side': block['side'], 'note': block.get('note') or '',
                               'start': span[0] if span else block.get('start'), 'end': span[1] if span else block.get('end'),
                               'isFile': block.get('start') is None, 'state': state})
                if block.get('start') is None:
                    covers.setdefault(file_key, []).append(('file', None))
                elif span:
                    covers.setdefault(file_key, []).append((block['side'], span))
            shown['items'].append({'title': item.get('title') or '', 'text': item.get('text') or '', 'blocks': blocks,
                                   'pending': sum(block['state'] == 'pending' for block in blocks)})
        for file_key, file in listed.items():
            if not file['all'] or file['isOther'] or file['isGenerated'] or len(shown['uncovered']) >= uncovered_shown:
                continue
            marks = covers.get(file_key, [])
            if any(side == 'file' for side, _ in marks):
                continue
            changed = facts_of(file_key)[2]
            if not changed:
                # No text to compare by lines, or a change of its own (a move, a mode): the file whole.
                if not marks:
                    shown['uncovered'].append({'key': file_key, 'path': file['path'], 'start': None, 'end': None})
                continue
            for _, a1, a2, b1, b2 in changed:
                if not any(touches((b1, b2) if side == 'disk' else (a1, a2), *span) for side, span in marks):
                    shown['uncovered'].append({'key': file_key, 'path': file['path'], 'start': b1 + 1, 'end': max(b1 + 1, b2),
                                               'isRemoval': b1 == b2})
        shown['time'] = outline.get('time')
        return shown

    def apply_item(self, index, stamp, signatures):
        """Accepts what is pending of one overview item, as one entry of the journal: in each file the
        changes its blocks touch, or the whole file for a block on all of it or on a deleted file. If the
        overview and the files are as the page showed them."""
        outline = read_json(self.folder / 'outline.json', None)
        if not isinstance(outline, dict) or outline.get('time') != stamp:
            raise Refusal(409, 'The overview changed since the page showed it.')
        items = outline.get('items') or []
        if not isinstance(index, int) or not 0 <= index < len(items):
            raise Refusal(400, 'No such item.')
        listed = {file['key']: file for file in self.listing()['files']}
        owner = {key(file['movedFrom']): file['key'] for file in listed.values() if file.get('movedFrom')}
        named = {}
        for block in items[index]['blocks']:
            named.setdefault(owner.get(block['key'], block['key']), []).append(block)
        if not isinstance(signatures, dict) or any(k in listed and listed[k]['signature'] != signatures.get(k) for k in named):
            raise Refusal(409, 'A file changed since the page showed it.')
        todo = []
        for file_key, blocks in named.items():
            if file_key not in listed or not listed[file_key]['pending']:
                continue
            file = self.file(file_key)
            # A moved file and a link are accepted whole, from their own file bar.
            if self.move_of(file_key) or file.get('link'):
                continue
            places = self.places(file_key, file)
            base, disk = self.read(file, 'base', places['base']), self.read(file, 'disk', places['disk'])
            if disk is None or any(block.get('start') is None for block in blocks):
                todo.append((file_key, file, None, base, disk))
                continue
            lines = self.lines_of(file_key)
            if None in (lines['base'], lines['disk']):
                continue
            touched = []
            for _, a1, a2, b1, b2 in changes_between(lines['base'], lines['disk']):
                for block in blocks:
                    # Lines taken away are found in the accepted text while it is still the old one.
                    side = 'disk' if block['side'] == 'disk' else 'base' if lines['orig'] == lines['base'] else None
                    span = side and located(lines[side], block)
                    if span and touches((b1, b2) if side == 'disk' else (a1, a2), *span):
                        touched.append({'leftStart': a1, 'leftEnd': a2, 'rightStart': b1, 'rightEnd': b2})
                        break
            if touched:
                todo.append((file_key, file, touched, base, disk))
        if not todo:
            raise Refusal(409, 'Nothing of it is pending.')
        group = f'item-{time.time_ns():x}'
        marks = {'item': items[index]['title'], 'files': len(todo)}
        entries = [self.applied(file_key, file, 'accept', change, base, disk, group, marks)
                   for file_key, file, change, base, disk in todo]
        return shown_entry(entries[0], entries)


def fenced(text):
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}\n{text}\n{fence}'


def described(comments, git=None, name=None):
    """The comments as the model is told them, and an ask for an overview among them; git is what a
    git review is of, name the review's."""
    changes = f"the changes in {git['repo']} against {git['spec']}" if git else 'your changes'
    asks = [comment for comment in comments if comment.get('describe')]
    comments = [comment for comment in comments if not comment.get('describe')]
    told = [outline_asked(changes, git, name)] if asks else []
    if not comments:
        return '\n'.join(told)
    out = [f'The user left these comments on {changes} on the redpen review page; each quotes the '
           'lines it is on as they saw them.']
    for comment in comments:
        reply = f", a reply in the thread of comment {comment['thread']}" if comment.get('thread') else ''
        if comment.get('item') is not None:
            # On an item of the overview: it quotes the item, its text and its blocks.
            out += ['', f"The overview's item \"{comment['item']}\" (comment {comment.get('id')}{reply}):"]
            if comment.get('quote'):
                out.append(fenced(comment['quote']))
            out.append(f"Comment: {comment['text']}")
            continue
        if comment.get('folder') is not None:
            # On a folder: it quotes the folder's changed files.
            out += ['', f"The folder {comment['folder']}, its changed files (comment {comment.get('id')}{reply}):"]
            if comment.get('quote'):
                out.append(fenced(comment['quote']))
            out.append(f"Comment: {comment['text']}")
            continue
        lines = f"line {comment['start']}" if comment['start'] == comment['end'] else f"lines {comment['start']}-{comment['end']}"
        if comment.get('isFile'):
            lines = 'the whole file'
        side = sides.get(comment.get('side'), '')
        if git and comment.get('side') == 'orig':
            side = ' (of the file as it was at the base commit)'
        out += ['', f"{comment['path']}, {lines}{side} (comment {comment.get('id')}{reply}):"]
        if comment.get('quote'):
            out.append(fenced(comment['quote']))
        out.append(f"Comment: {comment['text']}")
    out += ['', 'Besides what you say or do about each, put a short answer under it on the page '
            '(its Markdown is drawn: fenced code, lists, `code`, **bold**): '
            f"`python3 {here / 'review.py'} --answer <comment id>`, the answer on stdin; after a change made "
            'in answer, add `--change <file>:<line>`, and the answer links to it.']
    return '\n'.join(out + ['', *told] if told else out)


def outline_asked(changes, git, name, titles=None, is_forked=False, is_users=False):
    """What tells the model to write the overview the user asked for; with titles, the items of the one
    written before, the ask the page's own, made when the review opened with changes it does not cover,
    unless is_users, the user's Describe; is_forked, asked of a fork with no tools, which answers with the
    overview and is shown the changes."""
    diffed = f" (`git -C {git['repo']} diff {git['base']}`, and its untracked files)" if git and not is_forked else ''
    before = ' at the base commit' if git else ' before your edits'
    asker = 'The user asked on the redpen review page' if titles is None or is_users else \
        'The redpen review page opened with changes no overview covers, and asks'
    again = [] if not titles else [
        'It replaces the one written before, whose items were: ' + '; '.join(f'"{title}"' for title in titles) +
        '. Keep those that still hold, and give the changes they do not cover items of their own.']
    write = ('Answer with it as JSON alone, no words and no fence around it:' if is_forked else
             f"Write it as JSON and run `python3 {here / 'review.py'} --outline {name}` with it on stdin:")
    shown = [] if not is_forked else [
        'You have no tools here: the changes are below, by file, each changed stretch with the lines around it. '
        'A line starts with + (added), - (taken away) or neither (unchanged), then its number in the file as it is '
        f'now, or, in brackets, as it was{before}.']
    return '\n'.join([
        f'{asker} for an overview of {changes}{diffed}: what they do, by purpose, each part pointing at its '
        f'code. {write}',
        '```json',
        '{"items": [{"title": "<a few words>", "text": "<one or two sentences: what it does and why>",',
        '  "blocks": [{"path": "<absolute path>", "start": <first line>, "end": <last line>, "note": "<a short line>"}]}]}',
        '```',
        'Lines are counted from 1 in the file as it is now. For lines taken away, or a deleted file, add '
        f'"side": "orig" and count in the file as it was{before}. A block with no start and end is the whole '
        'file. Group the changes by purpose, not by file, in the order a reviewer should read them, and let '
        f'every change belong to an item: {"the page lists" if is_forked else "the command and the page list"} '
        'those no block covers. Leave out the '
        'files the project generates (lockfiles, those marked linguist-generated or by a generator\'s banner). Each note says '
        'what its block does, in a few words; say only what the code shows.', *again, *shown])


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
                    found.append((review, comments, asks, described(told, review.git, review.session)))
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
            # The comments made while nobody waited are taken with the sent ones.
            with channel.locked(review.sent):
                review.post(channel)
            handed += channel.hand_over(review.sent, 'wait',
                                        lambda ask: print(described(ask.get('comments') or [], review.git, review.session)))
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


def accepted_all(session, port=None):
    """Accept all from outside the page (the Claude Code mod's row): through the running server, under its
    lock as the page's actions are, else, with none running, here. Returns the History entry."""
    server = shared('server')
    port = port or server.default_port
    url = f"http://127.0.0.1:{port}{server.mounts['code']}/api/{session}/accept-all"
    request = urllib.request.Request(url, data=b'{}', method='POST',
                                     headers={'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            return json.load(answer)
    except urllib.error.HTTPError as e:
        sys.exit(json.load(e).get('error') or str(e))
    except OSError:
        pass
    try:
        return Review(session).accept_all()
    except Refusal as e:
        sys.exit(str(e))


def describe_if_needed(session):
    """Prints the question that asks for the session's overview, if the page's Describe waits for it or its
    review needs one; else nothing."""
    try:
        review = Review(session)
        if (store / session / 'files').is_dir() and (review.asked_state() == 'draft' or review.needs_overview()):
            print(review.describe_by_fork())
    except Refusal as e:
        sys.exit(str(e))


def outline(name):
    """Keeps the overview on stdin for the review's page, and says what it leaves out."""
    try:
        data = json.load(sys.stdin)
    except ValueError as e:
        sys.exit(f'The overview on stdin is not JSON: {e}')
    try:
        shown = Review(name).set_outline(data)
    except Refusal as e:
        sys.exit(str(e))
    if shown['isOld']:
        print(f'The overview is kept, but the page of {name} does not show it: the session edited its files since it '
              'was asked for. The end of the turn asks for one anew.')
        return
    blocks = sum(len(item['blocks']) for item in shown['items'])
    print(f"The overview is on the page of {name}: {len(shown['items'])} items, {blocks} blocks.")
    if shown['uncovered']:
        print(f"{len(shown['uncovered'])} changes no block covers: add each to an item, unless it needs no word.")
        for change in shown['uncovered'][:40]:
            lines = '' if change['start'] is None else f":{change['start']}-{change['end']}"
            print(f"  {change['path']}{lines}{' (lines taken away there)' if change.get('isRemoval') else ''}")


def git(repo, *args):
    """What a git command prints, run in the repository without writing there (no index refresh);
    None when it fails."""
    try:
        ran = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                             env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    except OSError:
        return None
    return None if ran.returncode else ran.stdout


@functools.lru_cache(maxsize=512)
def repository_top(folder):
    """The top of the git work tree the folder is in; None outside one."""
    top = git(folder, 'rev-parse', '--show-toplevel')
    return top.decode().strip() if top else None


def ignored(repository, paths):
    """Which of the paths, each in the repository, git ignores; all of them when git cannot say. git
    reads the whole index to say it, so what it said holds for ignore_seconds."""
    known = ignore_verdicts.setdefault(repository, {})
    if len(known) > 100_000:
        known.clear()
    asked = [path for path in paths if known.get(path, (False, 0))[1] < time.time() - ignore_seconds]
    if asked:
        try:
            ran = subprocess.run(['git', '-C', repository, 'check-ignore', '-z', '--stdin'],
                                 input=b'\0'.join(os.fsencode(path) for path in asked), capture_output=True,
                                 env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
        except OSError:
            return set(paths)
        # 1: none of them is ignored.
        if ran.returncode not in (0, 1):
            return set(paths)
        said = {os.fsdecode(path) for path in ran.stdout.split(b'\0') if path}
        known.update((path, (path in said, time.time())) for path in asked)
    # A path another read cleared meanwhile counts as ignored, till git is asked again.
    return {path for path in paths if known.get(path, (True, 0))[0]}


def generated_paths(paths):
    """The paths of the files generated, as their projects mark them (see generated_names)."""
    verdicts = attribute_verdicts(paths)
    return {path for path in paths
            if verdicts.get(path) or verdicts.get(path) is None and (os.path.basename(path) in generated_names or is_marked_generated(path))}


def attribute_verdicts(paths):
    """What each path's .gitattributes says of linguist-generated: True, False, or None for nothing, as
    for a path outside a git work tree."""
    verdicts, by_top = {}, {}
    for path in paths:
        # A deleted file's repository, by the nearest folder still there.
        folder = os.path.dirname(path)
        while folder not in ('', '/') and not os.path.isdir(folder):
            folder = os.path.dirname(folder)
        top = repository_top(folder) if folder not in ('', '/') else None
        if top:
            by_top.setdefault(top, []).append(path)
        else:
            verdicts[path] = None
    for top, members in by_top.items():
        known = generated_verdicts.setdefault(top, {})
        if len(known) > 100_000:
            known.clear()
        asked = {path: os.path.relpath(os.path.realpath(path), top) for path in members
                 if known.get(path, (None, 0))[1] < time.time() - ignore_seconds}
        if asked:
            try:
                ran = subprocess.run(['git', '-C', top, 'check-attr', '-z', '--stdin', 'linguist-generated'],
                                     input=b'\0'.join(os.fsencode(name) for name in asked.values()), capture_output=True,
                                     env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
                said = ran.stdout.split(b'\0') if ran.returncode == 0 else []
            except OSError:
                said = []
            # path NUL attribute NUL value NUL, for each.
            values = {os.fsdecode(said[at]): said[at + 2].decode() for at in range(0, len(said) - 2, 3)}
            for path, name in asked.items():
                known[path] = ({'set': True, 'true': True, 'unset': False, 'false': False}.get(values.get(name)), time.time())
        verdicts.update((path, known[path][0]) for path in members)
    return verdicts


def is_marked_generated(path):
    """Whether the file's first lines say it is generated."""
    place = pathlib.Path(path)
    place_signature = signature(place)
    if place_signature == '-':
        return False
    kept = generated_heads.get(path)
    if kept and kept[0] == place_signature:
        return kept[1]
    try:
        with open(place, 'rb') as f:
            head = f.read(4096)
    except OSError:
        head = b''
    is_marked = is_generated_banner(b'\n'.join(head.split(b'\n')[:generated_head_lines]).decode(errors='replace'))
    if len(generated_heads) > cached_answers:
        generated_heads.clear()
    generated_heads[path] = (place_signature, is_marked)
    return is_marked


def is_generated_banner(text):
    """Whether a file's first lines carry a generator's banner (see generated_banners)."""
    return any(banner.search(text) for banner in generated_banners) or bool(generated_word.search(text) and do_not_edit.search(text))


def listed_folders(top, wanted):
    """What each wanted folder of a review's tree holds: {folder: {folders, files, more}}, the names of
    its folders and files and how many files past listed_files are left out. top is the review's, from
    Review.top(); folders are paths under it with their closing slash. What is on disk, one level of
    it, with what git ignores left out in a git work tree. Read without the lock, as it reads no
    record."""
    if not top or not isinstance(wanted, list):
        raise Refusal(404, 'The review has no folder to show.')
    real_top = os.path.realpath(top).rstrip('/') + '/'
    # Of each folder, its children's names, each with whether it is a folder.
    found = {}
    # Of each repository, its folders' children by their paths in it, for git to say which it ignores.
    asked = {}
    for folder in wanted:
        is_under = isinstance(folder, str) and (folder == top or folder.startswith(top) and f'{os.path.normpath(folder)}/' == folder)
        if not is_under or not f'{os.path.realpath(folder)}/'.startswith(real_top):
            continue
        try:
            with os.scandir(folder) as entries:
                found[folder] = {entry.name: entry.is_dir(follow_symlinks=False) for entry in entries if entry.name != '.git'}
        except OSError:
            found[folder] = {}
        repository = repository_top(folder)
        if repository:
            place = os.path.relpath(os.path.realpath(folder), repository)
            if place.startswith('..'):
                found[folder] = {}
                continue
            for name in found[folder]:
                asked.setdefault(repository, {})[os.path.normpath(os.path.join(place, name))] = (folder, name)
    for repository, children in asked.items():
        for path in ignored(repository, list(children)):
            folder, name = children.get(path, (None, None))
            found.get(folder, {}).pop(name, None)
    shown_folders = {}
    for folder, held in found.items():
        files = sorted(name for name, is_folder in held.items() if not is_folder)
        shown_folders[folder] = {'folders': sorted(name for name, is_folder in held.items() if is_folder),
                                 'files': files[:listed_files], 'more': max(len(files) - listed_files, 0)}
    return shown_folders


def unchanged_text(top, path):
    """The text of a file a review's tree lists, for the page to show unchanged; top as for
    listed_folders(), and read without the lock too."""
    if not isinstance(path, str) or '/' not in path:
        raise Refusal(404, 'No such file.')
    folder, name = path.rsplit('/', 1)
    listed = listed_folders(top, [f'{folder}/']).get(f'{folder}/')
    if not listed or name not in listed['files'] or not os.path.realpath(path).startswith(os.path.realpath(top).rstrip('/') + '/'):
        raise Refusal(404, 'The tree has no such file.')
    data = read_bytes(pathlib.Path(path))
    if data is None:
        raise Refusal(404, 'The file is not on disk.')
    return {'path': path, 'sha': sha(data), 'text': shown(data)}


def is_other(path):
    """Whether the file is Claude's own or a temporary one, not the work's. One under ~/.claude in a
    git work tree of its own, a skill kept in its repository say, is the work's."""
    if path.startswith(other_roots[0]) and repository_top(os.path.dirname(path)):
        return False
    return any(path.startswith(root) for root in other_roots)


def git_info(record):
    """What the git review in the record is of; None for a session's record."""
    info = read_json(record / 'git.json')
    return info if isinstance(info, dict) and all(isinstance(info.get(n), str) for n in ('repo', 'base', 'spec')) else None


def git_review(folder, spec, owner):
    """Makes the git review of the folder's repository against the commit its work tree and spec both
    come from, or refreshes the one there is, carried over to that commit if its branch was rebased,
    and makes owner the session its comments reach. With no spec, it is the branch's upstream, else
    HEAD. Returns the record's name."""
    top = git(folder, 'rev-parse', '--show-toplevel')
    if top is None:
        raise Refusal(404, f'{folder} is in no git work tree.')
    top = pathlib.Path(os.fsdecode(top).rstrip('\n'))
    if spec is None:
        upstream = git(top, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}')
        spec = (upstream or b'').decode().strip() or 'HEAD'
    base = git(top, 'merge-base', spec, 'HEAD')
    if base is None:
        raise Refusal(404, f'{spec} names no commit that HEAD comes from.')
    base = base.decode().strip()
    branch = (git(top, 'rev-parse', '--abbrev-ref', 'HEAD') or b'').decode().strip()
    info = {
        'repo': str(top),
        'base': base,
        'spec': spec,
        'branch': branch,
        'title': titled(top, branch, spec, base),
        'owner': owner,
        'updated': now(),
    }
    name = record_named(info)
    old = git_info(store / name)
    if old and old['base'] != base:
        info = {**carried(store / name, base), 'owner': owner, 'updated': now()}
    elif old and old.get('rebased'):
        info['rebased'] = old['rebased']
    scanned(store / name, info)
    (store / name / 'files').mkdir(parents=True, exist_ok=True)
    write_atomic(store / name / 'git.json', json.dumps(info, indent=1).encode())
    return name


def titled(top, branch, spec, base):
    return f'{top.name}: {branch} against {spec} ({base[:10]})'


def branch_of(info):
    """The branch a git review is of; read out of the title of a record made before it was kept."""
    if isinstance(info.get('branch'), str):
        return info['branch']
    title = info.get('title') if isinstance(info.get('title'), str) else ''
    lead, tail = titled(pathlib.Path(info['repo']), '\0', info['spec'], info['base']).split('\0')
    return title[len(lead):-len(tail)] if title.startswith(lead) and title.endswith(tail) else ''


def is_against_head(info):
    """Whether a git review is against HEAD, or a commit HEAD names (HEAD~1, @^)."""
    return bool(re.fullmatch(r'(HEAD|@)([~^].*)?', info['spec']))


def is_followed(info):
    """Whether a git review follows its base: the review of a branch against another commit than its
    own HEAD. Against HEAD a rebase cannot be told from new work, and with no branch checked out
    (a rebase half done is so) there is none to follow."""
    return not is_against_head(info) and branch_of(info) not in ('', 'HEAD')


def is_rewritten(info):
    """Whether a review against HEAD lost its base: HEAD no longer comes from that commit, as an amend
    or a rebase of its branch rewrote it. A commit made on top keeps the base, and the work it took in
    stays in the review."""
    return (is_against_head(info) and branch_of(info) not in ('', 'HEAD')
            and git(info['repo'], 'merge-base', '--is-ancestor', info['base'], 'HEAD') is None)


def record_named(info):
    """The name of the record of the git review info describes: the one there is of the repository
    and the spec, of the same branch whatever its base when the review follows its base, else of the
    same base, or, against HEAD, of the same branch when an amend or a rebase rewrote it since; the
    one refreshed last of several. With none, a new name, of the base it starts from: a record keeps
    its name, and its page's URL, when its base moves."""
    def is_same(other):
        if (other['repo'], other['spec'], is_followed(other)) != (info['repo'], info['spec'], is_followed(info)):
            return False
        if is_followed(info):
            return branch_of(other) == info['branch']
        return other['base'] == info['base'] or branch_of(other) == info['branch'] and is_rewritten(other)

    found = [(str(other.get('updated') or ''), place.parent.name) for place in store.glob('git-*/git.json')
             for other in [git_info(place.parent)] if other and is_same(other)]
    if found:
        return max(found)[1]
    top = pathlib.Path(info['repo'])
    first = f"git-{re.sub(r'[^A-Za-z0-9_-]', '_', top.name)}-{sha(str(top).encode())[:6]}-{info['base'][:10]}"
    name, number = first, 1
    # Another review's: of another branch or spec that started from the same commit.
    while (store / name).exists():
        number += 1
        name = f'{first}-{number}'
    return name


def rebased_text(orig, accepted, new):
    """The new base's text with what was accepted put in again, orig → accepted, as a three-way merge
    does it, and whether it fit; where the two changed the same lines, or one of the three is no
    text (None: no file), the new base's text as it is. git merges temporary copies: the repository
    is not touched."""
    if accepted in (orig, new):
        return new, True
    if None in (orig, accepted, new):
        return new, False
    with tempfile.TemporaryDirectory(prefix='redpen-') as folder:
        for name, data in (('accepted', accepted), ('orig', orig), ('new', new)):
            pathlib.Path(folder, name).write_bytes(data)
        try:
            ran = subprocess.run(['git', 'merge-file', '-p', 'accepted', 'orig', 'new'], capture_output=True, cwd=folder)
        except OSError:
            return new, False
    return (new, False) if ran.returncode else (ran.stdout, True)


def carried(record, base):
    """Carries a git review's record over to another base commit, as its branch was rebased onto it.
    A file whose text is the same at both commits stays as it is. Of another, the new commit's text is
    the orig, and the baseline is that text with what was accepted put in again; where that does not
    fit, nothing of the file is accepted and the file is marked (uncarried) for the page to say so. A
    file that is the new commit's again, with nothing accepted, leaves the record. The comments and
    the journal stay, and the journal says what was done. Returns what the review is of now, as it
    wrote it to git.json. Under the channel's lock: the command and the server may both come here."""
    with shared('channel').locked(record / 'review/sent.jsonl'):
        info = git_info(record)
        if info['base'] == base:
            return info
        top = pathlib.Path(info['repo'])
        moved = git(top, 'diff', '--name-only', '-z', '--no-renames', '--ignore-submodules', info['base'], base, '--')
        # Without the old commit (pruned since), every file is looked at.
        moved = None if moved is None else {os.fsdecode(name) for name in moved.split(b'\0') if name}
        uncarried = dropped = 0
        for entry in sorted((record / 'files').glob('*.json')):
            file = read_json(entry)
            if not isinstance(file, dict) or not isinstance(file.get('path'), str):
                continue
            k, name = entry.stem, os.path.relpath(file['path'], top)
            if name.startswith('..') or moved is not None and name not in moved:
                continue
            # A submodule's file is compared with a commit of its own: the work tree's moving leaves it.
            if repository_top(os.path.dirname(file['path'])) not in (None, str(top)):
                continue
            places = {'orig': record / 'review/orig' / k, 'none': record / 'review/orig' / f'{k}.none',
                      'base': record / 'base' / k}
            orig, accepted = read_bytes(places['orig']), read_bytes(places['base'])
            new = committed_text(top, base, name) if file.get('link') else checked_out(top, base, name)
            # The record's own orig decides: a file carried before this was interrupted is not again.
            if new == orig:
                continue
            merged, is_carried = rebased_text(orig, accepted, new)
            on_disk = read_link_place(file['path']) if file.get('link') else read_bytes(pathlib.Path(file['path']))
            if merged == new and on_disk == new:
                for place in (entry, *places.values()):
                    place.unlink(missing_ok=True)
                dropped += 1
                continue
            for place, data in ((places['orig'], new), (places['base'], merged)):
                if data is None:
                    place.unlink(missing_ok=True)
                else:
                    write_atomic(place, data)
            if new is None:
                write_atomic(places['none'], b'')
            else:
                places['none'].unlink(missing_ok=True)
            mark = ({} if is_carried else {'uncarried': base}) | ({'link': True} if file.get('link') else {})
            if new is not None and not file.get('link'):
                # Executable as the new commit has it, and as accepted where that was another bit.
                was = file.get('executable') or {}
                is_on = (git(top, 'ls-tree', base, '--', name) or b'').startswith(executable_mode + b' ')
                mark['executable'] = {'orig': is_on, 'base': is_on if was.get('base') == was.get('orig') else was['base']}
            write_atomic(entry, json.dumps({'path': file['path'], 'new': new is None, **mark}).encode())
            uncarried += not is_carried
        moment = now()
        line = {'id': f'{time.time_ns():x}', 'time': moment, 'action': 'rebase', 'key': None, 'path': '',
                'change': None, 'from': info['base'], 'to': base, 'uncarried': uncarried, 'dropped': dropped,
                'isUndone': False}
        journal = read_json(record / 'review/journal.json', [])
        write_atomic(record / 'review/journal.json',
                     json.dumps((journal if isinstance(journal, list) else []) + [line], indent=1).encode())
        branch = branch_of(info)
        info = {**info, 'base': base, 'branch': branch, 'title': titled(top, branch, info['spec'], base),
                'updated': moment, 'rebased': {'from': info['base'], 'to': base, 'time': moment}}
        write_atomic(record / 'git.json', json.dumps(info, indent=1).encode())
        return info


def followed(review):
    """Carries a git review that follows its base, or one against HEAD whose branch was amended or
    rebased, over to the commit its branch and spec both come from now, if that is another one. Says
    whether it was."""
    info = review.git
    if not info or not (is_followed(info) or is_rewritten(info)):
        return False
    branch = (git(info['repo'], 'rev-parse', '--abbrev-ref', 'HEAD') or b'').decode().strip()
    base = (git(info['repo'], 'merge-base', info['spec'], 'HEAD') or b'').decode().strip()
    # Another branch is checked out, or none: its base is not this review's.
    if branch != branch_of(info) or not base or base == info['base']:
        return False
    review.git = carried(review.record, base)
    return True


def scanned(record, info):
    """Adds to a git review's record each file that differs from the base commit now, tracked or not
    (ignored ones apart), with that commit's text as its orig and its baseline; and of each submodule
    that differs, each of its files that differs from the commit the base keeps for it, with that
    commit's text. A file the record has stays as it is, so what was accepted stays accepted. Returns
    how many it added."""
    top = pathlib.Path(info['repo'])
    # The work tree and, under it, each submodule that differs, with the commit to compare it with:
    # the one the base keeps for it.
    found, trees = [], [(top, info['base'])]
    while trees:
        tree, base = trees.pop(0)
        listed = changed_files(tree, base)
        if listed is None:
            if tree == top:
                raise Refusal(404, f'git could not compare {top} with {info["base"][:10]}.')
            continue
        files, submodules = listed
        found += [(tree, base, name, modes) for name, modes in sorted(files.items())]
        # A submodule checked out, whose base commit it has: one added or taken away is not followed.
        trees += [(tree / path, commit) for path, commit in submodules
                  if (tree / path / '.git').exists() and git(tree / path, 'cat-file', '-e', f'{commit}^{{commit}}') is not None]
        if len(found) > scanned_files:
            raise Refusal(413, f'{len(found)} files differ from {info["spec"]}: not a change to review file by file.')
    added = 0
    for tree, base, name, modes in found:
        place = tree / name
        k = key(str(place))
        entry = record / 'files' / f'{k}.json'
        is_link = link_mode in modes or place.is_symlink()
        if entry.exists() or place.is_dir() and not place.is_symlink():
            continue
        old_mode = modes[0] if modes else b''
        old = checked_out(tree, base, name, old_mode == link_mode)
        if old is not None and old_mode == link_mode:
            old = link_mark + old
        if old is None and not os.path.lexists(place):
            continue
        if old is None:
            write_atomic(record / 'review/orig' / f'{k}.none', b'')
        else:
            write_atomic(record / 'review/orig' / k, old)
            write_atomic(record / 'base' / k, old)
        kept = {'path': str(place), 'new': old is None, **({'link': True} if is_link else {})}
        if old is not None and not is_link:
            # As it was at the base commit, whether git kept it executable.
            kept['executable'] = {'orig': old_mode == executable_mode, 'base': old_mode == executable_mode}
        write_atomic(entry, json.dumps(kept).encode())
        added += 1
    return added


def changed_files(top, base):
    """What differs under the work tree at top from the base commit: the files, tracked or not
    (ignored ones apart), by their paths in it, each with its modes then and now, which say a link
    (120000), () for an untracked one; and the submodules that differ, (path, the commit the base
    keeps for it). None where git cannot compare."""
    changed = git(top, 'diff', '--raw', '-z', '--no-renames', '--no-abbrev', base, '--')
    untracked = git(top, 'ls-files', '--others', '--exclude-standard', '-z')
    if changed is None or untracked is None:
        return None
    parts = changed.split(b'\0')
    files, submodules = {}, []
    for header, name in zip(parts[0::2], parts[1::2]):
        if not header.startswith(b':'):
            continue
        old_mode, new_mode, old_commit = header[1:].split(b' ')[:3]
        if submodule_mode in (old_mode, new_mode):
            if old_mode == new_mode:
                submodules.append((os.fsdecode(name), old_commit.decode()))
            continue
        files[os.fsdecode(name)] = (old_mode, new_mode)
    for name in untracked.split(b'\0'):
        if name:
            files.setdefault(os.fsdecode(name), ())
    return files, submodules


@functools.lru_cache(maxsize=512)
def committed_hash(top, commit, name):
    """The hash of a file's text at the commit, as committed_text() reads it."""
    return sha(committed_text(top, commit, name))


def committed_text(top, commit, name):
    """A file's text at the commit, a link's as a link's text; None where there is none."""
    listed = git(top, 'ls-tree', '-z', commit, '--', name)
    is_link = bool(listed) and listed.startswith(link_mode + b' ')
    data = checked_out(top, commit, name, is_link) if listed else None
    return None if data is None else link_mark + data if is_link else data


def checked_out(top, commit, name, is_link=False):
    """A file's bytes at the commit as checking it out writes them, the repository's line endings and
    filters applied (Git LFS's say), else, where a filter fails, as git keeps them; a link's target as
    git keeps it. None where there is none."""
    spec = f'{commit}:{name}'
    data = None if is_link else git(top, 'cat-file', '--filters', spec)
    return data if data is not None else git(top, 'cat-file', 'blob', spec)


def rescan(review, lock):
    """Finds a git review's files anew behind the page, one search at a time, after carrying the
    record over to its new base if the branch was rebased, under the records' lock; a failure
    leaves those it has."""
    def run():
        try:
            with lock:
                followed(review)
            scanned(review.record, review.git)
        except (Refusal, OSError):
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


def page_code():
    """What the review page's own code is now, by its files' signatures."""
    return ' '.join(signature(here / 'page' / name) for name in ('page.html', 'page.js', 'editor.js', 'page.css'))


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
        # A read, but of a list of folders too long for a query; without the lock, as top_of() says.
        found = re.fullmatch(r'/api/([\w-]+)/folders', path)
        if found:
            return send_json(h, listed_folders(top_of(h, found.group(1)), body.get('folders')))
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
            rescan(review, kept_lock(h))
        # The page says what code it is made of: it knows its code changed even when it did not start.
        page = (here / 'page' / 'page.html').read_bytes().replace(
            b'<meta name="redpen-code" content="">', f'<meta name="redpen-code" content="{page_code()}">'.encode())
        return send(h, 200, page, types['.html'])
    found = re.fullmatch(r'/api/([\w-]+)/plain', path)
    if found:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query)
        return send_json(h, unchanged_text(top_of(h, found.group(1)), (query.get('path') or [None])[0]))
    found = re.fullmatch(r'/api/([\w-]+)/(files|journal|outline|file/([0-9a-f]{16}))', path)
    if not found:
        raise Refusal(404, 'Not found.')
    with kept_lock(h):
        # Read under the lock: a git review may have been carried over to another base meanwhile.
        review = Review(found.group(1))
        if found.group(2) == 'files':
            # What a commit took of the session's changes is accepted before the list is read.
            review.checkpoint()
            review.take_others_changes()
            return send_json(h, {**review.listing(), 'committed': review.last_commit()})
        if found.group(2) == 'journal':
            return send_json(h, review.journal_shown())
        if found.group(2) == 'outline':
            return send_json(h, review.outline_shown())
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query)
        return send_json(h, review.opened(found.group(3), query.get('texts') == ['none']))


def top_of(h, session):
    """Where the review's tree starts, read under the lock; what is under it is read without, so a
    large repository's slow listing holds up no other request, of this review or another."""
    with kept_lock(h):
        return Review(session).top()


def post(h, path, body):
    found = re.fullmatch(r'/api/([\w-]+)/(apply-files|apply-item|accept-all|apply|flip|comment|uncomment|send|resolve|describe)', path)
    if not found:
        raise Refusal(404, 'Not found.')
    review = Review(found.group(1))
    if found.group(2) == 'apply':
        return send_json(h, review.apply(body.get('key'), body.get('action'), body.get('change'), body.get('expected')))
    if found.group(2) == 'apply-files':
        return send_json(h, review.apply_files(body.get('keys'), body.get('action'), body.get('signatures'), body.get('folder'),
                                               body.get('generated') is True))
    if found.group(2) == 'accept-all':
        return send_json(h, review.accept_all(body.get('signatures')))
    if found.group(2) == 'apply-item':
        return send_json(h, review.apply_item(body.get('index'), body.get('time'), body.get('signatures')))
    if found.group(2) == 'describe':
        return send_json(h, review.describe())
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
    parser.add_argument('--outline', nargs='?', const='', metavar='REVIEW')
    parser.add_argument('--generated', nargs='+', metavar='PATH')
    parser.add_argument('--accept-all', action='store_true', help="accept what is pending of every file of the session's review")
    # Not --describe, the flag of the mod that asked by a prompt in the chat: that mod, loaded until a
    # session reloads its plugins, now fails to ask, as it should, not posting this question there.
    parser.add_argument('--ask-overview', action='store_true',
                        help="print the question for an overview, logged as handed over, if the session's review needs one")
    parser.add_argument('--drop-ask', action='store_true',
                        help='the question for an overview brought none: the ask waits no more')
    parser.add_argument('--change', metavar='PATH[:LINE]')
    parser.add_argument('--git', nargs='?', const='.', metavar='REPO')
    parser.add_argument('--base', metavar='REV', help="default: the branch's upstream, else HEAD")
    args = parser.parse_args()
    # The hook reads the record alone, and no change to the server's code can fail it.
    if args.prompt_hook:
        return prompt_hook()
    if args.wait:
        return wait(args.session)
    if args.answer:
        return answer(args.session, args.answer, args.change)
    if args.outline is not None:
        return outline(args.outline or args.session)
    if args.generated:
        return print('\n'.join(sorted(generated_paths([os.path.abspath(path) for path in args.generated]))))
    if args.ask_overview:
        return describe_if_needed(args.session)
    if args.accept_all:
        entry = accepted_all(args.session, args.port)
        return print(f"Accepted {entry['files']} file{'s' if entry['files'] != 1 else ''}.")
    if args.drop_ask:
        return Review(args.session).drop_ask()
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
