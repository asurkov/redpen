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
// The most lines changed, added and removed, the editor is given to compare: a file of 19,000 took
// it 2.5 seconds, and it gives up at 5.
const comparedLines = 20000;
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
  // Whether All shows its diff; else the file as it is, its changes marked in the margin.
  isAllDiffShown: false,
  // The files whose diff the editor gave up on, by key, view and signature; and those whose diff was
  // asked for all the same, by key.
  givenUpFiles: new Set(),
  forcedDiffs: new Set(),
  areOthersOpen: false,
  // Whether the fold of the generated files is open.
  areGeneratedOpen: false,
  // The list's folders folded by hand, by their paths with the closing slash; and of those with no
  // changed file in them, the ones opened by hand.
  closedFolders: new Set(),
  openedFolders: new Set(),
  // The tree: the folder it starts from, as the server said it (none: the changed files' own folder,
  // and nothing else of the folders); what the folders read hold, by their paths ({folders, files,
  // more}); and how many reads found them changed. A folder shows its changed files alone, the rest
  // behind "N more", unless widened or narrowed by hand; with the unchanged files shown, a folder
  // holding a changed file starts widened.
  areUnchangedShown: false,
  // Whether Pending lists the reviewed files too, faded, where they were.
  areReviewedShown: true,
  // The folders gone with the files deleted from them, by their paths with the closing slash.
  goneFolders: new Set(),
  top: null,
  folders: new Map(),
  foldersRead: 0,
  widenedFolders: new Set(),
  narrowedFolders: new Set(),
  // The unchanged file open, by its path, instead of a changed one, and the hash of its text shown.
  plain: null,
  plainSha: null,
  // When the review was last carried over to another base commit, as the server said it.
  rebase: null,
  // The overview Claude wrote, as last read; what it was read for (the overview's and the files'
  // signatures), read again when that changes while the drawer is open; and the block stepped to, by
  // its place among all the blocks.
  outline: null,
  outlineRead: '',
  outlineStamp: '',
  block: -1,
};
// Where the browser keeps whether the list shows the unchanged files, for every review's page.
const keptUnchangedShown = 'redpenUnchangedShown';
// And whether Pending lists the reviewed files.
const keptReviewedHidden = 'redpenReviewedHidden';
let diffView;
// One request after another: a poll never reads between an action and its redraw.
let chain = Promise.resolve();
const queued = work => (chain = chain.then(work, work));
// The poll's reading of the folders while it runs.
let folderReading = null;

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

// The view's files, the generated ones too; and those the keys step through and the list opens one
// after another, without them.
const inView = () => state.files.filter(file => file[state.view]);
const listed = () => inView().filter(file => !file.isGenerated);
// A file reviewed in full: changed, and nothing of it pending.
const isReviewed = file => !file.pending && Boolean(file.all);
// What the list shows: the view's files, and in Pending, with Reviewed on, the reviewed ones too,
// faded; the keys step through the view's alone.
const shownInList = () => state.files.filter(file => file[state.view] || state.view === 'pending' && state.areReviewedShown && isReviewed(file));
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

