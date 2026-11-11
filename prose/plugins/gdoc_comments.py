"""Google Doc comments: --comments FILE holds the open comments made on the old side, {file: [{quote,
author, date, time, text, replies: [{author, time, text}], fate: "goes", "edited" or null, url}]} as
gdocsync's export writes them. They go to review.py as comments made elsewhere (remarks()), as the PR's
on GitHub do: each thread a mark where its quote reads in the old text, its card the comment, its replies
and what a push does to its text, with a link to it in the Google Doc; one whose quote no longer reads
there (edited after the comment was made) is listed in the Review panel, On Google Docs. The file's bar
says how many are on text a push changes. With --refresh CMD, ↻ Google Docs runs CMD, an export, in the
background: the Google Docs' text and comments are read again, and the page reloads into them.
"""
import json, pathlib, subprocess, threading

NOTE = {'goes': 'a push deletes its text', 'edited': 'a push edits its text'}
SOURCE = 'Google Docs'
# The --refresh command running, or that ran last: {'proc', 'out'} while it runs, then its failure, if any.
job, lock = {}, threading.Lock()


def add_args(p):
    p.add_argument('--comments', type=lambda f: str(pathlib.Path(f).resolve()),
                   help="comments made on the old side, as gdocsync's export writes them")
    p.add_argument('--refresh', help="a command that exports the Google Docs again (gdocsync's export), which "
                                     "↻ Google Docs runs")


def title(run):
    """A Google Doc against its Markdown, as gdocsync's export (<repo>-google) holds them: base google. The
    comments shown on another page (the repo's own edits, say) leave its title alone."""
    if not run.get('comments') or run.get('base') != 'google':
        return None
    return f'Google Docs vs Markdown · {pathlib.Path(run["repo"]).name.removesuffix("-google")}', None


def section(run, name):
    """For the file's bar, how many of its comments are on text a push changes; review.py's link beside
    it counts them all and goes to each."""
    touched = sum(1 for c in notes(run).get(name, []) if c.get('fate'))
    return (f'{touched} comment(s) on text a push changes', '') if touched else None


def remarks(run, out, refresh=None):
    """For review.py: the Google Docs' open comments as comments made elsewhere, read when the export was
    made (fetched); a comment's replies follow it, reply_to its id. refresh 'now' starts --refresh in the
    background (busy while it runs; error if it failed)."""
    f = pathlib.Path(run['comments']) if run.get('comments') else None
    if not f or not f.exists():
        return None
    with lock:
        if refresh == 'now' and run.get('refresh') and not busy():
            log = pathlib.Path(out) / 'refresh.log'
            job.clear()
            job.update(proc=subprocess.Popen(run['refresh'], shell=True, stdout=open(log, 'w'), stderr=subprocess.STDOUT),
                       log=log)
        running = busy()
        failed = job.get('proc') and not running and job['proc'].returncode
        error = (job['log'].read_text().strip().splitlines() or ['failed'])[-1][:200] if failed else None
    cs = []
    for name, comments in notes(run).items():
        for i, c in enumerate(comments):
            cid = (c.get('url') or '').partition('disco=')[2] or f'{name}#{i}'
            time = c.get('time') or c.get('date')
            cs.append({'id': cid, 'kind': 'line', 'author': c['author'], 'time': time, 'body': c['text'],
                       'url': c.get('url'), 'file': name, 'quote': c['quote'], 'before': '', 'side': 'L',
                       'note': NOTE.get(c.get('fate'))})
            cs += [{'id': f'{cid}-{j}', 'kind': 'line', 'reply_to': cid, 'author': r['author'],
                    'time': r.get('time') or time, 'body': r['text'], 'url': c.get('url')}
                   for j, r in enumerate(c.get('replies', []))]
    return {'source': SOURCE, 'open': 'Open in Google Docs ↗', 'fetched': f.stat().st_mtime, 'checked': None,
            'error': error, 'me': None, 'refresh': bool(run.get('refresh')), 'busy': running, 'posting': False,
            'comments': cs}


def store(run):
    """Where replies drafted for these comments are kept, shared by every page that shows them: beside the
    export (<export>-replies), not in it, so writing there does not count as the export moving on."""
    if not run.get('comments'):
        return None
    d = pathlib.Path(run['comments']).parent
    d = d.with_name(d.name + '-replies')
    d.mkdir(exist_ok=True)
    return d


def busy():
    return bool(job.get('proc')) and job['proc'].poll() is None


def notes(run):
    f = pathlib.Path(run['comments']) if run.get('comments') else None
    return json.loads(f.read_text()) if f and f.exists() else {}
