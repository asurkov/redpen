---
name: redpen
description: "Review Claude Code's file edits change by change: on a page in the desktop app's Browser pane (Accept/Reject per change and per file, undo, comments that reach Claude, an overview of the changes by purpose), or in Cursor as its own inline diffs; the page also reviews a git work tree's whole change against a commit (a CL made over several sessions). Use when the user invokes /redpen, or asks to review, accept or reject Claude's edits, to review a CL, or to see the edits in Cursor."
argument-hint: "[cursor | --git [REPO] [--base REV]] [--session ID] [--workspace ID] [--clear]"
---

# redpen

Review of what Claude changed, change by change. `code/` reviews a session's file edits on the review
page (`/redpen`) and in Cursor (`/redpen cursor`). `prose/` is docdiff, a skill of its own (`/docdiff`,
found through `~/.claude/skills/docdiff`, a link to `prose/`; its instructions are `prose/SKILL.md`).

How the page, Cursor's part and the Claude Code mod behave, the record and the server, is in
`README.md` beside this file: read it to answer the user's questions about them.

## The review page

```bash
python3 ~/.claude/skills/redpen/code/review.py
```

The script starts redpen's server unless it runs, and prints the page's URL for this session,
`http://127.0.0.1:7878/code/s/<session>/`. Open that URL in the Browser pane (`preview_start` with
`url`, or `navigate`) and tell the user it is open. Do not click Accept, Reject or Unaccept there
yourself, nor press `a`: those are the user's. `--session <id>` prints another session's page; the
server's index, http://127.0.0.1:7878/, lists every review.

The page shows what the session's Edit and Write calls changed, and what its shell commands changed in
a git work tree of the files each names (by name anywhere in it, a script on its input too, by a glob,
or, for an `rm` or `mv`, by a folder): a change a command does not name is not recorded, so name the
files a script or a formatter writes, or use Edit. A git review (below) shows a work tree's every
change, whoever made it. The page names the session's agent, Claude or Codex, over its answers.

**Links into the page.** `<page URL>#<path>:<line>` opens the page at that file and line, in a view
that has it (the path from the repository's top, or absolute; `:<line>` and a range's `-<line>` may be
left out); a file of the tree with no change opens as it is. To show the user a change you speak of,
`navigate` the Browser pane's tab to such a link: the page opens it without loading again, and takes
the link off its address, so the same link opens it again later.

**Waiting for comments.** After opening the page, keep one background Bash command
(`run_in_background`) waiting for the comments the user sends from it:

```bash
python3 ~/.claude/skills/redpen/code/review.py --wait
```

It waits on the session's page and on the git reviews made from the session, those made while it
waits too. It prints the comments as they are made and ends; those made between one wait and the next
are printed by the next as it starts. When it ends with comments, start it again at once, then answer
or act on each. A comment reaches you once: from this command, or from the `UserPromptSubmit` hook with
the user's next message. While a git review of the session's has no command waiting, the hook says so
with the prompt: start the command then.

Each comment names its file and lines and quotes them as the user saw them, and carries its id; one on
"the whole file" quotes nothing. A comment "of the accepted text" or "of the file as it was before your
edits" is on lines the file may no longer have. A reply names the thread it is in. A comment on an
item of the overview names the item and quotes it, its text and its blocks; one on a folder names
the folder and lists its changed files. Answer them the same way.

**Answering on the page.** Besides what you say in the chat or change in the files, put a short answer
under each comment, so the user reads it where they asked; the page draws its Markdown (fenced code,
lists, `code`, **bold**, web links):

```bash
python3 ~/.claude/skills/redpen/code/review.py --answer <comment id> <<'EOF'
The answer.
EOF
```

After a change made in answer to the comment, add `--change <file>:<line>` (the line in the file as it
is now, or the file alone): the answer then links to the change, and the user goes to it with one click
and resolves the thread.

**Writing the overview.** When the user presses Describe, the waiting command (or the hook) prints an
ask for an overview, naming the review, unless the Claude Code mod took it first for a fork of the
session (below). Write it as JSON on stdin of

```bash
python3 ~/.claude/skills/redpen/code/review.py --outline <review> <<'EOF'
{"items": [{"title": "…", "text": "…", "blocks": [{"path": "/abs/file", "start": 10, "end": 24, "note": "…"}]}]}
EOF
```

Items by purpose, in the order to read them; lines from 1 in the file as it is now, or with
`"side": "orig"` in the file as it was (lines taken away, a deleted file); no start and end for a whole
file. The command keeps each block's lines to find them again, refuses a path the review does not have
or lines its file does not have, saying which, and lists the changes no block covers: add each to an
item, unless it needs no word. Leave out the files the project generates. Writing one answers the ask
still waiting. The page does not show an overview asked for before the session's latest edits. The
redpen plugin asks the same, when the user opens the review and at the end of a turn that edited the
work, with no overview or one older than those edits, of a fork of the session with no tools
(`$.model.fork`): nothing shows in the chat, and the fork's
answer, the JSON alone, goes to `--outline` by the plugin, not by you.

### A git review

```bash
python3 ~/.claude/skills/redpen/code/review.py --git [REPO] [--base REV]
```

Every file of a git work tree that differs from a commit, whoever changed it, untracked files included:
for a whole change, such as a CL made over several sessions. `REPO` is a folder in the work tree
(default: the current one). The base is the commit the work tree and `REV` both come from:
`--base origin/main` for a branch with its commits, `--base HEAD` for uncommitted work alone; with no
`--base`, the branch's upstream, else `HEAD`. Open the URL it prints,
`http://127.0.0.1:7878/code/s/git-<folder>-<path hash>-<commit>/`, as above. Its comments reach the
session that ran the command last, through the same waiting command.

## In Cursor

```bash
python3 ~/.claude/skills/redpen/code/cursor.py
```

The script writes each file this session edited, with its baseline, into Cursor as Cursor's own inline
diffs, with Keep and Undo. Then tell the user to reload the Cursor window that holds the files (Command
Palette → "Developer: Reload Window"), and pass on what the script printed: which workspace got how
many files, and any skips. `--workspace <id>` picks the workspace, `--session <id>` another session,
`--clear` takes every such diff away.

## After changing redpen

- `python3 -m unittest discover -s ~/.claude/skills/redpen/code/tests` tests `review.py` and
  `recorder.py`, in a scratch home, in a few seconds; `claude plugin test ~/.claude/skills/redpen/code/mod`
  tests the Claude Code mod; `python3 -m unittest discover -s ~/.claude/skills/redpen/prose/tests` tests
  docdiff's plugins (where a comment's quote is in a PR's files, what a push sends, GitHub faked). Run them
  after a change to any of them.
- The server runs `code/` and `prose/` as they are on disk; only a change to `base/server.py` needs
  `python3 -I ~/.claude/skills/redpen/base/server.py --restart`. Its log is `~/.redpen/server.log`.
- The mod reaches an open session with `/reload-plugins`, the Cursor extension with `install.py` and a
  window reload; installing either is the user's to do (README, "Setup").
- `codex/SKILL.md` is this skill for Codex: a change to how the page or its commands are used goes into
  both. A change to `codex/hooks.json` reaches Codex once the user copies it and trusts it again (README,
  "code/ in Codex").
