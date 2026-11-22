// redpen in Cursor: the files Claude Code sessions edited, as redpen's recorder keeps them in
// ~/.redpen/code/<session>/ (see ../recorder.py), with their lines added and removed against their
// baselines: a file's text before the session's first edit to it, or as last accepted on redpen's page.
// Clicking a file opens it; Open Review opens the session's review page, where the changes are seen,
// accepted and rejected. The view only reads the record: the page alone writes it.
const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {execFile} = require('child_process');

const storeDir = path.join(os.homedir(), '.redpen', 'code');
const reviewScript = path.join(os.homedir(), '.claude', 'skills', 'redpen', 'code', 'review.py');

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

function readJson(file) {
  const text = readText(file);
  try {
    return text === null ? null : JSON.parse(text);
  } catch {
    return null;
  }
}

// Each session's record, newest first.
function readSessions() {
  let names;
  try {
    names = fs.readdirSync(storeDir);
  } catch {
    return [];
  }
  const sessions = [];
  for (const name of names) {
    const folder = path.join(storeDir, name);
    const info = readJson(path.join(folder, 'session.json'));
    if (!info) continue;
    let entries;
    try {
      entries = fs.readdirSync(path.join(folder, 'files'));
    } catch {
      entries = [];
    }
    const files = [];
    for (const entry of entries) {
      // The hooks write each file whole under a dot name, then rename it.
      if (entry.startsWith('.') || !entry.endsWith('.json')) continue;
      const file = readJson(path.join(folder, 'files', entry));
      if (file) files.push({...file, base: path.join(folder, 'base', entry.slice(0, -'.json'.length))});
    }
    sessions.push({...info, folder, files});
  }
  return sessions.sort((a, b) => b.updated.localeCompare(a.updated));
}

function inWorkspace(file) {
  return (vscode.workspace.workspaceFolders || []).some(
      folder => file.startsWith(folder.uri.fsPath + path.sep));
}

// Lines added and removed between two texts, by Myers' greedy algorithm, or null past maxD edits.
function lineChanges(oldText, newText, maxD = 5000) {
  const ids = new Map();
  const toIds = text => text.split(/\r\n|\r|\n/).map(line => {
    if (!ids.has(line)) ids.set(line, ids.size);
    return ids.get(line);
  });
  let a = toIds(oldText), b = toIds(newText);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) endA--, endB--;
  a = a.slice(start, endA);
  b = b.slice(start, endB);
  const n = a.length, m = b.length;
  const limit = Math.min(n + m, maxD);
  const v = new Int32Array(2 * limit + 3);
  const at = k => k + limit + 1;
  for (let d = 0; d <= limit; d++) {
    for (let k = -d; k <= d; k += 2) {
      let x = (k === -d || (k !== d && v[at(k - 1)] < v[at(k + 1)])) ? v[at(k + 1)] : v[at(k - 1)] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) x++, y++;
      v[at(k)] = x;
      if (x >= n && y >= m) return {added: (d - n + m) / 2, removed: (d + n - m) / 2};
    }
  }
  return null;
}

// What changed in a file since its baseline, for its line in the list. A file the session created has
// no baseline until it is accepted.
function fileStatus(file) {
  const base = readText(file.base);
  if (base === null && !file.new) return {kind: 'unknown', text: 'old text missing'};
  const now = readText(file.path);
  if (now === null) return base === null ? {kind: 'same', text: 'no changes left'} : {kind: 'changed', text: 'deleted'};
  if ((base ?? '') === now) return {kind: 'same', text: 'no changes left'};
  const changes = lineChanges(base ?? '', now);
  const counts = changes ? `+${changes.added} −${changes.removed}` : 'changed';
  return {kind: 'changed', text: base === null ? `new, ${counts}` : counts};
}

const isChanged = file => file.status.kind === 'changed';
// A change of the work's: not of a file its project generates, which is listed and not counted.
const isWorkChanged = file => isChanged(file) && !file.isGenerated;

// Whether a file is one its project generates, as review.py says (its .gitattributes'
// linguist-generated, lockfiles, a "DO NOT EDIT" head), by path, with when it said so: asked again
// after a minute, so an edited .gitattributes shows.
const generatedVerdicts = new Map();
const verdictMs = 60000;
let isAsking = false;

// Asks review.py about the paths not asked lately; calls done when it said something new.
function askGenerated(paths, done) {
  const now = Date.now();
  const asked = paths.filter(file => (generatedVerdicts.get(file)?.at ?? 0) < now - verdictMs);
  if (!asked.length || isAsking) return;
  isAsking = true;
  execFile('python3', [reviewScript, '--generated', ...asked], (error, stdout) => {
    isAsking = false;
    // Unsaid, a file counts as the work's.
    const said = new Set(error ? [] : stdout.split('\n').filter(Boolean));
    const isNew = asked.some(file => said.has(file) !== (generatedVerdicts.get(file)?.isGenerated ?? false));
    for (const file of asked) generatedVerdicts.set(file, {isGenerated: said.has(file), at: now});
    if (isNew) done();
  });
}

