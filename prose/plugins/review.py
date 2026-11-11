"""Review comments, and revisits with Claude's answers, on the page.

A comment is made on a change or on selected text (💬 in its popup, or c) and kept in <out>/review.json,
{comments: [{id, time, file, view, key, quote, side, before, after, section, body, github}]}: key the
change's (None on a selection), quote its text as it reads on its side, side L for the base's text (in
Old, or a change that only removes) and R otherwise, before and after the text of its block around it.
github is where a plugin's place() (github_pr.py's, with --pr) finds that text in the PR's files,
{pr, head, path, line, side, href}, found again when the run's PR or head is another. Pushed to the PR
as your pending review (a plugin's push(), github_pr.py's), a comment keeps what it became there,
pushed {review, id, body, time}, and review.json the review, {push: {review, body, url, time}}.

The page marks each comment on its change in every view that has the change, and elsewhere where its
quote reads, the text before it picking among repeats: so a comment stays on a change once it is
accepted. All there is at one place is one 💬 mark, which opens it, or a card of all of it; its look says
who sees the most public thing in it, everyone on GitHub, nobody yet (a draft) or you and Claude (a ✎
question). Revisits are marked the same way, from revisit.jsonl, each with the answers Claude appends to
answers.jsonl, {time: the revisit's time, answer: Markdown}. Revisits of one change or one quote are one
thread under one mark: pending while the last has no answer, new while an answer is unread, and resolved,
folded to a dot, while all of them are; asking again reopens it. On commits, accepting a change resolves the answered threads on it alone, and taking it
back reopens those. review.json keeps that by revisit time, {revisits: {time: {resolved, resolved_by,
seen}}}, resolved_by 'accept' for a thread its change's accept resolved, seen the number of its answers
read. A thread from elsewhere (on GitHub, where your login is known) has its comments by others newer than
your last look unread, until its card or panel entry is opened; review.json keeps the newest comment's time
read, by the thread's source and first comment, {threads: {"GitHub:id": {seen}}}.

An answer's blockquotes are its drafted comments, each a proposal: used (made into a comment), dismissed,
or replaced (a revisit that revises it, its fields thread and revises, got a new draft). review.json keeps
the first two by the time of the revisit that first proposed the draft and the draft's hash,
{proposals: {"time#hash": {state, comment}}}; deleting the comment a proposal made frees it again. The two
.jsonl files are logs, only ever appended to. The page polls GET /review for all of it, and posts /review
to add (using proposals), edit or delete a comment, to resolve, reopen or read revisits or threads from
elsewhere, or to set the state of proposals, shown in place.
"""
# docdiff, the running docdiff.py, is set by its load_plugins().
import collections, hashlib, html, json, pathlib, re, secrets, subprocess, threading, time

# Posts the page shows in place, with no rebuild.
IN_PLACE = ['/review']
# What the page sends of a new comment; the server adds its id, time and place in the PR.
FIELDS = ('file', 'view', 'key', 'quote', 'side', 'before', 'after', 'section', 'body')
lock = threading.Lock()
# Answers rendered, by their Markdown.
rendered = {}

STYLE = '''
.rvb{display:inline-block;margin-left:3px;padding:0 5px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--muted);font:400 11px/15px -apple-system,BlinkMacSystemFont,sans-serif;vertical-align:2px;cursor:pointer;user-select:none;-webkit-user-select:none}
.rvb::before{content:'💬'} .rvb-r::before{content:'✎'}
.rvb-g{background:color-mix(in srgb,var(--muted) 32%,var(--bg));border-color:var(--muted);color:var(--fg)} .rvb-c{border-color:var(--rw);color:var(--rw)}
.rvaud{font-weight:600} .rvaud-g{color:var(--fg)} .rvaud-c{color:var(--rw)} .rvaud-r{color:var(--muted)} .rvaud-new{color:var(--insb)}
.rvb-wait{border-style:dashed} .rvb-r.rvb-wait{border-color:var(--muted)} .rvb-wait::after{content:' …'} .rvb:hover{background:var(--code)} .rvb-g:hover{background:color-mix(in srgb,var(--muted) 44%,var(--bg))} .rvb-hit,.rvi-hit{outline:2px solid #bf8700} .rvi-hit{outline-offset:2px} .rvb-near{border-style:dashed}
.rvb-new,.rvb-new:hover{background:var(--insb);color:var(--bg);font-weight:700} #rvt.rvt-new{font-weight:700;border-color:var(--insb)}
.rvb-done{width:7px;height:7px;padding:0;border:4px solid transparent;border-radius:50%;background:var(--muted) content-box;opacity:.35;vertical-align:middle} .rvb-done::before{content:none} .rvb-done:hover{background:var(--muted) content-box;opacity:.8}
#cp{z-index:5} #rvp[hidden],#rvc[hidden]{display:none}
@media (min-width:840px){.rv-open main{margin-right:420px}}
@media (max-width:839px){#rvp{top:auto!important;height:45vh;width:100vw!important;border-left:0!important;border-top:1px solid var(--line);box-shadow:0 -2px 10px rgba(0,0,0,.1)} .rv-open main{padding-bottom:50vh}}
.rvh{flex-wrap:wrap} .rvonly{flex-basis:100%;text-align:left;font-size:12px;padding:1px 8px}
#rvp{position:fixed;right:0;bottom:0;width:min(420px,100vw);box-sizing:border-box;overflow:auto;overscroll-behavior:contain;background:var(--code);border-left:1px solid var(--line);box-shadow:-2px 0 10px rgba(0,0,0,.06);padding:8px 14px 24px;z-index:2;font-size:14px}
.rvh{display:flex;gap:8px;align-items:center;margin-bottom:6px} .rvh .n{flex:1} .rvh button,.rvx{font-size:12px;padding:1px 8px}
.rvi{margin:6px 0;padding:8px 10px;background:var(--bg);border:1px solid var(--line);border-radius:6px;cursor:pointer} .rvi:hover{border-color:color-mix(in srgb,var(--muted) 55%,var(--line))}
@media (prefers-color-scheme: dark){.rvi{background:color-mix(in srgb,var(--btn) 60%,var(--code))}}
.rvm{font-size:12px;color:var(--muted)} .rvt{white-space:pre-wrap;margin:2px 0} .rvt,.rva{overflow-wrap:anywhere} .rvx{margin:4px 6px 0 0}
.rvch,.rvih{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px} .rvch .rvm,.rvih .rvm{flex:1 1 12em} .rvch .rvx,.rvih .rvx{margin:0}
.rvx.rvsec{font-size:11px;padding:0 6px;color:var(--muted)}
.rvch{position:sticky;top:0;z-index:1;padding:6px 12px;background:var(--bg);border-bottom:1px solid var(--line)} .rvcb{padding:4px 12px 10px}
.rvfold{max-height:9em;overflow:hidden;-webkit-mask-image:linear-gradient(#000 60%,transparent);mask-image:linear-gradient(#000 60%,transparent)}
#rvp .rvmore,#rvp .rvmore:hover{border:0;background:none;color:var(--link);padding:0;height:auto;font-size:12px;cursor:pointer}
.rvpr{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px;margin:2px 0 10px} .rvpr .rvx{margin:0} .rvpr .rvm{margin-right:2px}
.rvgone{display:none} .rvstub{opacity:.65} .rvdup{opacity:.6} .rvrep>summary{font-size:12px;color:var(--muted);cursor:pointer} .rvrep[open]>blockquote{opacity:.7}
.rvused{color:var(--insb)} #rvp .rvtwin,#rvc .rvtwin,#cp .rvtwin{color:var(--rw)}
.rvok{display:inline-block;margin:0 3px;padding:0 5px;border-radius:9px;font:600 11px/15px -apple-system,BlinkMacSystemFont,sans-serif;vertical-align:2px;color:var(--insb);background:var(--ins);cursor:default;user-select:none;-webkit-user-select:none}
.rvbox{margin:8px 0;padding:8px;border:1px solid var(--line);border-radius:6px;display:flex;flex-direction:column;gap:6px;background:var(--bg)}
.rvbox textarea{width:100%;box-sizing:border-box;min-height:4.5em;font:inherit;padding:6px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg)}
.rvboxb{display:flex;gap:6px;align-items:center;justify-content:flex-end} .rvboxb .hint{margin-right:auto;font-size:12px}
.rvweigh{display:flex;gap:4px;align-items:center;color:var(--muted)}
.rvpol del{color:var(--delb);text-decoration:line-through;background:none} .rvpol ins{color:var(--insb);text-decoration:none;background:var(--ins)}
::highlight(rvgq){background-color:rgba(212,167,44,.22)} ::highlight(rvgq-on){background-color:rgba(212,167,44,.6)}
.rvfc{font:12px -apple-system,BlinkMacSystemFont,sans-serif;color:var(--link);text-decoration:none} .rvfc:hover{text-decoration:underline}
.rvb-posted::after{content:" " url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 16 16'%3E%3Cpath fill='%23848d97' d='M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z'/%3E%3C/svg%3E") attr(data-r)}
.panel>.rvcmh{flex:1 0 100%;font-size:12px} #cp .rvdel{color:var(--delb)} #cp .rvdel:not(:disabled):hover{background:var(--del)} .rvloc{margin:8px 0 0}
.rvgc{margin:6px 0} .rvgr{margin-left:16px} .rva.rvgh{border-color:var(--line)} .rvgs{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;margin:0 0 6px}
.rv-settled del.chg.done:not(.rvshow){display:none} .rv-settled ins.chg.done:not(.rvshow){background:none;box-shadow:none}
.rv-settled .blk.done:not(.rvshow):not(.fresh){border-left-color:color-mix(in srgb,var(--muted) 45%,transparent)} .rv-settled .blk.done[data-kind=rw]:not(.rvshow)>.was{display:none} .rv-settled .blk.done[data-kind=del]:not(.rvshow)>.was>.old{display:none}
.rv-settled .rvbar:not(.fresh-b){box-shadow:-8px 0 0 -6px color-mix(in srgb,var(--muted) 55%,transparent)}
::highlight(rvcm){text-decoration:underline dotted;text-decoration-color:var(--rw);text-decoration-thickness:2px;text-underline-offset:3px}
body:not(.show-diff) #rvsd{display:none}
#rvsum{display:inline-flex;flex-wrap:wrap;align-items:center;gap:0 6px;font-size:13px;color:var(--muted)} #rvsum button:hover{text-decoration:underline} #rvsum .rvarmed{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:3px}
header .rvn button,header .rvn button:hover,#rvsum button,#rvsum button:hover{border:0;background:none;padding:0;height:auto;line-height:inherit;font:inherit;color:var(--link);cursor:pointer}
#rvsum .rvaud-g{color:var(--fg)} #rvsum .rvaud-c{color:var(--rw)} #rvsum .rvaud-r{color:var(--muted)} #rvsum .rvaud-new{color:var(--insb);font-weight:600}
#rvp blockquote,#rvc blockquote{margin:4px 0;padding:2px 8px;border-left:3px solid color-mix(in srgb,var(--rw) 55%,var(--line));border-radius:0 4px 4px 0;background:var(--code);color:var(--muted);font-size:13px;max-height:4.8em;overflow:hidden}
.rva{border-left:3px solid var(--insb);padding-left:8px;margin-top:6px} .rva>:first-child{margin-top:0} .rva>:last-child{margin-bottom:0}
#rvp .rva blockquote,#rvc .rva blockquote{max-height:none;margin:6px 0 2px;padding:0 8px;border-left-color:var(--line);border-radius:0;background:none;font-size:inherit} .rvi-new{box-shadow:inset 3px 0 var(--insb)} .rvi-new>.rvm{font-weight:600}
.rvfs{display:flex;flex-wrap:wrap;gap:6px 10px;margin:0 0 6px} .rvf button{font-size:12px;padding:1px 8px} .rvg>summary{cursor:pointer;color:var(--muted);font-size:13px;padding:6px 0;border-top:1px solid var(--line)}
#rvc>.rvpart+.rvpart{border-top:1px solid var(--line)}
#rvpush .rvpl{flex:1 0 100%;max-height:40vh;overflow:auto;font-size:13px} #rvpush .rvcmh a{margin-left:6px}
.rvpg{margin:8px 0 2px;font-size:12px;font-weight:600;color:var(--muted)} .rvpg:first-child{margin-top:0}
.rvpi{display:flex;flex-wrap:wrap;gap:0 8px;align-items:baseline;padding:1px 0 1px 8px} .rvpw{flex:0 0 auto;font:12px ui-monospace,Menlo,monospace;color:var(--muted)}
.rvpt{flex:1 1 0;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .rvpwhy{flex:1 0 100%;font-size:12px;color:var(--rw)}
#rvc{position:absolute;z-index:0;overflow:auto;overscroll-behavior:contain;box-sizing:border-box;background:var(--bg);border:1px solid var(--line);border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.12);padding:0;font-size:14px}
'''

