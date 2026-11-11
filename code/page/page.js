// The review page: the session's files, the one open in the diff editor, and what is done to them.
import { DiffView, button, loaded } from './editor.js';

const session = location.pathname.split('/')[2];
const api = `/api/${session}`;
const byId = id => document.getElementById(id);

// Each view shows the changes between two of a file's three texts, and what can be done to them.
const views = {
  pending: {
    left: 'base',
    right: 'disk',
    changeActions: [['accept', 'Accept'], ['reject', 'Reject']],
    fileActions: [['reject', 'Reject file'], ['accept', 'Accept file', 'main']],
    none: 'Nothing is pending: every change is accepted or rejected.',
  },
  accepted: {
    left: 'orig',
    right: 'base',
    changeActions: [['unaccept', 'Unaccept']],
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

const state = {
  view: 'pending',
  files: [],
  comments: [],
  // The open file: its key, what the server sent for it, and its signature then.
  key: null,
  opened: null,
  signature: null,
  areOthersOpen: false,
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

// Reads the file list again and redraws what changed; opens another file when the open one left
// the view, the next one in the list.
async function refresh() {
  const order = listed().map(file => file.key);
  const listing = await asked('/files');
  state.files = listing.files.sort((a, b) => a.isOther - b.isOther || a.path.localeCompare(b.path));
  state.comments = listing.comments;
  byId('title').textContent = listing.title;
  document.title = `${listing.title} · Claude Edits`;
  drawViews();
  drawWaiting();
  const files = listed();
  const open = files.find(file => file.key === state.key);
  if (open) {
    drawList();
    if (open.signature !== state.signature) {
      await show(open.key, 'kept');
    } else {
      diffView.mark(state.comments.filter(comment => comment.key === state.key));
    }
    return;
  }
  const index = order.indexOf(state.key);
  const next = order.slice(index + 1).concat(order.slice(0, Math.max(index, 0))).find(key => files.some(file => file.key === key));
  await show(next || files[0]?.key || null);
}

async function show(key, place) {
  const file = state.files.find(candidate => candidate.key === key);
  const view = views[state.view];
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
  byId('filePath').textContent = `‎${shortened(file, root)}`;
  byId('filePath').title = file.path;
  byId('fileCounts').replaceChildren(...counts(file));
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
      comments: state.comments.filter(comment => comment.key === key),
      place,
    });
  }
}

// Carries out an action on a change of the open file, or on the whole file with no change named.
function act(action, change) {
  return queued(async () => {
    const view = views[state.view];
    const { key, opened } = state;
    const name = nameOf(opened.path);
    try {
      const expected = { left: opened.texts[view.left].sha, right: opened.texts[view.right].sha };
      const entry = await asked('/apply', { key, action, change, expected });
      toast(`${doneWords[action]} ${change ? 'a change in' : ''} ${name}`, 'Undo', () => flip(entry.id));
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

async function refreshAll() {
  await refresh();
  if (!byId('history').hidden) {
    await drawJournal();
  }
}

function counts(file) {
  const count = file[state.view];
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

// What the list and the waiting comments were last drawn from: a poll that found nothing new
// leaves them alone, with the pointer's place and the open fold.
const drawn = { list: '', waiting: '' };

function drawList() {
  const drawing = JSON.stringify([state.view, state.key, listed().map(file => [file.key, file[state.view], file.isNew, file.isGone])]);
  if (drawing === drawn.list) {
    return;
  }
  drawn.list = drawing;
  const root = workRoot();
  const row = file => {
    const made = document.createElement('button');
    const path = shortened(file, root);
    made.className = `file${file.key === state.key ? ' chosen' : ''}`;
    made.title = file.path;
    made.append(span('name', nameOf(path)), span('folder', `‎${path.slice(0, Math.max(path.lastIndexOf('/'), 0))}`));
    if (file.isGone || file.isNew) {
      made.append(span('state', file.isGone ? 'gone' : 'new'));
    }
    made.append(...counts(file));
    made.addEventListener('click', () => queued(() => show(file.key)));
    return made;
  };
  const files = listed();
  const others = files.filter(file => file.isOther);
  const list = byId('list');
  list.replaceChildren(...files.filter(file => !file.isOther).map(row));
  if (!files.length) {
    list.append(span('none', views[state.view].none));
  }
  if (others.length) {
    const fold = list.appendChild(document.createElement('details'));
    fold.open = state.areOthersOpen || others.some(file => file.key === state.key);
    fold.addEventListener('toggle', () => (state.areOthersOpen = fold.open));
    fold.appendChild(document.createElement('summary')).textContent = `${others.length} other: Claude's own and temporary`;
    fold.append(...others.map(row));
  }
}

async function drawJournal() {
  const entries = await asked('/journal');
  const rows = entries.map(entry => {
    const row = document.createElement('li');
    row.className = entry.isUndone ? 'undone' : '';
    const what = row.appendChild(span('what', `${doneWords[entry.action]} ${entry.change ? 'a change' : 'the file'}`));
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

// The comments waiting for the next message to Claude.
function drawWaiting() {
  const { comments } = state;
  if (JSON.stringify(comments) === drawn.waiting) {
    return;
  }
  drawn.waiting = JSON.stringify(comments);
  byId('waiting').hidden = !comments.length;
  byId('waitingButton').textContent = `${comments.length} comment${comments.length === 1 ? '' : 's'} for Claude: sent with your next message`;
  byId('waitingList').replaceChildren(...comments.map(comment => {
    const row = document.createElement('li');
    const lines = comment.start === comment.end ? comment.start : `${comment.start}–${comment.end}`;
    row.append(span('where', `${nameOf(comment.path)}:${lines}`));
    const text = button(row, comment.text, () => queued(async () => {
      await show(comment.key);
      if (comment.side === views[state.view].right) {
        diffView.reveal(comment.start);
      }
    }));
    text.className = 'text';
    button(row, '✕', () => queued(async () => {
      await asked('/uncomment', { id: comment.id });
      await refresh();
    }), 'quiet').title = 'Delete the comment';
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
  diffView = new DiffView(byId('editor'), { act, comment });
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
  byId('previousChange').addEventListener('click', () => diffView.go('previous'));
  byId('nextChange').addEventListener('click', () => diffView.go('next'));
  byId('historyButton').addEventListener('click', () => queued(async () => {
    byId('history').hidden = !byId('history').hidden;
    if (!byId('history').hidden) {
      await drawJournal();
    }
  }));
  byId('historyClose').addEventListener('click', () => (byId('history').hidden = true));
  byId('waitingButton').addEventListener('click', () => (byId('waitingList').hidden = !byId('waitingList').hidden));
  document.addEventListener('keydown', event => {
    if (event.altKey && (event.key === 'ArrowDown' || event.key === 'ArrowUp') && state.key) {
      event.preventDefault();
      diffView.go(event.key === 'ArrowDown' ? 'next' : 'previous');
    }
  }, true);
  await queued(refresh);
  setInterval(() => {
    if (!document.hidden) {
      queued(() => refresh().catch(() => {}));
    }
  }, 2000);
}

main().catch(error => (byId('note').textContent = `The page did not start: ${error.message}`));
