# redpen

Review of what Claude changed, change by change: accept, reject, revisit, comment. A Claude Code skill
(`SKILL.md`), kept in `~/.claude/skills/redpen`.

| Part | What it reviews | Where |
|---|---|---|
| `code/` | the file edits of a Claude Code session | a page in the desktop app's Browser pane; Cursor |

## code/

| File | What |
|---|---|
| `recorder.py` | the hook that records a session's edits: each file's baseline, in `~/.claude/cursordiff/<session>/` |
| `review.py` | the review page's server (127.0.0.1:7890), and the hook that hands the page's comments to Claude |
| `page/` | the review page |
| `cursor.py` | writes the record into Cursor as its inline diffs |
| `extension/` | the Claude Edits sidebar for Cursor |
| `vendor/fetch_monaco.py` | fetches the page's editor, Monaco, into `vendor/monaco/` (not kept in the repository) |

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