SCRIPT = '''
// Review comments (💬, c) and revisits with Claude's answers (plugins/review.py): each marked on its
// change in every view that has it, and elsewhere where its quote reads. The page polls the server for
// them, and shows a comment it saves in place.
(()=>{const mk=(t,p)=>Object.assign(document.createElement(t),p),norm=t=>(t||'').replace(/\\s+/g,' ').trim();
let data={comments:[],revisits:[]},seen=null,items=[],draft=null,mine={};
// The editor, laid out as the revisit panel: what it is on (muted), the text, then Delete apart, in red, and
// Cancel and Save.
const cp=mk('div',{id:'cp',className:'panel',hidden:true}),ch=mk('div',{className:'n rvcmh'}),cq=mk('blockquote'),ct=mk('textarea',{placeholder:'Comment'});
const cs=mk('button',{className:'primary',textContent:'Save'}),cd=mk('button',{className:'rvdel',textContent:'Delete'}),cc=mk('button',{textContent:'Cancel'});
cs.dataset.key='↵';cc.dataset.key='esc';cp.append(ch,cq,ct,cd,mk('span',{className:'hint n',textContent:'⇧↵ adds a line'}),cc,cs);
const lp=mk('aside',{id:'rvp',className:'ui',hidden:true}),card=mk('div',{id:'rvc',className:'ui',hidden:true}),rvt=mk('button',{id:'rvt',textContent:'💬'}),sum=mk('span',{id:'rvsum'});
const pc=mk('button',{id:'pc',textContent:'💬',title:'Comment (c)'});body.append(cp,lp,card);pr.after(pc);rev.after(rvt,sum);
const vorder=['diff','new','old'];
// Whether change k of a doc is reviewed (accepted).
const isDone=(file,k)=>groups.some(g=>g.done&&g.file===file&&g.k===k);
// A range's text as it reads on one side: L the base's (removed words and folded old blocks), R the new.
const drop=(f,side)=>{f.querySelectorAll(side==='L'?'ins,.now,summary':'del,.was').forEach(e=>e.remove());return f.textContent;};
// The block of the diff that node n is in, as the server pairs them: a child of the view, or an item of
// a list that is one.
function blockOf(n){let e=n.nodeType===1?n:n.parentElement;if(e.classList.contains('v'))return e;
for(let p=e.parentElement;!p.classList.contains('v');e=p,p=p.parentElement)if(/^[OU]L$/.test(p.tagName)&&p.parentElement.classList.contains('v'))break;return e;}
// A range's text on its side (L in Old, or where it reads only there), and the rest of its block before
// and after it; a whole block's change is its block.
function anchor(r,blk,old){const qr=norm(drop(r.cloneContents(),'R')),side=old||!qr?'L':'R',b=!blk&&blockOf(r.startContainer);
const a={quote:side==='R'?qr:norm(drop(r.cloneContents(),'L')),side,before:'',after:''},t=document.createRange();
if(b){t.selectNodeContents(b);t.setEnd(r.startContainer,r.startOffset);a.before=norm(drop(t.cloneContents(),side)).slice(-400);
if(b.contains(r.endContainer)){t.selectNodeContents(b);t.setStart(r.endContainer,r.endOffset);a.after=norm(drop(t.cloneContents(),side)).slice(0,400);}}return a;}
const span=els=>{const r=document.createRange();r.setStartBefore(els[0]);r.setEndAfter(els[els.length-1]);return r;};
// What a new comment is on: the selected text, or else the current change.
function anchorOf(){const s=getSelection(),sel=!s.isCollapsed&&s.rangeCount&&s.anchorNode.parentElement?.closest('main .v');if(!sel&&!c)return null;
const el=sel?s.anchorNode.parentElement:c.els[0],a=sel?anchor(s.getRangeAt(0),false,cur==='old'):anchor(span(c.els),el.classList.contains('blk'));
return a.quote?{file:el.closest('section').dataset.file,view:cur,key:sel?null:c.k,...a,section:heading(el)}:null;}
// Your remarks: your 💬 comments, and threads of yours on GitHub.
const said=it=>it.kind==='c'||it.kind==='g'&&it.yours;
// Who sees an item: everyone on its source, for a thread from elsewhere or a comment of yours posted there
// (g); nobody there yet, for your comment not posted, a draft where a source takes posts (c); you and Claude,
// for a question (r). Its tag says so, first on its card's and its panel entry's line.
const audOf=it=>it.kind==='g'||it.posted?'g':it.kind,posting=()=>(data.remotes||[]).find(r=>r.posting);
function tag(it){const k=audOf(it),s=(it.src||posting())?.source,p=pend(it),to=posting()?.source;
return mk('span',{className:'rvaud rvaud-'+k,textContent:k==='g'?'💬 On '+s:k==='r'?'✎ Asked Claude · private':to?'💬 Draft · '+(p||'not posted'):'💬 Your comment',
title:k==='g'?'Everyone who can read it on '+s+' sees it':k==='r'?'Between you and Claude: it never goes to '+(to||'anyone else'):
!to?'Kept here, for you and Claude':p?'In your pending review on '+to+': nobody else sees it until you submit the review':'Not on '+to+': nobody there sees it until you post it'});}
// A header line: the tag, then the rest that there is, muted.
const line=(t,xs)=>{const r=xs.filter(Boolean).join(' · ');return r?[t,' · '+r]:[t];};
// The text a comment is on: its change's new text, or its quote where findEnd() found it (e).
const spans=(v,k)=>[...v.querySelectorAll('.chg[data-k="'+k+'"]')].map(e=>e.classList.contains('blk')?e.querySelector('.now'):e.matches('ins')?e:null)
.filter(Boolean).map(e=>{const r=document.createRange();r.selectNodeContents(e);return r;});
const range=(x,e)=>{const r=document.createRange();r.setStart(x.ns[e[2]],x.os[e[2]]);r.setEnd(x.ns[e[3]-1],x.os[e[3]-1]+1);return r;};
// A view's text on one side, spaces collapsed, with the text node and offset of each character, and
// where each heading (as heading() names it) starts in it.
function textOf(v,side){const skip=side==='L'?'ins,.now,summary,.rvb':'del,.was,.rvb',ns=[],os=[],first=new Map();let s='',sp=true,n;
const w=document.createTreeWalker(v,NodeFilter.SHOW_TEXT,{acceptNode:t=>t.parentElement.closest(skip)?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT});
while(n=w.nextNode()){first.set(n,s.length);for(let i=0;i<n.data.length;i++){const ch=/\\s/.test(n.data[i])?' ':n.data[i];if(ch===' '&&sp)continue;sp=ch===' ';s+=ch;ns.push(n);os.push(i);}}
const hs=[...v.querySelectorAll('h1,h2,h3,h4')].map(h=>{const t=document.createTreeWalker(h,NodeFilter.SHOW_TEXT),k=h.cloneNode(true);let m;
while((m=t.nextNode())&&!first.has(m));k.querySelectorAll('del').forEach(d=>d.remove());return{at:m?first.get(m):-1,name:k.textContent.trim()};}).filter(h=>h.at>=0);
return{s,ns,os,hs};}
// Where q ends in that text, or the word it ends in, and where it starts and ends in it. Among repeats:
// one in its section sec first, then one of whole words, then one partly in an element that matches chg,
// then the one the text before it, b, best leads up to.
function findEnd(x,q,b,sec,chg){q=norm(q);b=norm(b);if(!q)return null;let best=-1,bestE=-1,most=-1;const w=ch=>/\\w/.test(ch||'');
const secs=x.hs.flatMap((h,i)=>h.name===sec?[[h.at,(x.hs[i+1]||{at:Infinity}).at]]:[]);
for(const[at,e]of hits(x,q)){const p=x.s.slice(Math.max(0,at-b.length-1),at).trimEnd();let k=0;
while(k<b.length&&k<p.length&&p[p.length-1-k]===b[b.length-1-k])k++;
k+=(secs.some(([l,h])=>at>=l&&at<h)?2e6:0)+(w(q[0])&&w(x.s[at-1])||w(q[q.length-1])&&w(x.s[e])?0:1e6)+(chg&&x.ns.slice(at,e).some(n=>n.parentElement.closest(chg))?5e5:0);
if(k>most){most=k;best=at;bestE=e;}}
if(best<0)return null;let e=bestE;while(w(x.s[e-1])&&w(x.s[e])&&x.ns[e]===x.ns[e-1])e++;return[x.ns[e-1],x.os[e-1]+1,best,e];}
// Where q starts and ends in that text: as it reads, or else with spaces ignored, as a selection across
// blocks keeps no space between them.
function hits(x,q){const r=[];for(let at=x.s.indexOf(q);at>=0;at=x.s.indexOf(q,at+1))r.push([at,at+q.length]);
const c=q.replace(/ /g,'');if(r.length||!c)return r;
if(!x.c){x.c='';x.ci=[];for(let i=0;i<x.s.length;i++)if(x.s[i]!==' '){x.c+=x.s[i];x.ci.push(i);}}
for(let at=x.c.indexOf(c);at>=0;at=x.c.indexOf(c,at+1))r.push([x.ci[at],x.ci[at+c.length-1]+1]);return r;}
// On a change in view v: after its last inline part, or at the end of its block's new text (in its
// folded summary when it only removes).
function onChange(v,k){const els=v.querySelectorAll('.chg[data-k="'+k+'"]'),e=els[els.length-1];if(!e)return null;
if(!e.classList.contains('blk'))return b=>e.after(b);let t=e.querySelector('.now');if(!t){const m=e.querySelector('summary');return b=>m.append(b);}
while(t.lastElementChild&&/^(P|UL|OL|LI|DL|DT|DD|DIV|BLOCKQUOTE|H[1-6])$/.test(t.lastElementChild.tagName))t=t.lastElementChild;return b=>t.append(b);}
// Where view v has neither its change nor its quote (an accepted change, removed words): at the end of the
// paragraph or list item holding the words before it in its block, c.before (the most of its last 8 that
// are there, 3 at least), or else the words after it, c.after, found as findEnd() finds a quote, in the
// text that reads now first; in a removed block's folded text, on its summary.
function near(v,c,sec,text){const b=norm(c.before).split(' ').filter(Boolean),a=norm(c.after).split(' ').filter(Boolean);
for(const side of['R','L']){const x=text(v,side);let n=null,e;
for(let i=Math.min(8,b.length);!n&&i>=3;i--)if(e=findEnd(x,b.slice(-i).join(' '),b.slice(0,-i).join(' '),sec,''))n=e[0];
for(let i=Math.min(8,a.length);!n&&i>=3;i--)if(e=findEnd(x,a.slice(0,i).join(' '),'',sec,''))n=x.ns[e[2]];
if(!n)continue;let t=n.parentElement.closest('p,li,dt,dd,td,th,h1,h2,h3,h4,h5,h6,pre,blockquote,figcaption');if(!t||!v.contains(t))t=blockOf(n);
const f=t.closest('.was');if(f)return b=>f.querySelector('summary').append(b);
const k=[...t.children].find(e=>/^(P|UL|OL|DL|DIV|BLOCKQUOTE|PRE|TABLE)$/.test(e.tagName));return b=>k?k.before(b):t.append(b);}
return null;}
// The words around a quote found at e in text x, as far as its block goes, for near() where only the quote
// was kept (a revisit on selected text).
function around(x,e){const k=blockOf(x.ns[e[2]]);let i=e[2],j=e[3];
while(i>0&&e[2]-i<400&&k.contains(x.ns[i-1]))i--;while(j<x.s.length&&j-e[3]<400&&k.contains(x.ns[j]))j++;
return{before:x.s.slice(i,e[2]),after:x.s.slice(e[3],j)};}
// Where a revisit is: by its change's own text and the text before it, from a view that has the change;
// failing that, by its quote, "−removed +added": the added text, best where the diff inserts it, or else
// the removed text, best where it deletes it.
function asked(r){const q=r.quote||'',p=q.match(/^(?:−(.*?) )?\\+(.*)$/s),d=!p&&q.match(/^−(.*)$/s),sel='.chg[data-k="'+r.key+'"]';
const s=r.key&&[...document.querySelectorAll('section')].find(s=>s.dataset.file===r.file),v=s&&[...s.querySelectorAll('.v')].find(v=>v.querySelector(sel));
const els=v?[...v.querySelectorAll(sel)]:[];
return els.length?anchor(span(els),els[0].classList.contains('blk')):{quote:p?p[2]:d?d[1]:q,side:r.view==='old'||d?'L':'R',before:'',after:'',chg:p?'ins,.now':d?'del,.old':''};}
// Revisits of one change, or of one quote, are one thread under one mark, so asking again adds to it and
// reopens it, and so does revising one of its drafts (thread names it). It is resolved while all of them
// are, pending while the last has no answer, and new while an answer is unread. It is where its last
// revisit that is not a revision is.
function threads(rs){const ts=[];
for(const r of rs){const t=ts.find(t=>t.file===r.file&&t.members.some(m=>r.thread===m.time||r.key&&m.key===r.key||m.quote===r.quote));t?t.members.push(r):ts.push({file:r.file,members:[r]});}
return ts.map(({file,members:ms})=>{const l=ms[ms.length-1],a=ms.findLast(m=>!m.revises)||l,k=ms.findLast(m=>m.key&&!m.revises);
// Its proposals: each distinct draft once, under the revisit that first proposed it.
const ps=new Map();for(const m of ms)for(const x of m.answers)for(const d of x.drafts)if(!ps.has(d.hash))ps.set(d.hash,proposal(m,d,ms));
return{kind:'r',file,members:ms,time:ms[0].time,view:a.view,section:a.section,sent:a.quote,key:k?k.key:null,...asked(k||a),proposals:ps,
handled:ps.size>0&&[...ps.values()].every(p=>p.state!=='proposed'),
done:ms.every(m=>m.resolved),wait:!l.answers.length,unread:ms.some(m=>m.answers.length>(m.seen||0))};});}
// A drafted comment's state: used while the comment made of it exists, else replaced once a revision of
// it has an answer with another draft, else dismissed, else proposed; revising while a revision waits.
function proposal(m,d,ms){const id=m.time+'#'+d.hash,st=data.proposals[id]||{},by=ms.filter(x=>x.revises===id),c=st.state==='used'&&data.comments.find(c=>c.id===st.comment);
return{id,text:d.text,comment:c||null,was:st.was,revising:by.length>0&&!by[by.length-1].answers.length,
state:c?'used':by.some(x=>x.answers.some(a=>a.drafts.some(e=>e.hash!==d.hash)))?'replaced':st.state==='dismissed'?'dismissed':'proposed'};}
// A comment of yours with what you asked Claude about it (cts: the threads of revisits naming it as their
// comment), and ask, for its mark and the header's counts: whether Claude is on one, and an answer unread.
function withAsks(c){const ts=threads(data.revisits.filter(r=>r.comment===c.id)).map(t=>({...t,draftOf:c.id}));
return{...c,cts:ts,ask:ts.length?{wait:ts.some(t=>t.wait),unread:ts.some(t=>t.unread)}:null};}
// A mark is one 💬 for all there is at one place (b._its, merged in paint()): your comments, your questions
// to Claude (revisit threads) and threads from elsewhere. A click opens the one it holds, or a card of them all.
function badge(it){const b=mk('span',{className:'rvb'});b._its=[it];
b.onmousedown=e=>e.preventDefault();b.onclick=e=>{e.preventDefault();e.stopPropagation();openIt(b._its,b);};return b;}
// Its look says who sees the most public thing open in it: solid with a thread from elsewhere or a comment of
// yours posted there, outlined amber with a comment of yours not posted, and a grey ✎ with questions to
// Claude alone. Then how many comments it holds (yours, open questions, and each comment of a thread from
// elsewhere), none written for one; dashed while Claude has a question or a reply to draft, filled while an
// answer or a draft is unread, GitHub's glyph and its replies' count for a comment of yours posted there (a ✓
// only ever means reviewed), and a faint dot for resolved questions alone.
function dress(b){const its=b._its,cs=its.filter(i=>i.kind==='c'&&!i.done),open=its.filter(i=>i.kind==='r'&&!i.done),gs=its.filter(i=>i.kind==='g'&&!i.done);
const n=cs.length+open.length+gs.reduce((k,g)=>k+g.shown,0),posted=cs.find(c=>c.posted),r=posted?.thread?.shown||0,
ps=[...gs,...cs.map(c=>c.thread||c.ask).filter(Boolean)],wait=open.some(t=>t.wait)||ps.some(g=>g.wait),unread=open.some(t=>t.unread)||ps.some(g=>!g.wait&&g.unread);
b.className='rvb rvb-'+(gs.length||posted?'g':cs.length?'c':'r')+(n?'':' rvb-done')+(wait?' rvb-wait':unread?' rvb-new':'')+(posted?' rvb-posted':'')+
(gs.length===its.length&&gs.every(g=>g.near)?' rvb-near':'');b.textContent=n>1?' '+n:'';if(posted&&r)b.dataset.r=' '+r+'↩';else delete b.dataset.r;
b.title=its.map(it=>it.kind==='g'?'On '+it.src.source+': '+it.root.author+': '+short(it.root.body)+(it.yours?' (yours)':'')+(it.done?' (resolved)':'')+(it.near?' (its text has changed: here at the closest match)':''):
it.kind==='c'?(it.posted?'On '+posting().source+', your comment'+(r?' · '+r+(r>1?' replies':' reply'):'')+(it.done?' (resolved)':''):tag(it).textContent)+': '+short(it.body):
tag(it).textContent+': '+(it.done?'resolved: ':'')+short(it.members.map(m=>m.prompt).join(' / '))).join('\\n')+
(wait?'\\n\\n(waiting for Claude)':unread?'\\n\\n(new answer or reply: click to read)':n?'':'\\n\\n(resolved: click to reopen)');}
// One comment of yours, not posted, opens in its editor; anything else in a card.
const openIt=(its,b)=>its.length===1&&its[0].kind==='c'&&!its[0].thread&&!its[0].cts.length?openCm(its[0]):showCard(its,b);
// Marks every comment and revisit; it.at holds its mark per view, and it.on the keys of the changes it is
// on there: its change's, or those its quote's text is part of.
function paint(){document.querySelectorAll('.rvb,.rvok').forEach(b=>{const p=b.parentNode;b.remove();p.normalize();});
const texts=new Map(),text=(v,side)=>{let m=texts.get(v);if(!m)texts.set(v,m={});return m[side]||(m[side]=textOf(v,side));},put=[];
const cx=it=>it.before||it.after?it:it.ctx,near1=(it,v,b)=>{const at=cx(it)&&near(v,cx(it),it.section,text);if(at){at(b);it.on[v.dataset.view]=new Set();}return!!at;},late=[];
const gts=ghThreads();items=[...data.comments.map(x=>withAsks({...x,kind:'c',done:!!x.thread?.done})),...threads(data.revisits.filter(r=>!r.remote&&!r.comment)),...gts];
for(const it of items){it.at={};it.on={};if(it.hidden)continue;for(const s of document.querySelectorAll('section'))if(s.dataset.file===it.file)for(const v of s.querySelectorAll('.v')){
// Made on a change, it is looked for in change markup first where its change has another key.
const w=v.dataset.view,b=badge(it),on=it.key&&onChange(v,it.key),x=!on&&text(v,it.side),
end=x&&findEnd(x,it.quote,it.before,it.section,it.chg||(it.key?it.side==='L'?'del,.old':'ins,.now':''));
if(on){on(b);it.on[w]=new Set([it.key]);}
else if(end){put.push([end[0],end[1],b]);it.on[w]=new Set(x.ns.slice(end[2],end[3]).map(n=>n.parentElement.closest('.chg')?.dataset.k).filter(Boolean));if(!cx(it))it.ctx=around(x,end);}
else if(!near1(it,v,b)){if(!it.before&&!it.after)late.push([it,v,b]);continue;}
it.at[w]=b;if(said(it)&&w==='diff'&&[...it.on[w]].some(k=>isDone(it.file,k)))it.ranges=on?spans(v,it.key):[range(x,end)];
// A comment from elsewhere highlights the text it is on (gqs), where that text reads.
if(it.kind==='g'&&!it.done)(it.gqs=it.gqs||[]).push(...(on?spans(v,it.key):end?[range(x,end)]:[]));}}
for(const[it,v,b]of late)if(near1(it,v,b))it.at[v.dataset.view]=b;
// A thread that a posted comment of yours stands for is where that comment's mark is.
for(const it of items)if(it.kind==='c'&&it.thread?.hidden)it.thread.at=it.at;
// One whose text reads in no view (rewritten since it was asked): on its section's heading.
for(const it of items)if(it.kind!=='g'&&!it.hidden&&it.section&&!Object.keys(it.at).length)for(const s of document.querySelectorAll('section'))if(s.dataset.file===it.file)for(const v of s.querySelectorAll('.v')){
const h=[...v.querySelectorAll('h1,h2,h3,h4')].find(h=>{const k=h.cloneNode(true);k.querySelectorAll('del').forEach(d=>d.remove());return k.textContent.trim()===it.section;});
if(h){const b=badge(it);h.append(b);it.at[v.dataset.view]=b;it.on[v.dataset.view]=new Set();}}
// One from elsewhere whose quote reads in no view (its text was edited after the comment was made): at the
// end of the paragraph that shares the most of its words, half of them at least, marked as the closest match.
const words=t=>new Set(norm(t).toLowerCase().match(/[\\p{L}\\p{N}]+/gu)||[]);
for(const it of items)if(it.kind==='g'&&!it.hidden&&it.quote&&!Object.keys(it.at).length){const q=words(it.quote);if(q.size<2)continue;
for(const s of document.querySelectorAll('section'))if(s.dataset.file===it.file)for(const v of s.querySelectorAll('.v')){let best=null,most=0;
for(const p of v.querySelectorAll('p,li,td,th,dt,dd,h1,h2,h3,h4')){if(p.querySelector('p,li,td'))continue;const k=p.cloneNode(true);k.querySelectorAll(it.side==='L'?'ins,.now,.rvb':'del,.was,.rvb').forEach(e=>e.remove());
const ws=words(k.textContent);let n=0;for(const w of q)if(ws.has(w))n++;if(n>most){most=n;best=p;}}
if(best&&most>=Math.max(2,q.size/2)){it.near=true;const b=badge(it);best.append(b);it.at[v.dataset.view]=b;it.on[v.dataset.view]=new Set();}}}
// From the end of the doc back, so splitting a text node leaves the places before it where they were;
// in a removed block's folded text, on its summary; after removed words, which a settled change hides.
put.sort((x,y)=>x[0]===y[0]?y[1]-x[1]:x[0].compareDocumentPosition(y[0])&Node.DOCUMENT_POSITION_FOLLOWING?1:-1);
for(const[t,o,b]of put){const f=t.parentElement.closest('.was'),d=t.parentElement.closest('del.chg');if(f)f.querySelector('summary').append(b);else if(d)d.after(b);else{t.splitText(o);t.after(b);}}
// Marks at one place, side by side, are one.
for(const b of document.querySelectorAll('main .rvb')){let p=b.previousSibling;while(p&&p.nodeType===3&&!p.data.trim())p=p.previousSibling;
if(!p?.classList?.contains('rvb'))continue;p._its.push(...b._its);for(const it of b._its)for(const w in it.at)if(it.at[w]===b)it.at[w]=p;b.remove();}
document.querySelectorAll('main .rvb').forEach(dress);
// Your comments' wording on reviewed changes, underlined: the change's new text, or the quote.
if(window.Highlight&&CSS.highlights)CSS.highlights.set('rvcm',new Highlight(...items.flatMap(i=>i.ranges||[])));
// The text the comments from elsewhere are on, highlighted as Google Docs does: pale, and darker for the one
// whose card is open or that was just gone to (gq()).
if(window.Highlight&&CSS.highlights)CSS.highlights.set('rvgq',new Highlight(...items.flatMap(i=>i.gqs||[])));
// Which changes your comments are on, per view, and how many: on commits, accepting one of them says
// ✓ reviewed, not looks good. Each reviewed change has its ✓, and its 💬 says what was said on it.
mine={};for(const it of items)if(said(it))for(const[w,ks]of Object.entries(it.on))for(const k of ks)(mine[w]=mine[w]||new Map()).set(k,(mine[w].get(k)||0)+1);
for(const g of groups.filter(g=>g.done)){const on=onChange(g.els[0].closest('.v'),g.k);
if(on)on(mk('span',{className:'rvok',textContent:'✓',title:!W.reviewed?'Accepted':mine.diff?.has(g.k)?'Reviewed, with your comments':'Looks good'}));}
status();
// The header's 💬 opens the Review panel, and beside it what was said is counted by who sees it, how many of
// them are unread beside each count; each count steps through its own.
steps.clear();const ts=tallies(order()),step=(k,l,text,w,title,cls)=>{const b=mk('button',{className:cls,textContent:text,title:title+': step through them, then with n and p (Esc stops)'});
steps.set(k,{l,w,b});b.onclick=()=>{arm(k);stepTo(k,l,w,1);};return b;};
// Unread, in brackets after its count: one item, so the header's gaps go around it.
const unread=(k,u,uw)=>{const x=mk('span');x.append('(',step(k+'-new',u,u.length+' unread',uw,k==='g'?'With a comment or a drafted reply you have not read':'With an answer you have not read','rvaud-new'),')');return x;};
rvt.title='Review: '+(ts.map(t=>t[2]+(t[4].length?' ('+t[4].length+' unread)':'')).join(', ')||'nothing yet')+'; all of it in the order of the doc';
sum.replaceChildren(...ts.flatMap(([k,l,w,t,u,uw],i)=>[...(i?[' · ']:[]),step(k,l,w,w,t,'rvaud-'+k),
...(u.length?[unread(k,u,uw)]:[])]));
rvt.classList.toggle('rvt-new',ts.some(t=>t[4].length));arm(steps.has(armed)?armed:null);if(!lp.hidden)list();
refreshCard();tally();chips();}
// What was said, by who sees it: your drafts, what is on GitHub, your open questions, and what Claude is on
// (pending). Each [who, its items with a mark, in the doc's order, words, title, those of them unread, words
// for those], none when there are none: unread, a question with an answer not read yet, or a thread on GitHub
// with a comment or a drafted reply not read yet.
function tallies(all){const o=all.filter(i=>!i.done&&!i.hidden&&Object.keys(i.at).length),of=k=>o.filter(i=>audOf(i)===k),to=posting()?.source,
g=of('g'),on='on '+[...new Set(g.map(i=>(i.src||posting()).source))].join(' and '),w=i=>i.kind==='c'?i.thread||i.ask:i,one=W.revisits.replace(/s$/,'');
return[['c',of('c'),to?'draft':'comment',to?'drafts':'comments',to?'Your comments not posted: nobody on '+to+' sees them yet':'Your comments'],
['g',g,on,on,'Threads '+on+', yours posted and others\\', which everyone there sees'],
['r',of('r'),one,W.revisits,'Your open '+W.revisits+' to Claude, which nobody else sees'],
['wait',o.filter(i=>w(i)?.wait),'pending','pending','Waiting for Claude']]
.filter(t=>t[1].length).map(([k,l,s1,sn,title])=>{const u=k==='wait'?[]:l.filter(i=>w(i)?.unread&&(i.kind==='r'||!w(i).wait));
return[k,l,l.length+' '+(l.length>1?sn:s1),title,u,u.length+' unread '+(k==='g'?on:u.length>1?sn:s1)];});}
// A count steps through its own, from the one it went to last, and the status line says which; the page's
// own scroll handler retells the status while no change is current, so those words are held through the
// scroll to it, until you scroll, click or press a key yourself.
const stepped={};let held=null;
function stepTo(k,l,w,d){const i=l.findIndex(x=>idOf(x)===stepped[k]),it=l[i<0?(d>0?0:l.length-1):(i+d+l.length)%l.length];stepped[k]=idOf(it);jump(it);
held=(l.indexOf(it)+1)+' of '+w+' · '+(it.section||it.kind==='g'&&ghLabel(it.root)||it.file);pos.textContent=held;}
addEventListener('scroll',()=>{if(held&&!c)pos.textContent=held;},{passive:true});
for(const t of['wheel','touchstart','mousedown','keydown'])addEventListener(t,()=>{held=null;},{capture:true,passive:true});
// A count clicked stays armed (underlined, with its n p chip): n and p (j and k) then step through its items
// instead of the changes, until Esc (with no card open) or another of the header's controls lets them go.
const steps=new Map();let armed=null;
function arm(k){armed=k;const a=k&&steps.get(k)?.b;for(const b of sum.querySelectorAll('button')){b.classList.toggle('rvarmed',b===a);if(b===a)b.dataset.key='n p';else delete b.dataset.key;}}
addEventListener('keydown',e=>{if(!armed||e.target.closest('input,textarea')||e.metaKey||e.ctrlKey||e.altKey)return;
if(e.key==='Escape'){if(card.hidden)arm(null);return;}
const d=e.key==='n'||e.key==='j'?1:e.key==='p'||e.key==='k'?-1:0,st=d&&steps.get(armed);if(!st)return;
e.preventDefault();e.stopImmediatePropagation();stepTo(armed,st.l,st.w,d);},true);
document.querySelector('header').addEventListener('click',e=>{if(armed&&!e.target.closest('#rvsum'))arm(null);},true);
// Each doc's bar counts the comments made elsewhere on it (on GitHub, in the Google Docs); a click opens the
// Review panel on that doc's comments, where each entry goes to its place.
function chips(){document.querySelectorAll('.rvfc').forEach(e=>e.remove());const all=order();
for(const s of document.querySelectorAll('section')){const ts=all.filter(i=>i.kind==='g'&&!i.done&&(!i.hidden||i.shown)&&i.file===s.dataset.file);if(!ts.length)continue;
const a=mk('a',{className:'rvfc ui',href:'#',textContent:'💬 '+ts.length+' comment'+(ts.length>1?'s':''),
title:'Comments on this doc, from '+[...new Set(ts.map(t=>t.src.source))].join(' and ')+': open them in the Review panel'});
a.onclick=e=>{e.preventDefault();only=s.dataset.file;filter='open';aud=null;toggle(true);lp.scrollTop=0;};
const bar=s.querySelector('.file');bar.insertBefore(a,bar.querySelector('.fbar'));}}
// Accepted (reviewed) changes that carry comments or open questions, counted on each file's line in the
// header, so they are not lost once they leave To review; a count steps through them.
function tally(){document.querySelectorAll('.rvn').forEach(e=>e.remove());
for(const s of document.querySelectorAll('section')){const n=document.querySelector('.files a[href="#'+s.id+'"]')?.nextElementSibling;if(!n)continue;
const on=f=>new Set(items.filter(it=>f(it)&&it.at.diff?.closest('section')===s).flatMap(it=>[...it.on.diff].filter(k=>isDone(s.dataset.file,k))));
for(const[ks,w]of[[on(said),k=>k>1?' with comments':' with a comment'],[on(it=>it.kind==='r'&&!it.done),k=>' open question'+(k>1?'s':'')]]){if(!ks.size)continue;
const b=mk('button',{textContent:ks.size+w(ks.size),title:'Step through these '+W.accepted+' changes'}),x=mk('span',{className:'rvn'});
b.onclick=()=>{if(cur!=='diff')view('diff');const l=groups.filter(g=>g.done&&g.sec===s&&ks.has(g.k));if(l.length)focus(l[(l.indexOf(c)+1)%l.length]);};x.append(' · ',b);n.append(x);}}}
function take(d){data=d;seen=JSON.stringify(d);paint();}
wordsOf=x=>x&&W.reviewed&&mine[x.view]?.has(x.k)?{...W,accept:W.reviewed,accept_key:W.reviewed_key,accept_title:W.reviewed_title}:W;
// On commits, accepting a change resolves the answered threads on it alone (not pending ones, nor ones on
// text outside a change or across several); taking it back reopens those, not ones resolved by hand.
onAccept=async(xs,accepting)=>{if(!W.reviewed)return;const ts=[];
for(const x of xs)for(const it of items)if(it.kind==='r'&&!ts.includes(it)&&it.on[x.view]?.size===1&&it.on[x.view].has(x.k)&&
(accepting?!it.done&&!it.wait:it.done&&it.members.every(m=>m.resolved_by==='accept')))ts.push(it);
if(ts.length)await post('review',{revisits:ts.flatMap(t=>t.members.map(m=>m.time)),resolve:accepting,by:'accept'});};
// Reviewed (accepted) changes show settled: the new text only, a thin bar beside a block with a change in
// it, and their ✓. Clicking one shows its diff again; ± diffs, kept per viewer, shows them all. Changes to
// review always show their diffs.
let diffs=false;try{diffs=localStorage.getItem(sk('docdiff-diffs'))==='1';}catch(e){}
const sd=mk('button',{id:'rvsd',textContent:'± diffs',title:'Show the diffs of '+W.accepted+' changes'});
function settle(){body.classList.toggle('rv-settled',!diffs);sd.setAttribute('aria-pressed',diffs);}
sd.onclick=()=>{diffs=!diffs;try{localStorage.setItem(sk('docdiff-diffs'),diffs?'1':'');}catch(e){}settle();};
$('views').before(sd);for(const g of groups)if(g.done&&!g.els[0].classList.contains('blk'))
g.els[0].closest('p,li,dt,dd,td,th,h1,h2,h3,h4,h5,h6,pre,blockquote,figcaption')?.classList.add('rvbar');
document.addEventListener('click',e=>{if(!body.classList.contains('rv-settled')||!getSelection().isCollapsed||e.target.closest('.rvb,.rvok,a,summary,.ui'))return;
const t=e.target.closest('.v-diff .chg.done'),g=t&&groups.find(x=>x.els.includes(t));if(g)g.els.forEach(el=>el.classList.toggle('rvshow'));});
settle();
// Comments made elsewhere, read only, one source per plugin that has them (remarks(): github_pr.py's on
// the PR, gdoc_comments.py's in the Google Docs), each named by its source: a thread is a comment with its
// replies, on the change or text it was placed on, or else in the panel's group for its source, with the
// PR's own comments and reviews. A comment of yours whose text one of them has (or has all of) was posted,
// on a source you post to (posting: GitHub).
const same=(a,b)=>(a=norm(a),b=norm(b),a&&b&&(a===b||a.length>=20&&b.includes(a)||b.length>=20&&a.includes(b)));
// With your login on a source known (me: GitHub's --me, or the token's user), a thread you wrote in (yours)
// counts as your comment does: its change is commented. A comment of yours that one of your 💬 holds shows
// as that 💬 alone (posted): the thread has no mark of its own, and the 💬 counts its replies and opens it
// (thread); gh is the comment on GitHub.
const isMe=c=>!!c.src?.me&&c.author===c.src.me;
function ghThreads(){const out=[],told=new Set();data.comments.forEach(c=>{c.posted=c.gh=c.thread=null;});
for(const[k,src]of(data.remotes||[]).entries()){const cs=src.comments||[],by=new Map(cs.map(c=>[c.id,c])),ts=new Map();cs.forEach(c=>c.src=src);
for(const c of cs){let r=c;while(r.reply_to&&by.has(r.reply_to))r=by.get(r.reply_to);if(!ts.has(r.id))ts.set(r.id,[]);ts.get(r.id).push(c);}
if(src.posting)for(const c of data.comments){const g=cs.find(g=>(!src.me||isMe(g))&&same(g.body,c.body));if(g){c.posted=g.url||null;c.gh=g;if(src.me)told.add(g);}}
out.push(...[...ts].map(([id,ms])=>{const r=by.get(id),left=ms.filter(c=>!told.has(c)),rid=src.source+':'+id,
// The replies drafted for it: requests naming it (remote), as threads of their own, kept in its card.
rts=threads(data.revisits.filter(x=>x.remote===rid)).map(t=>({...t,reply:true,remote:rid,at:{},on:{}}));
const t={kind:'g',id:'g'+k+'-'+id,rid,src,root:r,members:ms.sort((a,b)=>a.time<b.time?-1:a.time>b.time?1:0),time:r.time,file:r.file||null,key:r.key||null,
quote:r.quote||'',before:r.before||'',side:r.side||'R',section:'',yours:left.some(isMe),shown:left.length,hidden:!left.length||ms.some(c=>told.has(c)),
rts,wait:rts.some(t=>t.wait),unread:rts.some(t=>t.unread),done:!!r.resolved,by:r.resolved_by||null,settles:!!src.resolve&&r.kind==='line'};
// Where your login there is known, comments by others newer than your last look at it (seen: the newest
// comment's time read) are unread (fresh), until its card or panel entry is opened (looked()).
t.seen=data.threads?.[rid]?.seen||'';t.fresh=src.me?ms.filter(c=>!isMe(c)&&(c.time||'')>t.seen).length:0;
t.newest=ms.reduce((m,c)=>(c.time||'')>m?c.time:m,'');t.unread=t.unread||t.fresh>0;
for(const c of data.comments)if(ms.includes(c.gh))c.thread=t;return t;}));}
return out;}
// Opening a thread from elsewhere reads it: the newest comment's time is kept as read. A card keeps marking
// the comments that were new when it opened (fresh0, the thread's seen then) while it stays open.
const fresh0=new Map();
function looked(t){if(t?.fresh)post('review',{threads:{[t.rid]:t.newest}}).then(take).catch(()=>{});}
const newer=(it,c)=>!!it.src?.me&&!isMe(c)&&(c.time||'')>(fresh0.has(it.rid)?fresh0.get(it.rid):it.seen||'');
const ago=t=>{const s=(Date.now()-t)/1000;return s<90?'just now':s<5400?Math.round(s/60)+' min ago':s<129600?Math.round(s/3600)+' h ago':Math.round(s/86400)+' days ago';};
const at=c=>c.path?c.path.split('/').pop()+(c.line?':'+c.line:'')+(c.side==='L'?' (old)':''):'';
// Where a comment is, in its source's words: its line, or the PR or a review; a source's own note, such as
// what a push does to a Google Doc comment's text, comes after.
const ghLabel=c=>c.kind==='pr'?'on the PR':c.kind==='review'?'review'+(c.state?': '+c.state.toLowerCase().replace(/_/g,' '):''):
[at(c),c.outdated&&'outdated',!c.file&&'not placed',c.note].filter(Boolean).join(' · ');
// Your posted comment (own's), leading its thread under a header that names it, needs no line of its own.
const ghBodies=(it,own)=>it.members.map(c=>{const d=mk('div',{className:'rvgc'+(c.reply_to?' rvgr':'')}),x=mk('div',{className:'rva rvgh'});x.innerHTML=c.html;
const m=mk('div',{className:'rvm',textContent:c.author+(isMe(c)?' (you)':'')+' · '+ago(new Date(c.time))});if(newer(it,c))m.append(' · ',mk('b',{className:'rvaud-new',textContent:'new'}));
d.append(...(own&&c===own.gh&&!c.reply_to?[]:[m]),x);return d;});
// A posted comment's card or panel entry: its header words, and its copy kept here, which GitHub's does not
// follow: edit and delete act on that alone.
const postedAs=(own,t)=>line(tag(own),['your comment, posted '+ago(new Date(own.gh.time)),t.shown&&t.shown+(t.shown>1?' replies':' reply'),t.fresh&&t.fresh+' new',t.done&&ghSettled(t)]);
const ghSettled=t=>'resolved'+(t.by?' by '+t.by:'');
// ✓ Resolve or ↺ Reopen a thread on its source (GitHub), as its own Resolve conversation does, on this click
// alone; its state here is what the source answers. Until the server has read that (it takes a token) there
// is no button: one greyed out would read as resolved.
function ghSettle(t){if(!t.settles||!t.root.thread)return[];const b=act(t.done?'↺ Reopen':'✓ Resolve',(t.done?'Reopen this thread on ':'Resolve this thread on ')+t.src.source+(t.by?' (resolved by '+t.by+')':''),
async()=>{b.disabled=true;b.textContent=t.done?'reopening…':'resolving…';
try{const{refused,...st}=await post('review',{resolve_thread:t.root.thread,resolved:!t.done,source:t.src.source});take(st);if(refused)pos.textContent='not '+(t.done?'reopened':'resolved')+' on '+t.src.source+': '+refused;}
catch(e){pos.textContent='not saved: '+e.message+' (is the docdiff server running?)';}});return[b];}
function local(own){const r=mk('div',{className:'rvpr rvloc'}),e=act('edit','Edit the copy kept here; GitHub keeps the posted one',()=>{card.hidden=true;openCm(own);}),
x=act('delete','Delete the copy kept here; the comment stays on GitHub',()=>{if(x.textContent==='delete'){x.textContent='delete?';return;}card.hidden=true;save({id:own.id,delete:true});});
e.classList.add('rvsec');x.classList.add('rvsec');r.append(mk('span',{className:'rvm',textContent:'Local copy:'}),e,x);return r;}
const ghOpen=it=>mk('a',{href:it.root.url,target:'_blank',rel:'noopener',textContent:it.src.open||'Open ↗'});
const gq=it=>{if(window.Highlight&&CSS.highlights)CSS.highlights.set('rvgq-on',new Highlight(...(it?.gqs||[])));};
new MutationObserver(()=>{if(card.hidden){gq(null);fresh0.clear();}}).observe(card,{attributes:true,attributeFilter:['hidden']});
// A thread from elsewhere, as Claude reads it in a request for a reply: the text it is on, then each comment.
const threadText=it=>(it.quote?'On “'+norm(it.quote)+'”:\\n':'')+it.members.map(c=>(c.reply_to?'↳ ':'')+c.author+': '+norm(c.body)).join('\\n');
// ✎ Draft a reply opens a reply box inline, under the thread and its drafts, in its card or its panel entry
// (pressed again, it closes it): one text box, then which way to send it. Draft takes the text as notes for
// the reply, or with none has Claude draft on its own; Polish takes it as your reply, to fix and tighten,
// and waits for text. ↵ presses the one used last (highlighted), Esc closes the box. Weigh in has Claude
// also say whether it would answer differently. The request names the thread (remote), with its mode and
// weigh, and joins the thread's earlier drafts. ✎ Revise on a drafted reply opens a box under that draft.
// Each box is kept by its key while the page draws again, so text being typed survives.
let replyMode=(()=>{try{return localStorage.getItem(sk('docdiff-reply-mode'))||'draft';}catch(e){return 'draft';}})();
const boxes=new Map(),opened=new Set();
const leadFor=(it,m,w,notes)=>(m==='polish'?'Polish my reply to this '+it.src.source+' thread on '+it.file+': fix the language and tighten it, keeping my meaning and voice':
'Draft a reply to this '+it.src.source+' thread on '+it.file+(notes?' from my notes':' on your own judgment (no notes)'))+(w?'; and weigh in, briefly, if you would answer differently':'');
// A box for a reply (o.it: the thread) or for revising a draft (o.rev: the draft, o.t: its reply thread).
function box(key,o){let x=boxes.get(key);if(x){x.o=o;return x.el;}
const el=mk('div',{className:'rvbox ui'}),ta=mk('textarea'),btns=mk('div',{className:'rvboxb'});x={el,ta,o,weigh:false};boxes.set(key,x);
const close=()=>{opened.delete(key);boxes.delete(key);el.remove();};x.close=close;
btns.append(mk('span',{className:'hint n',textContent:'Esc closes · ⇧↵ new line'}));
let go;
if(o.rev){ta.placeholder='What should change? e.g. shorter';const b=mk('button',{className:'primary',textContent:'Revise'});b.onclick=()=>sendBox(x,'revise');btns.append(b);
go=()=>b.click();}
else{ta.placeholder='Notes for the reply, or your own reply to polish. Leave it empty and Claude drafts on its own.';
const w=mk('input',{type:'checkbox'}),wl=mk('label',{className:'rvweigh',title:'Also say, briefly, whether Claude would answer differently'});
w.onchange=()=>{x.weigh=w.checked;ta.focus();};wl.append(w,' weigh in');
const pb=mk('button',{textContent:'Polish',title:'Polish the text, as your reply'}),db=mk('button',{textContent:'Draft',title:'Draft a reply from the text as notes, or on its own'});
const set=()=>{pb.disabled=!ta.value.trim();const m=replyMode==='polish'&&!pb.disabled?'polish':'draft';pb.classList.toggle('primary',m==='polish');db.classList.toggle('primary',m==='draft');return m;};
const send=m=>{replyMode=m;try{localStorage.setItem(sk('docdiff-reply-mode'),m);}catch(e){}sendBox(x,m);};
pb.onclick=()=>send('polish');db.onclick=()=>send('draft');ta.addEventListener('input',set);btns.append(wl,pb,db);set();go=()=>send(set());}
ta.addEventListener('keydown',e=>{e.stopPropagation();if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();go();}if(e.key==='Escape')close();});
el.append(ta,btns);return el;}
// Sends a box's request as the revisit panel does; with nobody watching, the clipboard is the way in.
async function sendBox(x,mode){const ask=x.ta.value.trim();if(!ask&&mode!=='draft')return;const o=x.o,it=o.it,
quote=o.rev?o.rev.text:threadText(it),lead=o.rev?'Revise this reply drafted for a thread on '+o.t.file:leadFor(it,mode,x.weigh,!!ask);
const r={file:(it||o.t).file,section:'',quote,view:cur,key:null,remote:o.rev?o.t.remote:it.rid,thread:o.rev?o.t.time:it.rts[0]?.time,lead,prompt:ask,
...(o.rev?{revises:o.rev.id}:{mode,weigh:x.weigh})},text=lead+':\\n> '+quote.replace(/\\n/g,'\\n> ')+(ask?'\\n\\n'+ask:'');
// It goes to the store shared by every page showing the thread (see state()), where Claude watches.
let queued=true,st=null;try{st=await post('review',{reply:{...r,text}});take(st);}catch(e){queued=false;}
x.close();
// How it went shows in the thread's card for a few seconds: the header's own line is redrawn too often.
const src=r.remote.split(':')[0],seen=queued&&(st.stores?.[src]?st.stores[src].watched:await watching()),copied=!seen&&await copy(text);
sentNote.set(r.remote,seen?'sent, Claude is watching':(queued?'saved, but nobody is watching: ':'not saved: ')+(copied?'copied, paste it in the chat':'tell Claude'));
const g=items.find(i=>i.kind==='g'&&i.rid===r.remote);if(g)reshow(g);setTimeout(()=>{sentNote.delete(r.remote);const g=items.find(i=>i.kind==='g'&&i.rid===r.remote);if(g)reshow(g);},6000);}
const sentNote=new Map();
const reply=it=>act('✎ Draft a reply','Draft or polish a reply to this thread with Claude, to copy into '+it.src.source,()=>{
if(opened.has(it.rid)){boxes.get(it.rid)?.close();return;}opened.add(it.rid);reshow(it);});
// Draws the thread again where it shows, card or panel entry (unfolded, so a box is not cut off), with its
// boxes, and brings the newest box into view with the cursor in it.
function reshow(it){unfolded.add(it.id);if(inCard(it))refreshCard();else if(!lp.hidden)list();
const x=[...boxes.values()].reverse().find(x=>x.el.isConnected);if(x){x.el.scrollIntoView({block:'nearest'});x.ta.focus({preventScroll:true});}}
// A box being typed in keeps the cursor where it was when what holds it is drawn again.
function keepFocus(){const a=document.activeElement;if(!a?.closest?.('.rvbox'))return()=>{};const s=a.selectionStart,e=a.selectionEnd;
return()=>{if(a.isConnected){a.focus();a.setSelectionRange(s,e);}};}
// A thread from elsewhere in a card: the thread, its drafted replies (Copy, Revise, Dismiss), its reply box
// while open, and Open in its source. A thread a posted comment of yours stands for (own) leads with that: its
// header, the text it is on, GitHub's copy of it, the replies there, and its local copy last.
function ghPart(it,h,bd){const own=items.find(i=>i.kind==='c'&&i.thread?.rid===it.rid),m=mk('span',{className:'rvm'});
m.append(...(own?postedAs(own,it):line(tag(it),[ghLabel(it.root),it.near&&'its text has changed: here at the closest match',
it.members.length+' comment'+(it.members.length>1?'s':''),it.done&&ghSettled(it)])));h.append(m,...(it.file&&!it.done?[reply(it)]:[]),...ghSettle(it),ghOpen(it));
bd.append(...(own?[mk('blockquote',{textContent:own.quote})]:[]),...ghBodies(it,own),...drafts(it),...(sentNote.has(it.rid)?[mk('div',{className:'rvm rvsaid',textContent:sentNote.get(it.rid)})]:[]),
...(opened.has(it.rid)&&!it.done?[box(it.rid,{it})]:[]),...(own?[local(own)]:[]));}
// ✎ Ask Claude about a comment of yours not posted ("shorter", "as a question"): a revisit naming it as its
// comment and quoting its text, in the thread of what you asked about it before; a reworked draft in the
// answer offers Use, in its place. What you asked shows under the comment, private.
const askDraft=c=>act('✎ Ask Claude','Ask Claude about this draft: “shorter”, “as a question”; a reworked draft comes back to use in its place',()=>{card.hidden=true;
openRev({quote:c.body,el:elOf(c),key:c.key,fields:{comment:c.id,section:c.section,...(c.cts[0]?{thread:c.cts[0].time}:{})},context:'asks about your draft “'+short(c.body)+'”'});});
const draftAsks=c=>c.cts.length?[mk('div',{className:'rvm rvdh rvaud rvaud-r',textContent:'✎ Asked Claude · private',title:'Between you and Claude'}),...c.cts.flatMap(t=>asks(t))]:[];
const drafts=it=>it.rts.length?[mk('div',{className:'rvm rvdh rvaud rvaud-r',textContent:'✎ Drafted replies · private',title:'Between you and Claude, until you post one on '+it.src.source}),...it.rts.flatMap(t=>asks(t))]:[];
// When each source was last read, and why not since; ↻ asks that source again where it can be asked
// (refresh). A source being read in the background (busy) says so until its new copy comes in.
function ghStatus(){return(data.remotes||[]).map(g=>{const r=mk('div',{className:'rvgs rvm'});
r.append(g.busy?g.source+' being read…':(g.fetched?g.source+' read '+ago(g.fetched*1e3):g.source+' not read yet')+(g.error?' · '+g.error+(g.fetched?', showing that copy':''):''));
if(g.refresh&&!g.busy){const b=act('↻ '+g.source,'Read '+g.source+' again',async()=>{b.textContent='reading…';
try{take(await post('review',{github:'now',source:g.source}));}catch(e){b.textContent='↻ '+g.source;}});r.append(b);}return r;});}
async function load(){try{const r=await fetch('review');if(!r.ok)throw new Error(r.status);const d=await r.json();
// Served again after it was not: drawn again, which titles the header's 💬 anew.
if(rvt.disabled){rvt.disabled=false;seen=null;}if(JSON.stringify(d)!==seen)take(d);}catch(e){rvt.disabled=true;rvt.title='review: not served (restart the docdiff server)';}}
function openCm(it){if(!it){pos.textContent='select text, or step to a change, then press c';return;}
hidePop();card.hidden=true;draft=it;ch.replaceChildren(...(it.id?line(tag(it),[it.posted&&'local copy',it.section||it.file]):['💬 New comment · '+(it.section||it.file)]),...twinLink(it));
cq.textContent=it.quote;ct.value=it.body||'';cd.hidden=!it.id;cp.hidden=false;ct.focus();}
function closeCm(){cp.hidden=true;ct.value='';draft=null;}
async function save(req){try{take(await post('review',req));closeCm();}catch(e){pos.textContent='comment not saved: '+e.message+' (is the docdiff server running?)';}}
cs.onclick=()=>{const t=ct.value.trim();if(!draft)return;if(!t)return draft.id?save({id:draft.id,delete:true}):closeCm();
save(draft.id?{id:draft.id,body:t}:{...draft,body:t});};
cd.onclick=()=>draft?.id&&save({id:draft.id,delete:true});cc.onclick=closeCm;
ct.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();cs.click();}if(e.key==='Escape')closeCm();});
const act=(t,title,f)=>{const b=mk('button',{className:'rvx',textContent:t,title});b.onclick=e=>{e.stopPropagation();f();};return b;};
const short=t=>(t=norm(t)).length>70?t.slice(0,70).replace(/\\s+\\S*$/,'')+'…':t;
// A thread's requests, each with its answers, a revision with the draft it revises.
const asks=(it,shown=new Set())=>it.members.flatMap(m=>[...(m.revises?[mk('div',{className:'rvm',textContent:'↻ revise the draft “'+short(m.quote)+'”'})]:[]),
mk('div',{className:'rvt',textContent:m.remote&&!m.revises?(m.mode==='polish'?'Your reply: ':m.prompt?'Notes: ':'No notes: Claude drafts on its own')+m.prompt:m.prompt}),...answers(it,m,shown)]);
// Each drafted comment (a blockquote) shows as its proposal: with its state and actions under it, folded
// to a stub when dismissed and into a summary when replaced; one proposed again (shown has it) shows once,
// its repeats faded, or gone with it once it is dismissed or replaced.
function answers(it,m,shown){if(!m.answers.length)return[mk('div',{className:'rvm',textContent:'pending: Claude has not answered yet'})];
return m.answers.map(a=>{const d=mk('div',{className:'rva'});d.innerHTML=a.html;const qs=d.querySelectorAll(':scope>blockquote'),open=[];
if(qs.length!==a.drafts.length)return d;
qs.forEach((q,i)=>{const p=it.proposals.get(a.drafts[i].hash);if(shown.has(p.id)){if(p.state==='dismissed'||p.state==='replaced')q.classList.add('rvgone');else{q.classList.add('rvdup');q.after(mk('div',{className:'rvm rvpr',textContent:'the same draft as above'}));}return;}
shown.add(p.id);decorate(q,p,it,m,i);if(p.state==='proposed')open.push(p);});
if(open.length>1&&!it.reply&&!it.draftOf)d.append(act('💬 Use all '+open.length+' as one comment','One new comment of these drafts, open to edit',()=>use(it,open,open.map(p=>p.text).join('\\n\\n'))));return d;});}
// A polished reply (the answer's first draft, for a reply asked with mode polish) shows its edits against
// the text sent; Copy still takes it clean.
function wdiff(a,b){const A=a.split(/\\s+/).filter(Boolean),B=b.split(/\\s+/).filter(Boolean),n=A.length,m=B.length,f=document.createDocumentFragment();
const L=Array.from({length:n+1},()=>new Uint16Array(m+1));for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)L[i][j]=A[i]===B[j]?L[i+1][j+1]+1:Math.max(L[i+1][j],L[i][j+1]);
const put=(t,w)=>f.append(t?mk(t,{textContent:w}):w,' ');let i=0,j=0;
while(i<n&&j<m){if(A[i]===B[j]){put(null,A[i]);i++;j++;}else if(L[i+1][j]>=L[i][j+1])put('del',A[i++]);else put('ins',B[j++]);}
while(i<n)put('del',A[i++]);while(j<m)put('ins',B[j++]);return f;}
function decorate(q,p,it,m,i){const r=mk('div',{className:'rvpr'});
if(it.reply&&m?.mode==='polish'&&i===0&&p.state==='proposed'){q.replaceChildren(wdiff(m.prompt,p.text));q.classList.add('rvpol');q.title='Your reply, with the edits marked';}
if(p.state==='replaced'){const f=mk('details',{className:'rvrep'});q.replaceWith(f);f.append(mk('summary',{textContent:'replaced draft: '+short(p.text)}),q);return;}
if(p.state==='dismissed'){q.classList.add('rvgone');r.classList.add('rvstub');r.append(mk('span',{className:'rvm',textContent:'dismissed draft: '+short(p.text)}),act('↶ Undo','Show it as proposed again',()=>setProposal(p,null)));q.after(r);return;}
// A draft of yours Claude reworked is used in its place; Undo brings back the text it replaced.
if(p.state==='used'&&it.draftOf){r.append(mk('span',{className:'rvm rvused',textContent:'✓ used: your draft reads so now'}),
...(p.was!=null?[act('↶ Undo','Your draft as it was',()=>save({id:it.draftOf,body:p.was}).then(()=>setProposal(p,null)))]:[]));q.after(r);return;}
if(p.state==='used'){const u=act('↶ Undo','Delete the comment made of it',()=>{if(norm(p.comment.body)!==norm(p.text)&&u.textContent==='↶ Undo'){u.textContent='↶ Undo: delete your edited comment?';return;}save({id:p.comment.id,delete:true});});
r.append(mk('span',{className:'rvm rvused',textContent:'✓ used'}),act('💬 Open comment','Edit the comment made of it',()=>{card.hidden=true;openCm({...p.comment,kind:'c'});}),u);q.after(r);return;}
// A drafted reply to a thread from elsewhere is copied, to paste there, rather than used as your comment.
const c=it.reply&&act('Copy','Copy this reply, to paste in the thread',async()=>{c.textContent=await copy(p.text)?'copied':'copying failed';setTimeout(()=>c.textContent='Copy',1500);});
r.append(...(p.revising?[mk('span',{className:'rvm',textContent:'revising…'})]:[]),c||(it.draftOf?act('Use','In place of your draft (↶ Undo brings it back)',()=>save({id:it.draftOf,body:p.text,uses:[p.id]})):
act('💬 Use','A new comment of this draft, open to edit',()=>use(it,[p],p.text))),
act('✎ Revise','Ask Claude to rework this draft',()=>{if(!it.reply)return revise(it,p);opened.add(it.remote+'#'+p.id);const g=items.find(i=>i.kind==='g'&&i.rid===it.remote);if(g)reshow(g);}),
act('Dismiss','Fold it away',()=>setProposal(p,'dismissed')));q.after(r);
// A drafted reply is revised in a box under it, as replies are asked for.
if(it.reply&&opened.has(it.remote+'#'+p.id))r.after(box(it.remote+'#'+p.id,{rev:p,t:it}));}
const state=it=>it.done?'resolved':it.wait?'pending':it.unread?'new answer':it.handled?'drafts handled':'answered';
// A thread's actions, at the top of its card and of its panel entry, so they stay in reach of a long
// thread: Ask more (r while its card is open), and Resolve (urged once every draft is used or dismissed)
// or Reopen. On a card whose thread is on one change in To review (g, on commits), that change's accept
// comes first, as the toolbar words it (a while the card is open), and Resolve is the lesser choice.
const actions=(it,g)=>{const b=act(it.done?'↺ Reopen':'✓ Resolve',it.done?'Back to the open revisits':'Done with it: its mark folds to a dot',()=>{card.hidden=true;setThread(it,{resolve:!it.done});});
if(g){b.classList.add('rvsec');if(!it.done)b.title='Done asking, not done reviewing: resolves this thread alone, and its change stays in To review';}
else if(it.handled&&!it.done)b.classList.add('primary');
const w=g&&wordsOf(g),ok=g&&act(w.accept,w.accept_title.replace('the current change','its change').replace(/ \\(a\\)$/,'')+
(it.done?'':it.wait?'. This thread has no answer yet, so it stays open':'. Its answered threads resolve with it, this one too')+' (a)',()=>acceptFor(g));
if(ok)ok.classList.add('primary');
return[...(ok?[ok]:[]),act('✎ '+W.sent[0].toUpperCase()+W.sent.slice(1)+' more','Go on in this thread (r): it gets the new '+(W.sent==='ask'?'question':'request')+', and reopens if resolved',()=>more(it)),b];};
// The change a thread's card accepts for it: its one change, while that is still to review, on commits.
const toAccept=it=>W.reviewed&&cur==='diff'&&it.on.diff?.size===1&&groups.find(x=>!x.done&&x.file===it.file&&it.on.diff.has(x.k))||null;
function acceptFor(g){card.hidden=true;focus(g,false);accept();}
async function setThread(it,req){try{take(await post('review',{revisits:it.members.map(m=>m.time),...req}));}catch(e){pos.textContent='not saved: '+e.message+' (is the docdiff server running?)';}}
const setProposal=(p,state)=>post('review',{proposals:[p.id],state}).then(take).catch(e=>{pos.textContent='not saved: '+e.message+' (is the docdiff server running?)';});
// Use: the drafts become a comment on the thread's change or quote, opened to edit.
async function use(it,ps,body){try{const st=await post('review',{file:it.file,view:it.view,key:it.key,quote:it.quote,side:it.side,before:it.before,after:it.after,section:it.section,body,uses:ps.map(p=>p.id)});
take(st);card.hidden=true;openCm({...st.comments[st.comments.length-1],kind:'c'});}catch(e){pos.textContent='comment not made: '+e.message+' (is the docdiff server running?)';}}
// Revise: a revisit of the draft, in the thread; its answer's draft replaces it. Ask more: a revisit of the
// thread's own text that adds to the thread. Both name the thread (thread: its first revisit's time), so
// they join it whatever text the request quotes, and the panel says which thread they go on from.
const elOf=it=>it.at[cur]||Object.values(it.at)[0]||[...document.querySelectorAll('section')].find(s=>s.dataset.file===it.file);
const first=it=>short((it.members.find(m=>!m.revises)||it.members[0]).prompt);
function revise(it,p){card.hidden=true;openRev({quote:p.text,el:elOf(it),key:it.key,fields:{thread:it.time,revises:p.id,section:it.section,
...(it.draftOf?{comment:it.draftOf}:{}),...(it.reply?{remote:it.remote,lead:'Revise this reply drafted for a thread on '+it.file}:{})},context:'revises a draft in “'+first(it)+'”'});}
function more(it){card.hidden=true;openRev({quote:it.sent,el:elOf(it),key:it.key,fields:{thread:it.time,section:it.section},context:'goes on from “'+first(it)+'”'});}
// A mark's card, under it or, with more room there, over it: what the mark holds (its), each under a header
// (what it is, its state and actions) that stays at the top while its part scrolls under it. A question
// shows each ask with Claude's answers and drafts, Ask more and Resolve; a thread from elsewhere as
// ghPart() shows it; a comment of yours, not posted, with edit, copy and delete. It follows what it shows
// while open (cardIds), and opening it reads the answers and drafted replies in it; with a question in it,
// r asks more there and a accepts its change (carded, the question's time).
let carded=null,cardIds=null;
const idOf=it=>it.kind==='r'?'r'+it.time:it.kind==='g'?it.rid:'c'+it.id;
const inCard=it=>!card.hidden&&!!cardIds?.includes(idOf(it));
function part(it){const h=mk('div',{className:'rvch'}),bd=mk('div',{className:'rvcb'}),d=mk('div',{className:'rvpart'});
if(it.kind==='g')ghPart(it,h,bd);
else if(it.kind==='r'){const m=mk('span',{className:'rvm'});m.append(...line(tag(it),[it.section,it.time.replace('T',' '),state(it)]));h.append(m,...actions(it,toAccept(it)));
bd.append(mk('blockquote',{textContent:it.sent}),...asks(it));}
else{const x=act('delete','Delete this comment',()=>{if(x.textContent==='delete'){x.textContent='delete?';return;}card.hidden=true;save({id:it.id,delete:true});}),
c=act('copy','Copy as Markdown: its file:line, the quoted text and the comment',async()=>{c.textContent=await copy(markdown([it]))?'copied':'copying failed';setTimeout(()=>c.textContent='copy',1500);});
const hm=mk('span',{className:'rvm'});hm.append(...line(tag(it),[it.section||it.file]),...twinLink(it));
h.append(hm,askDraft(it),act('edit','Edit this comment',()=>{card.hidden=true;openCm(it);}),c,x);
bd.append(...(it.quote?[mk('blockquote',{textContent:it.quote})]:[]),mk('div',{className:'rvt',textContent:it.body}),...draftAsks(it));}
d.append(h,bd);return d;}
// Your comments come first, then threads from elsewhere, then questions, the resolved ones last; a posted
// comment of yours shows as the thread it is in. Again (refreshing), it keeps the card where it is.
const rank=it=>it.done?3:it.kind==='c'?0:it.kind==='g'?1:2;
function showCard(its,b,again){its=[...new Set(its.map(i=>i.kind==='c'&&i.thread||i))].sort((x,y)=>rank(x)-rank(y));const f=keepFocus(),y=card.scrollTop;
cardIds=its.map(idOf);carded=its.find(i=>i.kind==='r')?.time||null;card.replaceChildren(...its.map(part));
if(again)card.scrollTop=y;else place(b);f();gq({gqs:its.flatMap(i=>i.gqs||[])});
for(const it of its){if(it.fresh&&!fresh0.has(it.rid))fresh0.set(it.rid,it.seen);looked(it);for(const t of it.kind==='r'?[it]:it.kind==='c'?it.cts:it.rts||[])if(t.unread)setThread(t,{seen:true});}}
// The open card drawn again, with what its mark holds now.
function refreshCard(){if(card.hidden||!cardIds)return;const it=items.find(i=>idOf(i)===cardIds[0]),b=it&&elOf(it);if(it)showCard(b?._its||[it],b,true);}
// A card is placed and sized in the visual viewport, which a page wider than the screen does not stretch,
// and left of the Review panel while that is open beside the doc.
function place(b){const v=visualViewport||{width:innerWidth,offsetLeft:0},p=lp.hidden?null:lp.getBoundingClientRect(),
right=p&&p.left>v.offsetLeft+v.width/2?p.left:v.offsetLeft+v.width,w=Math.min(560,(right-v.offsetLeft)*.95);
card.style.left=v.offsetLeft+scrollX+'px';card.style.width=w+'px';card.hidden=false;card.scrollTop=0;
const r=b.getBoundingClientRect(),below=innerHeight-r.bottom-12,above=r.top-document.querySelector('header').getBoundingClientRect().bottom-12,down=below>=300||below>=above;
card.style.maxHeight=Math.max(120,down?below:above)+'px';card.style.left=Math.max(v.offsetLeft+8,Math.min(r.left,right-card.offsetWidth-8))+scrollX+'px';
card.style.top=(down?r.bottom+4:r.top-4-card.offsetHeight)+scrollY+'px';}
// The Review panel: comments and revisits in the order they come in the doc, each one going to its mark.
const where=it=>{const g=it.github;return g&&g.path?g.path+(g.line?':'+g.line+(g.side==='L'?' (old)':''):''):'';};
function order(){const secs=[...document.querySelectorAll('section')].map(s=>s.dataset.file);
const at=it=>{const v=it.at[cur]?cur:vorder.find(x=>it.at[x]);if(!v)return 2;const b=it.at[v],w=b.closest('.v'),r=document.createRange();
r.setStart(w,0);r.setEndBefore(b);return r.toString().length/Math.max(1,w.textContent.length);};
return items.map(it=>[(i=>i<0?1e9:i)(secs.indexOf(it.file)),at(it),it]).sort((x,y)=>x[0]-y[0]||x[1]-y[1]||(x[2].time<y[2].time?-1:1)).map(x=>x[2]);}
function jump(it){const v=it.at[cur]?cur:vorder.find(x=>it.at[x]);if(!v)return;if(v!==cur)view(v);
const g=it.key&&groups.find(x=>x.view===cur&&x.file===it.file&&x.k===it.key),b=it.at[cur];
if(g)focus(g);else{focus(null,false);b.scrollIntoView({block:'center',behavior:'smooth'});}
b.classList.add('rvb-hit');if(it.kind==='g')gq(it);setTimeout(()=>{b.classList.remove('rvb-hit');if(it.kind==='g'&&card.hidden)gq(null);},1500);}
// A panel entry: its line (where, state, actions), then its body, folded to a few lines when long.
function row(it,files){const r=it.kind==='r',d=mk('div',{className:'rvi'+((it.kind==='c'?it.thread||it.ask:it)?.unread&&!it.done?' rvi-new':'')}),h=mk('div',{className:'rvih'}),m=mk('span',{className:'rvm'}),bd=mk('div',{className:'rvib'}),w=where(it);
d.dataset.id=r?'r'+it.time:it.kind==='g'?it.id:'c'+it.id;
if(it.kind==='g'){m.append(...line(tag(it),[files>1&&it.file,ghLabel(it.root),it.fresh&&it.fresh+' new',it.done&&ghSettled(it),it.rts.length&&(it.wait?'reply being drafted':it.rts.some(x=>x.unread)?'new drafted reply':'drafted replies')]));
h.append(m,...(it.file&&!it.done?[reply(it)]:[]),...ghSettle(it),ghOpen(it));
bd.append(...ghBodies(it),...drafts(it),...(opened.has(it.rid)&&!it.done&&!inCard(it)?[box(it.rid,{it})]:[]));d.append(h,bd);
d.onclick=e=>{if(e.target.closest('a,button'))return;jump(it);looked(it);for(const t of it.rts)if(t.unread)setThread(t,{seen:true});};return d;}
const t=it.kind==='c'&&it.thread;m.append(...(t?postedAs(it,t):[...line(tag(it),[files>1&&it.file,it.section,r&&state(it)]),Object.keys(it.at).length?'':' · not in the page now']));
if(w)m.append(' · ',mk('a',{href:it.github.href,target:'_blank',rel:'noopener',textContent:w.split('/').pop()+' ↗'}));if(!r&&!t)m.append(...twinLink(it));
if(it.quote)bd.append(mk('blockquote',{textContent:r?it.sent:it.quote}));
if(r)h.append(m,...actions(it)),bd.append(...asks(it));else{const e=act('edit','',()=>openCm(it)),x=act('delete','',()=>x.textContent==='delete'?x.textContent='delete?':save({id:it.id,delete:true})),
c=act('copy','Copy as Markdown: its file:line, the quoted text and the comment',async()=>{c.textContent=await copy(markdown([it]))?'copied':'copying failed';setTimeout(()=>c.textContent='copy',1500);});
// Posted, it shows as its thread on GitHub does, with its local copy's edit and delete under it.
if(t){h.append(m,...(t.file&&!t.done?[reply(t)]:[]),...ghSettle(t),ghOpen(t));bd.append(...ghBodies(t,it),...drafts(t),...(opened.has(t.rid)&&!t.done&&!inCard(t)?[box(t.rid,{it:t})]:[]));
d.append(h,bd,local(it));d.onclick=e=>{if(e.target.closest('a,button'))return;jump(it);looked(t);for(const x of t.rts)if(x.unread)setThread(x,{seen:true});};return d;}
h.append(m,askDraft(it),e,c,x);bd.append(mk('div',{className:'rvt',textContent:it.body}),...draftAsks(it));}
d.append(h,bd);d.onclick=e=>{if(e.target.closest('a,button'))return;jump(it);for(const t of r?[it]:it.cts)if(t.unread)setThread(t,{seen:true});};return d;}
// Folds an entry's body when it is longer than a few lines; Show more and Show less, kept across re-renders.
const unfolded=new Set();
function fold(bd){const id=bd.closest('.rvi').dataset.id;bd.nextElementSibling?.classList.contains('rvmore')&&bd.nextElementSibling.remove();
bd.classList.add('rvfold');const long=bd.scrollHeight>bd.clientHeight+4;bd.classList.toggle('rvfold',long&&!unfolded.has(id));if(!long)return;
bd.after(act(unfolded.has(id)?'Show less':'Show more','',()=>{unfolded.has(id)?unfolded.delete(id):unfolded.add(id);fold(bd);}));bd.nextElementSibling.classList.add('rvmore');}
// Open (comments and unresolved revisits), then the resolved revisits folded; or either alone. And who sees
// them (aud): on GitHub, your drafts, your questions to Claude, or all. The panel opens on Open and all; a
// reload keeps whichever was chosen.
let filter='open',folded=true,aud=null;
const auds=()=>{const to=posting()?.source,on=[...new Set((data.remotes||[]).map(r=>r.source))].join(' · ');
return[...(on?[['g',on,'On '+on+': everyone there sees them']]:[]),['c',to?'Drafts':'Comments',to?'Your comments not posted: nobody on '+to+' sees them yet':'Your comments'],
['r','Claude','Your '+W.revisits+' to Claude, which nobody else sees']];};
// Comments from elsewhere that have no place in the doc fold into a group per source (On GitHub, On Google
// Docs), under the open ones.
let ghFolded=false;
// One doc's entries only, from its bar's comment count; the panel's 💬 button shows every doc's again.
let only=null;
function list(){if(aud&&!auds().some(a=>a[0]===aud))aud=null;
const refocus=keepFocus(),mine=order().filter(i=>!only||i.file===only),all=mine.filter(i=>!aud||audOf(i)===aud),files=document.querySelectorAll('section').length,done=all.filter(i=>i.done&&!i.hidden),
loose=all.filter(i=>i.kind==='g'&&!i.hidden&&!i.done&&!Object.keys(i.at).length),its=all.filter(i=>!loose.includes(i)&&!i.hidden);
const f=mk('div',{className:'seg rvf'}),fa=mk('div',{className:'seg rvf'}),g=mk('details',{className:'rvg',open:!folded});g.ontoggle=()=>{folded=!g.open;keep();if(g.open)g.querySelectorAll('.rvib').forEach(fold);};
for(const[v,l]of[['open','Open'],['resolved','Resolved'],['all','All']]){const b=mk('button',{textContent:l});b.setAttribute('aria-pressed',v===filter);
b.onclick=()=>{filter=v;keep();list();};f.append(b);}
// Pressed again, an audience shows them all.
for(const[v,l,t]of auds()){const b=mk('button',{className:'rvaud-'+v,textContent:l,title:t});b.setAttribute('aria-pressed',v===aud);
b.onclick=()=>{aud=aud===v?null:v;keep();list();};fa.append(b);}
g.append(mk('summary',{textContent:'Resolved ('+done.length+')'}),...done.map(it=>row(it,files)));
// Copy drafts (N) copies your 💬 comments not posted yet, whatever the audience filter, to post by hand where
// ⇪ Push cannot (no token); it is offered only while there are some. A posted one is on GitHub already, and
// pasted again would be posted twice; so is, in other words, one much like a comment of yours there (twin()),
// which it leaves out too. Each one has its own copy too.
const to=posting()?.source,unsent=mine.filter(i=>i.kind==='c'&&!i.posted&&!twin(i)),twins=mine.filter(i=>i.kind==='c'&&!i.posted).length-unsent.length,
cl=(to?'Copy drafts (':'Copy comments (')+unsent.length+')',h=mk('div',{className:'rvh'}),x=mk('button',{textContent:'×',title:'Close'}),cm=mk('button',{textContent:cl,
title:to?'Your comments not on '+to+' yet, as Markdown, each with its file:line and quote, to post there by hand'+((data.remotes||[]).some(r=>r.push)?'':' (⇪ Push needs a GITHUB_TOKEN on the server)')+
(twins?'; not the '+twins+' much like comments of yours there':''):'Your comments as Markdown, each with its file, section and quote'});
cm.onclick=async()=>{cm.textContent=await copy(markdown(unsent))?'copied '+unsent.length+(twins?' · left out '+twins+' already on GitHub in other words':''):'copying failed';
setTimeout(()=>cm.textContent=cl,twins?3000:1500);};
// ⇪ Push, while some of your comments are not on GitHub, with how many are new or edited since pushed.
const ups=unposted(),fresh=ups.filter(c=>c.pushed?.body!==c.body).length,pb=ups.length&&(data.remotes||[]).some(r=>r.push)&&
mk('button',{textContent:fresh?'⇪ Push '+fresh+' as a pending review':'⇪ Pending review',title:'Your comments not on GitHub yet, as your pending review on the PR, which you submit there'});
if(pb)pb.onclick=openPush;
x.onclick=()=>toggle(false);h.append(mk('b',{textContent:'Review'}),mk('span',{className:'n',textContent:[...tallies(mine).filter(t=>'cgr'.includes(t[0])).map(t=>t[2]+(t[4].length?' ('+t[4].length+' unread)':'')),mine.filter(i=>i.done&&!i.hidden).length+' resolved'].join(' · ')}),...(unsent.length?[cm]:[]),...(pb?[pb]:[]),x);
if(only){const o=mk('button',{className:'rvonly',textContent:'Only '+only+' · show all',title:'Show the entries on every doc'});o.onclick=()=>{only=null;list();};h.append(o);}
const bySource=new Map();for(const it of loose)bySource.set(it.src.source,[...(bySource.get(it.src.source)||[]),it]);
const ghs=[...bySource].map(([n,l])=>{const gh=mk('details',{className:'rvg',open:!ghFolded});gh.ontoggle=()=>{ghFolded=!gh.open;if(gh.open)gh.querySelectorAll('.rvib').forEach(fold);};
gh.append(mk('summary',{textContent:'On '+n+' ('+l.length+')'}),...l.map(it=>row(it,files)));return gh;});
const fs=mk('div',{className:'rvfs'});fs.append(f,fa);
lp.replaceChildren(h,...ghStatus(),fs,...(filter==='resolved'?done:its.filter(i=>!i.done)).map(it=>row(it,files)),
...(filter!=='resolved'?ghs:[]),...(filter==='all'&&done.length?[g]:[]));
lp.querySelectorAll('.rvib').forEach(fold);refocus();}
// Paste-ready: per comment, its file:line (linked to it on GitHub) or else its file and section, the
// quoted text and the comment.
function markdown(its){return its.filter(it=>it.kind==='c').map(it=>{const w=where(it),loc=w||it.file+(it.section?' · '+it.section:'');
const q=it.quote.length>300?it.quote.slice(0,300).replace(/\\s+\\S*$/,'')+'…':it.quote;
return '**'+(w&&it.github.href?'['+loc+']('+it.github.href+')':loc)+'**\\n\\n'+(q?'> '+q+'\\n\\n':'')+it.body;}).join('\\n\\n---\\n\\n');}
// Your comments pushed to the PR as your pending review: whether one here is there as it reads here, or
// edited since; one GitHub has the text of counts as posted instead.
const pend=it=>it.kind==='c'&&!it.posted&&it.pushed?(it.pushed.body===it.body?'in your pending review':'edited since pushed'):'';
const unposted=()=>order().filter(i=>i.kind==='c'&&!i.posted);
// ⇪ Push sends your comments not on GitHub yet, in the doc's order, to the PR as your pending review, which
// stays pending until you submit it there (push() in github_pr.py). Its panel first lists what a push would
// do, which the server works out after reading your pending review on GitHub (dry); Push does it, and the
// panel then says what went, and where to submit it. (No confirm(): an embedded browser may block it.)
const pp=mk('div',{id:'rvpush',className:'panel',hidden:true}),ph=mk('div',{className:'n rvcmh'}),pl=mk('div',{className:'rvpl'}),
pq=mk('button',{textContent:'Cancel'}),pg=mk('button',{className:'primary',textContent:'Push'});pq.dataset.key='esc';pg.dataset.key='↵';
pp.append(ph,pl,mk('span',{className:'hint n',textContent:'It stays pending: you submit it on GitHub'}),pq,pg);body.append(pp);
const groupsOf=[['add','Add'],['update','Update, edited here since pushed'],['text','In the review text, as it has no line'],['same','In your pending review already'],['leave','Left out']];
let pushing=null;
// A comment of yours not posted that reads much like one of yours on GitHub (most of their words shared) is
// most likely a copy reworded here after it was posted: no longer posted, it would go to GitHub again. Its row
// and card headers say so, and its editor's, linking to the one there; Copy drafts leaves it out, and a push's list says so.
const wordset=t=>new Set(norm(t).toLowerCase().match(/[\\p{L}\\p{N}]+/gu)||[]),like=(a,b)=>{const x=wordset(a),y=wordset(b);let n=0;for(const w of x)if(y.has(w))n++;return n/Math.max(1,x.size+y.size-n);};
const twin=c=>(data.remotes||[]).flatMap(r=>r.posting&&r.me?r.comments.filter(g=>g.author===r.me):[]).find(g=>like(g.body,c.body)>=.6);
const twinLink=it=>{const g=it.kind==='c'&&!it.posted&&twin(it);return g?[' · ',mk('a',{className:'rvtwin',href:g.url,target:'_blank',rel:'noopener',
textContent:'on GitHub in other words ↗',title:'Much like your comment on GitHub: “'+short(g.body)+'”. Copy drafts leaves it out; open it there to edit it'})]:[];};
function showPlan(got,sent){const by=new Map(data.comments.map(c=>[c.id,c])),sends=got.plan.filter(p=>['add','update','text'].includes(p.act));pl.replaceChildren();
for(const[a,l]of groupsOf){const ps=got.plan.filter(p=>p.act===a&&by.has(p.id));if(!ps.length)continue;pl.append(mk('div',{className:'rvpg',textContent:l+' ('+ps.length+')'}));
for(const p of ps){const c=by.get(p.id),r=mk('div',{className:'rvpi'}),miss=sent&&a!=='same'&&a!=='leave'&&!(p.id in got.pushed);
r.append(mk('span',{className:'rvpw',textContent:where(c).split('/').pop()||c.section||c.file}),mk('span',{className:'rvpt',textContent:short(c.body)}));
const g=!sent&&a==='add'&&twin(c),why=miss?'not sent':p.why||g&&'much like your comment on GitHub: “'+short(g.body)+'”';
if(why)r.append(mk('span',{className:'rvpwhy',textContent:why}));pl.append(r);}}
if(!got.plan.length)pl.append(mk('div',{className:'rvpg',textContent:'Nothing to push'}));return sends.length;}
const submitAt=got=>mk('a',{href:got.url,target:'_blank',rel:'noopener',textContent:'Submit it on GitHub, under Files changed ↗'});
async function openPush(){pushing=unposted().map(c=>c.id);ph.textContent='⇪ Your pending review on GitHub: reading it…';pl.replaceChildren();
pg.textContent='Push';pg.disabled=true;delete pg.dataset.done;pq.hidden=false;pp.hidden=false;pq.focus();
try{const{pushed:got,...st}=await post('review',{push:pushing,dry:true});take(st);if(pp.hidden)return;
if(got.error){ph.textContent='✗ Cannot push: '+got.error;return;}const n=showPlan(got,false);
ph.textContent='⇪ Push to your pending review on GitHub: '+(n?n+' comment'+(n>1?'s':''):'nothing new');
if(n){pg.textContent='Push '+n;pg.disabled=false;pg.focus();}else if(got.plan.some(p=>p.act==='same'))ph.append(submitAt(got));}
catch(e){ph.textContent='✗ Not read: '+e.message+' (is the docdiff server running?)';}}
function closePush(){pp.hidden=true;pushing=null;}
pq.onclick=closePush;pp.addEventListener('keydown',e=>{if(e.key==='Escape'){e.stopPropagation();closePush();}});
pg.onclick=async()=>{if(pg.dataset.done)return closePush();pg.disabled=true;pq.hidden=true;pg.textContent='pushing…';
try{const{pushed:got,...st}=await post('review',{push:pushing,dry:false});take(st);showPlan(got,true);
const k=a=>got.plan.filter(p=>p.act===a&&p.id in got.pushed).length,said=[[k('add'),'added'],[k('update'),'updated'],[k('text'),'in its text']].filter(x=>x[0]).map(x=>x.join(' '));
ph.textContent=(got.error?'✗ '+got.error+(said.length?'; before that, ':''):'✓ ')+(said.length?said.join(', ')+': in your pending review on GitHub, not submitted.':'');
if(got.review)ph.append(submitAt(got));}catch(e){ph.textContent='✗ Not pushed: '+e.message+' (is the docdiff server running?)';}
pg.textContent='Done';pg.disabled=false;pg.dataset.done=1;pg.focus();};
function toggle(on){lp.hidden=!on;body.classList.toggle('rv-open',on);if(on){lp.style.top=document.querySelector('header').getBoundingClientRect().bottom+'px';list();}keep();}
// The panel, its filter and its fold outlast reloads.
function keep(){try{sessionStorage.setItem(sk('docdiff-review'),JSON.stringify({on:!lp.hidden,filter,folded,aud}));}catch(e){}}
rvt.onclick=()=>{only=null;if(lp.hidden){filter='open';aud=null;}toggle(lp.hidden);};addEventListener('resize',()=>{if(!lp.hidden)lp.style.top=document.querySelector('header').getBoundingClientRect().bottom+'px';});
pc.onmousedown=e=>e.preventDefault();pc.onclick=()=>{const a=anchorOf();hidePop();openCm(a);};
// The doc's own click and selection handling leaves the panel and the card alone.
for(const e of [lp,card])for(const t of ['click','mouseup'])e.addEventListener(t,ev=>ev.stopPropagation());
document.addEventListener('click',e=>{if(!e.target.closest('#rvc'))card.hidden=true;});
// With a thread's card open, r asks more in that thread, and a accepts the change the card offers, before
// the page's own r and a see them.
addEventListener('keydown',e=>{if(e.key!=='r'&&e.key!=='a'||card.hidden||!carded||e.target.closest('input,textarea')||e.metaKey||e.ctrlKey||e.altKey)return;
const it=items.find(i=>i.kind==='r'&&i.time===carded),g=it&&e.key==='a'&&toAccept(it);if(!it||e.key==='a'&&!g)return;
e.preventDefault();e.stopImmediatePropagation();e.key==='r'?more(it):acceptFor(g);},true);
document.addEventListener('keydown',e=>{if(e.target.closest('input,textarea')||e.metaKey||e.ctrlKey||e.altKey)return;
if(e.key==='Escape')card.hidden=true;if(e.key==='c'){e.preventDefault();openCm(anchorOf());}});
try{const st=JSON.parse(sessionStorage.getItem(sk('docdiff-review')))||{};filter=st.filter||filter;aud=st.aud||null;folded=st.folded!==false;if(st.on)toggle(true);}catch(e){}
load().then(()=>post('review',{github:'auto'})).then(take).catch(()=>{});setInterval(load,2000);})();
'''


