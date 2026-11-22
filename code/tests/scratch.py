"""What the tests share: a home of their own, so the records and the server's state are scratch ones
(set before review.py and recorder.py are loaded, which read it once); the two scripts, loaded by
their paths; git repositories made for a test; and the recorder's hooks, run as Claude Code runs them.

Run them all: python3 -m unittest discover -s ~/.claude/skills/redpen/code/tests
"""
import atexit, importlib.util, json, os, pathlib, shutil, subprocess, sys, tempfile, uuid

home = pathlib.Path(os.path.realpath(tempfile.mkdtemp(prefix='redpen-tests-')))
atexit.register(shutil.rmtree, home, ignore_errors=True)
os.environ['HOME'] = str(home)
code = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(f'redpen_{name}', code / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = load('review')
recorder = load('recorder')
# The repositories are made under the scratch home: the work's files, not Claude's own or temporary
# ones, as they would be under the system's temporary folder.
review.other_roots = (f'{home}/.claude/',)
# A file Reject takes away goes, not to the user's Trash.
review.trash_command = ['/bin/rm', '-f']


def repo(files):
    """A git repository with the files ({name: text}) committed; its top."""
    top = pathlib.Path(tempfile.mkdtemp(dir=home, prefix='repo-'))
    git(top, 'init', '-q')
    git(top, 'config', 'user.email', 'test@example.com')
    git(top, 'config', 'user.name', 'test')
    write(top, files)
    git(top, 'add', '-A')
    git(top, 'commit', '-qm', 'base')
    return top


def write(top, files):
    """Writes each file ({name: text}); a text of None deletes the file."""
    for name, text in files.items():
        place = top / name
        if text is None:
            place.unlink()
            continue
        place.parent.mkdir(parents=True, exist_ok=True)
        place.write_text(text)


def git(top, *args):
    return subprocess.run(['git', '-C', str(top), *args], check=True, capture_output=True, text=True).stdout


def reviewed(top):
    """The git review of the repository's work tree against HEAD, made or found afresh."""
    return review.Review(review.git_review(str(top), 'HEAD', ''))


def listed(review_of):
    """The review's files as the page lists them, by their paths from the top."""
    top = review_of.top()
    return {file['path'][len(top):]: file for file in review_of.listing()['files']}


def expected(review_of, key, action):
    """The hashes of the two texts an action reads, as the page sends them."""
    left, right = review.actions[action][:2]
    texts = review_of.opened(key)['texts']
    return {'left': texts[left]['sha'], 'right': texts[right]['sha']}


def session():
    """A new session's id."""
    return f'test-{uuid.uuid4().hex[:12]}'


def hook(phase, session_id, cwd, tool, tool_input, tool_id='call'):
    """Runs the recorder's pre or post hook as Claude Code does, with the call's JSON on stdin."""
    data = {'session_id': session_id, 'tool_name': tool, 'cwd': str(cwd), 'tool_use_id': tool_id,
            'tool_input': tool_input, 'transcript_path': ''}
    ran = subprocess.run([sys.executable, str(code / 'recorder.py'), phase], input=json.dumps(data), text=True,
                         capture_output=True, env={**os.environ, 'HOME': str(home)})
    if ran.stderr:
        raise AssertionError(f'the recorder said: {ran.stderr}')


def shell(session_id, top, command, tool_id='call', between=None):
    """Runs a shell command between the recorder's hooks, as Claude's Bash tool does; between, if
    given, runs after the command and before the post hook."""
    hook('pre', session_id, top, 'Bash', {'command': command}, tool_id)
    subprocess.run(command, shell=True, cwd=top, check=True, executable='/bin/sh', capture_output=True)
    if between:
        between()
    hook('post', session_id, top, 'Bash', {'command': command}, tool_id)


def recorded(session_id):
    """The session's record: {file name: (whether new, baseline text or None, whether marked as taking
    in changes not the session's)}."""
    folder = home / '.redpen/code' / session_id
    found = {}
    for entry in (folder / 'files').glob('*.json'):
        file = json.loads(entry.read_text())
        base = folder / 'base' / entry.stem
        found[pathlib.Path(file['path']).name] = (file['new'], base.read_text() if base.exists() else None,
                                                  (folder / 'outside' / entry.stem).exists())
    return found
