"""ReSpec specs: a changed .html file under an index.html that holds a respecConfig stands for that
spec, which shows as one doc. The page renders it: each side loads in a hidden frame, and SCRIPT posts
the renders to the server, which keeps them in <out>/rendered/<sources id>.html, so an unchanged side
never renders twice. The frame gets the spec's sources at a commit, /src/<commit>/<path>, or in the working
tree, /src/new/<path> (/src/old/ is the base); with ?render=1 the entry file also gets RENDER, which posts
the rendered spec up. A commit other than the base and the head is a version a review round names.
"""
# docdiff, the running docdiff.py, is set by its load_plugins().
import hashlib, html, mimetypes, os, pathlib, re

PATHS = ['*.html']

STYLE = '''
.render{position:fixed;right:0;bottom:0;width:800px;height:600px;border:0;opacity:0;pointer-events:none}
.note,.issue,.example,.ednote,.warning{border-left:4px solid var(--line);padding:2px 12px;margin:1em 0}
.issue,.warning{border-color:var(--rw)} .marker{font-weight:600;color:var(--muted)}
'''

SCRIPT = '''
// Specs not rendered yet: each side loads in a frame on localhost, another origin than this page's
// 127.0.0.1, so the spec's own scripts cannot reach this page or post to the server. ReSpec renders it
// there and RENDER posts the result up, which comes into this page only cleaned of anything that runs.
// The server keeps the renders and rebuilds, and the page reloads into the diff.
const frames=[...document.querySelectorAll('iframe.render')];
if(frames.length)Promise.all(frames.map(f=>new Promise(ok=>{const state=f.closest('section').querySelector('.state');
const fail=m=>{state.textContent='· not rendered: '+m;ok(null);},late=setTimeout(()=>fail('timed out'),120000);
addEventListener('message',e=>{if(e.source!==f.contentWindow)return;clearTimeout(late);
typeof e.data?.html==='string'?ok({id:f.dataset.id,html:inert(e.data.html)}):fail(e.data?.error||'no result');});
f.src='http://localhost:'+location.port+location.pathname.replace(/[^/]*$/,'')+f.dataset.src;}))).then(rs=>{rs=rs.filter(Boolean);
if(rs.length)post('rendered',{renders:rs}).catch(e=>{pos.textContent='renders not saved: '+e.message;});});
function inert(h){const d=new DOMParser().parseFromString(h,'text/html');
d.querySelectorAll('script,iframe,frame,object,embed,base,meta,link,style,form').forEach(e=>e.remove());
for(const e of d.body.querySelectorAll('*'))for(const a of [...e.attributes])
if(/^on/i.test(a.name)||/^\\s*javascript:/i.test(a.value))e.removeAttribute(a.name);
return d.body.innerHTML;}
'''

# Runs in a spec's render frame, after the spec: once ReSpec is done, posts the page the rendered spec
# cut down to what is worth diffing. Navigation, the index and section numbers go (numbers would mark
# every heading after an added section); sections and heading wrappers are unwrapped, so headings,
# paragraphs and list items are the blocks the diff pairs. A framed ReSpec reports progress to its parent
# through parent.location, which a frame of another origin cannot read, and stops; so the frame
# keeps its parent for the result and tells ReSpec it has none.
RENDER = '''(async()=>{const up=parent,t=Date.now(),say=m=>up.postMessage(m,'*');window.parent=window;
while(!document.respec){if(Date.now()-t>60000)return say({error:'ReSpec did not load'});await new Promise(r=>setTimeout(r,200));}
try{await document.respec.ready;}catch(e){return say({error:'ReSpec failed: '+e});}
const b=document.body.cloneNode(true);
b.querySelectorAll('script,style,link,meta,iframe,#toc,#toc-nav,#index,#respec-ui,.dfn-panel,.self-link,.secno,.respec-button-copy-paste').forEach(e=>e.remove());
b.querySelectorAll('section,.header-wrapper').forEach(e=>e.replaceWith(...e.childNodes));
b.querySelectorAll('[id^="ref-for-"]').forEach(e=>e.removeAttribute('id'));
say({html:b.innerHTML});})();'''


def doc(repo, revs, path, memo):
    return spec_index(repo, revs, path, memo.setdefault('respec', {})) if path.endswith('.html') else None


def spec_index(repo, revs, path, memo):
    """The ReSpec spec a source file belongs to: the nearest index.html at or above it that holds a
    respecConfig on either side; None if there is none."""
    d = os.path.dirname(path)
    if d not in memo:
        index = os.path.join(d, 'index.html')
        if any(b'respecConfig' in (docdiff.read(repo, rev, index) or b'') for rev in revs):
            memo[d] = index
        else:
            memo[d] = spec_index(repo, revs, d, memo) if d else None
    return memo[d]


def spec_id(repo, rev, index):
    """Names a spec's sources as they are at rev (None: the working tree): the hash of its directory."""
    d = os.path.dirname(index)
    if rev is not None:
        return docdiff.git(repo, 'rev-parse', f'{rev}:{d}').stdout.decode().strip()
    h = hashlib.sha1()
    for f in sorted(docdiff.git(repo, 'ls-files', '-co', '--exclude-standard', '--', d or '.').stdout.decode().splitlines()):
        h.update(f.encode() + b'\0' + (docdiff.read(repo, None, f) or b'') + b'\0')
    return h.hexdigest()


def htmls(repo, base, head, old, new, out):
    """A spec at base and at head, as rendered; until both sides are, the frames that render them."""
    if not (new or old).endswith('.html'):
        return None
    sides = [(s, rev, p, spec_id(repo, rev, p)) for s, rev, p in (('old', base, old), ('new', head, new)) if p]
    got = {s: out / 'rendered' / f'{i}.html' for s, _, _, i in sides}
    pending = [x for x in sides if not got[x[0]].exists()]
    text = lambda s: got[s].read_text() if s in got and got[s].exists() else ''
    frames = ''.join(f'<iframe class="render" sandbox="allow-scripts allow-same-origin" data-id="{i}" '
                     f'data-src="src/{docdiff.commit(repo, rev) if rev else "new"}/{html.escape(p)}?render=1"></iframe>'
                     for s, rev, p, i in pending)
    return text('old'), text('new'), ('rendering with ReSpec…', frames) if pending else None


def get(run, path, query):
    """A spec's sources at a commit of its repo, or on either side, for its render frame."""
    m = re.fullmatch(r'/src/(old|new|[0-9a-f]{40})/(.+)', path)
    if not m:
        return None
    path = os.path.normpath(m.group(2))
    rev = run['base'] if m.group(1) == 'old' else run.get('head') if m.group(1) == 'new' else m.group(1)
    data = None if os.path.isabs(path) or path.startswith('..') else docdiff.read(pathlib.Path(run['repo']), rev, path)
    if data is not None and query == 'render=1':
        data += f'<script>{RENDER}</script>'.encode()
    return data, mimetypes.guess_type(path)[0] or 'application/octet-stream'


def post(out, path, req):
    """Keeps the renders the page posts."""
    if path != '/rendered':
        return None
    (out / 'rendered').mkdir(exist_ok=True)
    for r in req['renders']:
        if re.fullmatch(r'[0-9a-f]{40,64}', r['id']):
            (out / 'rendered' / f'{r["id"]}.html').write_text(r['html'])
    return {}