def get(run, path, query):
    """The comments, and the revisits with their answers, for the page."""
    if path != '/review':
        return None
    return json.dumps(state(run), ensure_ascii=False).encode(), 'application/json'


def post(out, path, req):
    """Adds a comment, marking the proposals req['uses'] names as used by it, or edits (with uses too: a
    draft reworked by Claude in its place) or deletes the one req['id'] names; or resolves (resolve true), reopens (false) or reads (seen) the revisits
    req['revisits'] names by time (by 'accept': as their change was accepted); or reads the threads from
    elsewhere req['threads'] names, {rid: the newest comment's time}; or sets the state of the
    proposals req['proposals'] names, dismissed or none; or brings the PR's comments on GitHub up to date
    (req['github']: 'auto' or 'now', as remarks() in github_pr.py takes it); or files a request for a reply
    to a thread from elsewhere (req['reply']); or pushes comments to the PR (req['push'], see push()); or
    resolves (resolved true) or reopens the thread from elsewhere that req['resolve_thread'] names, on its
    source (req['source']), saying what went wrong as refused. Returns what get() does."""
    if path != '/review':
        return None
    run = json.loads((out / '.run.json').read_text())
    if req.get('github'):
        return state(run, req['github'], req.get('source'))
    if 'push' in req:
        return push(run, out, req)
    if req.get('resolve_thread'):
        m = next((m for m in docdiff.PLUGINS if hasattr(m, 'resolve') and getattr(m, 'SOURCE', None) == req.get('source')), None)
        why = (m.resolve(run, out, str(req['resolve_thread']), bool(req.get('resolved'))) if m
               else f"threads on {req.get('source')} cannot be resolved from here")
        return {**state(run), 'refused': why}
    # A request for a reply to a thread from elsewhere goes to its source's shared store (or else the page's
    # revisit.jsonl), with where that is (store), for Claude to answer there.
    if req.get('reply'):
        r = dict(req['reply'])
        d = stores(run).get(str(r.get('remote') or '').partition(':')[0]) or out
        with open(d / 'revisit.jsonl', 'a') as f:
            f.write(json.dumps({'time': time.strftime('%Y-%m-%dT%H:%M:%S'), **r, 'store': str(d)}, ensure_ascii=False) + '\n')
        return state(run)
    with lock:
        doc = load(out)
        cs = doc['comments']
        was = next((c for c in cs if req.get('id') and c['id'] == req['id']), None)
        if isinstance(req.get('threads'), dict):
            for rid, t in req['threads'].items():
                doc['threads'][str(rid)] = {'seen': str(t)}
        elif req.get('proposals'):
            for p in map(str, req['proposals']):
                if req.get('state') == 'dismissed':
                    doc['proposals'][p] = {'state': 'dismissed', 'time': now()}
                else:
                    doc['proposals'].pop(p, None)
        elif req.get('revisits'):
            read = collections.Counter(a.get('time') for a in answer_records(run))
            for t in map(str, req['revisits']):
                st = doc['revisits'].setdefault(t, {})
                # Resolved by accepting its change (by 'accept'), it reopens when that is taken back; by hand, not.
                if req.get('resolve'):
                    st.update(resolved=now(), **({'resolved_by': 'accept'} if req.get('by') == 'accept' else {}))
                    if req.get('by') != 'accept':
                        st.pop('resolved_by', None)
                elif 'resolve' in req:
                    st.pop('resolved', None)
                    st.pop('resolved_by', None)
                if req.get('seen'):
                    st['seen'] = read[t]
        elif was and req.get('delete'):
            cs.remove(was)
            doc['proposals'] = {p: st for p, st in doc['proposals'].items() if st.get('comment') != was['id']}
        elif was:
            # A draft Claude reworked, used in its place, keeps the text it replaced, for Undo.
            for p in map(str, req.get('uses') or []):
                doc['proposals'][p] = {'state': 'used', 'comment': was['id'], 'was': was['body'], 'time': now()}
            was['body'], was['edited'] = str(req['body']), now()
        elif not req.get('id'):
            cs.append({'id': secrets.token_hex(4), 'time': now(), **{k: req.get(k) for k in FIELDS}})
            locate(run, cs[-1:])
            for p in map(str, req.get('uses') or []):
                doc['proposals'][p] = {'state': 'used', 'comment': cs[-1]['id'], 'time': now()}
        save(out, doc)
    return state(run)