function ago(iso) {
  const minutes = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString();
}

class EditsProvider {
  constructor() {
    this.changed = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.changed.event;
    this.sessions = [];
  }

  refresh() {
    this.sessions = readSessions()
        .map(session => ({...session, shown: session.files.filter(file => inWorkspace(file.path))}))
        .filter(session => session.shown.length);
    for (const session of this.sessions) {
      for (const file of session.shown) {
        file.status = fileStatus(file);
        file.isGenerated = generatedVerdicts.get(file.path)?.isGenerated ?? false;
      }
    }
    askGenerated([...new Set(this.sessions.flatMap(session => session.shown.map(file => file.path)))], () => this.onVerdicts?.());
    this.changed.fire();
  }

  // The listed files with changes of the work's, newest session first.
  changedFiles() {
    return this.sessions.flatMap(session => session.shown.filter(isWorkChanged));
  }

  getChildren(node) {
    if (!node) return this.sessions.map((session, i) => ({kind: 'session', session, newest: i === 0}));
    if (node.kind !== 'session') return [];
    return [...node.session.shown]
        .sort((a, b) => a.path.localeCompare(b.path))
        .map(file => ({kind: 'file', session: node.session, file}));
  }

  getTreeItem(node) {
    if (node.kind === 'session') {
      const {session} = node;
      const item = new vscode.TreeItem(session.title, node.newest ?
          vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.Collapsed);
      item.id = session.session;
      const work = session.shown.filter(file => !file.isGenerated);
      item.description = `${work.filter(isChanged).length} of ${work.length} changed, ${ago(session.updated)}`;
      item.tooltip = `${session.title}\nSession ${session.session}\nLast edit ${new Date(session.updated).toLocaleString()}`;
      item.iconPath = new vscode.ThemeIcon('history');
      item.contextValue = 'session';
      return item;
    }
    const {session, file} = node;
    const item = new vscode.TreeItem(vscode.Uri.file(file.path), vscode.TreeItemCollapsibleState.None);
    item.id = `${session.session}:${file.path}`;
    const folder = vscode.workspace.asRelativePath(path.dirname(file.path), true);
    item.description = `${file.status.text}${file.isGenerated ? '  generated' : ''}  ${folder}`;
    item.tooltip = `${file.path}\n${file.status.text}`;
    item.contextValue = 'file';
    // A deleted file has nothing to open: its review is the page's.
    item.command = fs.existsSync(file.path) ?
      {command: 'vscode.open', title: 'Open File', arguments: [vscode.Uri.file(file.path)]} :
      {command: 'redpen.openReview', title: 'Open Review', arguments: [{session}]};
    return item;
  }
}

// The session's review page, its server started unless it runs.
function reviewUrl(session) {
  return new Promise((resolve, reject) => execFile('python3', [reviewScript, '--session', session],
      (error, stdout, stderr) => error ?
        reject(new Error(stderr.trim().split('\n')[0] || error.message)) :
        resolve(stdout.trim().split('\n').pop())));
}

// Opens the session's review page in an editor beside the code, or in the default browser where Cursor
// has no Simple Browser.
async function openReview(session) {
  let url;
  try {
    url = await reviewUrl(session.session);
  } catch (error) {
    vscode.window.showErrorMessage(`redpen did not start: ${error.message}`);
    return;
  }
  try {
    await vscode.commands.executeCommand('simpleBrowser.show', url);
  } catch {
    await vscode.env.openExternal(vscode.Uri.parse(url));
  }
}

function activate(context) {
  const provider = new EditsProvider();
  const view = vscode.window.createTreeView('redpen.files', {treeDataProvider: provider, showCollapseAll: true});

  const refresh = () => {
    provider.refresh();
    const changed = provider.changedFiles();
    view.badge = changed.length ? {value: changed.length, tooltip: `${changed.length} files with changes`} : undefined;
    view.message = provider.sessions.length ? undefined : 'No Claude Code edits in this workspace yet.';
  };
  let timer;
  const refreshSoon = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  };
  // review.py told which files are generated: the counts change.
  provider.onVerdicts = refreshSoon;

  fs.mkdirSync(storeDir, {recursive: true});
  const watcher = fs.watch(storeDir, {recursive: true}, refreshSoon);

  context.subscriptions.push(
      view,
      {dispose: () => watcher.close()},
      vscode.commands.registerCommand('redpen.refresh', refresh),
      // On a session, its page; from the view's title, the newest session's.
      vscode.commands.registerCommand('redpen.openReview', node => {
        const session = node?.session || provider.sessions[0];
        if (session) return openReview(session);
        vscode.window.showInformationMessage('No Claude Code edits in this workspace yet.');
      }),
      vscode.workspace.onDidSaveTextDocument(refreshSoon),
      vscode.workspace.onDidChangeWorkspaceFolders(refreshSoon),
      vscode.window.onDidChangeWindowState(state => state.focused && refreshSoon()),
      view.onDidChangeVisibility(event => event.visible && refreshSoon()));
  refresh();
}

function deactivate() {}

module.exports = {activate, deactivate, lineChanges};
