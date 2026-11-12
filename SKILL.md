---
name: redpen
description: "Review this Claude Code session's file edits, change by change: on a page in the desktop app's Browser pane, the way Cursor shows its agent's edits (each file whole with red and green lines, Accept/Reject per change and per file, an Accepted view to revisit and unaccept, undo history, comments on lines that reach Claude at once or with the next message), or in Cursor itself as its own inline diffs with Undo/Keep. Use when the user invokes /redpen, or asks to review, accept or reject Claude's edits in Claude's own UI, or to see or review them in Cursor, \"Cursor style\", or as Cursor inline diffs."
argument-hint: "[cursor] [--session ID] [--workspace ID] [--clear]"
---

# redpen

Review of what Claude changed, change by change. `code/` reviews a session's file edits in two places:
on the review page (`/redpen`) and in Cursor (`/redpen cursor`). Both read the record at the end.

`prose/` is docdiff, the review of a repo's documents. It is a skill of its own, `/docdiff`, found
through `~/.claude/skills/docdiff`, a link to `prose/`; its instructions are `prose/SKILL.md`.

## The review page

```bash
python3 ~/.claude/skills/redpen/code/review.py
```

The script starts redpen's server ([below](#the-server)) unless it runs, and prints the page's URL for
this session, `http://127.0.0.1:7878/code/s/<session>/`. Open that URL in the Browser pane
(`preview_start` with `url`, or `navigate`) and tell the user it is open. Do not click Accept, Reject or
Unaccept there yourself, nor press `a`: those are the user's.

The page shows what the session's Edit and Write calls changed, not what a shell command changed.

What to tell the user when it matters:
- **Pending** is what is not accepted yet (accepted text → the file). **Accept** records a change as
  accepted and leaves the file alone. **Reject** writes the old lines back into the file on disk, right
  away; rejecting a file the session created moves it to the Trash.
- **Accepted** is what was accepted (the text before the session → accepted text). **Unaccept** makes
  a change pending again; the file is not touched.
- **All** is everything the session changed, pending or accepted, with no buttons.
- **History** lists every accept, reject and unaccept with Undo/Redo. An entry whose text changed
  since is greyed out.
- A comment (select text → Comment, or Comment under a change) waits in the strip at the bottom.
  While Claude's waiting command runs (below), the strip has **Send now**, which hands the comments to
  Claude at once. Otherwise they go to Claude with the user's next message in that session, through
  the `UserPromptSubmit` hook `python3 ~/.claude/skills/redpen/code/review.py --prompt-hook` in
  `~/.claude/settings.json`; without that hook they stay in the strip.
- Keys: `n` and `p` (or `j` and `k`) step to the next and the previous change, past a file's last or
  first one into the neighbouring file. The current change is the one the cursor is in, with the
  tinted bar: `a` accepts it in Pending and unaccepts it in Accepted, `r` opens a comment on the
  selected text, or else on it. Reject has no key.
- Files under `~/.claude` and temporary folders are listed apart, under "other".
- The page follows the files: an edit by Claude or the user shows up within two seconds.

**Waiting for comments.** After opening the page, keep one background Bash command
(`run_in_background`) waiting for the comments the user sends from it:

```bash
python3 ~/.claude/skills/redpen/code/review.py --wait
```

It prints the comments sent with Send now and ends; those sent between one wait and the next are
printed by the next as it starts. When it ends with comments, start it again at once, then answer or
act on each. A comment reaches you once: from this command, or from the `UserPromptSubmit` hook with
the user's next message.

Each comment names its file and lines and quotes them as the user saw them. A comment "of the accepted
text" or "of the file as it was before your edits" is on lines the file may no longer have.

`--session <id>` prints another session's page; the server's index lists every session with a record.
The page's editor is Monaco, in `code/vendor/monaco/`; `code/vendor/fetch_monaco.py` fetches it where it
is missing.

## In Cursor

```bash
python3 ~/.claude/skills/redpen/code/cursor.py
```

For each file this session edited, the script writes the file's baseline (its text before the session's
first edit to it, or as last kept in the Claude Edits sidebar) into Cursor's state database
(`~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`, table `cursorDiskKV`) as an
`inlineDiff:<workspaceId>:claude-<uuid>` record. When a window opens, Cursor diffs that old text against
the file on disk and shows the result like an agent edit. The baselines come from the record; a file
edited before the hooks were installed is added to it from Claude Code's file history
(`~/.claude/file-history/<session>/<sha256(path)[:16]>@v1`). A file the session created shows all green.

After running it, tell the user to reload the Cursor window that holds the files (Command Palette →
"Developer: Reload Window"); Cursor reads these records only when a window loads. Pass on what the script
printed: which workspace got how many files, and any skips.

What to tell the user when it matters:
- **Keep** keeps the file as it is on disk. **Undo** puts the old text back into the editor; the file
  on disk changes when it is saved. Undo on a file the session created empties it.
