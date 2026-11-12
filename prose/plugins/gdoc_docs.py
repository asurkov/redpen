"""The repo's docs and their Google Docs: --docs FILE, as gdocsync's export writes it, [{file, title, url,
local, unpublished, edited, stand, comments}] in the repo's order, lists every document above the diff:
its title, a link to its Google Doc and its open comments. In a project (--project) each of the
project's links has a column, as the versions flow: what the doc has left on that link (to review, to
commit, to publish), the page's own link's a jump to the doc below and another link's a link to that
doc there. In no project, one column says where the doc stands (its Markdown against git and its Google
Doc against the Markdown, in gdocsync's words), with a jump to what a push would change.
"""
import html, json, pathlib, urllib.parse

STYLE = '''
.docs{margin:16px 0 8px;font-size:14px;overflow-wrap:normal} .docs table{border-collapse:collapse;width:100%}
.docs th,.docs td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.docs th{color:var(--muted);font-weight:400;font-size:12px} .docs td:first-child{font-weight:600}
.docs .ok{color:var(--muted)} .docs td:first-child .dl{color:var(--fg)} .docs .links{font-size:13px;margin-top:2px}
.docs th.on{color:var(--fg);font-weight:600} .docs .since{color:var(--muted);font-size:12px} .docs a[target]{white-space:nowrap}
'''

SCRIPT = '''
// Each doc's name, and its counts on the page's own link, scroll to its section, just below the sticky
// header; a doc with no section (nothing changed on this link) is plain text.
for(const a of document.querySelectorAll('.docs .dl')){const s=[...document.querySelectorAll('section')].find(s=>s.dataset.file===a.dataset.file);
if(!s){a.replaceWith(a.textContent);continue;}
a.onclick=e=>{e.preventDefault();scrollTo({top:s.getBoundingClientRect().top+scrollY-document.querySelector('header').offsetHeight-8,behavior:'smooth'});};}
'''


def add_args(p):
    p.add_argument('--docs', type=lambda f: str(pathlib.Path(f).resolve()),
                   help="the repo's docs and where each stands against its Google Doc, as gdocsync's export "
                        "writes them")


def top(run):
    """The docs, as a table of class ui, so a click in it is not a click on the text."""
    f = pathlib.Path(run['docs']) if run.get('docs') else None
    if not f or not f.exists():
        return None
    e, links = html.escape, docdiff.linked(run)
    heads = ''.join(f'<th class="{"on" if x["current"] else ""}">{e(x["from"])} → {e(x["to"])}</th>' for x in links)
    rows = ''.join(row(d, links) for d in json.loads(f.read_text()))
    return (f'<div class="docs ui"><table><tr><th>Document</th>{heads or "<th>Where they stand</th>"}<th>Google Doc</th>'
            f'<th>Open comments</th></tr>{rows}</table></div>')


def row(d, links):
    e = html.escape
    jump = lambda words: f'<a class="dl" href="#" data-file="{e(d["file"])}" title="{e(d["file"])}">{e(words)}</a>'
    link = f'<a href="{e(d["url"])}" target="_blank" rel="noopener">Google Doc ↗</a>' if d['url'] else '<span class="ok">none</span>'
    # What a push does not know of: the Google Doc edited since the last one, or never pushed from here.
    note = (f'{d["edited"]} paragraph{"s" if d["edited"] != 1 else ""} edited there since the last push' if d.get('edited')
            else 'never pushed from here' if d['url'] and 'edited' in d and d['edited'] is None else '')
    cells = ''.join(f'<td>{left(d, x, jump)}</td>' for x in links) or stand(d, jump)
    return (f'<tr><td>{jump(d.get("title") or d["file"])}</td>{cells}<td>{link}'
            f'{f"<div class=since>{e(note)}</div>" if note else ""}</td><td>{d["comments"] or ""}</td></tr>')


def left(d, x, jump):
    """What the doc has left on a link of its project: nothing said where the link has no such doc (none
    changed on it), 'in step' where it has and nothing is left."""
    if d['file'] not in x['files']:
        return ''
    words = ', '.join(docdiff.left(x, d['file']))
    if not any(x['files'][d['file']]):
        return f'<span class="ok">{html.escape(words)}</span>'
    return jump(words) if x['current'] else (f'<a class="lnk" href="../{html.escape(x["part"])}/#file='
                                             f'{urllib.parse.quote(d["file"], safe="")}">{html.escape(words)}</a>')


def stand(d, jump):
    """In no project: where the doc stands, in gdocsync's words, and a jump to what a push would change."""
    fine = d.get('local') == 'committed' and d.get('unpublished') == 0 and not d.get('edited')
    more = f'<div class=links>{jump("what a push would change ↓")}</div>' if d.get('unpublished') else ''
    return f'<td class="{"ok" if fine else ""}">{html.escape(d.get("stand", ""))}{more}</td>'
