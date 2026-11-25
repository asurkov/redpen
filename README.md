# redpen

Review of what a coding agent changed, change by change: accept, reject, revisit, comment, with the
comments handed back to the agent. Written for Claude Code and packaged as its skill (`SKILL.md`), kept in
`~/.claude/skills/redpen`; [what ties it to Claude Code](#what-is-tied-to-claude-code) is below. `code/`
runs in Codex too, with its own skill and hooks in `codex/` ([code/ in Codex](#code-in-codex)).

| Part | What it reviews | Where |
|---|---|---|
| `code/` | the file edits of an agent's session | a local page, shown in the Claude desktop app's Browser pane or Codex's in-app browser; Cursor |
| `prose/` | the changes to a repo's documents: Markdown, ReSpec specs, a GitHub PR | a local page; its own skill, `/docdiff` |

`base/` is what the two share: so far the server, one for both parts' pages (127.0.0.1:7878), with one
index of every review, and the channel that hands a page's comments and requests to the agent.

## What is tied to Claude Code

The review is not. `code/` reviews a record of plain files (for each edited file, its path and its text
before the session's first edit), and `prose/` reviews what git has. The ends are:

| | `code/` | `prose/` |
|---|---|---|
| What records the changes | `recorder.py`, a Claude Code or Codex hook on its edit tools (Codex's `apply_patch`), and on its shell commands for what they change in a git work tree | nothing: git has them |
| How a comment reaches the agent | `review.py --wait`, a command the skill has Claude keep waiting: a comment is handed over as it is made; else `review.py --prompt-hook`, a Claude Code or Codex hook, with the user's next message, or in Claude Code with the mod's own prompt, sent while the session is idle or as its turn ends | `base/channel.py --wait`, a command the skill has Claude keep waiting on the review's `revisit.jsonl` |
| Where the state is kept | `~/.redpen/code/<session>/`, under Claude Code's session id or Codex's thread id | `~/.docdiff/<name>/` |

`review.py --git` needs neither end to show a page: it reviews a git work tree against a commit, whoever
changed the files (`~/.redpen/code/git-<…>/`).

Codex's ends are written: [code/ in Codex](#code-in-codex). For another agent they have to be: a hook
that saves a file's text before the agent's first edit to it, and a way to hand the agent a comment:
`base/channel.py --wait` prints them for any agent that can keep a command running.

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
in a git work tree (a `sed -i`, a script, an `rm` or `mv`), but not outside one: of a shell command,
only the files it names, by name anywhere in it (in a script on its input too), by a glob, or, for an
`rm` or `mv`, by a folder of theirs. A hook cannot tell who wrote a file, so a change the command does
not name (a formatter's on `.`, a background job's, another session's, an editor's) is left out rather
than guessed; a [git review](#a-git-review) shows a work tree's every change.

- **Pending** is what is not accepted yet (accepted text → the file). **Accept** records a change as
  accepted and leaves the file alone. **Reject** writes the old lines back into the file on disk, right
  away; rejecting a file the session created moves it to the Trash, and takes away the folders that
  leaves empty (a moved folder's), never the review's top nor above it, nor a folder with anything
  in it.
- **Accepted** is what was accepted (the text before the session → accepted text). **Unaccept** makes
  a change pending again; the file is not touched.
- **Accept all · N**, in Pending's header while anything is pending, is a rubber stamp: everything pending,
  accepted at once as one History line ("Accepted all · N files"), which one Undo or Cmd+Z takes back. The
  row above the prompt has it too (`review.py --accept-all`, through the server, under its lock); the
  files are never touched.
- **Commit**, in the header of a session's review, opens a drawer: by git work tree, the session's files
  to commit, ticked (nothing of them pending, their text not HEAD's; a moved file with its old place),
  those left out and why (still to review, edited elsewhere), and the message. The Claude Code mod drafts
  it by a fork of the session, in the style of the tree's last 10 subjects, a subject line alone, no
  attribution (`review.py --ask-commit`, `--commit-message`), and it lands in the box unless you typed
  there. **Commit** commits the ticked files alone, as they read (`git add -A -- <paths>`, `git commit -m …
  -- <paths>`), whatever else is staged; **Commit and push** pushes the branch too (60 seconds at most). A
  commit git refuses (a hook, say) leaves staged only what was, and says why; your click is the go-ahead,
  nothing commits without it. A git review has none: its changes are anyone's.
- A commit is a checkpoint: what it took of the session's changes counts as accepted, as one History
  line, "Committed in <sha> · N files", with Undo, and a toast says so on the open page. So after a
  commit Pending holds only what came after it, and All still everything since the session began.
  The review keeps each work tree's HEAD it saw last (`review/heads.json`, first noted when the record
  gets a file there); where HEAD moved, each file of the record the new commits touched gets the
  committed text as its baseline. Any commit counts, whoever made it; the recorder asks the server to
  look right after a git command of the session's that moves HEAD, and the page looks as it reads its
  list. A session's record is one, across all its commits. A file committed as it is before the review
  kept HEAD (its text HEAD's, its baseline not) counts as accepted too, once, at the first look
  (`review/settled` marks it done).
- A file a subagent of the session edited (the Agent tool) says so in the list: "by Explore", its type.
  The record holds the session's own edits and its subagents', never another session's.
- A file deleted is "gone", its name struck through in the removed lines' colour, in the list and
  the file bar, and so is a folder gone with it; one made is "new", its name in the added lines'
  colour. A file moved or renamed
  (gone from one place, and a new file at least half of whose lines are its old ones, as git finds
  renames: the same text first, then the same name, then any; in a refactor of thousands, only files
  of one extension are compared) is one row at its new place, `old → new` when renamed in its
  folder, else its name and, quietly, "from" its old path, its diff against its old text. Its changes
  are accepted and rejected one by one as any file's, at its new place; the move itself goes whole,
  with the file's: **Accept file** keeps the move, **Reject file** puts the file back at its old
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
  and on the scrollbar, the current change's lines tinted. **Diff** in the file bar, or `d`, shows the
  removed lines and colours again. The file bar's ↑ and ↓, at its right end in every view, step
  through the changes as `p` and `n` do.
- **History** lists every accept, reject and unaccept with Undo/Redo. An entry whose text changed
  since is greyed out. `Cmd+Z` (`Ctrl+Z`) undoes the newest entry not undone, again and again down
  the list; `Cmd+Shift+Z` redoes, in turn, what it undid, until the next accept or reject. In a
  comment box both are the box's own.
- A comment (select text → Comment, Comment under a change, or Comment in the file bar for the
  whole file, whose thread stands above its first line) stays under its lines as a thread,
  which says whether it is sent and whether Claude has it, and shows Claude's answer under it. The
  page names the session's agent, Claude or Codex (session.json's `agent`; a git review, that of the
  session its comments reach), in these words and over its answers; buttons are verbs alone. The
  page's own words around a comment (its box's heading and the note on where it goes), and a change's
  buttons, cannot be selected, and the page's text is not dragged anywhere but in a field: a stray drag
  once carried a box's note and a code line into the chat as the user's message.
  **Reply** adds to the thread, **Resolve** takes it off the page. An answer that came with a change
  links to that change. The header counts the open threads, with how many Claude answered last, and
  steps to the next one on a click; the strip at the bottom lists the threads of every file. While Claude's
  waiting command runs (SKILL.md, "Waiting for comments"), a comment goes to Claude as it is made, and its thread says so
  until the answer comes. Otherwise it goes to Claude when the command next starts, or with the user's
  next message in that session, through the `UserPromptSubmit` hook
  `python3 ~/.claude/skills/redpen/code/review.py --prompt-hook` in `~/.claude/settings.json`; without
  that hook they stay unsent. In a session with the Claude Code mod no message is needed: the mod sends
  one of its own, at once while the session is idle, else as its turn ends ([code/ in Claude
  Code](#code-in-claude-code)). The comment box and the thread say which: "Goes to Claude at once",
  "when its turn ends", or "with your next message in the chat".
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
  nothing hides behind it. An overview of a session's review asked for before the session's latest
  edits is not shown, as its items would pass for theirs: the drawer says when it was written, and the
  end of the turn asks for one anew (the page's Reject and a commit leave it shown). A git review's
  goes so once any change, anyone's, makes a work file's text other than the one it saw (`seen`;
  the page's own Reject and Undo follow it), and Describe asks for one anew. **Describe** asks Claude for the overview, or for a new one, the way a
  comment goes: at once while the waiting command runs, else with the next message; but in a session
  with the Claude Code mod, the mod sees it waiting within two seconds and asks it of a fork of the
  session at once, as Review's own ask, nothing in the chat. **Comment** on an
  item ("why this approach?") goes to Claude the same way, quoting the item; its thread stands under
  the item, with Claude's answer, **Reply** and **Resolve**, and the strip at the bottom lists it as
  "Overview: <title>", a click opening the drawer at it.
- Keys: `n` and `p` (or `j` and `k`) step to the next and the previous change, past a file's last or
  first one into the neighbouring file. The current change is the one the cursor is in, with the
  tinted bar: `a` accepts it in Pending and unaccepts it in Accepted, `r` opens a comment on the
  selected text, or else on it. `a` and a change's Accept, Reject and Unaccept leave the view where it
  is, for a careful review; **Space**, or the **↓** beside Accept (Unaccept in Accepted), does the same
  and then goes on to the next change as `n` does, for a quick one: each press chooses, no mode and no
  key held. Reject has no key. Shift means nothing here: Shift+click extends a selection, as in any
  editor. A button or folder reached by Tab takes Space as its own press; one clicked leaves Space
  to accept. The keys need the page to have the focus, which the app leaves in the chat when it opens
  the page; till then the current bar says "Click the page to use the keys" in the keys' place. With lines selected, `a`, Space and the Accept, ↓ and
  Reject buttons at the selection, beside Comment, take only the selected lines of the
  changes, whole lines; removed lines go with the lines on both sides of them. While those buttons,
  or a comment box, are open, the bars of the changes they stand over are hidden, their room kept.
  `t` and `Shift+t` step to the next and the previous thread, through every file's.
- Where the room is short the buttons show icons for their words, the tooltip saying what each is:
  the header's Files, Unchanged, Reviewed, Overview and History once its row has no room for the
  words and a title of 180px (then on two rows if it still has not), and a folder's Comment in a list
  320px wide or less. Accept and Reject keep their words.
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
  **Comment** there ("these belong under `lib/`") goes to Claude as a line's does, naming the folder
  and listing its changed files as they were; its thread stands under the folder's row, and the strip
  at the bottom lists it as "Folder: <path>", a click unfolding the folder at it.
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
- The page follows its own code too: the server stamps the page with its code's version as it sends it,
  and once that code changes and holds still for two looks (about four seconds; an edit of several of
  its files is not caught halfway), an open page loads itself again, unless a comment is being typed. A
  page that did not start (its code broken, or not loaded) keeps watching, and loads again once the code
  changes, so it heals when the code is fixed.
- A session's diff is against the file before the session's first edit to it, so a change made
  since by someone or something else (another session, the user, a formatter, a shell command the
  recorder does not follow) shows as the session's. Such a file says **edited elsewhere**, in the
  list and the file bar, from the moment it differs from how the session's last edit (or the page's
  Reject) left it, for good once the session edits it again; Reject's tooltip warns that it undoes
  those changes too. In Pending, a file with nothing pending does not say it (a formatter or a commit
  hook touched it since its review was done); Accepted and All, whose diffs show those changes, do. But once all of the session's changes to a file are accepted (or committed: the
  text its last edit left is HEAD's), a change since holds none of the session's, each time until the
  session edits it again: the page takes the file's text as its baseline (it shows under Unchanged,
  edited elsewhere, the text it took at marked in the record's `review/taken/`), the row above the
  prompt does not count it, and the session's next edit of it is all of its diff, not edited elsewhere.
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
  (the one command waits on the git reviews too), else by the mod's prompt there, or with the user's
  next message there.
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
terminal and in the desktop app. While the session's work has changes, a row above the prompt says, on one
line, `redpen · <what is new> +N · Review ×`, leaving the side of the window to the page. What is new is
what the session edited since you last looked (you look by closing the row or opening the review from it
or the pane): the overview's item that covers the newest edit, where the overview was written after it,
else that file's name, and +N the new files it leaves out; with nothing new, the count of the files
changed. Only that title gives way, cut at its end. The agents' own and temporary files (under
`~/.claude` and Codex's `~/.codex` (or `$CODEX_HOME`), unless in a git work tree of their own, as a
skill kept in its repository; the desktop app's side-session notes; `/tmp`), and generated ones, are
not counted; the page folds them apart, as "Claude's own" or "Codex's own" by the session's agent. **×** closes the row until the session edits a file of the work again: its recorder's
`after/` mark of the file rewritten. What the session edited before it started (a resume) is not new.

Opening the review yourself (from the row or the pane), and the end of a turn that edited the work (below), asks
Claude for an overview when the review has none yet, or has one the session's edits made old since it
was asked for (the recorder's `edited`, when they last changed the work, is not the one kept with it);
the changes its writer left out stay under **Not in the overview** and ask for nothing (an overview
kept before `edited` was, and a git review's, are asked for anew while they leave changes out): `review.py
--ask-overview` says whether one is needed, none while an ask waits (an ask Claude took 20 minutes ago
with no overview written waits no more: its turn was cut short), and prints the question, logged as
handed over: how to write it, the items written before, and the changes, each line numbered. Once an
overview saw the files (`seen` in `outline.json`: each work file's text as it read, kept among the
blobs), the ask that comes by itself is an update: the items numbered, with their blocks where their
lines are now, and only the changes since; the answer keeps items by their numbers
(`{"keep": [...], "items": [...]}`, an item with `replaces` in the place of the one it rewrites), so a
large overview is not written whole again for a turn's few edits. The user's Describe asks for the
whole anew. The mod
asks it of a fork of the session (`$.model.fork`): one question over the session's own conversation, so
Claude knows why it made the changes, with no tools, served mostly from the prompt cache, and nothing in
the chat, no turn. The answer, its JSON, goes to `review.py --outline`; one refused is asked again once,
with the refusal; no answer drops the ask (`review.py --drop-ask`), so it can be asked again. Each
step leaves a line in the record's `review/ask.log`, a failure a toast, and the overview written a toast
too. So the overview and the row's titles keep up without Describe. `/edits` opens a pane that lists the files this session
edited with their lines added and removed, each a way to its review (a link into the page). The mod
only reads the record: the review itself is the page. Open review opens
this session's review page in the desktop app's Browser pane through its MCP server (`Claude Browser`),
in the tab it opened before while that tab stays open, and brings the pane to the front (`ccd_view`'s
`show_pane`); from the terminal, in the default browser.

In the desktop app the review also opens by itself, the same way, as a turn ends that edited the work:
a file of the work, not Claude's own, temporary or generated, that the session edited in the turn (its
recorder's `after/` mark rewritten, a subagent's edit included) and that still differs from its
baseline. A turn that used the Browser pane itself leaves it as the turn left it, and an interrupted or
failed turn, or one in the terminal, leaves it to the row. Such a turn's end, but an interrupted or failed
one's, also asks for the overview, in the terminal too and whether the page opens or not, so it is there
when you look: one fork of the session per such turn, mostly read from the prompt cache.

A comment made on the page while no `review.py --wait` of Claude's waits reaches the session without a
message of yours. The server marks it in the record of the session it reaches
(`review/unwatched-comment`, a git review's in its owner's); every 1.5 s the mod looks at the mark, and
while the main loop runs no turn it has `review.py --count-comments` count the comments waiting (of the
session's page and its git reviews', not an ask for an overview, which goes to the fork) and submits a
prompt of its own (`$.prompt.submit`), "2 new comments on the review page.", which the redpen plugin's
frame names in the chat. The prompt hook hands the comments over with it, as with a message you type.
While a turn runs, they wait for its end and go a beat after it, unless a message you queued meanwhile
took them first. The mod also rewrites `review/mod-watcher` every beat, `idle` or `busy`, and the page
says by it where a comment goes; a word older than 5 seconds is nobody's (the session closed). Comments
left waiting before the session started or reloaded its plugins go with the next message, as before.
A prompt the engine refuses leaves them for that message too, with a toast.

The folder is its own marketplace, `redpen`, and the plugin is read from it as it is on disk, so an edit
there reaches a session with `/reload-plugins`. It loads in every session once the user has installed it
(the commands change `~/.claude/settings.json`, so they are the user's to run, from a terminal):

```bash
claude plugin marketplace add ~/.claude/skills/redpen/code/mod
claude plugin install redpen@redpen --scope user
```

`claude plugin uninstall redpen@redpen` removes it. Its tests: `claude plugin test
~/.claude/skills/redpen/code/mod`.

## code/ in Codex

`codex/` is redpen's skill for Codex (`codex/SKILL.md`) and its hooks (`codex/hooks.json`), for Codex
0.167 on, in the CLI and the desktop app. Codex hands its hooks Claude Code's hook input, so the same
`recorder.py` and `review.py --prompt-hook` are its hooks. What differs:

- Codex edits files with one tool, `apply_patch`. The recorder reads the files a patch adds, updates,
  deletes and moves to from its `*** Add File:`, `*** Update File:`, `*** Delete File:` and
  `*** Move to:` lines, paths from the call's folder, and records each as an Edit's. Its shell commands
  come as `Bash`, as Claude Code's do. A call is Codex's by the `turn_id` only its hook input has.
- The session is the Codex thread. `review.py` and `cursor.py` take its id from `$CODEX_THREAD_ID` when
  `$CLAUDE_CODE_SESSION_ID` is not set (it is, in a Codex started from a Claude Code session: pass
  `--session` there). The record's title is the thread's name in `~/.codex/session_index.jsonl`, else
  "Codex session".
- A comment reaches Codex with the user's next message, by the prompt hook, or while a turn waits on
  `review.py --wait`. A command left running does not wake Codex when it ends, so the hook does not tell
  Codex to start one. `codex queue --thread <id> --message …` keeps a message for a thread but sends it
  only once the thread is resumed (`--resume` needs a local daemon), so redpen does not push comments
  with it.
- Codex's workspace-write sandbox stops `review.py`: it writes `~/.redpen` and reaches 127.0.0.1:7878.
  The skill has Codex run it outside the sandbox, which Codex asks you to approve each time, unless a rule
  allows it (below).
- Nothing stands for the mod: no row above the prompt, no review opened as a turn ends, no overview
  written unasked (Describe asks Codex through the comments).

Setup, from a terminal (it changes Codex's config, so it is yours to run). The hooks, or, with hooks of
your own in that file, their three entries merged into it:

```bash
cp ~/.claude/skills/redpen/codex/hooks.json ~/.codex/hooks.json
```

The skill, where Codex finds a user's skills:

```bash
mkdir -p ~/.agents/skills && ln -s ~/.claude/skills/redpen/codex ~/.agents/skills/redpen
```

The rules that let `review.py` and `cursor.py` run outside the sandbox without asking, as the skill
writes them and as Codex may spell them:

```bash
cat >> ~/.codex/rules/default.rules <<EOF
prefix_rule(pattern=["python3", "~/.claude/skills/redpen/code/review.py"], decision="allow")
prefix_rule(pattern=["python3", "$HOME/.claude/skills/redpen/code/review.py"], decision="allow")
prefix_rule(pattern=["python3", "~/.claude/skills/redpen/code/cursor.py"], decision="allow")
prefix_rule(pattern=["python3", "$HOME/.claude/skills/redpen/code/cursor.py"], decision="allow")
EOF
```

Then trust the hooks once: Settings → Hooks in the desktop app, `/hooks` in the CLI; a change to the file
asks again. `codex execpolicy check --rules ~/.codex/rules/default.rules -- python3
~/.claude/skills/redpen/code/review.py` says whether a command matches the rules.

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
`~/.claude/settings.json`), and in Codex on apply_patch|Bash (`~/.codex/hooks.json`), and records every session's edits as they happen in
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
running over 30 seconds changed (someone else may have), or one the command's text does not mention
by its name (your editor's save while it ran, say), is marked as edited elsewhere; a file another
session's own edit left as it is during the command is that session's, not recorded, and so is a file
that changed while a shell command of another session was running in the tree, begun before the change,
when this command names no path there: that command's post hook records it. A command of another
session begun over an hour ago is not running (cut short, its post hook never came): its snapshot goes. A command that edits a file
of the record and commits it, naming it, leaves it clean, which git's status cannot tell: its text, now
the committed one, is kept as the session's own edit. A change by something no
session records (your editor, a terminal) during a command still lands in the session's record, marked
as edited elsewhere when the command ran long. A tree whose
status took git over 1.5 seconds is asked, for a day, only of the files a command's text names, in a script
on its input too, and whole only around an `rm`, `mv`, `unlink`, `trash` or `find -delete`
(`~/.redpen/code/slow-trees.json`). The review page keeps its own state beside them, in the record's `review/`
folder; of the comments: `comments.json`, those waiting to be sent, `sent.jsonl`, the batches sent,
`handed.jsonl`, which of the batches Claude was handed and how (`base/channel.py` keeps it),
`answers.jsonl`, Claude's answers and the threads the user resolved, and `outline.json`, the overview
(an ask for one goes among the comments as `{describe: true}`); of the session's Claude Code mod:
`unwatched-comment`, when the last comment no command waited for was made, and `mod-watcher`, `idle` or
`busy` (code/ in Claude Code).

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
