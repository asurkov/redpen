// Claude Edits: the files Claude Code sessions edited, as redpen's recorder keeps them in
// ~/.redpen/code/<session>/ (see ../recorder.py), for review. A file's changes are
// against its baseline: its text before the session's first edit to it, or as last kept. Clicking a file
// opens its changes side by side, where the diff editor's arrows revert single changes. Keep moves the
// baseline to the file as it is, so later edits show alone; Undo puts the baseline back into the file.
const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');

const storeDir = path.join(os.homedir(), '.redpen', 'code');
// Documents of this scheme hold a file's baseline: the path is the file's, the query the baseline's.
const baseScheme = 'claude-edits-base';

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

// Readers see the old file or the new one, never a part of it.
function writeAtomic(target, data) {
  fs.mkdirSync(path.dirname(target), {recursive: true});
  const tmp = path.join(path.dirname(target), `.tmp-${process.pid}-${Date.now()}`);
  fs.writeFileSync(tmp, data);
  fs.renameSync(tmp, target);
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
// no baseline until it is kept.
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
      for (const file of session.shown) file.status = fileStatus(file);
    }
    this.changed.fire();
  }

  // The listed files with changes, newest session first.
  changedFiles() {
    return this.sessions.flatMap(session => session.shown.filter(isChanged));
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
      const changed = session.shown.filter(isChanged).length;
      item.description = `${changed} of ${session.shown.length} changed, ${ago(session.updated)}`;
      item.tooltip = `${session.title}\nSession ${session.session}\nLast edit ${new Date(session.updated).toLocaleString()}`;
      item.iconPath = new vscode.ThemeIcon('history');
      item.contextValue = 'session';
      return item;
    }
    const {session, file} = node;
    const item = new vscode.TreeItem(vscode.Uri.file(file.path), vscode.TreeItemCollapsibleState.None);
    item.id = `${session.session}:${file.path}`;
    const folder = vscode.workspace.asRelativePath(path.dirname(file.path), true);
    item.description = `${file.status.text}  ${folder}`;
    item.tooltip = `${file.path}\n${file.status.text}`;
    item.contextValue = isChanged(file) ? 'file.changed' : 'file';
    item.command = {command: 'claudeEdits.openDiff', title: 'Open Changes', arguments: [node]};
    return item;
  }
}

function baseUri(file) {
  // The file's own path keeps its language for the diff editor.
  return vscode.Uri.from({scheme: baseScheme, path: file.path, query: file.base});
}

// Moves the file's baseline to the file as it is.
async function keep(file) {
  const doc = vscode.workspace.textDocuments.find(d => d.uri.scheme === 'file' && d.uri.fsPath === file.path);
  // Changes reverted in the diff editor count once saved.
  if (doc && doc.isDirty) await doc.save();
  // A deleted file has no text to keep.
  if (fs.existsSync(file.path)) writeAtomic(file.base, fs.readFileSync(file.path));
}

// Puts the file's baseline back into it.
async function undo(file) {
  const uri = vscode.Uri.file(file.path);
  if (!fs.existsSync(file.base)) {
    const answer = await vscode.window.showWarningMessage(`Move ${path.basename(file.path)} to the Trash?`,
        {modal: true, detail: 'Claude created it in this session.'}, 'Move to Trash');
    if (answer) await vscode.workspace.fs.delete(uri, {useTrash: true});
    return;
  }
  if (!fs.existsSync(file.path)) {
    await vscode.workspace.fs.writeFile(uri, fs.readFileSync(file.base));
    return;
  }
  // An edit of the document rather than of the disk, so Undo in its editor takes it back.
  const doc = await vscode.workspace.openTextDocument(uri);
  const edit = new vscode.WorkspaceEdit();
  edit.replace(uri, doc.validateRange(new vscode.Range(0, 0, doc.lineCount, 0)), fs.readFileSync(file.base, 'utf8'));
  await vscode.workspace.applyEdit(edit);
  await doc.save();
}

