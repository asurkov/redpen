# redpen

Review of what a coding agent changed, change by change: accept, reject, revisit, comment, with the
comments handed back to the agent. Written for Claude Code and packaged as its skill (`SKILL.md`), kept in
`~/.claude/skills/redpen`; [what ties it to Claude Code](#what-is-tied-to-claude-code) is below.

| Part | What it reviews | Where |
|---|---|---|
| `code/` | the file edits of an agent's session | a local page (127.0.0.1:7890), shown in the Claude desktop app's Browser pane; Cursor |
| `prose/` | the changes to a repo's documents: Markdown, ReSpec specs, a GitHub PR | a local page (127.0.0.1:7878); its own skill, `/docdiff` |

## What is tied to Claude Code

The review is not. `code/` reviews a record of plain files (for each edited file, its path and its text
before the session's first edit), and `prose/` reviews what git has. The ends are:

| | `code/` | `prose/` |
|---|---|---|
| What records the changes | `recorder.py`, a Claude Code hook on its edit tools | nothing: git has them |
| How a comment reaches the agent | `review.py --prompt-hook`, a Claude Code hook: the comment goes with the user's next message | a command the skill has Claude keep waiting on the review's `revisit.jsonl` |
| Where the state is kept | `~/.claude/cursordiff/<session>/`, under Claude Code's session id | `~/.docdiff/<name>/` |

For another agent those ends have to be written: something that saves a file's text before the agent's
first edit to it, and a way to hand the agent a comment. Neither exists yet.

## code/

| File | What |
|---|---|
| `recorder.py` | the hook that records a session's edits: each file's baseline, in `~/.claude/cursordiff/<session>/` |
| `review.py` | the review page's server (127.0.0.1:7890), and the hook that hands the page's comments to Claude |
| `page/` | the review page |
| `cursor.py` | writes the record into Cursor as its inline diffs |
| `extension/` | the Claude Edits sidebar for Cursor |
| `vendor/fetch_monaco.py` | fetches the page's editor, Monaco, into `vendor/monaco/` (not kept in the repository) |

## prose/

docdiff, merged in with its history. It keeps its name, its skill (`/docdiff`, in `prose/SKILL.md`), its
server and its state.

| File | What |
|---|---|
| `docdiff.py` | builds a review's page from two revisions of a repo (rendered with pandoc), and serves every review |
| `plugins/` | review comments and questions to Claude, ReSpec specs, a GitHub PR, Google Docs and their comments |

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
        "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "python3 ~/.claude/skills/redpen/code/recorder.py pre" }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit",
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

The Claude Edits sidebar, for Cursor:

```bash
python3 ~/.claude/skills/redpen/code/extension/install.py
```