- The diff is the old text against the disk now, so it also shows changes made after the session's
  first edit by anyone else (the user, another session).
- Each file goes to the Cursor workspace whose folders hold it, the one used last when several do
  (a folder opened alone and a multi-root `.code-workspace` holding it are different workspaces).
  `--workspace <id>` picks one; the ids are the folder names under
  `~/Library/Application Support/Cursor/User/workspaceStorage/`.
- Skipped: files equal to their old text, deleted files, files outside every Cursor workspace, and files
  Cursor already has a pending diff of its own for (so Cursor's own agent review is not overwritten).
- Rerunning replaces this script's earlier diff for a file. `--clear` removes every diff the script
  wrote, writes nothing, and takes effect on the next reload.
- `--session <id>` shows another Claude Code session's edits (default: `$CLAUDE_CODE_SESSION_ID`).

The record format is Cursor's internal one, verified on Cursor 3.29.9; if a reload shows nothing, check
the window's dev tools console for `[inlineDiff]` lines. `REDPEN_CURSOR_USER_DIR` points the script at
another Cursor user data directory, such as a test profile started with `--user-data-dir`.

### Claude Edits sidebar

The Claude Edits extension in `code/extension/` shows the record in its own Activity Bar view, newest
session first, for review. It shows only the files inside the window's folders, each with its
`+added −removed` line counts against its baseline, and updates as each edit lands, without a reload;
the badge counts the files with changes. Clicking a file opens its changes side by side, where the diff
editor's arrows revert single changes in the file (saved like any edit). **Keep** (✓, on a file in the
list or in the title bar of its editor) moves the baseline to the file as it is, saving it first, so
Claude's next edits show alone; **Keep All** on a session does that for each of its files. **Undo** puts
the baseline back into the file as an editor edit that Cmd+Z takes back; for a file the session created,
it asks, then moves the file to the Trash. From an editor, Keep applies to every session that edited the
file and Undo to the newest one. The close button on a session dismisses it, deleting its record, not the
files. The view can be dragged into the secondary sidebar next to Cursor's Agents.

Install or update the extension with `python3 ~/.claude/skills/redpen/code/extension/install.py`, which
installs it into every Cursor profile (each has its own extensions; `--profile <name>` picks one), then
reload the window. Cursor's Agents Window runs only Cursor's built-in extensions, so the view shows only
in editor windows. `install.py --uninstall` removes it.

## In Claude Code: the Claude Edits pane

The mod in `code/mod/` (the plugin `edit-review`) shows the same record inside Claude Code, in the
terminal and in the desktop app: `/edits` opens a pane listing the files this session edited, each
opening as its changes, removed lines red and added lines green, with Keep and Undo per file or change.
Comments on changes and on single lines go to the model with the next message, and the status line
counts the files with pending changes.

The folder is its own marketplace, `redpen`, and the plugin is read from it as it is on disk, so an edit
there reaches a session with `/reload-plugins`. It loads in every session once the user has installed it
(the commands change `~/.claude/settings.json`, so they are the user's to run, from a terminal):

```bash
claude plugin marketplace add ~/.claude/skills/redpen/code/mod
claude plugin install edit-review@redpen --scope user
```

`claude plugin uninstall edit-review@redpen` removes it. Its tests: `claude plugin test
~/.claude/skills/redpen/code/mod`.

## The server

`base/server.py` is one server for both parts, on `127.0.0.1:7878`: the review pages under `/code/`,
docdiff's reviews under `/<name>/`, and at http://127.0.0.1:7878/ one index of both, the last active
first. It runs a part's code as it is on disk, loading a changed script, plugin or other module of
`base/` again at the next request, so only a change to `base/server.py` itself needs a restart:

```bash
python3 -I ~/.claude/skills/redpen/base/server.py --restart
```

`--stop` stops it, and `--port N` works with another server on port N, a test's say. Its log is
`~/.redpen/server.log`. The LaunchAgent that keeps it running is in `prose/SKILL.md`, under "The
server".

## The record

`code/recorder.py` runs as a PreToolUse and PostToolUse hook on Edit|Write|MultiEdit|NotebookEdit (set in
`~/.claude/settings.json`) and records every session's edits as they happen in
`~/.redpen/code/<session>/`: `session.json` (title, time of the last edit), `files/<key>.json` (the
path, and whether the session created the file) and `base/<key>` (the file's baseline: its text before
the session's first edit to it, or as last kept or accepted; a file the session created has none until
then). The title is the one the desktop app shows, else the session's first prompt. Edits made through
Bash are not recorded. The review page keeps its own state beside them, in the record's `review/`
folder; of the comments: `comments.json`, those waiting to be sent, `sent.jsonl`, the batches sent, and
`handed.jsonl`, which of the batches Claude was handed and how (`base/channel.py` keeps it).