// The files as a tree of their folders under root, in the tree's order. A folder holding one folder
// and no file is one row with it, "tests/www/unit", its segments each the name, path and hidden count
// of a folder.
// With what folders were read to hold (the full tree), a folder that isWide(path) shows the rest of
// what it holds too, its files as {path, isPlain}, and is a row of its own; another one says how many
// it leaves out (hidden). A folder with no changed file in it shows all it holds.
function treeOf(files, root, listings, isWide = () => false) {
  const folderAt = (path, name) => ({ path, name, folders: new Map(), files: [], more: 0, hidden: 0, isChanged: false, isWide: false });
  const top = folderAt(root, '');
  // The folder holding a path under root, made with those it is in where there are none yet; all of
  // them marked as holding a changed file, for a changed file's path.
  const folderOf = (path, isChanged = false) => {
    let folder = top;
    folder.isChanged ||= isChanged;
    for (const name of path.slice(root.length).split('/').slice(0, -1)) {
      if (!folder.folders.has(name)) {
        folder.folders.set(name, folderAt(`${folder.path}${name}/`, name));
      }
      folder = folder.folders.get(name);
      folder.isChanged ||= isChanged;
    }
    return folder;
  };
  for (const file of files) {
    folderOf(file.path, true).files.push(file);
  }
  const changed = new Set(files.map(file => file.path));
  // The folders read, a folder before those in it: what a folder shows decides which of those in it
  // are in the tree at all.
  const read = [...listings || []].filter(([path]) => path.startsWith(root)).sort(([a], [b]) => a.length - b.length);
  for (const [path, listing] of read) {
    const folder = path === root ? top : path.slice(root.length, -1).split('/').reduce((up, name) => up?.folders.get(name), top);
    if (!folder) {
      continue;
    }
    const plainFiles = listing.files.filter(name => !changed.has(path + name));
    folder.isWide = !folder.isChanged || isWide(path);
    if (folder.isWide) {
      listing.folders.forEach(name => folderOf(`${path}${name}/`));
      folder.files.push(...plainFiles.map(name => ({ path: path + name, isPlain: true })));
      folder.more = listing.more;
    } else {
      folder.hidden = listing.folders.filter(name => !folder.folders.has(name)).length + plainFiles.length + listing.more;
    }
  }
  const byName = (a, b) => a.name.localeCompare(b.name);
  // A folder with what it holds in order, its folders joined with those they hold alone.
  const ordered = folder => ({
    ...folder,
    files: folder.files.map(file => ({ file, name: nameOf(file.path) })).sort(byName).map(({ file }) => file),
    folders: [...folder.folders.values()].sort(byName).map(joined),
  });
  const joined = folder => {
    let row = { ...folder, segments: [{ name: folder.name, path: folder.path, hidden: folder.hidden }] };
    while (!row.isWide && !row.files.length && row.folders.size === 1) {
      const [only] = row.folders.values();
      row = { ...only, name: `${row.name}/${only.name}`, segments: [...row.segments, { name: only.name, path: only.path, hidden: only.hidden }] };
    }
    return ordered(row);
  };
  return ordered(top);
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

// Takes up what loadAgain() left, once; says whether it did.
async function resume() {
  let left = null;
  try {
    left = JSON.parse(sessionStorage.getItem(resumed));
    sessionStorage.removeItem(resumed);
  } catch {
    return false;
  }
  if (left?.at !== location.pathname || !views[left.view] || !state.files.some(file => file.key === left.key)) {
    return false;
  }
  state.view = left.view;
  drawViews();
  await show(left.key, left.line || undefined);
  return true;
}

// Takes the link off the address once it is opened, or passed over: the same link again is then a
// change of the address, and opens it again.
function takeLink() {
  if (location.hash) {
    history.replaceState(null, '', location.pathname + location.search);
  }
}

// Opens what a link into the page names, #<path>[:<line>], the path absolute or from the review's top
// (a range, :<line>-<line>, opens at its first line): a file of the review at the line, in a view that
// has it; a file of the tree it has no change in, as it is.
function openLinked() {
  let target;
  try {
    target = decodeURIComponent(location.hash.slice(1));
  } catch {
    return;
  }
  const [, where, line] = /^(.*?)(?::(\d+)(?:-\d+)?)?$/.exec(target) || [];
  if (!where) {
    return;
  }
  takeLink();
  const root = state.top || workRoot() || '/';
  const path = where.startsWith('/') ? where : `${root}${where.replace(/^\.\//, '')}`;
  const number = line ? Number(line) : undefined;
  const file = state.files.find(candidate => candidate.path === path) || state.files.find(candidate => candidate.path.endsWith(`/${where}`));
  if (file) {
    return open({ key: file.key, line: number });
  }
  if (state.top && path.startsWith(state.top)) {
    return queued(() => openPlain(path, number));
  }
  toast(`The review has no file ${where}.`);
}

// Reads the file list again and redraws what changed; opens another file when the open one left
// the view, the next one in the list.
async function refresh() {
  const order = listed().map(file => file.key);
  const listing = await asked('/files');
  state.files = listing.files.sort(treeOrder);
  state.comments = listing.comments;
  state.threads = listing.threads;
  state.goneFolders = new Set((listing.goneFolders || []).map(path => `${path}/`));
  state.isWatched = listing.isWatched;
  diffView.isWatched = listing.isWatched;
  state.isScanning = listing.isScanning;
  state.outlineStamp = JSON.stringify([listing.outline, listing.files.map(file => file.signature)]);
  if (!byId('overview').hidden && state.outlineStamp !== state.outlineRead) {
    await drawOverview();
  }
  if (state.page && listing.page && listing.page !== state.page && loadAgain()) {
    return;
  }
  state.page = state.page || listing.page;
  tellRebase(listing.rebased);
  byId('title').textContent = listing.title;
  document.title = `${listing.title} · redpen`;
  drawViews();
  drawProgress();
  drawWaiting();
  state.top = listing.top;
  byId('unchangedButton').hidden = !state.top;
  // What the folders hold is read beside the list's drawing and the actions, one read at a time, as
  // it takes a while in a large repository: the list shows the changed files at once, "N more" once
  // read.
  folderReading ||= readFolders().finally(() => {
    folderReading = null;
    queued(drawList);
  });
  if (state.plain) {
    return openPlain(state.plain, 'kept');
  }
  const files = listed();
  const open = inView().find(file => file.key === state.key);
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

// Reads what the full tree's folders hold, at each poll those the list shows open: the top, those on
// the way from it to each changed file down to one folded by hand (to say how many files the narrow
// ones leave out), and those opened or widened by hand. Or, named, just those, and says when they
// cannot be read.
async function readFolders(named) {
  const top = state.top;
  let folders = named;
  if (!top) {
    return;
  }
  if (!folders) {
    folders = new Set([top, ...[...state.openedFolders, ...state.widenedFolders].filter(path => path.startsWith(top))]);
    for (const file of state.files.filter(file => !file.isOther && file.path.startsWith(top))) {
      for (let at = file.path.indexOf('/', top.length); at >= 0; at = file.path.indexOf('/', at + 1)) {
        const folder = file.path.slice(0, at + 1);
        if (state.closedFolders.has(folder)) {
          break;
        }
        folders.add(folder);
      }
    }
  }
  try {
    const read = await asked('/folders', { folders: [...folders] });
    let isChanged = false;
    for (const [path, listing] of Object.entries(read)) {
      isChanged ||= JSON.stringify(listing) !== JSON.stringify(state.folders.get(path));
      state.folders.set(path, listing);
    }
    // The list is drawn again only for what changed: a poll leaves the pointer's place alone.
    state.foldersRead += isChanged ? 1 : 0;
  } catch (error) {
    if (named) {
      toast(error.message);
    }
  }
}

// Widens a folder of the full tree, to show all it holds, opened; or narrows it to its changed files.
function widen(path, isWide) {
  const wasFocused = byId('list').contains(document.activeElement);
  return queued(async () => {
    state.widenedFolders.delete(path);
    state.narrowedFolders.delete(path);
    (isWide ? state.widenedFolders : state.narrowedFolders).add(path);
    if (isWide) {
      state.closedFolders.delete(path);
      if (!state.folders.has(path)) {
        await readFolders([path]);
      }
    }
    drawList();
    // The keys stay on the folder: its name, drawn anew.
    if (wasFocused) {
      [...byId('list').querySelectorAll('.segment')].find(made => made.dataset.path === path)?.focus();
    }
  });
}

// Shows the unchanged files of the folders holding changed ones, or leaves them behind "N more", and
// keeps the choice.
function showUnchanged(isOn) {
  return queued(async () => {
    state.areUnchangedShown = isOn;
    byId('unchangedButton').classList.toggle('chosen', isOn);
    try {
      localStorage.setItem(keptUnchangedShown, isOn ? 'yes' : '');
    } catch {
      // A browser that keeps nothing: the choice lasts until the page is loaded again.
    }
    drawList();
  });
}

// Shows a file the review has no change in, as it is on disk: nothing to accept or comment on there.
// A file of the review's with no change in the view open opens in one where it has some.
function showPlain(path) {
  return queued(() => openPlain(path));
}

// Opens a file reviewed in full where what was done to it shows: in Accepted, where it can be
// unaccepted, or with nothing accepted (rejected, but for lines that stayed), in All.
function openReviewed(file) {
  state.view = file.accepted ? 'accepted' : 'all';
  drawViews();
  return show(file.key);
}

// Lists the reviewed files in Pending, faded, or leaves them out, and keeps the choice.
function showReviewed(isOn) {
  return queued(async () => {
    state.areReviewedShown = isOn;
    byId('reviewedButton').classList.toggle('chosen', isOn);
    try {
      localStorage.setItem(keptReviewedHidden, isOn ? '' : 'yes');
    } catch {
      // A browser that keeps nothing: the choice lasts until the page is loaded again.
    }
    drawList();
  });
}

// Opens a file the review has no change in; with place 'kept', the one open again, at the poll, only
// as its text changed, and the first changed file instead once it is gone.
async function openPlain(path, place) {
  const recorded = state.files.find(file => file.path === path);
  if (recorded) {
    state.view = diskView(recorded);
    drawViews();
    return show(recorded.key);
  }
  let opened;
  try {
    opened = await asked(`/plain?path=${encodeURIComponent(path)}`);
  } catch (error) {
    return place === 'kept' ? show(listed()[0]?.key || null) : toast(error.message);
  }
  if (place === 'kept' && opened.sha === state.plainSha) {
    return drawList();
  }
  const view = views[state.view];
  state.key = null;
  state.opened = null;
  state.signature = null;
  state.plain = path;
  state.plainSha = opened.sha;
  state.isText = opened.text !== null;
  drawList();
  byId('fileBar').hidden = false;
  byId('filePath').textContent = `‎${path.startsWith(state.top) ? path.slice(state.top.length) : path}`;
  byId('filePath').title = path;
  byId('filePath').className = '';
  byId('fileCounts').replaceChildren(span('state', 'unchanged'));
  byId('fileActions').replaceChildren();
  byId('work').classList.toggle('bare', !state.isText);
  drawImages(null);
  byId('note').textContent = state.isText ? '' : 'Not a text the page can show: too large, or not UTF-8.';
  if (state.isText) {
    diffView.show({
      path,
      left: opened.text,
      right: opened.text,
      changeActions: [],
      canSplice: false,
      sides: { left: view.left, right: view.right },
      threads: [],
      place,
      canComment: false,
    });
  }
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
  if (file && key !== state.key) {
    unfold(file.path);
  }
  state.plain = null;
  state.key = file ? key : null;
  // Past so many changed lines the editor stalls the page comparing the texts, or gives up and calls
  // all of them one change: such a file is counted, not compared, and its texts are not even sent,
  // unless its diff was asked for.
  const count = file?.[state.view];
  const changedLines = count && !count.isBinary ? count.added + count.removed : 0;
  const hasGivenUp = state.givenUpFiles.has(`${key} ${state.view} ${file?.signature}`);
  const isTooChanged = Boolean(file) && !state.forcedDiffs.has(key) && (changedLines > comparedLines || hasGivenUp);
  state.opened = file ? await asked(`/file/${key}${isTooChanged ? '?texts=none' : ''}`) : null;
  state.signature = file?.signature;
  drawList();
  byId('fileBar').hidden = !file;
  byId('work').classList.toggle('bare', !file);
  byId('note').textContent = file ? '' : view.none;
  drawImages(null);
  if (!file) {
    return;
  }
  const root = workRoot();
  const left = state.opened.texts[view.left];
  const right = state.opened.texts[view.right];
  const isText = !isTooChanged && left.text !== null && right.text !== null;
  // The editor compares lines, not how they end: a change of the line endings alone is said, not
  // compared, and one beside other changes is named in the file bar.
  const endings = isText ? [endingsOf(left.text), endingsOf(right.text)] : ['', ''];
  const isEndingsOnly = isText && left.text !== right.text && left.text.replace(/\r\n?/g, '\n') === right.text.replace(/\r\n?/g, '\n');
  const isShown = isText && !isEndingsOnly;
  state.isText = isShown;
  // An image, or none on one side: the two side by side.
  const areImages = !isTooChanged && !isText && [left, right].some(side => side.image) && [left, right].every(side => side.image || !side.exists);
  drawImages(areImages ? [[view.left, left], [view.right, right]] : null);
  const shownPath = file.movedFrom ? moveShown(shortened({ ...file, path: file.movedFrom }, root), shortened(file, root)) : shortened(file, root);
  byId('filePath').textContent = `‎${shownPath}`;
  byId('filePath').title = file.movedFrom ? `Moved from ${file.movedFrom}\nto ${file.path}` : file.path;
  byId('filePath').className = file.isGone ? 'gone' : file.isNew ? 'new' : '';
  const endingsChange = endings.every(Boolean) && endings[0] !== endings[1] && !isEndingsOnly
    ? [Object.assign(span('state mode', `${endings[0]} → ${endings[1]}`), { title: 'The line endings changed too: the diff compares lines, not how they end' })]
    : [];
  byId('fileCounts').replaceChildren(...endingsChange, ...modeChange(file, state.view), ...editedElsewhere(file), ...counts(file[state.view]));
  byId('fileActions').replaceChildren();
  if (isShown && state.view === 'all') {
    const diff = button(byId('fileActions'), 'Diff', toggleAllDiff, `quiet${state.isAllDiffShown ? ' chosen' : ''}`);
    diff.title = 'Show the removed lines and the changes in colour; off, the file as it is, its changes marked in the margin (d)';
  }
  if (isShown) {
    button(byId('fileActions'), 'Comment', () => diffView.openBox(diffView.fileTarget()), 'quiet').title = 'Comment for Claude on the whole file';
  }
  for (const [action, label, kind] of view.fileActions) {
    const made = button(byId('fileActions'), label, () => act(action, null), kind);
    if (file.movedFrom) {
      made.title = { accept: 'Keep the move', reject: `Put the file back at ${file.movedFrom}`, unaccept: 'Make the move pending again' }[action];
    } else if (file.isLink && action === 'reject') {
      made.title = 'Put the link back as it was, or take a new one away';
    }
    if (file.isEditedElsewhere && action === 'reject') {
      made.title = `${made.title ? `${made.title}. ` : ''}The file was edited elsewhere too: this undoes those edits as well`;
    }
  }
  byId('work').classList.toggle('bare', !isShown);
  if (isTooChanged) {
    const lines = `${changedLines.toLocaleString('en')} lines changed`;
    byId('note').replaceChildren(`${hasGivenUp ? `The editor gave up comparing the texts (${lines})` : `${lines}: too many to compare line by line without stalling the page`}. The file as a whole can still be accepted or rejected. `);
    button(byId('note'), 'Show the diff anyway', () => queued(() => {
      state.forcedDiffs.add(key);
      return show(key);
    }), 'quiet').title = 'It may stall the page for seconds, and show the file as one change';
  } else if (isEndingsOnly) {
    byId('note').textContent = `Only the line endings changed: ${endings[0] || 'none'} → ${endings[1] || 'none'}. The file as a whole can be accepted or rejected.`;
  } else {
    byId('note').textContent = isShown || areImages ? '' : 'Not a text the page can show: too large, or not UTF-8. The file as a whole can still be accepted or rejected.';
  }
  if (isShown) {
    diffView.show({
      path: file.path,
      left: left.text,
      right: right.text,
      // A moved file, and a link, is accepted or rejected whole.
      changeActions: file.movedFrom || file.isLink ? [] : view.changeActions,
      canSplice: DiffView.splits(left.text) && DiffView.splits(right.text),
      sides: { left: view.left, right: view.right },
      threads: state.threads.filter(thread => thread.key === key),
      place,
      isDiffHidden: state.view === 'all' && !state.isAllDiffShown,
    });
  }
}

// The editor gave up comparing the open file's texts: the file is shown counted instead, unless its
// diff was asked for.
function gaveUp() {
  const file = state.files.find(candidate => candidate.key === state.key);
  if (!file || state.forcedDiffs.has(file.key)) {
    return;
  }
  state.givenUpFiles.add(`${file.key} ${state.view} ${file.signature}`);
  queued(() => show(file.key));
}

// Shows All's diff, or hides it for the file as it is; the view stays where it is.
function toggleAllDiff() {
  return queued(() => {
    state.isAllDiffShown = !state.isAllDiffShown;
    return show(state.key, 'kept');
  });
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

// Carries out a whole-file action on each file under a folder that has something in the view, as
// one entry of History, undone with one Undo.
function actFolder(action, folder, files) {
  return queued(async () => {
    try {
      const entry = await asked('/apply-files', {
        keys: files.map(file => file.key),
        action,
        signatures: Object.fromEntries(files.map(file => [file.key, file.signature])),
        folder: folder.path,
      });
      toast(`${doneWords[action]} ${entry.files} file${entry.files === 1 ? '' : 's'} in ${placeName(entry)}`, 'Undo', () => flip(entry.id));
    } catch (error) {
      toast(error.message);
    }
    await refreshAll();
  });
}

// Accepts every generated file with something pending, as one History line with one Undo.
function actGenerated(files) {
  return queued(async () => {
    try {
      const entry = await asked('/apply-files', {
        keys: files.map(file => file.key),
        action: 'accept',
        signatures: Object.fromEntries(files.map(file => [file.key, file.signature])),
        generated: true,
      });
      toast(`Accepted ${entry.files} generated file${entry.files === 1 ? '' : 's'}`, 'Undo', () => flip(entry.id));
    } catch (error) {
      toast(error.message);
    }
    await refreshAll();
  });
}

// What a History entry was on, by name: a file, a folder its files were in, an overview item, or the
// generated files.
function placeName(entry) {
  if (entry.item) {
    return `“${entry.item}”`;
  }
  if (entry.generated) {
    return 'generated files';
  }
  return entry.folder ? `${nameOf(entry.path.slice(0, -1))}/` : nameOf(entry.path);
}

function flip(id) {
  return queued(async () => {
    try {
      const entry = await asked('/flip', { id });
      toast(`${entry.isUndone ? 'Undid' : 'Redid'}: ${doneWords[entry.action].toLowerCase()} in ${placeName(entry)}`);
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
    if (state.plain && files.length) {
      // From an unchanged file, to the changed one after or before it in the tree.
      const here = { path: state.plain, isOther: false };
      const next = direction === 'next'
        ? files.find(file => treeOrder(file, here) > 0) || files[0]
        : files.findLast(file => treeOrder(file, here) < 0) || files.at(-1);
      return show(next.key, direction === 'next' ? undefined : 'last');
    }
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
    unfold(file.path);
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
  // Its file comes into sight even when it is the one open, in a folder folded since.
  unfold(file.path);
  await show(thread.key, thread.isFile || thread.side === right ? thread.start : thread.anchorSide === right ? thread.anchor : undefined);
}

// Opens the folders the file at path is in that were folded by hand.
function unfold(path) {
  for (const folder of state.closedFolders) {
    if (path.startsWith(folder)) {
      state.closedFolders.delete(folder);
    }
  }
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
// t and Shift+t step through the threads; d shows or hides All's diff.
function takeKey(event) {
  const key = (event.altKey ? 'Alt+' : '') + event.key;
  // A letter typed into a field is the field's; the editor's own text is no such field.
  const isTyped = !event.altKey && event.target.closest?.('input, textarea') && !diffView.hasTextFocus();
  const isOpen = state.key || state.plain;
  if (event.metaKey || event.ctrlKey || event.isComposing || isTyped || (!isOpen && !state.threads.length)) {
    return;
  }
  if (key === 't' || key === 'T') {
    stepThread(key === 't' ? 'next' : 'previous');
  } else if (!isOpen) {
    return;
  } else if (steps[key]) {
    step(steps[key]);
  } else if (key === 'a') {
    // One press, one change: a key held down does not go on accepting.
    if (state.isText && !event.repeat) {
      diffView.actByKey(key);
    }
  } else if (key === 'd') {
    if (state.view === 'all' && state.key && state.isText) {
      toggleAllDiff();
    }
  } else if (key === 'r') {
    if (state.plain) {
      toast('Comments go on the changed files.');
    } else if (!state.isText || !diffView.startComment()) {
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
  if (!byId('overview').hidden) {
    await drawOverview();
  }
}

// How a text ends its lines: CRLF, LF or CR, mixed with more than one, '' with no line break.
function endingsOf(text) {
  const kinds = [[/\r\n/, 'CRLF'], [/(?<!\r)\n/, 'LF'], [/\r(?!\n)/, 'CR']].filter(([found]) => found.test(text)).map(([, name]) => name);
  return kinds.length > 1 ? 'mixed' : kinds[0] || '';
}

// A session's file changed since its last edit by someone or something else: its diff shows those
// changes as the session's.
function editedElsewhere(file) {
  if (!file.isEditedElsewhere) {
    return [];
  }
  const made = span('state again', 'edited elsewhere');
  made.title = 'Changed since this session\'s last edit to it by someone or something else (another session, you, a formatter, a shell command): its diff shows those changes as the session\'s, and Reject undoes them too';
  return [made];
}

// The file made executable in the view, or no longer: +x or −x; else nothing.
function modeChange(file, view) {
  const [before, after] = [file.mode?.[views[view].left], file.mode?.[views[view].right]];
  if (before == null || after == null || before === after) {
    return [];
  }
  const made = span('state mode', after ? '+x' : '−x');
  made.title = after ? 'Made executable; Accept or Reject the file takes it along' : 'No longer executable; Accept or Reject the file takes it along';
  return [made];
}

// What each of a file's three texts is called beside its picture.
const sideNames = { orig: 'Before', base: 'Accepted', disk: 'Now' };

// Shows an image's two versions side by side, [[text's name, {image, size}], …], each on a
// checkerboard with its size; or, with none, hides them.
function drawImages(sides) {
  const panel = byId('images');
  panel.hidden = !sides;
  panel.replaceChildren(...(sides || []).map(([name, side]) => {
    const pane = document.createElement('figure');
    pane.className = 'imagePane';
    const caption = pane.appendChild(document.createElement('figcaption'));
    caption.append(span('side', sideNames[name]));
    const frame = pane.appendChild(document.createElement('div'));
    frame.className = 'imageFrame';
    if (!side.image) {
      frame.append(span('none', 'No file'));
      return pane;
    }
    const image = frame.appendChild(document.createElement('img'));
    image.alt = sideNames[name];
    image.addEventListener('load', () => caption.append(span('size', `${image.naturalWidth} × ${image.naturalHeight} · ${bytesShown(side.size)}`)));
    image.src = side.image;
    return pane;
  }));
}

function bytesShown(size) {
  return size < 1024 ? `${size} B` : size < 1 << 20 ? `${(size / 1024).toFixed(1)} KB` : `${(size / (1 << 20)).toFixed(1)} MB`;
}

// Where a file moved to another folder was: from the tree's top, or, for a file outside the work's
// tree, from the folder it is in now (../util.js).
function movedFromShown(file) {
  const root = file.isOther ? '' : state.top || workRoot();
  if (root && file.movedFrom.startsWith(root)) {
    return file.movedFrom.slice(root.length);
  }
  const here = file.path.split('/').slice(0, -1);
  const there = file.movedFrom.split('/');
  let shared = 0;
  while (shared < here.length && shared < there.length - 1 && here[shared] === there[shared]) {
    shared++;
  }
  return [...Array(here.length - shared).fill('..'), ...there.slice(shared)].join('/');
}

// A move as git shows a rename: the folders both paths share, then {old → new} of the rest, so the
// end of it, what a narrow bar keeps, says both.
function moveShown(from, to) {
  let shared = 0;
  for (let at = 0; at < Math.min(from.length, to.length) && from[at] === to[at]; at++) {
    if (from[at] === '/') {
      shared = at + 1;
    }
  }
  return `${to.slice(0, shared)}{${from.slice(shared)} → ${to.slice(shared)}}`;
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
    tab.querySelector('.count').textContent = state.files.filter(file => file[tab.dataset.view] && !file.isGenerated).length || '';
  }
  byId('reviewedButton').hidden = state.view !== 'pending';
}

// How far the review is, in the work's own files: how many have nothing pending, of those the
// review has; and the threads, with how many Claude answered last and how many wait for its answer.
function drawProgress() {
  const files = state.files.filter(file => !file.isOther && !file.isGenerated && (file.pending || file.accepted));
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
  const drawing = JSON.stringify([state.view, state.key, state.plain,
    state.areUnchangedShown, state.areReviewedShown, state.top, state.foldersRead, ...state.widenedFolders, '', ...state.narrowedFolders, '', ...state.closedFolders, '', ...state.goneFolders,
    shownInList().map(file => [file.key, file.movedFrom, file[state.view], file.all, file.isNew, file.isGone, file.isUncarried, file.isGenerated, !file.pending, !file.accepted])]);
  if (drawing === drawn.list) {
    return;
  }
  drawn.list = drawing;
  const isFull = Boolean(state.top);
  // A file of the full tree with no change in the view: greyed, with its mark if the review has it.
  const plainRow = (file, depth) => {
    const made = document.createElement('button');
    const recorded = state.files.find(candidate => candidate.path === file.path);
    made.className = `file plain${file.path === state.plain ? ' chosen' : ''}`;
    made.style.setProperty('--depth', depth);
    made.title = file.path;
    made.append(span(`mark${recorded && !recorded.pending ? ' done' : ''}`, !recorded ? '' : !recorded.pending ? '✓' : recorded.accepted ? '◐' : ''));
    made.append(span('name', nameOf(file.path)));
    made.addEventListener('click', () => showPlain(file.path));
    return made;
  };
  const fileRow = (file, depth) => {
    if (file.isPlain) {
      return plainRow(file, depth);
    }
    const made = document.createElement('button');
    // Reviewed, in Pending: faded, with the lines it changed, and opened where what was done shows.
    const isFaded = state.view === 'pending' && !file.pending;
    made.className = `file${isFaded ? ' reviewed' : ''}${file.key === state.key ? ' chosen' : ''}`;
    made.style.setProperty('--depth', depth);
    made.title = isFaded ? `${file.path}\nReviewed: nothing pending. Opens in ${file.accepted ? 'Accepted' : 'All'}.` : file.path;
    // ✓ with nothing of the file pending, ◐ with a part of it accepted.
    const mark = made.appendChild(span(`mark${file.pending ? '' : ' done'}`, !file.pending ? '✓' : file.accepted ? '◐' : ''));
    mark.title = !file.pending ? 'Nothing pending' : file.accepted ? 'Accepted in part' : '';
    // A file moved within its folder: its old name, then its new one; moved to another folder: its
    // name, and where it was in the tooltip.
    const isRenamed = file.movedFrom && file.movedFrom.slice(0, file.movedFrom.lastIndexOf('/')) === file.path.slice(0, file.path.lastIndexOf('/'));
    // A file deleted is struck through, one made is in the added lines' colour.
    made.append(span(`name${file.isGone ? ' gone' : file.isNew ? ' new' : ''}`, isRenamed ? `${nameOf(file.movedFrom)} → ${nameOf(file.path)}` : nameOf(file.path)));
    if (file.movedFrom) {
      made.title = `${made.title}\nMoved from ${file.movedFrom}`;
      if (!isRenamed) {
        made.append(span('from', `from ${movedFromShown(file)}`));
      }
    }
    made.append(span('spring', ''));
    if (file.isLink) {
      made.appendChild(span('state', 'link')).title = 'A symbolic link: its change is where it points, accepted or rejected whole';
    }
    made.append(...modeChange(file, isFaded ? 'all' : state.view), ...editedElsewhere(file));
    if (file.isGone || file.isNew) {
      made.append(span('state', file.isGone ? 'gone' : 'new'));
    }
    if (file.isUncarried) {
      made.appendChild(span('state again', 'rebased')).title = 'The branch was rebased, and what was accepted in this file did not fit the new base: nothing of it is accepted now, look at it again';
    }
    made.append(...counts(isFaded ? file.all : file[state.view]));
    made.addEventListener('click', () => queued(() => isFaded ? openReviewed(file) : show(file.key)));
    return made;
  };
  // A folder's row folds what is in it. It says how far the changed files in it are, ✓ and ◐ as a
  // file's mark, and, folded, their lines added and removed. A folder of the full tree with none is
  // greyed and folded until opened, and read as it is.
  const folderRow = (folder, depth, canWiden) => {
    const under = filesUnder(folder).filter(file => !file.isPlain);
    const isChanged = under.length > 0;
    const fold = document.createElement('details');
    fold.className = `folder${isChanged ? '' : ' plain'}`;
    fold.open = isChanged ? !state.closedFolders.has(folder.path) : state.openedFolders.has(folder.path);
    fold.addEventListener('toggle', () => {
      if (!fold.open) {
        state.closedFolders.add(folder.path);
        state.openedFolders.delete(folder.path);
        return;
      }
      state.closedFolders.delete(folder.path);
      state.openedFolders.add(folder.path);
      if (canWiden && !state.folders.has(folder.path)) {
        queued(async () => {
          await readFolders([folder.path]);
          drawList();
        });
      }
    });
    const head = fold.appendChild(document.createElement('summary'));
    head.style.setProperty('--depth', depth);
    head.title = folder.path;
    // A folder gone with its files is struck through, as they are, and says so.
    const isGone = state.goneFolders.has(folder.path);
    const name = head.appendChild(span(`name${isGone && !(canWiden && isChanged) ? ' gone' : ''}`, canWiden && isChanged ? '' : folder.name));
    if (canWiden && isChanged) {
      // In the full tree each folder of the row widens or narrows on a click, the fold the rest of it.
      // Its tooltip says how much a narrow one leaves out.
      folder.segments.forEach(({ name: segment, path, hidden }, at) => {
        const isWide = at === folder.segments.length - 1 && folder.isWide;
        const made = name.appendChild(span(`segment${isWide ? ' wide' : ''}${state.goneFolders.has(path) ? ' gone' : ''}`, segment));
        made.title = isWide ? `Show only the changed files of ${segment}` : `Show every file of ${segment}${hidden ? ` (${hidden} more)` : ''}`;
        // A button to the keys too: Tab reaches it, Enter or Space widens or narrows.
        made.tabIndex = 0;
        made.setAttribute('role', 'button');
        made.dataset.path = path;
        made.addEventListener('click', event => {
          event.preventDefault();
          widen(path, !isWide);
        });
        made.addEventListener('keydown', event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            event.stopPropagation();
            widen(path, !isWide);
          }
        });
        if (at < folder.segments.length - 1) {
          name.append('/');
        }
      });
    }
    if (isChanged) {
      const isDone = under.every(file => !file.pending);
      // In Pending, a folder with nothing pending is faded as its files are.
      fold.classList.toggle('reviewed', isDone && state.view === 'pending');
      const isBegun = under.some(file => file.accepted || !file.pending);
      head.appendChild(span(`mark${isDone ? ' done' : ''}`, isDone ? '✓' : isBegun ? '◐' : '')).title =
        isDone ? 'Nothing pending in it' : isBegun ? 'Accepted in part' : '';
      head.append(span('spring', ''));
      // The view's whole-file actions on every file in it with something in the view: shown on hover.
      const acted = under.filter(file => file[state.view]);
      const labels = { accept: 'Accept', reject: 'Reject', unaccept: 'Unaccept' };
      if (acted.length && views[state.view].fileActions.length) {
        const tools = head.appendChild(span('folderActions', ''));
        for (const [action] of views[state.view].fileActions) {
          const made = button(tools, labels[action], event => {
            // The fold is not the button's.
            event.preventDefault();
            event.stopPropagation();
            actFolder(action, folder, acted);
          }, 'quiet');
          const count = `${acted.length} file${acted.length === 1 ? '' : 's'}`;
          made.title = { accept: `Accept the ${count} in it`, reject: `Reject the ${count} in it: their changes undone, deleted ones put back`, unaccept: `Unaccept the ${count} in it` }[action];
        }
      }
      if (isGone) {
        head.appendChild(span('state', 'gone')).title = 'The folder is gone, with the files deleted from it';
      }
      const texts = under.map(file => file[state.view]).filter(count => count && !count.isBinary);
      const total = side => texts.reduce((sum, count) => sum + count[side], 0);
      head.append(...counts(texts.length ? { added: total('added'), removed: total('removed') } : null));
    }
    fold.append(...rowsOf(folder, depth + 1, canWiden));
    return fold;
  };
  // A folder's rows: its folders', then its files', then how many it leaves out.
  const rowsOf = (folder, depth, canWiden = false) => {
    const rows = [...folder.folders.map(child => folderRow(child, depth, canWiden)), ...folder.files.map(file => fileRow(file, depth))];
    if (folder.hidden) {
      const more = document.createElement('button');
      more.className = 'more';
      more.textContent = `${folder.hidden} more`;
      more.title = `Show every file of ${folder.path}`;
      more.style.setProperty('--depth', depth);
      more.addEventListener('click', () => widen(folder.path, true));
      rows.push(more);
    }
    if (folder.more) {
      const more = span('none more', `${folder.more} more file${folder.more === 1 ? '' : 's'} not listed`);
      more.style.setProperty('--depth', depth);
      rows.push(more);
    }
    return rows;
  };
  const files = shownInList();
  const others = files.filter(file => file.isOther);
  const generated = files.filter(file => !file.isOther && file.isGenerated);
  const work = files.filter(file => !file.isOther && !file.isGenerated);
  const list = byId('list');
  // A folder shows all it holds once widened by hand, or, with the unchanged files shown, if it holds
  // a changed file, unless narrowed by hand.
  const holdsChanged = new Set(work.map(file => file.path.slice(0, file.path.lastIndexOf('/') + 1)));
  const isWide = path => !state.narrowedFolders.has(path) && (state.widenedFolders.has(path) || state.areUnchangedShown && holdsChanged.has(path));
  // With no changed file in the view, the list says so alone, not the rest of the repository.
  list.replaceChildren(...!work.length ? [] : rowsOf(isFull ? treeOf(work, state.top, state.folders, isWide) : treeOf(work, workRoot()), 0, isFull));
  if (!listed().length) {
    list.append(span('none', views[state.view].none));
  }
  if (generated.length) {
    const fold = list.appendChild(document.createElement('details'));
    fold.className = 'others generated';
    fold.open = state.areGeneratedOpen || generated.some(file => file.key === state.key);
    fold.addEventListener('toggle', () => (state.areGeneratedOpen = fold.open));
    const head = fold.appendChild(document.createElement('summary'));
    head.append(span('name', `${generated.length} generated`));
    head.title = "Files the project generates: marked linguist-generated in its .gitattributes, lockfiles, or saying @generated or DO NOT EDIT in their first lines. Not counted, and the keys step past them";
    head.append(span('spring', ''));
    const pending = generated.filter(file => file.pending);
    if (state.view === 'pending' && pending.length) {
      button(head, 'Accept generated', event => {
        event.preventDefault();
        event.stopPropagation();
        actGenerated(pending);
      }, 'quiet').title = `Accept the ${pending.length} generated file${pending.length === 1 ? '' : 's'} with something pending, as one History line`;
    }
    fold.append(...rowsOf(treeOf(generated, isFull ? state.top : workRoot()), 0));
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
  if ((state.key || state.plain) !== drawn.chosen) {
    drawn.chosen = state.key || state.plain;
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
    const files = `${entry.files} file${entry.files === 1 ? '' : 's'}`;
    const done = entry.item ? placeName(entry) : entry.generated ? `${entry.files} generated file${entry.files === 1 ? '' : 's'}` : entry.folder ? `${files} in` : whatOf(entry.change);
    const what = row.appendChild(span('what', `${doneWords[entry.action]} ${done}`));
    const where = entry.item ? files : entry.generated ? '' : entry.movedFrom ? `${nameOf(entry.movedFrom)} → ${nameOf(entry.path)}` : placeName(entry);
    what.append(span('where', where), span('when', new Date(entry.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
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

// What an ask for the overview waits on, as the drawer says it.
const askedWords = {
  draft: 'Asked: Claude writes it with your next message in the chat.',
  sent: 'Asked: Claude takes it next.',
  taken: 'Claude is writing it.',
};
const blockStates = { pending: 'pending', accepted: 'accepted', unchanged: 'unchanged', stale: 'changed since' };

// Where a block or a change left out is, by its file's name and lines.
function blockPlace(block) {
  const lines = block.start == null ? '' : block.start === block.end ? `:${block.start}` : `:${block.start}–${block.end}`;
  return `${nameOf(block.path)}${lines}${block.side === 'orig' ? ' (before)' : ''}`;
}

// The overview's blocks in its order, each item's in turn.
const outlineBlocks = () => (state.outline?.items || []).flatMap(item => item.blocks);

// Reads the overview and draws the drawer: each item with what is left of it pending and its own
// Accept, its blocks each a way to its lines, then the changes no block covers.
async function drawOverview() {
  state.outlineRead = state.outlineStamp;
  try {
    state.outline = await asked('/outline');
  } catch (error) {
    byId('outline').replaceChildren(Object.assign(document.createElement('p'), { className: 'none', textContent: error.message }));
    return;
  }
  const { items, uncovered, asked: askedState } = state.outline;
  byId('describeButton').textContent = items.length ? 'Describe again' : 'Describe';
  byId('describeButton').disabled = Boolean(askedState);
  const parts = [];
  if (askedState) {
    parts.push(Object.assign(document.createElement('p'), { className: 'asked', textContent: askedWords[askedState] }));
  }
  if (!items.length && !askedState) {
    parts.push(Object.assign(document.createElement('p'), {
      className: 'none',
      textContent: 'No overview yet. Describe asks Claude for one: the changes by purpose, each part a way to its code.',
    }));
  }
  let at = 0;
  items.forEach((item, index) => {
    const section = document.createElement('section');
    section.className = 'item';
    const head = section.appendChild(document.createElement('div'));
    head.className = 'itemHead';
    head.append(span('title', item.title));
    const stale = item.blocks.filter(block => block.state === 'stale').length;
    head.append(span(`progress${item.pending ? '' : ' done'}`, item.pending ? `${item.pending} pending` : stale ? `${stale} changed since` : 'reviewed'));
    if (item.pending) {
      button(head, 'Accept', () => acceptItem(index, item), 'quiet').title = 'Accept what is pending in these blocks, as one History line';
    }
    if (item.text) {
      section.append(Object.assign(document.createElement('p'), { textContent: item.text }));
    }
    const list = section.appendChild(document.createElement('ol'));
    for (const block of item.blocks) {
      const place = at++;
      const row = list.appendChild(document.createElement('li'));
      row.className = `${block.state}${place === state.block ? ' current' : ''}`;
      const where = button(row, blockPlace(block), () => openBlock(place), 'where');
      where.title = `${block.path}\n${blockStates[block.state]}`;
      if (block.note) {
        row.append(span('note', block.note));
      }
    }
    parts.push(section);
  });
  if (items.length && uncovered.length) {
    const section = document.createElement('section');
    section.className = 'item uncovered';
    section.append(span('title', `Not in the overview: ${uncovered.length} change${uncovered.length === 1 ? '' : 's'}`));
    const list = section.appendChild(document.createElement('ol'));
    for (const change of uncovered) {
      const row = list.appendChild(document.createElement('li'));
      button(row, change.isRemoval ? `${nameOf(change.path)}: lines taken away after ${change.start - 1}` : blockPlace(change),
        () => open({ key: change.key, line: change.start || undefined }), 'where').title = change.path;
    }
    parts.push(section);
  }
  byId('outline').replaceChildren(...parts);
}

// Opens a block of the overview, by its place among all of them: its file at its lines.
function openBlock(place) {
  const block = outlineBlocks()[place];
  if (!block) {
    return;
  }
  state.block = place;
  for (const row of byId('outline').querySelectorAll('li.current')) {
    row.classList.remove('current');
  }
  byId('outline').querySelectorAll('.item:not(.uncovered) li')[place]?.classList.add('current');
  return open({ key: block.key, line: block.side === 'disk' && block.start ? block.start : undefined });
}

// Steps to the overview's next or previous block, round the ends.
function stepBlock(direction) {
  const count = outlineBlocks().length;
  if (!count) {
    return toast('The overview has no blocks.');
  }
  const place = state.block < 0 ? (direction === 'next' ? 0 : count - 1) : (state.block + (direction === 'next' ? 1 : count - 1)) % count;
  return openBlock(place);
}

// Accepts what is pending of an overview item, as one History line with one Undo.
function acceptItem(index, item) {
  return queued(async () => {
    try {
      const entry = await asked('/apply-item', {
        index,
        time: state.outline.time,
        signatures: Object.fromEntries(state.files.map(file => [file.key, file.signature])),
      });
      toast(`Accepted “${item.title}” in ${entry.files} file${entry.files === 1 ? '' : 's'}`, 'Undo', () => flip(entry.id));
    } catch (error) {
      toast(error.message);
    }
    await refreshAll();
  });
}

// Asks Claude to describe the changes.
function describe() {
  return queued(async () => {
    try {
      const { asked: askedState } = await asked('/describe', {});
      toast(askedWords[askedState] || 'Asked Claude for an overview.');
    } catch (error) {
      toast(error.message);
    }
    await drawOverview();
  });
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
    row.append(span('where', thread.isFile ? nameOf(thread.path) : `${nameOf(thread.path)}:${lines}`));
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
  diffView = new DiffView(byId('editor'), { act, comment, uncomment, resolve, open, gaveUp });
  diffView.openable = () => state.files.map(file => file.key);
  byId('threadsButton').addEventListener('click', () => stepThread('next'));
  byId('listButton').addEventListener('click', () => document.body.classList.toggle('listHidden'));
  try {
    state.areUnchangedShown = localStorage.getItem(keptUnchangedShown) === 'yes';
  } catch {
    // A browser that keeps nothing: the unchanged files wait behind "N more".
  }
  byId('unchangedButton').classList.toggle('chosen', state.areUnchangedShown);
  byId('unchangedButton').addEventListener('click', () => showUnchanged(!state.areUnchangedShown));
  try {
    state.areReviewedShown = localStorage.getItem(keptReviewedHidden) !== 'yes';
  } catch {
    // A browser that keeps nothing: the reviewed files are listed.
  }
  byId('reviewedButton').classList.toggle('chosen', state.areReviewedShown);
  byId('reviewedButton').addEventListener('click', () => showReviewed(!state.areReviewedShown));
  sizeListBySplitter();
  for (const tab of document.querySelectorAll('#views button')) {
    tab.addEventListener('click', () => queued(async () => {
      state.view = tab.dataset.view;
      drawViews();
      // The same file in the other view, if it has changes there.
      await show(inView().some(file => file.key === state.key) ? state.key : listed()[0]?.key || null);
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
  byId('overviewButton').addEventListener('click', () => queued(async () => {
    byId('overview').hidden = !byId('overview').hidden;
    byId('overviewButton').classList.toggle('chosen', !byId('overview').hidden);
    if (!byId('overview').hidden) {
      await drawOverview();
    }
  }));
  byId('overviewClose').addEventListener('click', () => {
    byId('overview').hidden = true;
    byId('overviewButton').classList.remove('chosen');
  });
  byId('describeButton').addEventListener('click', describe);
  byId('previousBlock').addEventListener('click', () => stepBlock('previous'));
  byId('nextBlock').addEventListener('click', () => stepBlock('next'));
  byId('waitingButton').addEventListener('click', () => (byId('waitingList').hidden = !byId('waitingList').hidden));
  document.addEventListener('keydown', takeKey, true);
  await queued(refresh);
  // A page loaded again goes back where it was, its link taken already; else a link into it opens
  // what it names.
  if (await queued(resume)) {
    takeLink();
  } else {
    openLinked();
  }
  window.addEventListener('hashchange', openLinked);
  setInterval(() => {
    if (!document.hidden) {
      queued(() => refresh().catch(() => {}));
    }
  }, 2000);
}

main().catch(error => (byId('note').textContent = `The page did not start: ${error.message}`));
