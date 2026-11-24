---
name: redpen
description: "Review Codex's file edits change by change: on a local page (Accept/Reject per change and per file, undo, comments that reach Codex, an overview of the changes by purpose), or in Cursor as its own inline diffs; the page also reviews a git work tree's whole change against a commit (a CL made over several sessions). Use when the user invokes redpen, or asks to review, accept or reject Codex's edits, to review a CL, or to see the edits in Cursor."
---

# redpen

Review of what Codex changed, change by change: redpen's skill for Codex. Its scripts are Claude Code's
(`../SKILL.md` is that skill), and Codex's hooks (`hooks.json` beside this file) keep the record of this
thread's edits. How the page, Cursor's part, the record and the server behave is in `../README.md`: read
it to answer the user's questions about them.

**Outside the sandbox.** Each `review.py` and `cursor.py` command below writes `~/.redpen` and reaches
redpen's server at 127.0.0.1:7878, which the sandbox blocks: run it outside the sandbox from the start
(escalated), unless a rule allows it already. The commands take this thread's id from
`$CODEX_THREAD_ID`; one that names another session (Codex started from a Claude Code session) takes
`--session <this thread's id>`.

## The review page

```bash
python3 ~/.claude/skills/redpen/code/review.py
```

The script starts redpen's server unless it runs, and prints the page's URL for this thread,
`http://127.0.0.1:7878/code/s/<thread>/`. Open that URL in the in-app browser, or else with `open <URL>`
in the default browser, and tell the user it is open. Do not click Accept, Reject or Unaccept there
yourself, nor press `a`: those are the user's. `--session <id>` prints another session's page; the
server's index, http://127.0.0.1:7878/, lists every review.

The page shows what the thread's patches changed, and what its shell commands changed in a git work
tree; a git review (below) shows a work tree's every change, whoever made it.

**Links into the page.** `<page URL>#<path>:<line>` opens the page at that file and line, in a view
that has it (the path from the repository's top, or absolute; `:<line>` and a range's `-<line>` may be
left out); a file of the tree with no change opens as it is.

**Comments.** The comments the user sends from the page reach you with their next message: the
`UserPromptSubmit` hook adds them. When the user asks you to wait for their comments, run

```bash
python3 ~/.claude/skills/redpen/code/review.py --wait
```

and keep waiting on it: it prints the comments as they are made and ends. Act on them, then run it again,
until the user says they are done. A command left running does not wake you once your turn ends.

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

**Writing the overview.** When the user presses Describe, an ask for an overview, naming the review,
comes with their next message or from the waiting command. Write it as JSON on stdin of

```bash
python3 ~/.claude/skills/redpen/code/review.py --outline <review> <<'EOF'
{"items": [{"title": "…", "text": "…", "blocks": [{"path": "/abs/file", "start": 10, "end": 24, "note": "…"}]}]}
EOF
```

Items by purpose, in the order to read them; lines from 1 in the file as it is now, or with
`"side": "orig"` in the file as it was (lines taken away, a deleted file); no start and end for a whole
file. The command keeps each block's lines to find them again, refuses a path the review does not have
or lines its file does not have, saying which, and lists the changes no block covers: add each to an
item, unless it needs no word. Leave out the files the project generates.

### A git review

```bash
python3 ~/.claude/skills/redpen/code/review.py --git [REPO] [--base REV]
```

Every file of a git work tree that differs from a commit, whoever changed it, untracked files included:
for a whole change, such as a CL made over several sessions. `REPO` is a folder in the work tree
(default: the current one). The base is the commit the work tree and `REV` both come from:
`--base origin/main` for a branch with its commits, `--base HEAD` for uncommitted work alone; with no
`--base`, the branch's upstream, else `HEAD`. Open the URL it prints as above. Its comments reach the
thread that ran the command last, the same ways.

## In Cursor

```bash
python3 ~/.claude/skills/redpen/code/cursor.py
```

The script writes each file this thread edited, with its baseline, into Cursor as Cursor's own inline
diffs, with Keep and Undo. Then tell the user to reload the Cursor window that holds the files (Command
Palette → "Developer: Reload Window"), and pass on what the script printed: which workspace got how
many files, and any skips. `--workspace <id>` picks the workspace, `--session <id>` another session,
`--clear` takes every such diff away.