def state(run, refresh=None, source=None):
    out = pathlib.Path(run['out'])
    with lock:
        doc = load(out)
        # Another PR or head (the PR moved on): find the comments in its files again.
        moved = [c for c in doc['comments']
                 if [(c.get('github') or {}).get(k) for k in ('pr', 'head')] != [run.get('pr'), run.get('head')]]
        if moved:
            locate(run, moved)
            save(out, doc)
    answers = {}
    for a in answer_records(run):
        text = str(a.get('answer') or '')
        answers.setdefault(a.get('time'), []).append({'html': markdown(text), 'drafts': [
            {'text': d, 'hash': hashlib.sha1(' '.join(d.split()).encode()).hexdigest()[:8]} for d in drafts(text)]})
    # Comments made elsewhere, read only, from each plugin that has them: github_pr.py's on the PR (with
    # --pr), gdoc_comments.py's in the Google Docs (with --comments).
    # A refresh named for one source (its ↻) goes to that source's plugin (its SOURCE) alone.
    remotes = [r for r in (m.remarks(run, out, refresh if not source or getattr(m, 'SOURCE', None) == source else None)
                           for m in docdiff.PLUGINS if hasattr(m, 'remarks')) if r]
    for r in remotes:
        for c in r.get('comments', []):
            c['html'] = markdown(c['body'])
    return {'comments': doc['comments'], 'proposals': doc['proposals'], 'threads': doc['threads'], 'remotes': remotes,
            'revisits': [{**{k: r.get(k) for k in ('time', 'file', 'section', 'quote', 'view', 'prompt', 'key',
                                                    'thread', 'revises', 'remote', 'mode', 'weigh', 'comment')},
                          'answers': answers.get(r.get('time'), []), **doc['revisits'].get(r.get('time'), {})}
                         for r in revisit_records(run)],
            # Whether Claude is watching each shared store, for a reply request sent there.
            'stores': {src: {'watched': (d / 'watcher').exists() and time.time() - (d / 'watcher').stat().st_mtime < 5}
                       for src, d in stores(run).items()}}


