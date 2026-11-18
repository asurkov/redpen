// The review page: the session's files, the one open in the diff editor, and what is done to them.
import { DiffView, button, loaded } from './editor.js';

const session = location.pathname.split('/').at(-2);
const api = `../../api/${session}`;
const byId = id => document.getElementById(id);

// Each view shows the changes between two of a file's three texts, and what can be done to them:
// [action, label, key] for a change, the key where the page's key does it to the current change.
const views = {
  pending: {
    left: 'base',
    right: 'disk',
    changeActions: [['accept', 'Accept', 'a'], ['reject', 'Reject']],
    fileActions: [['reject', 'Reject file'], ['accept', 'Accept file', 'main']],
    none: 'Nothing is pending: every change is accepted or rejected.',
  },
  accepted: {
    left: 'orig',
    right: 'base',
    changeActions: [['unaccept', 'Unaccept', 'a']],
    fileActions: [['unaccept', 'Unaccept file']],
    none: 'Nothing is accepted yet.',
  },
  all: {
    left: 'orig',
    right: 'disk',
    changeActions: [],
    fileActions: [],
    none: 'The session changed no file.',
  },
};
const doneWords = { accept: 'Accepted', reject: 'Rejected', unaccept: 'Unaccepted' };
// Where the page keeps the last rebase of the review it told of.
const toldRebase = `redpenRebase:${session}`;
const steps = { n: 'next', j: 'next', 'Alt+ArrowDown': 'next', p: 'previous', k: 'previous', 'Alt+ArrowUp': 'previous' };

const state = {
  view: 'pending',
  files: [],
  comments: [],
  // The comments with what followed them, Claude's answers too: what the editor and the strip show.
  threads: [],
  // Whether Claude's command waits for comments now.
  isWatched: false,
  // The thread last stepped to, by its id.
  thread: null,
  // The open file: its key, what the server sent for it, its signature then, and whether the editor
  // shows its two texts.
  key: null,
  opened: null,
  signature: null,
  isText: false,
  areOthersOpen: false,
  // The list's folders folded by hand, by their paths with the closing slash.
  closedFolders: new Set(),
  // When the review was last carried over to another base commit, as the server said it.
  rebase: null,
};
let diffView;
// One request after another: a poll never reads between an action and its redraw.
let chain = Promise.resolve();
const queued = work => (chain = chain.then(work, work));

