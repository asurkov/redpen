// What the Claude Edits pane draws: the files this session edited, as the cursordiff hooks record them
// in ~/.redpen/code/<session>/, each against its baseline; the comments waiting for the next
// message; and what the person has open in the pane.

// One hunk of `git diff` from the baseline to the file.
export type Hunk = {
  // The hunk's lines, its `@@` header first.
  text: string
  oldStart: number
  oldLines: number
  newStart: number
  newLines: number
  added: number
  removed: number
}

export type EditedFile = {
  path: string
  // The file's name in the record: the first 16 hex digits of the SHA-256 of its path.
  key: string
  // The session created it.
  isNew: boolean
  // The file's and its baseline's mtime and size, as last diffed.
  signature: string
  kind: 'changed' | 'same' | 'unknown'
  // Said beside the name: new, deleted, binary, old text missing.
  note: string
  added: number
  removed: number
  hunks: Hunk[]
}

// A comment on a change or on one line, sent with the person's next message.
export type Comment = {
  id: string
  path: string
  start: number
  end: number
  // The change, or the line, as the person saw it.
  quote: string
  text: string
}

// What shows of the open file beyond its changes, good while the file stays as it was diffed.
export type OpenView = {
  path: string
  // The file's `signature` when the person last pressed.
  signature: string
  // The unchanged lines asked for, by the gap they are in and the side they count from: `<gap>:down`
  // on from the change above or the file's first line, `<gap>:up` back from the change below or the
  // file's last line.
  shown: Record<string, number>
  // Which page of the view shows, from 0.
  page: number
}

declare module 'claude-code' {
  interface PluginState {
    'edit-review': {
      files: Shaped<EditedFile[]>
      comments: Shaped<Comment[]>
      // The file whose changes show, by path.
      openFile: string
      // What a comment is being written on: a change, `<path>#<hunk index>`, or a line,
      // `<path>:<line number>`.
      commenting: string
      // The new file waiting for a second press to go to the Trash.
      confirming: string
      openView: OpenView
      // Whether Claude's own files and the temporary ones are listed.
      areOthersOpen: boolean
    }
  }
}
