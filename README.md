# redpen

Review of what a coding agent changed, change by change: accept, reject, revisit, comment, with the
comments handed back to the agent. Written for Claude Code and packaged as its skill (`SKILL.md`), kept in
`~/.claude/skills/redpen`; [what ties it to Claude Code](#what-is-tied-to-claude-code) is below.

| Part | What it reviews | Where |
|---|---|---|
| `code/` | the file edits of an agent's session | a local page, shown in the Claude desktop app's Browser pane; Cursor |
| `prose/` | the changes to a repo's documents: Markdown, ReSpec specs, a GitHub PR | a local page; its own skill, `/docdiff` |

`base/` is what the two share: so far the server, one for both parts' pages (127.0.0.1:7878), with one
index of every review, and the channel that hands a page's comments and requests to the agent.

## What is tied to Claude Code

The review is not. `code/` reviews a record of plain files (for each edited file, its path and its text
before the session's first edit), and `prose/` reviews what git has. The ends are:

| | `code/` | `prose/` |
|---|---|---|
| What records the changes | `recorder.py`, a Claude Code hook on its edit tools, and on its shell commands for what they change in a git work tree | nothing: git has them |
| How a comment reaches the agent | `review.py --wait`, a command the skill has Claude keep waiting: a comment is handed over as it is made; else `review.py --prompt-hook`, a Claude Code hook, with the user's next message | `base/channel.py --wait`, a command the skill has Claude keep waiting on the review's `revisit.jsonl` |
| Where the state is kept | `~/.redpen/code/<session>/`, under Claude Code's session id | `~/.docdiff/<name>/` |

`review.py --git` needs neither end to show a page: it reviews a git work tree against a commit, whoever
changed the files (`~/.redpen/code/git-<…>/`).

For another agent those ends have to be written: something that saves a file's text before the agent's
first edit to it, which does not exist yet, and a way to hand the agent a comment: `base/channel.py
--wait` prints them for any agent that can keep a command running.

## code/

| File | What |
|---|---|
| `recorder.py` | the hook that records a session's edits, and what its shell commands change in a git work tree: each file's baseline, in `~/.redpen/code/<session>/`; it deletes the records idle for 30 days (`prune`) |
| `review.py` | answers the review page's requests (`/code/…` on the server), hands the page's comments to Claude (`--wait`, the command Claude keeps waiting, and `--prompt-hook`, the hook), puts Claude's answer under a comment (`--answer`, with `--change` a link to the change made in answer), keeps the overview Claude writes when asked (`--outline`), and says which files their projects generate (`--generated`) |
| `page/` | the review page |
| `cursor.py` | writes the record into Cursor as its inline diffs |
| `extension/` | redpen in Cursor: a status-bar item, "redpen · N to review", that opens the review |
| `mod/` | redpen in Claude Code itself: a row above the prompt with Open review, and the edited files in a pane (`/edits`), a plugin read from this folder |
| `vendor/fetch_monaco.py` | fetches the page's editor, Monaco, into `vendor/monaco/` (not kept in the repository) |
| `tests/` | `review.py` on git reviews of scratch repositories and `recorder.py` through its hooks, in a scratch home: `python3 -m unittest discover -s code/tests` (a few seconds) |

## The review page

The page shows what the session's Edit and Write calls changed, and what its shell commands changed
in a git work tree (a `sed -i`, a script, a formatter, an `rm` or `mv`), but not outside one; a
[git review](#a-git-review) shows a work tree's every change.

- **Pending** is what is not accepted yet (accepted text → the file). **Accept** records a change as
  accepted and leaves the file alone. **Reject** writes the old lines back into the file on disk, right
  away; rejecting a file the session created moves it to the Trash, and takes away the folders that
  leaves empty (a moved folder's), never the review's top nor above it, nor a folder with anything
  in it.
- **Accepted** is what was accepted (the text before the session → accepted text). **Unaccept** makes
  a change pending again; the file is not touched.
- A file deleted is "gone", its name struck through in the removed lines' colour, in the list and
  the file bar, and so is a folder gone with it; one made is "new", its name in the added lines'
  colour. A file moved or renamed
  (gone from one place, and a new file at least half of whose lines are its old ones, as git finds
  renames: the same text first, then the same name, then any; in a refactor of thousands, only files
  of one extension are compared) is one row at its new place, `old → new` when renamed in its
  folder, else its name and, quietly, "from" its old path, its diff against its old text. It
  is accepted or rejected whole: **Accept** keeps the move, **Reject** puts the file back at its old
  place, the new one to the Trash; History has one line for it, and one Undo. A file renamed by its
  case alone (`git mv Foo.js foo.js`), which macOS's file system finds under either name, is such a
  rename, `Foo.js → foo.js`; its Reject takes the new name away first, then puts the old one back. A
  case rename git does not see (a plain `mv`) is not shown.
- A symbolic link made, retargeted or deleted (in a git review, or by a session's shell command), or
  a file a link took the place of or gave its place to, is a "link" row: its text is
  `symbolic link → target`, accepted or rejected whole, and **Reject** puts the link back, or takes a
  new one away, not a file with its target's path in it. A file edited through a link is that file.
- A file made executable, or no longer (the bit git keeps), says `+x` or `−x`, in the list and the
  file bar; one that changed no more than that is listed `+0 −0`. The whole file's Accept, Reject and
  Unaccept take the bit along, and Undo puts it back; a deleted script put back is executable again.
  A git review reads the bit from git, a session from the file at its first edit and from a `chmod`
  that names the file.
- A file that is not text (not UTF-8, or past 16 MB) is "binary" and accepted or rejected whole. An
  image (PNG, JPEG, GIF, WebP, AVIF, BMP, ICO) shows its two versions side by side, each with its
  size in pixels and bytes; SVG is text, and diffed as text.
- The diff compares lines, not how they end. A change of the line endings alone says so ("Only the
  line endings changed: LF → CRLF"), accepted or rejected whole; one beside other changes is named
  in the file bar. A line ending the page's editor and the server split differently (a lone CR)
  leaves the file's changes to be accepted or rejected whole. A git review takes a file's old text as
  checking it out writes it: the repository's line endings and filters (Git LFS's say) applied.
- A text with more than 20,000 lines changed (added and removed, a lockfile or a bundle say), or one
  the editor gave up comparing (after 5 seconds, it calls the whole file one change), is counted, not
  compared: accepted or rejected whole. **Show the diff anyway** compares it all the same, which may
  stall the page for seconds.
- **All** is everything the session changed, pending or accepted, with no buttons: the file as it is,
  its changes marked in the margin (green added, blue changed, a red notch where lines were removed)
  and on the scrollbar. **Diff** in the file bar, or `d`, shows the removed lines and colours again.
- **History** lists every accept, reject and unaccept with Undo/Redo. An entry whose text changed
  since is greyed out.
- A comment (select text → Comment, Comment under a change, or Comment in the file bar for the
  whole file, whose thread stands above its first line) stays under its lines as a thread,
  which says whether it is sent and whether Claude has it, and shows Claude's answer under it.
  **Reply** adds to the thread, **Resolve** takes it off the page. An answer that came with a change
  links to that change. The header counts the open threads, with how many Claude answered last, and
  steps to the next one on a click; the strip at the bottom lists the threads of every file. While Claude's
  waiting command runs (SKILL.md, "Waiting for comments"), a comment goes to Claude as it is made, and its thread says so
  until the answer comes. Otherwise it goes to Claude when the command next starts, or with the user's
  next message in that session, through the `UserPromptSubmit` hook
  `python3 ~/.claude/skills/redpen/code/review.py --prompt-hook` in `~/.claude/settings.json`; without
  that hook they stay unsent.
- **Generated files** fold into "N generated" at the end of the list, with **Accept generated** for
  those with something pending, as one History line: the files the project's `.gitattributes` marks
  `linguist-generated` (as GitHub reads it), and where it says nothing, lockfiles (`package-lock.json`,
  `yarn.lock`, `Cargo.lock`, `go.sum`, …) and files whose first five lines carry a generator's banner:
  `@generated`, "generated" with "do not edit" (Go's `Code generated … DO NOT EDIT.`, protoc's,
  Prisma's), or "this file is autogenerated" (pip-compile's), not "DO NOT EDIT" alone;
  `-linguist-generated` (or `=false`) keeps a file out. They open and reject as any file,
  but the view counts and the progress leave them out, `n` and `p` step past them, the overview does
  not describe them, and the row above the prompt, the pane and the Cursor status-bar item do not count them
  (`review.py --generated PATH...` says which are).
- **Overview** (header) opens a drawer with the changes by purpose, as Claude described them: items,
  each a title, a sentence or two, and its blocks, `file:lines` and a short note, each a way to its
  lines; ↑ and ↓ step through the blocks in the overview's order. An item says how many of its blocks
  are pending, and its **Accept** accepts what is pending in them, as one History line with one Undo (a
  block on a whole file, or on a deleted one, takes the file whole; a moved file and a link are left
  to their own file bar). A block follows its lines when lines above them move, and is struck through
  as changed since when they are gone. **Not in the overview** lists the changes no block covers, so
  nothing hides behind it. **Describe** asks Claude for the overview, or for a new one, the way a
  comment goes: at once while the waiting command runs, else with the next message. **Comment** on an
  item ("why this approach?") goes to Claude the same way, quoting the item; its thread stands under
  the item, with Claude's answer, **Reply** and **Resolve**, and the strip at the bottom lists it as
  "Overview: <title>", a click opening the drawer at it.
- Keys: `n` and `p` (or `j` and `k`) step to the next and the previous change, past a file's last or
  first one into the neighbouring file. The current change is the one the cursor is in, with the
  tinted bar: `a` accepts it in Pending and unaccepts it in Accepted, `r` opens a comment on the
  selected text, or else on it. Reject has no key. With lines selected, `a` and the Accept and
  Reject buttons at the selection, beside Comment, take only the selected lines of the
  changes, whole lines; removed lines go with the lines on both sides of them. `t` and `Shift+t` step to the next and the
  previous thread, through every file's.
- The header says how many files are reviewed (nothing of them pending) of those the review has; in
  the list such a file has ✓, and one accepted in part ◐. In Pending a reviewed file stays where it
  was, dimmed, with ✓ and its lines added and removed faded; `n` and `p` pass it, and a click opens
  it in Accepted. **Reviewed**, in the header, leaves such files out; the browser keeps the choice.
  Unchanged files are lighter still, with no counts.
- The list nests the files in their folders, a folder holding only one folder joined with it
  (`tests/www/unit`). A folder has ✓ when nothing in it is pending and ◐ when part of it is accepted,
  and, folded, its lines added and removed. Stepping into a file opens the folders it is in. A
  folder's row, under the pointer, has the view's whole-file actions for every file in it with
  something in the view (Accept and Reject in Pending, Unaccept in Accepted): one line in History,
  one Undo. On a folder gone with its files, Accept takes their deletion, Reject puts them back.
- The tree starts at the top of the repository (a git review's work tree; for a session, the
  repository its files are in, else the folder they all are in). Each folder shows its changed files
  and ends in "N more" for the rest, what git ignores left out; a folder name's tooltip says how much
  it leaves out. A click on a folder's name, also within a joined
  row (`www` in `tests/www/unit`), or on "N more" widens it to everything in it, its folders folded; a
  click on the name of a widened one narrows it again; the arrow and the rest of the row fold it.
  **Unchanged**, in the header, starts each folder holding a changed file widened; the browser keeps
  the choice. An unchanged file is greyed and opens as it is, with nothing to accept or comment on;
  `n` and `p` still step through the changed files. The open folders follow the disk within two
  seconds.
- Files under `~/.claude` and temporary folders are listed apart, under "other", but for those in a
  git repository of their own under `~/.claude`, such as a skill's: they are the work, in its tree.
- The page follows the files: an edit by Claude or the user shows up within two seconds. An action
  on a text that changed since the page showed it is refused, and the page shows it anew.
- A session's diff is against the file before the session's first edit to it, so a change made
  since by someone or something else (another session, the user, a formatter, a shell command the
  recorder does not follow) shows as the session's. Such a file says **edited elsewhere**, in the
  list and the file bar, from the moment it differs from how the session's last edit (or the page's
  Reject) left it, for good once the session edits it again; Reject's tooltip warns that it undoes
  those changes too.
- A link into the page, `<page URL>#<path>:<line>`, opens it at that file and line (see SKILL.md).

### A git review

The same page for a git work tree: every file that differs from a commit, whoever changed it and how
(any session, a shell command, the user), untracked files included and ignored ones not, and in each
submodule that differs, its files that differ from the commit the base keeps for it (a submodule
moved to another commit shows the files that commit changed; one added or taken away, or whose base
commit it does not have, is not followed; Reject writes its files, not its commit). Use it for a
whole change, such as a CL made over several sessions. `REPO` is a folder in the work tree (default: the
current one). The base is the commit the work tree and `REV` both come from: `--base origin/main` for a
branch with its commits, `--base HEAD` for uncommitted work alone. With no `--base`, `REV` is the
branch's upstream, else `HEAD`. The script prints the page's URL,
`http://127.0.0.1:7878/code/s/git-<folder>-<path hash>-<commit>/`, which Claude opens in the Browser pane.

- Pending, Accept, Reject, Accepted, Unaccept, History, comments and keys work as for a session, with
  the base commit's text in place of the text before the session. Reject writes into the work tree;
  git's index and commits are never written.
- The review of a branch against another commit than `HEAD` follows its base. After a rebase, the
  page, loaded again, or the command, run again, carries the record over to the new base commit, under
  the same URL (the `<commit>` in it is the one the review started from): a file upstream did not
  change stays as it is; in one it changed, what was accepted is put into the new text again where
  it fits, and where it does not, nothing of the file is accepted and the list marks it `rebased`;
  a file that is the base's again leaves the review. The page says so once, and History has a line.
  A review is found by its repository, `REV` and branch.
- Against `HEAD`, or with no branch checked out, what was accepted stays accepted while the base
  commit is the same; another base commit (after a commit) is another review, with nothing accepted.
  Once an amend or a rebase rewrote the branch, so that `HEAD` no longer comes from the base, the
  review against `HEAD` is carried over to `HEAD` as above, under the same URL: with everything
  committed it has nothing pending.
- The page shows the files the record has at once and looks for others behind it (the header says
  so while it does). A file that changes after the page was opened appears when the page is reloaded
  or the command is run again.
- Comments reach the session that ran the command last: at once while its `review.py --wait` runs
  (the one command waits on the git reviews too), else with the user's next message there.
The page's editor is Monaco, in `code/vendor/monaco/`; `code/vendor/fetch_monaco.py` fetches it where it
is missing.

## code/ in Cursor

For each file this session edited, the script writes the file's baseline (its text before the session's
first edit to it, or as last accepted on the review page) into Cursor's state database
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

### The status-bar item

The redpen extension in `code/extension/` is one item in Cursor's status bar, "redpen · N to review":
the files in the window's folders that sessions of the last 12 hours changed and that differ from
their baselines, generated ones aside. It updates as each edit lands, without a reload, and hides with
nothing to review. A click opens the session's review page in Cursor's Simple Browser, beside the
code, starting redpen's server unless it runs; with several sessions, it asks which (its tooltip lists
them). Accept, Reject and comments are the page's; the item only reads the record. The command
palette has it as "redpen: Open Review".

Install or update the extension with `python3 ~/.claude/skills/redpen/code/extension/install.py`, which
installs it into every Cursor profile (each has its own extensions; `--profile <name>` picks one) and
takes out the Claude Edits extension it replaces (`local.claude-edits`), then reload the window.
Cursor's Agents Window runs only Cursor's built-in extensions, so the item shows only in editor windows.
`install.py --uninstall` removes it.

## code/ in Claude Code

The mod in `code/mod/` (the plugin `redpen`) is the review page's way in from Claude Code, in the
terminal and in the desktop app. While the session's work has changes, a row above the prompt counts
them beside Open review, leaving the side of the window to the page; Claude's own and temporary files,
and generated ones, are not counted there. `/edits` opens a pane that lists the files this session
edited with their lines added and removed, each a way to its review (a link into the page). The mod
only reads the record: the review itself is the page. Open review opens
this session's review page in the desktop app's Browser pane through its MCP server (`Claude Browser`),
in the tab it opened before while that tab stays open, and brings the pane to the front (`ccd_view`'s
`show_pane`); from the terminal, in the default browser.

In the desktop app the review also opens by itself, the same way, as a turn ends that edited the work:
a file of the work, not Claude's own, temporary or generated, that the session edited in the turn (its
recorder's `after/` mark rewritten, a subagent's edit included) and that still differs from its
baseline. A turn that used the Browser pane itself leaves it as the turn left it, and an interrupted or
failed turn, or one in the terminal, leaves it to the row.

The folder is its own marketplace, `redpen`, and the plugin is read from it as it is on disk, so an edit
there reaches a session with `/reload-plugins`. It loads in every session once the user has installed it
(the commands change `~/.claude/settings.json`, so they are the user's to run, from a terminal):

```bash
claude plugin marketplace add ~/.claude/skills/redpen/code/mod
claude plugin install redpen@redpen --scope user
```

`claude plugin uninstall redpen@redpen` removes it. Its tests: `claude plugin test
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

`code/recorder.py` runs as a PreToolUse and PostToolUse hook on Edit|Write|MultiEdit|NotebookEdit|Bash (set in
`~/.claude/settings.json`) and records every session's edits as they happen in
`~/.redpen/code/<session>/`: `session.json` (title, time of the last edit), `files/<key>.json` (the
path, whether the session created the file, whether it is a link, and whether it was executable
before the session and as accepted) and `base/<key>` (the file's baseline: its text before
the session's first edit to it, or as last kept or accepted; a file the session created has none until
then). The title is the one the desktop app shows, else the session's first prompt. Of a shell
command, what it did in a git work tree the session works in (the command's folder's, its recorded
files', and those the paths it names are in, a submodule's included) is recorded: a tracked file it
changed or deleted, with its last committed text as its baseline, and a file it made, as new; git's
status before and after the command tells them, of the places the command names (the whole tree when
it names none; an `mv`'s destination too, not there yet, so a folder moved is its files gone and made,
which the page pairs as moves), each changed file by its signature. A command made of reading ones
alone (`ls`, `cat`, `grep`, `git log`, `sed` without `-i`, …) writing no output into a file is not
watched; one is about 35 ms in all, 65 with git asked in a tree like camperaid's. A file changed
already before the command (its last committed text then is no exact baseline), or one a command
running over 30 seconds changed (someone else may have), is marked as edited elsewhere; a file another
session's own edit left as it is during the command is that session's, not recorded. A tree whose
status took git over 1.5 seconds is watched, for a day, only around an `rm`, `mv`, `unlink`, `trash` or
`find -delete` (`~/.redpen/code/slow-trees.json`). The review page keeps its own state beside them, in the record's `review/`
folder; of the comments: `comments.json`, those waiting to be sent, `sent.jsonl`, the batches sent,
`handed.jsonl`, which of the batches Claude was handed and how (`base/channel.py` keeps it),
`answers.jsonl`, Claude's answers and the threads the user resolved, and `outline.json`, the overview
(an ask for one goes among the comments as `{describe: true}`).

A git review's record, `~/.redpen/code/git-<…>/`, has the same files, made by `review.py --git` from a
commit, and `git.json` (repository, base commit, branch, title, the session its comments reach, the
last rebase it was carried over) in place of
`session.json`, so the Cursor status-bar item and the redpen pane do not count it.

Records are kept until nothing was done in one for 30 days: no edit recorded, no action, comment or
overview on its page. Then the recorder's post hook, which looks once a day (`~/.redpen/code/.pruned`
says when), deletes it, pending changes or not: by then the files' history is the review, and the files
themselves are never touched. A session's own record is never deleted by its own hook.
`python3 ~/.claude/skills/redpen/code/recorder.py prune [DAYS] [--dry-run]` deletes those idle for
`DAYS` (30) now, or says which it would and their size.

## prose/

docdiff, merged in with its history. It keeps its name, its skill (`/docdiff`, in `prose/SKILL.md`), its
reviews' addresses and its state.

| File | What |
|---|---|
| `docdiff.py` | builds a review's page from two revisions of a repo (rendered with pandoc), and answers every review's requests |
| `plugins/` | review comments and questions to Claude, ReSpec specs, a GitHub PR, Google Docs and their comments |

## base/

| File | What |
|---|---|
| `server.py` | the server (127.0.0.1:7878): the index of every review, `/code/…` handed to `code/review.py` and the rest to `prose/docdiff.py`, each run as it is on disk; `--restart`, `--stop`, and `--install-agent` for a LaunchAgent that keeps it running |
| `channel.py` | the channel from a page to the agent: the asks a page sends, kept in a log (`prose/`'s `revisit.jsonl`, `code/`'s `sent.jsonl`), each handed over once, with `handed.jsonl` beside it saying by what; `--wait` prints the asks nobody was handed, or waits for the next |

## Setup

```bash
python3 ~/.claude/skills/redpen/code/vendor/fetch_monaco.py
```

The hooks, in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [{ "type": "command", "command": "python3 ~/.claude/skills/redpen/code/recorder.py pre" }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [{ "type": "command", "command": "python3 ~/.claude/skills/redpen/code/recorder.py post" }]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/skills/redpen/code/review.py --prompt-hook", "timeout": 5 }
        ]
      }
    ]
  }
}
```

The redpen status-bar item, for Cursor:

```bash
python3 ~/.claude/skills/redpen/code/extension/install.py
```

The redpen pane, for Claude Code:

```bash
claude plugin marketplace add ~/.claude/skills/redpen/code/mod
claude plugin install redpen@redpen --scope user
```

`/docdiff`: a link that makes `prose/` a skill folder of its own. docdiff, gdocsync and a LaunchAgent
installed before `base/server.py` name `~/.claude/skills/docdiff/docdiff.py`, and reach it through the
link.

```bash
ln -s redpen/prose ~/.claude/skills/docdiff
```