async function asked(path, body) {
  const response = await fetch(api + path, body && {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(answer.error || `The server answered ${response.status}.`);
  }
  return answer;
}

const listed = () => state.files.filter(file => file[state.view]);
const nameOf = path => path.slice(path.lastIndexOf('/') + 1);

// The folder all the work's files are under, with its closing slash.
function workRoot() {
  const paths = state.files.filter(file => !file.isOther).map(file => file.path);
  if (!paths.length) {
    return '';
  }
  let root = paths[0].slice(0, paths[0].lastIndexOf('/') + 1);
  while (root && !paths.every(path => path.startsWith(root))) {
    root = root.slice(0, root.lastIndexOf('/', root.length - 2) + 1);
  }
  return root;
}

const shortened = (file, root) => (!file.isOther && root && file.path.startsWith(root) ? file.path.slice(root.length) : file.path);

// The list's order, the tree's: in each folder its folders, then its files, each by name; Claude's own
// and temporary files after the work's.
function treeOrder(a, b) {
  if (a.isOther !== b.isOther) {
    return a.isOther - b.isOther;
  }
  const left = a.path.split('/');
  const right = b.path.split('/');
  // The first place where they part, or where one of them reaches its file's name.
  let at = 0;
  while (left[at] === right[at] && at < left.length - 1 && at < right.length - 1) {
    at++;
  }
  return (at === left.length - 1) - (at === right.length - 1) || left[at].localeCompare(right[at]);
}

// The files as a tree of their folders under root, in the files' order. A folder holding one folder
// and no file is one row with it: "tests/www/unit".
function treeOf(files, root) {
  const folderAt = (path, name) => ({ path, name, folders: new Map(), files: [] });
  const top = folderAt(root, '');
  for (const file of files) {
    let folder = top;
    for (const name of file.path.slice(root.length).split('/').slice(0, -1)) {
      if (!folder.folders.has(name)) {
        folder.folders.set(name, folderAt(`${folder.path}${name}/`, name));
      }
      folder = folder.folders.get(name);
    }
    folder.files.push(file);
  }
  const joined = folder => {
    let row = folder;
    while (!row.files.length && row.folders.size === 1) {
      const [only] = row.folders.values();
      row = { ...only, name: `${row.name}/${only.name}` };
    }
    return { ...row, folders: [...row.folders.values()].map(joined) };
  };
  return { ...top, folders: [...top.folders.values()].map(joined) };
}

const filesUnder = folder => [...folder.files, ...folder.folders.flatMap(filesUnder)];

// What the page takes up again after loading itself: the view, the file and the line at the top.
const resumed = 'redpenResumed';

// Loads the page again, as its code changed, unless something typed would be lost; says whether.
function loadAgain() {
  if ([...document.querySelectorAll('textarea')].some(input => input.value.trim())) {
    return false;
  }
  try {
    sessionStorage.setItem(resumed, JSON.stringify({ at: location.pathname, view: state.view, key: state.key, line: diffView.topLine() }));
  } catch {
    // Without the storage the page opens as it does the first time.
  }
  location.reload();
  return true;
}

// Takes up what loadAgain() left, once.
async function resume() {
  let left = null;
  try {
    left = JSON.parse(sessionStorage.getItem(resumed));
    sessionStorage.removeItem(resumed);
  } catch {
    return;
  }
  if (left?.at !== location.pathname || !views[left.view] || !state.files.some(file => file.key === left.key)) {
    return;
  }
  state.view = left.view;
  drawViews();
  await show(left.key, left.line || undefined);
}

// Reads the file list again and redraws what changed; opens another file when the open one left
// the view, the next one in the list.
async function refresh() {
  const order = listed().map(file => file.key);
  const listing = await asked('/files');
  state.files = listing.files.sort(treeOrder);
  state.comments = listing.comments;
  state.threads = listing.threads;
  state.isWatched = listing.isWatched;
  diffView.isWatched = listing.isWatched;
  state.isScanning = listing.isScanning;
  if (state.page && listing.page && listing.page !== state.page && loadAgain()) {
    return;
  }
  state.page = state.page || listing.page;
  tellRebase(listing.rebased);
  byId('title').textContent = listing.title;
  document.title = `${listing.title} · Claude Edits`;
  drawViews();
  drawProgress();
  drawWaiting();
  const files = listed();
  const open = files.find(file => file.key === state.key);
  if (open) {
    drawList();
    if (open.signature !== state.signature) {
      await show(open.key, 'kept');
    } else {
      diffView.mark(state.threads.filter(thread => thread.key === state.key));
    }
    return;
  }
  const index = order.indexOf(state.key);
  const next = order.slice(index + 1).concat(order.slice(0, Math.max(index, 0))).find(key => files.some(file => file.key === key));
  await show(next || files[0]?.key || null);
}

// Says once that the review was carried over to another base commit, to a page open then or
// opened since.
function tellRebase(rebased) {
  if (!rebased || rebased.time === state.rebase) {
    return;
  }
  state.rebase = rebased.time;
  try {
    if (localStorage.getItem(toldRebase) === rebased.time) {
      return;
    }
    localStorage.setItem(toldRebase, rebased.time);
  } catch {
    // Without the storage the page says it each time it loads.
  }
  toast(`The branch was rebased: the review now compares with ${rebased.to.slice(0, 10)}`);
}

async function show(key, place) {
  const file = state.files.find(candidate => candidate.key === key);
  const view = views[state.view];
  // A file stepped to opens the folders it is in.
  for (const folder of state.closedFolders) {
    if (file && key !== state.key && file.path.startsWith(folder)) {
      state.closedFolders.delete(folder);
    }
  }
  state.key = file ? key : null;
  state.opened = file ? await asked(`/file/${key}`) : null;
  state.signature = file?.signature;
  drawList();
  byId('fileBar').hidden = !file;
  byId('work').classList.toggle('bare', !file);
  byId('note').textContent = file ? '' : view.none;
  if (!file) {
    return;
  }
  const root = workRoot();
  const left = state.opened.texts[view.left];
  const right = state.opened.texts[view.right];
  const isText = left.text !== null && right.text !== null;
  state.isText = isText;
  byId('filePath').textContent = `‎${shortened(file, root)}`;
  byId('filePath').title = file.path;
  byId('fileCounts').replaceChildren(...counts(file[state.view]));
  byId('fileActions').replaceChildren();
  for (const [action, label, kind] of view.fileActions) {
    button(byId('fileActions'), label, () => act(action, null), kind);
  }
  byId('work').classList.toggle('bare', !isText);
  byId('note').textContent = isText ? '' : 'Not a text the page can show: too large, or not UTF-8. The file as a whole can still be accepted or rejected.';
  if (isText) {
    diffView.show({
      path: file.path,
      left: left.text,
      right: right.text,
      changeActions: view.changeActions,
      canSplice: DiffView.splits(left.text) && DiffView.splits(right.text),
      sides: { left: view.left, right: view.right },
      threads: state.threads.filter(thread => thread.key === key),
      place,
    });
  }
}

// What an action was on: a change, the selected lines of some (a list), or the whole file.
function whatOf(change) {
  if (Array.isArray(change)) {
    return 'the selected lines';
  }
  return change ? 'a change' : 'the file';
}

// Carries out an action on a change of the open file, on the selected lines of its changes (a
// list), or on the whole file with no change named.
function act(action, change) {
  const view = views[state.view];
  const { key, opened } = state;
  return queued(async () => {
    // Shown anew since it was asked for, after an earlier action say: the change's lines were read
    // from texts the file no longer has.
    if (opened !== state.opened) {
      return;
    }
    const name = nameOf(opened.path);
    try {
      const expected = { left: opened.texts[view.left].sha, right: opened.texts[view.right].sha };
      const entry = await asked('/apply', { key, action, change, expected });
      toast(`${doneWords[action]} ${change ? `${whatOf(change)} in` : ''} ${name}`, 'Undo', () => flip(entry.id));
    } catch (error) {
      toast(error.message);
      // The file is not what was shown: show it as it is.
      state.signature = null;
    }
    await refreshAll();
  });
}

function flip(id) {
  return queued(async () => {
    try {
      const entry = await asked('/flip', { id });
      toast(`${entry.isUndone ? 'Undid' : 'Redid'}: ${doneWords[entry.action].toLowerCase()} in ${nameOf(entry.path)}`);
    } catch (error) {
      toast(error.message);
    }
    await refreshAll();
  });
}

// Steps to the next or previous change; past the open file's last or first one, into the file
// after or before it in the list, round the list's ends.
function step(direction) {
  return queued(async () => {
    const files = listed();
    if (!state.key || !files.length || (state.isText && diffView.go(direction))) {
      return;
    }
    const index = files.findIndex(file => file.key === state.key);
    const neighbour = files[(index + (direction === 'next' ? 1 : files.length - 1)) % files.length];
    await show(neighbour.key, direction === 'next' ? undefined : 'last');
  });
}

// The view that shows a line of the file's text on disk: the one open if it does, else Pending, or
// All for a file with nothing pending.
function diskView(file) {
  return views[state.view].right === 'disk' && file[state.view] ? state.view : file.pending ? 'pending' : 'all';
}

// Opens the change Claude made in answer to a comment: its file at its line.
function open({ key, line }) {
  return queued(async () => {
    const file = state.files.find(candidate => candidate.key === key);
    if (!file) {
      return toast('The page no longer lists that file.');
    }
    state.view = diskView(file);
    drawViews();
    await show(key, line || undefined);
  });
}

// The threads in the list's order, a file's by their lines.
function orderedThreads() {
  const rank = new Map(state.files.map((file, index) => [file.key, index]));
  return [...state.threads].sort((a, b) => (rank.get(a.key) ?? Infinity) - (rank.get(b.key) ?? Infinity) || a.start - b.start);
}

// Shows a thread: its file at its lines, in a view that has the text it is on.
async function showThread(thread) {
  const file = state.files.find(candidate => candidate.key === thread.key);
  state.thread = thread.id;
  diffView.currentThread = thread.id;
  if (!file) {
    return toast('The page no longer lists the file of that thread.');
  }
  const isShown = side => side === views[state.view].right && file[state.view];
  if (!isShown(thread.side) && !isShown(thread.anchorSide)) {
    // A thread on removed lines stands in the text they were removed from.
    const side = thread.side === 'orig' ? thread.anchorSide : thread.side;
    state.view = side === 'base' ? 'accepted' : diskView(file);
    drawViews();
  }
  const right = views[state.view].right;
  await show(thread.key, thread.side === right ? thread.start : thread.anchorSide === right ? thread.anchor : undefined);
}

// Steps to the next or previous thread, through every file's, round the ends.
function stepThread(direction) {
  return queued(async () => {
    const threads = orderedThreads();
    if (!threads.length) {
      return toast('No thread is open.');
    }
    const index = threads.findIndex(thread => thread.id === state.thread);
    const turn = direction === 'next' ? 1 : threads.length - 1;
    await showThread(threads[index < 0 ? (direction === 'next' ? 0 : turn) : (index + turn) % threads.length]);
  });
}

// The page's keys: n or j and p or k step through the changes, as Alt+↓ and Alt+↑ do; a carries out
// the current change's own action; r opens a comment on the selected text, or else on that change;
// t and Shift+t step through the threads.
function takeKey(event) {
  const key = (event.altKey ? 'Alt+' : '') + event.key;
  // A letter typed into a field is the field's; the editor's own text is no such field.
  const isTyped = !event.altKey && event.target.closest?.('input, textarea') && !diffView.hasTextFocus();
  if (event.metaKey || event.ctrlKey || event.isComposing || isTyped || (!state.key && !state.threads.length)) {
    return;
  }
  if (key === 't' || key === 'T') {
    stepThread(key === 't' ? 'next' : 'previous');
  } else if (!state.key) {
    return;
  } else if (steps[key]) {
    step(steps[key]);
  } else if (key === 'a') {
    // One press, one change: a key held down does not go on accepting.
    if (state.isText && !event.repeat) {
      diffView.actByKey(key);
    }
  } else if (key === 'r') {
    if (!state.isText || !diffView.startComment()) {
      toast('Select text, or step to a change, then press r.');
    }
  } else {
    return;
  }
  // The editor must not take the key for its own.
  event.preventDefault();
  event.stopPropagation();
}

async function refreshAll() {
  await refresh();
  if (!byId('history').hidden) {
    await drawJournal();
  }
}

function counts(count) {
  if (!count) {
    return [];
  }
  if (count.isBinary) {
    return [span('state', 'binary')];
  }
  return [span('plus', `+${count.added}`), ' ', span('minus', `−${count.removed}`)];
}

function span(kind, text) {
  const made = document.createElement('span');
  made.className = kind;
  made.textContent = text;
  return made;
}

function drawViews() {
  for (const tab of document.querySelectorAll('#views button')) {
    tab.classList.toggle('chosen', tab.dataset.view === state.view);
    tab.querySelector('.count').textContent = state.files.filter(file => file[tab.dataset.view]).length || '';
  }
}

// How far the review is, in the work's own files: how many have nothing pending, of those the
// review has; and the threads, with how many Claude answered last and how many wait for its answer.
function drawProgress() {
  const files = state.files.filter(file => !file.isOther && (file.pending || file.accepted));
  const done = files.filter(file => !file.pending).length;
  const counted = files.length ? `${done} of ${files.length} reviewed` : '';
  byId('progress').title = `${counted}: the files with nothing pending, of those the review has`;
  byId('progress').textContent = [counted, state.isScanning ? 'looking for changed files…' : ''].filter(Boolean).join(' · ');
  const answered = state.threads.filter(thread => thread.entries.at(-1).by === 'claude').length;
  byId('threadsButton').hidden = !state.threads.length;
  const awaited = state.threads.filter(thread => thread.entries.at(-1).by === 'user').length;
  byId('threadsButton').textContent = `${state.threads.length} thread${state.threads.length === 1 ? '' : 's'}${answered ? `, ${answered} answered` : ''}${awaited ? `, ${awaited} waiting` : ''}`;
}

// What the list and the waiting comments were last drawn from: a poll that found nothing new
// leaves them alone, with the pointer's place and the open fold. And the file the list last brought
// into sight.
const drawn = { list: '', waiting: '', chosen: null };

function drawList() {
  const drawing = JSON.stringify([state.view, state.key, listed().map(file => [file.key, file[state.view], file.isNew, file.isGone, file.isUncarried, !file.pending, !file.accepted])]);
  if (drawing === drawn.list) {
    return;
  }
  drawn.list = drawing;
  const fileRow = (file, depth) => {
    const made = document.createElement('button');
    made.className = `file${file.key === state.key ? ' chosen' : ''}`;
    made.style.setProperty('--depth', depth);
    made.title = file.path;
    // ✓ with nothing of the file pending, ◐ with a part of it accepted.
    const mark = made.appendChild(span(`mark${file.pending ? '' : ' done'}`, !file.pending ? '✓' : file.accepted ? '◐' : ''));
    mark.title = !file.pending ? 'Nothing pending' : file.accepted ? 'Accepted in part' : '';
    made.append(span('name', nameOf(file.path)), span('spring', ''));
    if (file.isGone || file.isNew) {
      made.append(span('state', file.isGone ? 'gone' : 'new'));
    }
    if (file.isUncarried) {
      made.appendChild(span('state again', 'rebased')).title = 'The branch was rebased, and what was accepted in this file did not fit the new base: nothing of it is accepted now, look at it again';
    }
    made.append(...counts(file[state.view]));
    made.addEventListener('click', () => queued(() => show(file.key)));
    return made;
  };
  // A folder's row folds what is in it. It says how far the files in it are, ✓ and ◐ as a file's
  // mark, and, folded, their lines added and removed.
  const folderRow = (folder, depth) => {
    const fold = document.createElement('details');
    fold.className = 'folder';
    fold.open = !state.closedFolders.has(folder.path);
    fold.addEventListener('toggle', () => (fold.open ? state.closedFolders.delete(folder.path) : state.closedFolders.add(folder.path)));
    const head = fold.appendChild(document.createElement('summary'));
    head.style.setProperty('--depth', depth);
    head.title = folder.path;
    const under = filesUnder(folder);
    const isDone = under.every(file => !file.pending);
    const isBegun = under.some(file => file.accepted || !file.pending);
    head.append(span('name', folder.name));
    head.appendChild(span(`mark${isDone ? ' done' : ''}`, isDone ? '✓' : isBegun ? '◐' : '')).title =
      isDone ? 'Nothing pending in it' : isBegun ? 'Accepted in part' : '';
    head.append(span('spring', ''));
    const texts = under.map(file => file[state.view]).filter(count => count && !count.isBinary);
    const total = side => texts.reduce((sum, count) => sum + count[side], 0);
    head.append(...counts(texts.length ? { added: total('added'), removed: total('removed') } : null));
    fold.append(...rowsOf(folder, depth + 1));
    return fold;
  };
  const rowsOf = (folder, depth) => [...folder.folders.map(child => folderRow(child, depth)), ...folder.files.map(file => fileRow(file, depth))];
  const files = listed();
  const others = files.filter(file => file.isOther);
  const list = byId('list');
  list.replaceChildren(...rowsOf(treeOf(files.filter(file => !file.isOther), workRoot()), 0));
  if (!files.length) {
    list.append(span('none', views[state.view].none));
  }
  if (others.length) {
    const fold = list.appendChild(document.createElement('details'));
    fold.className = 'others';
    fold.open = state.areOthersOpen || others.some(file => file.key === state.key);
    fold.addEventListener('toggle', () => (state.areOthersOpen = fold.open));
    fold.appendChild(document.createElement('summary')).textContent = `${others.length} other: Claude's own and temporary`;
    // Their folders from the top, as they are far from the work's.
    const fromTop = treeOf(others, '/');
    fold.append(...rowsOf({ ...fromTop, folders: fromTop.folders.map(folder => ({ ...folder, name: `/${folder.name}` })) }, 0));
  }
  // A file stepped to comes into sight; a redraw for anything else leaves the list where it was scrolled.
  if (state.key !== drawn.chosen) {
    drawn.chosen = state.key;
    list.querySelector('.file.chosen')?.scrollIntoView({ block: 'nearest' });
  }
}

async function drawJournal() {
  const entries = await asked('/journal');
  const rows = entries.map(entry => {
    const row = document.createElement('li');
    if (entry.action === 'rebase') {
      const what = row.appendChild(span('what', `Rebased: compares with ${entry.to.slice(0, 10)}, not ${entry.from.slice(0, 10)}`));
      what.append(span('when', new Date(entry.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
      return row;
    }
    row.className = entry.isUndone ? 'undone' : '';
    const what = row.appendChild(span('what', `${doneWords[entry.action]} ${whatOf(entry.change)}`));
    what.append(span('where', nameOf(entry.path)), span('when', new Date(entry.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
    const flipButton = button(row, entry.isUndone ? 'Redo' : 'Undo', () => flip(entry.id));
    flipButton.disabled = !entry.canFlip;
    flipButton.title = entry.canFlip ? '' : 'The file changed since';
    return row;
  });
  if (!rows.length) {
    rows.push(Object.assign(document.createElement('li'), { className: 'none', textContent: 'Nothing accepted or rejected yet.' }));
  }
  byId('journal').replaceChildren(...rows);
}

// The strip at the bottom: how many threads there are and how many of them wait for Claude; and,
// opened, the threads of every file, each a way to its line.
function drawWaiting() {
  const { comments, threads, isWatched } = state;
  const drawing = JSON.stringify({ comments, threads, isWatched });
  if (drawing === drawn.waiting) {
    return;
  }
  drawn.waiting = drawing;
  const counted = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;
  byId('waiting').hidden = !threads.length;
  const awaited = threads.filter(thread => thread.entries.at(-1).by === 'user').length;
  byId('waitingButton').hidden = !threads.length;
  byId('waitingButton').textContent = [counted(threads.length, 'thread'), awaited ? `${awaited} waiting for Claude` : '',
    comments.length && !isWatched ? 'Claude takes them with your next message in the chat' : ''].filter(Boolean).join(' · ');
  byId('waitingList').replaceChildren(...threads.map(thread => {
    const row = document.createElement('li');
    const last = thread.entries.at(-1);
    const lines = thread.start === thread.end ? thread.start : `${thread.start}–${thread.end}`;
    row.append(span('where', `${nameOf(thread.path)}:${lines}`));
    row.append(span(`state ${last.by}`, last.by === 'claude' ? 'Claude' : { draft: 'not sent', sent: 'sent', taken: 'Claude answers' }[last.state]));
    const text = button(row, last.text, () => queued(() => showThread(thread)));
    text.className = 'text';
    if (thread.entries.length === 1 && last.state === 'draft') {
      button(row, '✕', () => uncomment(last.id), 'quiet').title = 'Delete the comment';
    }
    return row;
  }));
}

function comment(target) {
  return queued(async () => {
    try {
      await asked('/comment', { key: state.key, ...target });
    } catch (error) {
      toast(error.message);
    }
    await refresh();
  });
}

// Deletes a comment not sent yet.
function uncomment(id) {
  return queued(async () => {
    try {
      await asked('/uncomment', { id });
    } catch (error) {
      toast(error.message);
    }
    await refresh();
  });
}

// Takes a thread off the page.
function resolve(id) {
  return queued(async () => {
    try {
      await asked('/resolve', { id });
    } catch (error) {
      toast(error.message);
    }
    await refresh();
  });
}

let toastTimer;
function toast(text, label, act) {
  const node = byId('toast');
  node.replaceChildren(text.replace(/\s+/g, ' '));
  if (label) {
    button(node, label, () => {
      node.hidden = true;
      act();
    });
  }
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 7000);
}

// The splitter sizes the list: its width beside the editor, or its height over the editor in a
// narrow pane. The browser keeps the two sizes, for every session's page.
function sizeListBySplitter() {
  const splitter = byId('splitter');
  const frame = splitter.parentElement;
  const sizeName = () => (getComputedStyle(frame).flexDirection === 'column' ? '--listHeight' : '--listWidth');
  const keep = (name, size) => {
    try {
      size ? localStorage.setItem(`redpen${name}`, size) : localStorage.removeItem(`redpen${name}`);
    } catch {
      // A browser that keeps nothing: the size lasts until the page is loaded again.
    }
  };
  for (const name of ['--listWidth', '--listHeight']) {
    try {
      frame.style.setProperty(name, localStorage.getItem(`redpen${name}`) || '');
    } catch {
      // The same: the list has its own size.
    }
  }
  splitter.addEventListener('pointerdown', event => {
    event.preventDefault();
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add('dragged');
  });
  splitter.addEventListener('pointermove', event => {
    if (!splitter.hasPointerCapture(event.pointerId)) {
      return;
    }
    const box = frame.getBoundingClientRect();
    const [at, whole, least] = sizeName() === '--listHeight'
      ? [event.clientY - box.top, box.height, 60] : [event.clientX - box.left, box.width, 140];
    frame.style.setProperty(sizeName(), `${Math.round(Math.max(least, Math.min(at, whole * 0.7)))}px`);
  });
  splitter.addEventListener('lostpointercapture', () => {
    splitter.classList.remove('dragged');
    keep(sizeName(), frame.style.getPropertyValue(sizeName()));
  });
  splitter.addEventListener('dblclick', () => {
    frame.style.removeProperty(sizeName());
    keep(sizeName(), '');
  });
}

async function main() {
  await loaded();
  diffView = new DiffView(byId('editor'), { act, comment, uncomment, resolve, open });
  diffView.openable = () => state.files.map(file => file.key);
  byId('threadsButton').addEventListener('click', () => stepThread('next'));
  byId('listButton').addEventListener('click', () => document.body.classList.toggle('listHidden'));
  sizeListBySplitter();
  for (const tab of document.querySelectorAll('#views button')) {
    tab.addEventListener('click', () => queued(async () => {
      state.view = tab.dataset.view;
      drawViews();
      // The same file in the other view, if it has changes there.
      await show(listed().some(file => file.key === state.key) ? state.key : listed()[0]?.key || null);
    }));
  }
  byId('previousChange').addEventListener('click', () => step('previous'));
  byId('nextChange').addEventListener('click', () => step('next'));
  byId('historyButton').addEventListener('click', () => queued(async () => {
    byId('history').hidden = !byId('history').hidden;
    if (!byId('history').hidden) {
      await drawJournal();
    }
  }));
  byId('historyClose').addEventListener('click', () => (byId('history').hidden = true));
  byId('waitingButton').addEventListener('click', () => (byId('waitingList').hidden = !byId('waitingList').hidden));
  document.addEventListener('keydown', takeKey, true);
  await queued(refresh);
  await queued(resume);
  setInterval(() => {
    if (!document.hidden) {
      queued(() => refresh().catch(() => {}));
    }
  }, 2000);
}

main().catch(error => (byId('note').textContent = `The page did not start: ${error.message}`));