def push(run, out, req):
    """Sends the comments req['push'] names, in that order, to the PR as your pending review, through the
    plugin that pushes (github_pr.py's push()), or with req['dry'] only says what that would do; keeps
    what each became there in its pushed, and the review in push. Returns what get() does, with the
    push's own answer as pushed."""
    m = next((m for m in docdiff.PLUGINS if hasattr(m, 'push')), None)
    if not m or not run.get('pr') or not run.get('head'):
        return {**state(run), 'pushed': {'error': 'nothing to push to: this page is not a PR\'s (--pr)'}}
    ids = {str(i): n for n, i in enumerate(req['push'])}
    doc = load(out)
    cs = sorted((c for c in doc['comments'] if c['id'] in ids), key=lambda c: ids[c['id']])
    got = m.push(run, [{'id': c['id'], 'body': c['body'], 'pushed': c.get('pushed'), 'quote': c.get('quote'),
                        'where': ' · '.join(filter(None, [(c.get('github') or {}).get('path') or c['file'], c.get('section')])),
                        **{k: (c.get('github') or {}).get(k) for k in ('path', 'line', 'side')}} for c in cs],
                 doc.get('push'), bool(req.get('dry')))
    if not req.get('dry') and (got['pushed'] or got['review']):
        with lock:
            doc = load(out)
            for c in doc['comments']:
                if c['id'] in got['pushed']:
                    c['pushed'] = {**got['pushed'][c['id']], 'time': now()}
            if got['review']:
                doc['push'] = {**got['review'], 'url': got['url'], 'time': now()}
            save(out, doc)
    return {**state(run), 'pushed': got}


