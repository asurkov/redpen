// What the redpen pane draws: the files this session edited, as redpen's recorder hooks record
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
  // The project generates it (a lockfile, a file marked linguist-generated): listed, not counted.
  isGenerated: boolean
}

// The overview Claude wrote of the review, as the row above the prompt uses it: when it was written, and
// each item's title with the keys of the files its blocks are in.
export type Overview = {
  time: string
  items: { title: string; keys: string[] }[]
}

declare module 'claude-code' {
  interface PluginState {
    'redpen': {
      files: Shaped<EditedFile[]>
      // Whether Claude's own files and the temporary ones are listed.
      areOthersOpen: boolean
      // The Browser pane tab "Open review" opened, and the page it opened there.
      redpenTab: { id: string; url: string }
      // The record's edit marks by key, with when each was written: as last read, and as they were when
      // the person last looked (null before the session's first start); and whether they closed the row
      // above the prompt.
      marks: Record<string, number>
      lookedMarks: Record<string, number> | null
      isRowClosed: boolean
      overview: Overview | null
    }
  }
}
