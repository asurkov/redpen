// The review page: the session's files, the one open in the diff editor, and what is done to them.
import { DiffView, awaitedWords as waitingWords, button, loaded, marked } from './editor.js';

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
// The icons buttons show in place of their words where the room is short: Icons8's iOS line icons
// (https://icons8.com), each a path on a 50 by 50 grid.
const iconPaths = {
  files: 'M 2 6 A 1.0001 1.0001 0 0 0 1 7 L 1 43 A 1.0001 1.0001 0 0 0 2 44 L 48 44 A 1.0001 1.0001 0 0 0 49 43 L 49 7 A 1.0001 1.0001 0 0 0 48 6 L 2 6 z M 3 8 L 47 8 L 47 42 L 3 42 L 3 8 z M 6 10 A 1.0001 1.0001 0 0 0 5 11 L 5 39 A 1.0001 1.0001 0 0 0 6 40 L 10 40 A 1.0001 1.0001 0 0 0 11 39 L 11 11 A 1.0001 1.0001 0 0 0 10 10 L 6 10 z M 7 12 L 9 12 L 9 38 L 7 38 L 7 12 z',
  unchanged: 'M 35 6 C 33.355469 6 32 7.355469 32 9 L 32 12.5 L 18 23 L 18 21 C 18 19.355469 16.644531 18 15 18 L 3 18 C 1.355469 18 0 19.355469 0 21 L 0 29 C 0 30.644531 1.355469 32 3 32 L 15 32 C 16.644531 32 18 30.644531 18 29 L 18 27 L 32 37.5 L 32 41 C 32 42.644531 33.355469 44 35 44 L 47 44 C 48.644531 44 50 42.644531 50 41 L 50 33 C 50 31.355469 48.644531 30 47 30 L 35 30 C 33.355469 30 32 31.355469 32 33 L 32 35 L 18.6875 25 L 32 15 L 32 17 C 32 18.644531 33.355469 20 35 20 L 47 20 C 48.644531 20 50 18.644531 50 17 L 50 9 C 50 7.355469 48.644531 6 47 6 Z M 35 8 L 47 8 C 47.554688 8 48 8.445313 48 9 L 48 17 C 48 17.554688 47.554688 18 47 18 L 35 18 C 34.445313 18 34 17.554688 34 17 L 34 9 C 34 8.445313 34.445313 8 35 8 Z M 3 20 L 15 20 C 15.554688 20 16 20.445313 16 21 L 16 29 C 16 29.554688 15.554688 30 15 30 L 3 30 C 2.445313 30 2 29.554688 2 29 L 2 21 C 2 20.445313 2.445313 20 3 20 Z M 35 32 L 47 32 C 47.554688 32 48 32.445313 48 33 L 48 41 C 48 41.554688 47.554688 42 47 42 L 35 42 C 34.445313 42 34 41.554688 34 41 L 34 33 C 34 32.445313 34.445313 32 35 32 Z',
  reviewed: 'M 37.90625 8.96875 C 37.863281 8.976563 37.820313 8.988281 37.78125 9 C 37.554688 9.054688 37.355469 9.1875 37.21875 9.375 L 12.9375 38.5 L 1.71875 27.28125 C 1.320313 26.882813 0.679688 26.882813 0.28125 27.28125 C -0.117188 27.679688 -0.117188 28.320313 0.28125 28.71875 L 12.28125 40.71875 C 12.484375 40.921875 12.765625 41.03125 13.050781 41.011719 C 13.339844 40.992188 13.605469 40.851563 13.78125 40.625 L 38.78125 10.625 C 39.058594 10.3125 39.113281 9.863281 38.917969 9.492188 C 38.722656 9.125 38.320313 8.914063 37.90625 8.96875 Z M 48.90625 8.96875 C 48.863281 8.976563 48.820313 8.988281 48.78125 9 C 48.554688 9.054688 48.355469 9.1875 48.21875 9.375 L 23.9375 38.5 L 21.375 35.96875 C 21.132813 35.671875 20.746094 35.535156 20.371094 35.621094 C 20 35.707031 19.707031 36 19.621094 36.371094 C 19.535156 36.746094 19.671875 37.132813 19.96875 37.375 L 23.28125 40.71875 C 23.484375 40.921875 23.765625 41.03125 24.050781 41.011719 C 24.339844 40.992188 24.605469 40.851563 24.78125 40.625 L 49.78125 10.625 C 50.058594 10.3125 50.113281 9.863281 49.917969 9.492188 C 49.722656 9.125 49.320313 8.914063 48.90625 8.96875 Z',
  overview: 'M 7 2 L 7 48 L 43 48 L 43 2 Z M 9 4 L 41 4 L 41 46 L 9 46 Z M 29.984375 8.984375 C 29.433594 8.996094 28.992188 9.449219 29 10 L 29 39 C 28.996094 39.359375 29.183594 39.695313 29.496094 39.878906 C 29.808594 40.058594 30.191406 40.058594 30.503906 39.878906 C 30.816406 39.695313 31.003906 39.359375 31 39 L 31 10 C 31.003906 9.730469 30.898438 9.46875 30.707031 9.277344 C 30.515625 9.085938 30.253906 8.980469 29.984375 8.984375 Z M 14 11 C 13.640625 10.996094 13.304688 11.183594 13.121094 11.496094 C 12.941406 11.808594 12.941406 12.191406 13.121094 12.503906 C 13.304688 12.816406 13.640625 13.003906 14 13 L 25 13 C 25.359375 13.003906 25.695313 12.816406 25.878906 12.503906 C 26.058594 12.191406 26.058594 11.808594 25.878906 11.496094 C 25.695313 11.183594 25.359375 10.996094 25 11 Z M 35 11 C 34.640625 10.996094 34.304688 11.183594 34.121094 11.496094 C 33.941406 11.808594 33.941406 12.191406 34.121094 12.503906 C 34.304688 12.816406 34.640625 13.003906 35 13 L 36 13 C 36.359375 13.003906 36.695313 12.816406 36.878906 12.503906 C 37.058594 12.191406 37.058594 11.808594 36.878906 11.496094 C 36.695313 11.183594 36.359375 10.996094 36 11 Z M 14 19 C 13.640625 18.996094 13.304688 19.183594 13.121094 19.496094 C 12.941406 19.808594 12.941406 20.191406 13.121094 20.503906 C 13.304688 20.816406 13.640625 21.003906 14 21 L 25 21 C 25.359375 21.003906 25.695313 20.816406 25.878906 20.503906 C 26.058594 20.191406 26.058594 19.808594 25.878906 19.496094 C 25.695313 19.183594 25.359375 18.996094 25 19 Z M 35 19 C 34.640625 18.996094 34.304688 19.183594 34.121094 19.496094 C 33.941406 19.808594 33.941406 20.191406 34.121094 20.503906 C 34.304688 20.816406 34.640625 21.003906 35 21 L 36 21 C 36.359375 21.003906 36.695313 20.816406 36.878906 20.503906 C 37.058594 20.191406 37.058594 19.808594 36.878906 19.496094 C 36.695313 19.183594 36.359375 18.996094 36 19 Z M 14 27 C 13.640625 26.996094 13.304688 27.183594 13.121094 27.496094 C 12.941406 27.808594 12.941406 28.191406 13.121094 28.503906 C 13.304688 28.816406 13.640625 29.003906 14 29 L 25 29 C 25.359375 29.003906 25.695313 28.816406 25.878906 28.503906 C 26.058594 28.191406 26.058594 27.808594 25.878906 27.496094 C 25.695313 27.183594 25.359375 26.996094 25 27 Z M 35 27 C 34.640625 26.996094 34.304688 27.183594 34.121094 27.496094 C 33.941406 27.808594 33.941406 28.191406 34.121094 28.503906 C 34.304688 28.816406 34.640625 29.003906 35 29 L 36 29 C 36.359375 29.003906 36.695313 28.816406 36.878906 28.503906 C 37.058594 28.191406 37.058594 27.808594 36.878906 27.496094 C 36.695313 27.183594 36.359375 26.996094 36 27 Z M 14 35 C 13.640625 34.996094 13.304688 35.183594 13.121094 35.496094 C 12.941406 35.808594 12.941406 36.191406 13.121094 36.503906 C 13.304688 36.816406 13.640625 37.003906 14 37 L 25 37 C 25.359375 37.003906 25.695313 36.816406 25.878906 36.503906 C 26.058594 36.191406 26.058594 35.808594 25.878906 35.496094 C 25.695313 35.183594 25.359375 34.996094 25 35 Z M 35 35 C 34.640625 34.996094 34.304688 35.183594 34.121094 35.496094 C 33.941406 35.808594 33.941406 36.191406 34.121094 36.503906 C 34.304688 36.816406 34.640625 37.003906 35 37 L 36 37 C 36.359375 37.003906 36.695313 36.816406 36.878906 36.503906 C 37.058594 36.191406 37.058594 35.808594 36.878906 35.496094 C 36.695313 35.183594 36.359375 34.996094 36 35 Z',
  history: 'M 7.90625 0.96875 C 7.863281 0.976563 7.820313 0.988281 7.78125 1 C 7.316406 1.105469 6.988281 1.523438 7 2 L 7 12 L 17 12 C 17.359375 12.003906 17.695313 11.816406 17.878906 11.503906 C 18.058594 11.191406 18.058594 10.808594 17.878906 10.496094 C 17.695313 10.183594 17.359375 9.996094 17 10 L 10.34375 10 C 14.105469 6.304688 19.269531 4 25 4 C 36.664063 4 46 13.335938 46 25 C 46 36.664063 36.664063 46 25 46 C 13.335938 46 4 36.664063 4 25 C 4 21.566406 4.847656 18.207031 6.28125 15.34375 L 4.5 14.4375 C 2.933594 17.574219 2 21.234375 2 25 C 2 37.734375 12.265625 48 25 48 C 37.734375 48 48 37.734375 48 25 C 48 12.265625 37.734375 2 25 2 C 18.777344 2 13.117188 4.496094 9 8.5 L 9 2 C 9.011719 1.710938 8.894531 1.433594 8.6875 1.238281 C 8.476563 1.039063 8.191406 0.941406 7.90625 0.96875 Z M 24 9 L 24 23.28125 C 23.402344 23.628906 23 24.261719 23 25 C 23 25.171875 23.019531 25.339844 23.0625 25.5 L 16.28125 32.28125 L 17.71875 33.71875 L 24.5 26.9375 C 24.660156 26.980469 24.828125 27 25 27 C 26.105469 27 27 26.105469 27 25 C 27 24.261719 26.597656 23.628906 26 23.28125 L 26 9 Z',
  comment: 'M 25 3.0703125 C 12.415167 3.0703125 2.0703125 11.925365 2.0703125 23 C 2.0703125 29.427318 5.6219606 35.086611 11 38.712891 C 10.9916 38.928457 11.008101 39.280691 10.726562 40.324219 C 10.377821 41.617047 9.6731852 43.431687 8.2402344 45.464844 A 0.92959293 0.92959293 0 0 0 8.9960938 46.929688 C 15.173361 46.956575 18.748849 42.904801 19.304688 42.251953 C 21.133469 42.659115 23.028437 42.929687 25 42.929688 C 37.583869 42.929688 47.929688 34.074694 47.929688 23 C 47.929688 11.925306 37.583869 3.0703125 25 3.0703125 z M 25 4.9296875 C 36.716131 4.9296875 46.070312 13.088694 46.070312 23 C 46.070312 32.911306 36.716131 41.070312 25 41.070312 C 22.998026 41.070312 21.063469 40.831229 19.230469 40.384766 A 0.92959293 0.92959293 0 0 0 18.285156 40.705078 C 18.285156 40.705078 15.394898 43.984833 10.787109 44.759766 C 11.62014 43.264901 12.235187 41.869931 12.521484 40.808594 C 12.920492 39.329422 12.925781 38.326172 12.925781 38.326172 A 0.92959293 0.92959293 0 0 0 12.494141 37.541016 C 7.2672049 34.227589 3.9296875 28.943265 3.9296875 23 C 3.9296875 13.088635 13.282833 4.9296875 25 4.9296875 z M 14 16 A 1.0001 1.0001 0 1 0 14 18 L 36 18 A 1.0001 1.0001 0 1 0 36 16 L 14 16 z M 10 22 A 1.0001 1.0001 0 1 0 10 24 L 27 24 A 1.0001 1.0001 0 1 0 27 22 L 10 22 z M 33 22 A 1.0001 1.0001 0 1 0 33 24 L 40 24 A 1.0001 1.0001 0 1 0 40 22 L 33 22 z M 14 28 A 1.0001 1.0001 0 1 0 14 30 L 36 30 A 1.0001 1.0001 0 1 0 36 28 L 14 28 z',
};
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
  // The comment or reply being written under an overview item: its title and the text so far.
  itemReply: null,
  // The comment or reply being written under a folder of the list: its path and the text so far.
  folderReply: null,
  // What Cmd+Z undid, by History's ids, the last undone last: what Cmd+Shift+Z redoes, until the next
  // action.
  redoable: [],
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
  // An action done leaves nothing for Cmd+Shift+Z to redo.
  if (['/apply', '/apply-files', '/apply-item', '/accept-all'].includes(path)) {
    state.redoable = [];
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

// A folder's path from the tree's top or the work's folder, with its closing slash; the whole path
// outside them.
function folderShown(path) {
  const root = state.top || workRoot();
  return root && path.startsWith(root) && path !== root ? path.slice(root.length) : path;
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
  // A commit took some of the session's changes since the page last read: they count as accepted now. The
  // page says so once, not for one there before it loaded.
  if ((listing.committed?.id ?? null) !== state.committedId) {
    const entry = listing.committed;
    if (entry && state.committedId !== undefined) {
      toast(`Committed in ${entry.commit}: ${entry.files} file${entry.files === 1 ? '' : 's'} accepted`, 'Undo', () => flip(entry.id));
    }
    state.committedId = entry?.id ?? null;
  }
  state.comments = listing.comments;
  state.threads = listing.threads;
  state.goneFolders = new Set((listing.goneFolders || []).map(path => `${path}/`));
  state.isWatched = listing.isWatched;
  diffView.isWatched = listing.isWatched;
  state.isScanning = listing.isScanning;
  state.outlineStamp = JSON.stringify([listing.outline, listing.files.map(file => file.signature),
    listing.threads.filter(thread => thread.item != null).map(thread => [thread.id, thread.entries.length, thread.entries.at(-1).state])]);
  if (!byId('overview').hidden && state.outlineStamp !== state.outlineRead) {
    await drawOverview();
  }
  // The page's code changed, and held still for two looks, so no edit of more than one of its files is
  // under way: the page loads itself again. What it is made of, the server said as it sent it.
  const isCodeStill = listing.page === state.pageSeen;
  state.pageSeen = listing.page;
  state.page = state.page || document.querySelector('meta[name="redpen-code"]')?.content || listing.page;
  if (listing.page && listing.page !== state.page && isCodeStill && loadAgain()) {
    return;
  }
  tellRebase(listing.rebased);
  byId('title').textContent = listing.title;
  document.title = `${listing.title} · redpen`;
  drawViews();
  drawProgress();
  drawWaiting();
  state.top = listing.top;
  byId('unchangedButton').hidden = !state.top;
  fitHeader();
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
// isGoingOn (Space, or a change's ↓): then on to the next change, as n goes: the one that took this one's
// place, or the next one down, or into the next file. A file left with nothing more in the view opens the
// next anyway.
function act(action, change, isGoingOn = false) {
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
      isGoingOn = false;
    }
    await refreshAll();
    if (isGoingOn && state.key === key && state.isText) {
      // The editor has the file anew: once it has put the view in place (never waited for long).
      await Promise.race([diffView.settled(), new Promise(resolve => setTimeout(resolve, 3000))]);
      if (diffView.current >= 0) {
        diffView.goTo(diffView.current);
      } else {
        await stepNow('next');
      }
    }
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
  if (entry.commit) {
    return `commit ${entry.commit}`;
  }
  if (entry.all) {
    return 'all files';
  }
  if (entry.generated) {
    return 'generated files';
  }
  return entry.folder ? `${nameOf(entry.path.slice(0, -1))}/` : nameOf(entry.path);
}

function flip(id) {
  return queued(() => flipNow(id));
}

// Undoes a History entry, or redoes an undone one; says whether it did.
async function flipNow(id) {
  let isFlipped = false;
  try {
    const entry = await asked('/flip', { id });
    toast(`${entry.isUndone ? 'Undid' : 'Redid'}: ${doneWords[entry.action].toLowerCase()} in ${placeName(entry)}`);
    isFlipped = true;
  } catch (error) {
    toast(error.message);
  }
  await refreshAll();
  return isFlipped;
}

// Cmd+Z: undoes History's newest entry not undone, as its Undo does.
function undoLast() {
  return queued(async () => {
    const entry = (await asked('/journal')).find(candidate => candidate.action !== 'rebase' && !candidate.isUndone);
    if (!entry) {
      return toast('Nothing to undo.');
    }
    if (!entry.canFlip) {
      return toast(`Not undone: ${placeName(entry)} changed since it was ${doneWords[entry.action].toLowerCase()}.`);
    }
    if (await flipNow(entry.id)) {
      state.redoable.push(entry.id);
    }
  });
}

// Cmd+Shift+Z: redoes what Cmd+Z undid last, while History still has it undone.
function redoLast() {
  return queued(async () => {
    const id = state.redoable.pop();
    const entry = id && (await asked('/journal')).find(candidate => candidate.id === id);
    if (!entry?.isUndone) {
      state.redoable = [];
      return toast('Nothing to redo.');
    }
    if (!(await flipNow(id))) {
      state.redoable.push(id);
    }
  });
}

// Steps to the next or previous change; past the open file's last or first one, into the file
// after or before it in the list, round the list's ends.
function step(direction) {
  return queued(() => stepNow(direction));
}

async function stepNow(direction) {
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
  // A thread on an overview item stands under it, in the drawer.
  if (thread.item != null) {
    byId('overview').hidden = false;
    byId('overviewButton').classList.add('chosen');
    await drawOverview();
    const item = [...byId('outline').querySelectorAll('.item')].find(section => section.querySelector('.title')?.textContent === thread.item);
    return item?.scrollIntoView({ block: 'nearest' });
  }
  // A thread on a folder stands under its row, in a view that lists the folder.
  if (thread.folder != null) {
    unfold(thread.folder);
    const row = () => [...byId('list').querySelectorAll('details.folder')].find(fold => fold.dataset.path === thread.folder);
    drawList();
    if (!row() && state.view !== 'all') {
      state.view = 'all';
      drawViews();
      drawList();
    }
    return row() ? row().scrollIntoView({ block: 'nearest' }) : toast('The list no longer shows the folder of that thread.');
  }
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
// the current change's own action and stays, Space that and on to the next change; r opens a comment on the selected text, or else on that change; t and Shift+t step through the
// threads; d shows or hides All's diff; Cmd+Z (Ctrl+Z) undoes the last Accept or Reject, Cmd+Shift+Z
// redoes it.
function takeKey(event) {
  const key = (event.altKey ? 'Alt+' : '') + event.key;
  // A letter typed into a field is the field's; the editor's own text is no such field.
  const isTyped = !event.altKey && event.target.closest?.('input, textarea') && !diffView.hasTextFocus();
  const isOpen = state.key || state.plain;
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'z' && !isTyped && !event.isComposing) {
    event.preventDefault();
    event.stopPropagation();
    if (!event.repeat) {
      event.shiftKey ? redoLast() : undoLast();
    }
    return;
  }
  if (event.metaKey || event.ctrlKey || event.isComposing || isTyped || (!isOpen && !state.threads.length)) {
    return;
  }
  if (key === 't' || key === 'T') {
    stepThread(key === 't' ? 'next' : 'previous');
  } else if (!isOpen) {
    return;
  } else if (steps[key]) {
    step(steps[key]);
  } else if (key === 'a' || key === ' ') {
    // One press, one change: a key held down does not go on accepting. Space then goes on to the next.
    if (state.isText && !event.repeat) {
      diffView.actByKey('a', key === ' ');
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
  // A rubber stamp: everything pending, accepted at once.
  const pending = state.files.filter(file => file.pending).length;
  byId('acceptAllButton').hidden = state.view !== 'pending' || !pending;
  byId('acceptAllButton').textContent = `Accept all · ${pending}`;
}

// Accepts what is pending of every file, as one History line with one Undo.
function acceptAll() {
  return queued(async () => {
    try {
      const entry = await asked('/accept-all', {
        signatures: Object.fromEntries(state.files.filter(file => file.pending).map(file => [file.key, file.signature])),
      });
      toast(`Accepted all · ${entry.files} file${entry.files === 1 ? '' : 's'}`, 'Undo', () => flip(entry.id));
    } catch (error) {
      toast(error.message);
    }
    await refreshAll();
  });
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
    shownInList().map(file => [file.key, file.movedFrom, file[state.view], file.all, file.isNew, file.isGone, file.isUncarried, file.isGenerated, !file.pending, !file.accepted]),
    state.threads.filter(thread => thread.folder != null).map(thread => [thread.id, thread.folder, thread.id === state.thread,
      ...thread.entries.map(entry => [entry.by, entry.state, entry.text])]), state.folderReply?.path]);
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
    if (file.agents?.length) {
      made.appendChild(span('state', `by ${file.agents.join(', ')}`)).title = `Edited by the session's subagent${file.agents.length > 1 ? 's' : ''}: ${file.agents.join(', ')}`;
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
    // Its thread, and the box to comment or reply in while it is open.
    const thread = state.threads.find(candidate => candidate.folder === folder.path);
    const writing = state.folderReply?.path === folder.path ? state.folderReply : null;
    const fold = document.createElement('details');
    fold.className = `folder${isChanged ? '' : ' plain'}`;
    fold.dataset.path = folder.path;
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
      const tools = head.appendChild(span('folderActions', ''));
      if (!thread && !writing) {
        iconed(button(tools, 'Comment', event => {
          event.preventDefault();
          event.stopPropagation();
          openFolderBox(folder.path);
        }, 'quiet'), 'comment').title = 'Comment for Claude on this folder';
      }
      if (acted.length && views[state.view].fileActions.length) {
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
    if (isChanged && (thread || writing)) {
      const box = fold.appendChild(threadBox(thread, writing, {
        placeholder: 'Comment for Claude on this folder',
        send: text => commentFolder(folder.path, text, thread?.id),
        cancel: () => {
          state.folderReply = null;
          drawList();
        },
        reply: () => openFolderBox(folder.path),
      }));
      box.style.setProperty('--depth', depth + 1);
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
    // A commit's: what it took of the session's changes, accepted.
    const what = row.appendChild(span('what', entry.commit ? `Committed in ${entry.commit}` : entry.all ? 'Accepted all' : `${doneWords[entry.action]} ${done}`));
    const where = entry.item || entry.commit || entry.all ? files : entry.generated ? '' : entry.movedFrom ? `${nameOf(entry.movedFrom)} → ${nameOf(entry.path)}` : placeName(entry);
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
  renderOverview();
}

// Draws the drawer from the overview as last read.
function renderOverview() {
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
    if (!state.threads.some(thread => thread.item === item.title) && state.itemReply?.title !== item.title) {
      button(head, 'Comment', () => openItemBox(item), 'quiet').title = 'Comment for Claude on this item';
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
    section.append(...itemThread(item, index));
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

// What was said under an overview item, as a line's thread shows it, and the box to comment or reply
// in; nothing while neither is.
function itemThread(item, index) {
  const thread = state.threads.find(candidate => candidate.item === item.title);
  const writing = state.itemReply?.title === item.title ? state.itemReply : null;
  if (!thread && !writing) {
    return [];
  }
  return [threadBox(thread, writing, {
    placeholder: 'Comment for Claude on this item',
    send: text => commentItem(index, item, text, thread?.id),
    cancel: () => {
      state.itemReply = null;
      renderOverview();
    },
    reply: () => openItemBox(item),
  })];
}

// A thread that stands apart from a file's lines, an overview item's or a folder's, as a line's thread
// shows it, and the box to comment or reply in while writing, the box's draft, is there. on: the box's
// placeholder, send(text), cancel() and reply().
function threadBox(thread, writing, on) {
  const box = document.createElement('div');
  box.className = `thread${thread?.entries.at(-1).by === 'claude' ? ' answered' : ''}${thread?.id === state.thread ? ' current' : ''}`;
  for (const entry of thread?.entries || []) {
    const row = box.appendChild(document.createElement('div'));
    row.className = `entry ${entry.by} ${entry.state || ''}`;
    const head = row.appendChild(document.createElement('div'));
    head.className = 'head';
    head.appendChild(document.createElement('b')).textContent = entry.by === 'claude' ? 'Claude' : 'You';
    head.append(new Date(entry.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), span('spring', ''));
    if (entry.state === 'draft') {
      button(head, '✕', () => uncomment(entry.id), 'quiet').title = 'Delete the comment';
    }
    row.appendChild(marked(entry.text));
  }
  const last = thread?.entries.at(-1);
  if (last?.by === 'user' && waitingWords[last.state]) {
    const row = box.appendChild(document.createElement('div'));
    row.className = `entry claude awaited ${last.state}`;
    row.appendChild(document.createElement('div')).className = 'head';
    row.lastChild.appendChild(document.createElement('b')).textContent = 'Claude';
    row.appendChild(Object.assign(document.createElement('div'), { className: 'said', textContent: waitingWords[last.state] }));
  }
  const foot = document.createElement('div');
  foot.className = 'foot';
  if (writing) {
    const input = box.appendChild(document.createElement('textarea'));
    input.placeholder = thread ? 'Reply to Claude' : on.placeholder;
    input.value = writing.text;
    input.addEventListener('input', () => (writing.text = input.value));
    const send = () => input.value.trim() && on.send(input.value);
    const cancel = on.cancel;
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        send();
      } else if (event.key === 'Escape') {
        cancel();
      }
    });
    foot.append(state.isWatched ? 'Goes to Claude at once' : 'Goes to Claude with your next message in the chat', span('spring', ''));
    button(foot, 'Cancel', cancel, 'quiet');
    button(foot, thread ? 'Add reply' : 'Add comment', send, 'main');
    // Focused as it opens, and again when drawn anew while it has the keys.
    if (writing.isOpening || document.activeElement === writing.input) {
      writing.isOpening = false;
      requestAnimationFrame(() => input.focus());
    }
    writing.input = input;
  } else {
    foot.append(span('spring', ''));
    if (thread.entries.some(entry => entry.state !== 'draft')) {
      button(foot, 'Reply', on.reply, 'quiet');
    }
    button(foot, 'Resolve', () => resolve(thread.id), 'quiet').title = 'Take the thread off the page';
  }
  box.append(foot);
  return box;
}

// Opens the box under an overview item, to comment on it or reply in its thread.
function openItemBox(item) {
  state.itemReply = { title: item.title, text: '', isOpening: true };
  renderOverview();
}

// Sends a comment on an overview item, or a reply in its thread, the way a line's comment goes.
function commentItem(index, item, text, thread) {
  return queued(async () => {
    try {
      await asked('/comment', { item: index, title: item.title, text, thread });
      state.itemReply = null;
    } catch (error) {
      toast(error.message);
    }
    await refresh();
    await drawOverview();
  });
}

// A button's icon and its word: the word alone, or, where the room is short, the icon alone, the word
// kept for screen readers.
function iconed(made, name) {
  const label = span('label', made.textContent);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 50 50');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  svg.appendChild(document.createElementNS('http://www.w3.org/2000/svg', 'path')).setAttribute('d', iconPaths[name]);
  made.replaceChildren(svg, label);
  return made;
}

// The header with its buttons' words while its row has room for them and a title of --titleRoom, with
// their icons while it has not (and, short of room still, on two rows): the room the words take is
// measured as they go, so they come back only when it is there.
let wordsRoom = 0;
function fitHeader() {
  const top = byId('top');
  const style = getComputedStyle(top);
  const needed = () => [...top.children]
    .filter(child => child.id !== 'title' && !child.hidden)
    .reduce((sum, child) => sum + (child.id === 'progress' ? child.scrollWidth : child.offsetWidth) + parseFloat(style.columnGap), 0)
    + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) + parseFloat(style.getPropertyValue('--titleRoom'));
  const isCompact = document.body.classList.contains('compactTop');
  if (!isCompact && needed() > top.clientWidth) {
    const worded = needed();
    document.body.classList.add('compactTop');
    wordsRoom = worded - needed();
  } else if (isCompact && needed() + wordsRoom <= top.clientWidth) {
    document.body.classList.remove('compactTop');
  }
}

// Opens the box under a folder of the list, to comment on it or reply in its thread; a folded folder
// opens.
function openFolderBox(path) {
  state.folderReply = { path, text: '', isOpening: true };
  state.closedFolders.delete(path);
  drawList();
}

// Sends a comment on a folder, or a reply in its thread, the way a line's comment goes.
function commentFolder(path, text, thread) {
  return queued(async () => {
    try {
      await asked('/comment', { folder: path, text, thread });
      state.folderReply = null;
    } catch (error) {
      toast(error.message);
    }
    await refresh();
    drawList();
  });
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
    const where = thread.item != null ? `Overview: ${thread.item}` : thread.folder != null ? `Folder: ${folderShown(thread.folder)}`
      : thread.isFile ? nameOf(thread.path) : `${nameOf(thread.path)}:${lines}`;
    row.append(span('where', where));
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
  for (const made of document.querySelectorAll('#top [data-icon]')) {
    iconed(made, made.dataset.icon);
  }
  new ResizeObserver(fitHeader).observe(byId('top'));
  byId('listButton').addEventListener('click', () => document.body.classList.toggle('listHidden'));
  byId('acceptAllButton').addEventListener('click', acceptAll);
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

// Started, the page's own looks follow its code; until then the watchdog in page.html does.
main().then(() => (window.redpenStarted = true), error => (byId('note').textContent = `The page did not start: ${error.message}`));