def counts(run):
    """For the index of reviews, by who sees them, as the page's header counts them (tallies()): your
    comments not posted (drafts, where a source takes posts), the open threads from elsewhere with a place in
    the doc and your comments posted there, and the open threads of revisits, grouped as the page groups
    them (threads()): by the thread a revisit goes on in, its change, or its quote. Comments from elsewhere
    are the copy the page read last (remarks() with no refresh): no request for them."""
    out, ts = pathlib.Path(run['out']), []
    doc = load(out)
    remotes = [r for r in (m.remarks(run, out) for m in docdiff.PLUGINS if hasattr(m, 'remarks')) if r]
    posted, on = set(), collections.Counter()
    for r in remotes:
        cs, me = r.get('comments') or [], r.get('me')
        by, told = {c['id']: c for c in cs}, set()
        mine = [g for g in cs if not me or g.get('author') == me]
        for c in doc['comments'] if r.get('posting') else []:
            g = next((g for g in mine if same(g.get('body'), c.get('body'))), None)
            if g:
                posted.add(c['id'])
                on[r['source']] += not root(g, by).get('resolved')
                if me:
                    told.add(g['id'])
        roots = collections.defaultdict(list)
        for c in cs:
            roots[root(c, by)['id']].append(c)
        on[r['source']] += sum(1 for i, ms in roots.items() if by[i].get('file') and not by[i].get('resolved')
                               and not any(c['id'] in told for c in ms))
    for r in (r for r in records(out / 'revisit.jsonl') if not r.get('remote')):
        t = next((t for t in ts if t[0].get('file') == r.get('file') and any(
            r.get('thread') == m.get('time') or r.get('key') and m.get('key') == r.get('key') or m.get('quote') == r.get('quote')
            for m in t)), None)
        t.append(r) if t else ts.append([r])
    n, k = len(doc['comments']) - len(posted), sum(1 for t in ts if not all(doc['revisits'].get(m.get('time'), {}).get('resolved') for m in t))
    what, mine = ('question' if run.get('head') else 'revisit'), ('draft' if any(r.get('posting') for r in remotes) else 'comment')
    return [w for w in (n and f'{n} {mine}{"s" * (n != 1)}', *(f'{g} on {src}' for src, g in on.items() if g),
                        k and f'{k} open {what}{"s" * (k != 1)}') if w]


