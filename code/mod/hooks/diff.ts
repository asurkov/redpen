import type { Hunk } from '../types'

// Hunks of a unified diff, and taking one back out of the text it made.

const hunkHeader = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/

export function parseHunks(diff: string): Hunk[] {
  const hunks: Hunk[] = []
  const lines = diff.split('\n')
  for (let i = 0; i < lines.length; i++) {
    const header = lines[i] ?? ''
    const m = hunkHeader.exec(header)
    if (!m) continue
    const hunk: Hunk = {
      text: '',
      oldStart: Number(m[1]),
      oldLines: m[2] === undefined ? 1 : Number(m[2]),
      newStart: Number(m[3]),
      newLines: m[4] === undefined ? 1 : Number(m[4]),
      added: 0,
      removed: 0,
    }
    const body = [header]
    let oldLeft = hunk.oldLines
    let newLeft = hunk.newLines
    while (i + 1 < lines.length) {
      const line = lines[i + 1] ?? ''
      // "\ No newline at end of file" belongs to the line before it.
      if (!line.startsWith('\\') && oldLeft === 0 && newLeft === 0) break
      if (line.startsWith('+')) {
        newLeft--
        hunk.added++
      } else if (line.startsWith('-')) {
        oldLeft--
        hunk.removed++
      } else if (!line.startsWith('\\')) {
        oldLeft--
        newLeft--
      }
      // An empty context line comes as "" under diff.suppressBlankEmpty.
      body.push(line === '' ? ' ' : line)
      i++
    }
    hunk.text = body.join('\n')
    hunks.push(hunk)
  }
  return hunks
}

// The hunk's lines before and after the change, each with its line end.
function sides(hunk: Hunk) {
  const before: string[] = []
  const after: string[] = []
  let last = ' '
  const unended = (side: string[]) => side.push((side.pop() ?? '').replace(/\n$/, ''))
  for (const line of hunk.text.split('\n').slice(1)) {
    if (line.startsWith('\\')) {
      if (last !== '+') unended(before)
      if (last !== '-') unended(after)
      continue
    }
    last = line[0] ?? ' '
    if (last !== '+') before.push(line.slice(1) + '\n')
    if (last !== '-') after.push(line.slice(1) + '\n')
  }
  return { before, after }
}

// The text with one change taken back, or undefined when the text no longer reads as the change left it.
export function reverted(text: string, hunk: Hunk) {
  const lines = text.match(/[^\n]*\n|[^\n]+$/g) ?? []
  const { before, after } = sides(hunk)
  const at = hunk.newLines === 0 ? hunk.newStart : hunk.newStart - 1
  if (lines.slice(at, at + after.length).join('') !== after.join('')) return undefined
  lines.splice(at, after.length, ...before)
  return lines.join('')
}

// The lines the change touches in the file as it is, first and last; lines removed with none put in
// their place count as the line after them.
export function changedLines(hunk: Hunk) {
  let line = hunk.newStart
  let first = 0
  let last = 0
  for (const row of hunk.text.split('\n').slice(1)) {
    if (row.startsWith('\\')) continue
    if (row.startsWith('+') || row.startsWith('-')) {
      first ||= line
      last = line
    }
    if (!row.startsWith('-')) line++
  }
  return { first: Math.max(first, 1), last: Math.max(last, 1) }
}

// One line of a file's view, numbered as in the file as it is; a removed line has no number there.
export type Line = { kind: 'line'; tone: 'same' | 'added' | 'removed'; number: number | undefined; text: string }
// Unchanged lines not shown, before change `index`, or after the last change when it is their count.
export type Gap = { kind: 'gap'; index: number; hidden: number }
// Where the buttons of change `index` go: after its last line.
export type ChangeEnd = { kind: 'end'; index: number }
export type Part = Line | Gap | ChangeEnd

// How many unchanged lines one press shows.
export const moreLines = 20
// A line longer than this shows cut.
const maxLineLength = 2_000
// A page holds at most this many characters: one drawing has room for 100,000.
const maxPageLength = 40_000

// The file's view from its first line to its last: each change after the unchanged lines before it,
// of which those show that a press asked for (`shown`: `<gap>:down` counts on from the change above or
// the file's first line, `<gap>:up` back from the change below or the file's last line) and the rest
// stand as a gap. `lines` are the file's, absent when it cannot be read: then no gap opens, and the
// lines after the last change are not counted.
export function parts(hunks: Hunk[], lines: string[] | undefined, shown: Record<string, number>): Part[] {
  const out: Part[] = []
  // The first line of the file no part holds yet.
  let next = 1
  const line = (tone: Line['tone'], number: number | undefined, text: string): Line =>
    ({ kind: 'line', tone, number, text: printable(text.length > maxLineLength ? `${text.slice(0, maxLineLength)}…` : text) })
  const unchanged = (index: number, last: number) => {
    const count = last - next + 1
    if (count <= 0) return
    const down = lines ? Math.min(shown[`${index}:down`] ?? 0, count) : 0
    const up = lines ? Math.min(shown[`${index}:up`] ?? 0, count - down) : 0
    for (let n = next; n < next + down; n++) out.push(line('same', n, lines?.[n - 1] ?? ''))
    if (count > down + up) out.push({ kind: 'gap', index, hidden: count - down - up })
    for (let n = last - up + 1; n <= last; n++) out.push(line('same', n, lines?.[n - 1] ?? ''))
  }
  hunks.forEach((hunk, index) => {
    const first = hunk.newLines ? hunk.newStart : hunk.newStart + 1
    unchanged(index, first - 1)
    let number = first
    for (const row of hunk.text.split('\n').slice(1)) {
      if (row.startsWith('\\')) continue
      const tone = row.startsWith('+') ? 'added' : row.startsWith('-') ? 'removed' : 'same'
      out.push(line(tone, tone === 'removed' ? undefined : number++, row.slice(1)))
    }
    out.push({ kind: 'end', index })
    next = first + hunk.newLines
  })
  if (lines) unchanged(hunks.length, lines.length)
  return out
}

// The view cut into pages of at most `maxRows` rows, a gap and a change's buttons counting as two. A
// change's buttons stay with its last line, and a gap with the lines on both its sides, so what a press
// on it shows is on its page.
export function pages(all: Part[], maxRows: number) {
  const out: Part[][] = [[]]
  let rows = 0
  let length = 0
  all.forEach((part, at) => {
    const size = part.kind === 'line' ? part.text.length : 0
    const isFull = rows >= maxRows || length + size > maxPageLength
    if (isFull && part.kind === 'line' && all[at - 1]?.kind !== 'gap') {
      out.push([])
      rows = 0
      length = 0
    }
    out.at(-1)?.push(part)
    rows += part.kind === 'line' ? 1 : 2
    length += size
  })
  return out
}

// Text as the engine draws it: a line's end is a newline alone, and any other control character or half
// of a surrogate pair, which the engine refuses or reads as a line's end, shows as a mark.
export function printable(text: string) {
  return text.replace(/\r(?=\n|$)/g, '').replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f\u{10EEEE}]|[\uD800-\uDFFF]/gu, '�')
}
