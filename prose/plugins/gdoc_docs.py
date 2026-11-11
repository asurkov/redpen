"""The repo's docs and their Google Docs: --docs FILE, as gdocsync's export writes it, [{file, title, url,
local, unpublished, edited, stand, comments, local_diff}] in the repo's order, lists every document above
the diff: its title, a link to its Google Doc, where it stands (its Markdown against git and its Google
Doc against the Markdown, in gdocsync's words) with links to its uncommitted edits (local_diff, another
docdiff page) and to what a push would change (the doc below), and its open comments. --sessions FILE,
as gdocsync's browse writes it, [{name, path, branch, docs, url}], lists the edit sessions in worktrees of
their own above that: each one's branch, edited docs and page of its edits (url).
"""
import html, json, pathlib

STYLE = '''
.docs{margin:16px 0 8px;font-size:14px} .docs table{border-collapse:collapse;width:100%}
.docs table+table{margin-top:12px}
.docs th,.docs td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.docs th{color:var(--muted);font-weight:400;font-size:12px} .docs td:first-child{font-weight:600}
.docs .ok{color:var(--muted)} .docs td:first-child .dl{color:var(--fg)} .docs .links{font-size:13px;margin-top:2px}
'''

SCRIPT = '''
// Each doc's name scrolls to its section, just below the sticky header; a doc with no section (no Google
// Doc) is plain text.
for(const a of document.querySelectorAll('.docs .dl')){const s=[...document.querySelectorAll('section')].find(s=>s.dataset.file===a.dataset.file);
if(!s){a.replaceWith(a.textContent);continue;}
a.onclick=e=>{e.preventDefault();scrollTo({top:s.getBoundingClientRect().top+scrollY-document.querySelector('header').offsetHeight-8,behavior:'smooth'});};}
'''


def add_args(p):
    p.add_argument('--docs', type=lambda f: str(pathlib.Path(f).resolve()),
                   help="the repo's docs and where each stands against its Google Doc, as gdocsync's export "
                        "writes them")
    p.add_argument('--sessions', type=lambda f: str(pathlib.Path(f).resolve()),
                   help="edit sessions in worktrees of their own, each with a page of its edits, as gdocsync's "
                        "browse writes them")


def top(run):
    """The edit sessions, if any, then the docs, as tables of class ui, so a click in them is not a click on
    the text."""
    f, g = (pathlib.Path(run[k]) if run.get(k) else None for k in ('docs', 'sessions'))
    if not f or not f.exists():
        return None
    found = json.loads(g.read_text()) if g and g.exists() else []
    sessions = ('<table><tr><th>Edit session</th><th>Branch</th><th>Edited docs</th><th></th></tr>'
                f'{"".join(session(x) for x in found)}</table>' if found else '')
    rows = ''.join(row(d) for d in json.loads(f.read_text()))
    return (f'<div class="docs ui">{sessions}<table><tr><th>Document</th><th>Google Doc</th><th>Where they stand</th>'
            f'<th>Open comments</th></tr>{rows}</table></div>')


def session(x):
    """An edit session in a worktree of its own: its name, branch, edited docs and the page of its edits, to
    review and commit them onto the main checkout."""
    e = html.escape
    link = f'<a href="{e(x["url"])}" target="_blank" rel="noopener">review ↗</a>' if x.get('url') else ''
    return (f'<tr><td>{e(x["name"])}</td><td class="ok">{e(x["branch"] or "detached")}</td>'
            f'<td>{e(", ".join(x["docs"]))}</td><td>{link}</td></tr>')


def row(d):
    e = html.escape
    name = f'<a class="dl" href="#" data-file="{e(d["file"])}" title="{e(d["file"])}">{e(d.get("title") or d["file"])}</a>'
    link = f'<a href="{e(d["url"])}" target="_blank" rel="noopener">Google Doc ↗</a>' if d['url'] else ''
    diffs = ([f'<a href="{e(d["local_diff"])}" target="_blank" rel="noopener">uncommitted edits ↗</a>'] if d.get('local_diff') else []) + \
            ([f'<a class="dl" href="#" data-file="{e(d["file"])}">what a push would change ↓</a>'] if d.get('unpublished') else [])
    fine = d.get('local') == 'committed' and d.get('unpublished') == 0 and not d.get('edited')
    return (f'<tr><td>{name}</td><td>{link}</td><td class="{"ok" if fine else ""}">{e(d.get("stand", ""))}'
            f'{"<div class=links>" + " · ".join(diffs) + "</div>" if diffs else ""}</td><td>{d["comments"] or ""}</td></tr>')
