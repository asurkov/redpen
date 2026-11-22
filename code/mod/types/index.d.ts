// What the Claude Edits pane draws: the files this session edited, as redpen's recorder hooks record
// them in ~/.redpen/code/<session>/, each counted against its baseline.

export type EditedFile = {
  path: string
  // The file's name in the record: the first 16 hex digits of the SHA-256 of its path.
  key: string
  // The session created it.
  isNew: boolean
  // The file's and its baseline's mtime and size, as last counted.
  signature: string
  kind: 'changed' | 'same' | 'unknown'
  // Said beside the name: new, deleted, binary, old text missing.
  note: string
  added: number
  removed: number
}

declare module 'claude-code' {
  interface PluginState {
    'edit-review': {
      files: Shaped<EditedFile[]>
      // Whether Claude's own files and the temporary ones are listed.
      areOthersOpen: boolean
      // The Browser pane tab "Open in redpen" opened, and the page it opened there.
      redpenTab: { id: string; url: string }
    }
  }
}