def same(a, b):
    """Whether two comments say the same, spacing aside, or one holds the other (20 characters at least), as
    the page finds a comment of yours posted (same())."""
    a, b = ' '.join(str(a or '').split()), ' '.join(str(b or '').split())
    return bool(a and b and (a == b or len(a) >= 20 and a in b or len(b) >= 20 and b in a))


def root(c, by):
    """The first comment of the thread c is in, following reply_to."""
    while c.get('reply_to') in by:
        c = by[c['reply_to']]
    return c


def stores(run):
    """Each source's shared store of drafted replies, {source: dir}, from its plugin (store(), as
    gdoc_comments.py's): replies drafted for its comments there are seen by every page that shows them."""
    return {m.SOURCE: d for m in docdiff.PLUGINS if hasattr(m, 'store') and hasattr(m, 'SOURCE') and (d := m.store(run))}


def revisit_records(run):
    """The page's revisits, then each store's reply requests; a reply request to a source with a store is read
    from the store alone, so one sent before the store existed is not counted twice."""
    out, ss = pathlib.Path(run['out']), stores(run)
    own = [r for r in records(out / 'revisit.jsonl') if str(r.get('remote') or '').partition(':')[0] not in ss]
    return own + [r for d in ss.values() for r in records(d / 'revisit.jsonl')]