function activate(context) {
  const provider = new EditsProvider();
  const view = vscode.window.createTreeView('claudeEdits.files', {treeDataProvider: provider, showCollapseAll: true});
  const baseChanged = new vscode.EventEmitter();

  const refresh = () => {
    provider.refresh();
    const changed = provider.changedFiles();
    view.badge = changed.length ? {value: changed.length, tooltip: `${changed.length} files with changes`} : undefined;
    view.message = provider.sessions.length ? undefined : 'No Claude Code edits in this workspace yet.';
    // Shows Keep and Undo in the title bar of these files' editors.
    vscode.commands.executeCommand('setContext', 'claudeEdits.changedFiles', changed.map(file => file.path));
    // An open diff follows its baseline when Keep moves it.
    for (const doc of vscode.workspace.textDocuments) {
      if (doc.uri.scheme === baseScheme) baseChanged.fire(doc.uri);
    }
  };
  let timer;
  const refreshSoon = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  };

  // The listed files with changes that an editor's command acts on, newest session first.
  const listed = uri => {
    uri = uri || vscode.window.activeTextEditor?.document.uri;
    if (!uri) return [];
    const file = uri.scheme === baseScheme ? uri.path : uri.fsPath;
    return provider.changedFiles().filter(f => f.path === file);
  };

  fs.mkdirSync(storeDir, {recursive: true});
  const watcher = fs.watch(storeDir, {recursive: true}, refreshSoon);

  context.subscriptions.push(
      view,
      baseChanged,
      {dispose: () => watcher.close()},
      vscode.workspace.registerTextDocumentContentProvider(baseScheme, {
        onDidChange: baseChanged.event,
        provideTextDocumentContent: uri => readText(uri.query) || '',
      }),
      vscode.commands.registerCommand('claudeEdits.refresh', refresh),
      vscode.commands.registerCommand('claudeEdits.openFile', node =>
        vscode.commands.executeCommand('vscode.open', vscode.Uri.file(node.file.path))),
      vscode.commands.registerCommand('claudeEdits.openDiff', node => {
        const name = path.basename(node.file.path);
        if (!fs.existsSync(node.file.path)) {
          return vscode.commands.executeCommand('vscode.open', baseUri(node.file));
        }
        return vscode.commands.executeCommand('vscode.diff', baseUri(node.file),
            vscode.Uri.file(node.file.path), `${name}: Claude's changes`);
      }),
      // From an editor, Keep applies to every session that edited the file, Undo to the newest one.
      vscode.commands.registerCommand('claudeEdits.keepFile', async arg => {
        for (const file of arg?.kind === 'file' ? [arg.file] : listed(arg)) await keep(file);
        refresh();
      }),
      vscode.commands.registerCommand('claudeEdits.undoFile', async arg => {
        const file = arg?.kind === 'file' ? arg.file : listed(arg)[0];
        if (file) await undo(file);
        refresh();
      }),
      vscode.commands.registerCommand('claudeEdits.keepSession', async node => {
        for (const file of node.session.shown.filter(isChanged)) await keep(file);
        refresh();
      }),
      vscode.commands.registerCommand('claudeEdits.dismiss', async node => {
        const {session} = node;
        const answer = await vscode.window.showWarningMessage(
            `Dismiss "${session.title}"?`,
            {modal: true, detail: `Its record of ${session.files.length} files and their baselines is deleted. The files stay as they are.`},
            'Dismiss');
        if (answer !== 'Dismiss') return;
        fs.rmSync(session.folder, {recursive: true, force: true});
        refresh();
      }),
      vscode.workspace.onDidSaveTextDocument(refreshSoon),
      vscode.workspace.onDidChangeWorkspaceFolders(refreshSoon),
      vscode.window.onDidChangeWindowState(state => state.focused && refreshSoon()),
      view.onDidChangeVisibility(event => event.visible && refreshSoon()));
  refresh();
}

function deactivate() {}

module.exports = {activate, deactivate, lineChanges};
