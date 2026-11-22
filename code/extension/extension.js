// redpen in Cursor: a status-bar item that counts the files Claude Code sessions changed in the
// window's folders, as redpen's recorder keeps them in ~/.redpen/code/<session>/ (see
// ../recorder.py), and opens their review on redpen's page. A file is to review while it differs from
// its baseline: its text before the session's first edit to it, or as last accepted on the page.
// Only the sessions of the last hours count, and not the files their projects generate. The item
// only reads the record: the page alone writes it.
const vscode = require('vscode');
const fs = require('fs');
const os = require('os');
const path = require('path');
const {execFile} = require('child_process');

const storeDir = path.join(os.homedir(), '.redpen', 'code');
const reviewScript = path.join(os.homedir(), '.claude', 'skills', 'redpen', 'code', 'review.py');
// A session with no edit for this long is not counted: its review waits on the page, not here. As
// long as review.py's active_seconds.
const recentMs = 12 * 3600 * 1000;

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

function inWorkspace(file) {
  return (vscode.workspace.workspaceFolders || []).some(
      folder => file.startsWith(folder.uri.fsPath + path.sep));
}

// Whether a file differs from its baseline. A file the session created has none until accepted; one
// whose old text is missing is not counted.
function isChanged(file) {
  const base = readText(file.base);
  if (base === null && !file.new) return false;
  const now = readText(file.path);
  if (now === null) return base !== null;
  return (base ?? '') !== now;
}

// Whether a file is one its project generates, as review.py says (its .gitattributes'
// linguist-generated, lockfiles, a generator's banner), by path, with when it said so: asked again
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

// The sessions of the last hours with files to review in the window's folders, newest first, each
// with those files' paths. Asks review.py about the paths it has not said of, and calls asked when it
// said something new.
function sessionsToReview(asked) {
  let names;
  try {
    names = fs.readdirSync(storeDir);
  } catch {
    return [];
  }
  const sessions = [];
  const paths = new Set();
  for (const name of names) {
    const folder = path.join(storeDir, name);
    const info = readJson(path.join(folder, 'session.json'));
    if (!info || !(Date.now() - Date.parse(info.updated) < recentMs)) continue;
    let entries;
    try {
      entries = fs.readdirSync(path.join(folder, 'files'));
    } catch {
      continue;
    }
    const changed = [];
    for (const entry of entries) {
      // The hooks write each file whole under a dot name, then rename it.
      if (entry.startsWith('.') || !entry.endsWith('.json')) continue;
      const file = readJson(path.join(folder, 'files', entry));
      if (!file || !inWorkspace(file.path)) continue;
      paths.add(file.path);
      const base = path.join(folder, 'base', entry.slice(0, -'.json'.length));
      if (!generatedVerdicts.get(file.path)?.isGenerated && isChanged({...file, base})) changed.push(file.path);
    }
    if (changed.length) sessions.push({...info, changed});
  }
  askGenerated([...paths], asked);
  return sessions.sort((a, b) => b.updated.localeCompare(a.updated));
}

function ago(iso) {
  const minutes = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} h ago`;
}

const counted = count => `${count} file${count === 1 ? '' : 's'} to review`;

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
  const item = vscode.window.createStatusBarItem('redpen.review', vscode.StatusBarAlignment.Left, 100);
  item.name = 'redpen';
  item.command = 'redpen.openReview';
  let sessions = [];
  let timer;
  const refreshSoon = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  };
  const refresh = () => {
    sessions = sessionsToReview(refreshSoon);
    const files = new Set(sessions.flatMap(session => session.changed));
    if (!files.size) {
      item.hide();
      return;
    }
    item.text = `$(checklist) redpen · ${files.size} to review`;
    item.tooltip = new vscode.MarkdownString(['Open the review of Claude\'s changes', '',
      ...sessions.map(session => `- ${session.title}: ${counted(session.changed.length)}, ${ago(session.updated)}`)].join('\n'));
    item.show();
  };

  fs.mkdirSync(storeDir, {recursive: true});
  const watcher = fs.watch(storeDir, {recursive: true}, refreshSoon);

  context.subscriptions.push(
      item,
      {dispose: () => watcher.close()},
      // One session's page at once; with several, the one picked.
      vscode.commands.registerCommand('redpen.openReview', async () => {
        refresh();
        if (sessions.length < 2) {
          return sessions[0] ? openReview(sessions[0]) :
            vscode.window.showInformationMessage('Nothing of Claude\'s to review in this window.');
        }
        const picked = await vscode.window.showQuickPick(sessions.map(session => ({
          label: session.title,
          description: `${counted(session.changed.length)}, ${ago(session.updated)}`,
          session,
        })), {placeHolder: 'The review of which session?'});
        if (picked) return openReview(picked.session);
      }),
      vscode.workspace.onDidSaveTextDocument(refreshSoon),
      vscode.workspace.onDidChangeWorkspaceFolders(refreshSoon),
      vscode.window.onDidChangeWindowState(state => state.focused && refreshSoon()));
  refresh();
}

function deactivate() {}

module.exports = {activate, deactivate};