def answer_records(run):
    """Claude's answers, the page's and each store's, each once."""
    seen, out = set(), []
    for a in [*records(pathlib.Path(run['out']) / 'answers.jsonl'), *(x for d in stores(run).values() for x in records(d / 'answers.jsonl'))]:
        k = (a.get('time'), a.get('answer'))
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out


def locate(run, cs):
    """Sets each comment's github to where a plugin's place() finds its text in the PR's files: {pr,
    head, path, line, side, href}, with no path, line or href where none does."""
    finder = next((m for m in docdiff.PLUGINS if hasattr(m, 'place')), None)
    files = {}
    if finder and run.get('pr'):
        repo = pathlib.Path(run['repo'])
        files = {new or old: (old, new) for old, new in docdiff.files_for(repo, run['base'], run.get('head'), run['paths'])[0]}
    for c in cs:
        got = c['file'] in files and finder.place(run, *files[c['file']], c['side'], c['before'] or '',
                                                  f"{c['quote'] or ''} {c['after'] or ''}")
        c['github'] = {'pr': run.get('pr'), 'head': run.get('head'), 'side': c['side'],
                       **(dict(zip(('path', 'line', 'href'), got)) if got else {})}


def load(out):
    f = out / 'review.json'
    doc = json.loads(f.read_text()) if f.exists() else {}
    return {'comments': doc.get('comments', []), 'revisits': doc.get('revisits', {}), 'proposals': doc.get('proposals', {}),
            'threads': doc.get('threads', {}),
            **({'push': doc['push']} if doc.get('push') else {})}


def save(out, doc):
    # Written whole and then moved in, so a crash never leaves half a file.
    f = out / 'review.json'
    f.with_suffix('.tmp').write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    f.with_suffix('.tmp').replace(f)


def records(path):
    """A .jsonl file's records; a line that does not parse is skipped."""
    out = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def drafts(text):
    """An answer's drafted comments: its blockquotes, as Markdown without the quote marks."""
    out, inside = [], False
    for line in text.splitlines():
        m = re.match(r' {0,3}> ?(.*)', line)
        if m and not inside:
            out.append([])
        if m:
            out[-1].append(m.group(1))
        inside = bool(m)
    return [d for d in ('\n'.join(q).strip() for q in out) if d]


def markdown(text):
    """An answer rendered, raw HTML in it shown as text, and no javascript: links. pandoc's Markdown, as
    its gfm reader keeps raw HTML even without raw_html; with GFM's bare links, straight quotes, and lists,
    quotes and headings right after a line of text."""
    if text not in rendered:
        r = subprocess.run(['pandoc', '-f', 'markdown-raw_html-raw_tex-raw_attribute-smart-blank_before_blockquote'
                            '-blank_before_header+autolink_bare_uris+lists_without_preceding_blankline', '-t', 'html'],
                           input=text, capture_output=True, text=True)
        rendered[text] = (re.sub(r'\shref="\s*javascript:[^"]*"', '', r.stdout, flags=re.I) if not r.returncode
                          else f'<pre>{html.escape(text)}</pre>')
    return rendered[text]


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')
