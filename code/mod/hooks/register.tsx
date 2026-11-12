import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Comment, EditedFile, Hunk, OpenView } from '../types'
import { changedLines, moreLines, pages, parseHunks, parts, reverted } from './diff'
import type { Gap, Line } from './diff'

// The files this session edited, from the record redpen's recorder keeps in
// ~/.redpen/code/<session>/ (files/<key>.json and base/<key>), each diffed against its baseline:
// its text before the session's first edit to it, or as last kept here. Keep moves the baseline to the
// file as it is; Undo puts the baseline back, for the file or one change. Comments on changes and on
// single lines go to the model with the person's next message. The open file shows as its changes,
// removed lines red and added lines green, among as many of the unchanged lines around them as the
// person asks for.

const paneId = 'edit-review'
const files = atom({ plugin: 'edit-review', key: 'files' } as const, [], { shape: 'files-1' })
const comments = atom({ plugin: 'edit-review', key: 'comments' } as const, [], { shape: 'comments-1' })
const openFile = atom({ plugin: 'edit-review', key: 'openFile' } as const, '')
const commenting = atom({ plugin: 'edit-review', key: 'commenting' } as const, '')
const confirming = atom({ plugin: 'edit-review', key: 'confirming' } as const, '')
const openView = atom({ plugin: 'edit-review', key: 'openView' } as const, { path: '', signature: '', shown: {}, page: 0 })
const areOthersOpen = atom({ plugin: 'edit-review', key: 'areOthersOpen' } as const, false)

const cursor = '/Applications/Cursor.app/Contents/Resources/app/bin/cursor'
// The desktop draws no tree of more than 2,000 elements and texts. Of those a listed file takes up to
// 12, a waiting comment 8, a row of the open file 6, and the rest of the pane under 60.
const maxNodes = 1_900
const maxPageRows = 250
// The backgrounds of a changed line, which both the terminal and the desktop have as line tints.
const tints = { added: 'diffAddedDimmed', removed: 'diffRemovedDimmed' } as const

type Places = { record: string; cwd: string; home: string }

async function places($: EngineInterface): Promise<Places> {
  const home = (await $.env.get('HOME')) ?? ''
  return { record: `${home}/.redpen/code/${await $.session.id()}`, cwd: await $.session.cwd(), home }
}

const baseOf = (record: string, file: EditedFile) => `${record}/base/${file.key}`

async function signatureOf($: EngineInterface, path: string) {
  const stat = await $.fs.stat(path).catch(() => undefined)
  return stat ? `${stat.mtimeMs}:${stat.size}` : '-'
}

// Diffs the file against its baseline and says what the list shows for it.
async function diffed($: EngineInterface, record: string, entry: Omit<EditedFile, 'kind' | 'note' | 'added' | 'removed' | 'hunks'>): Promise<EditedFile> {
  const base = baseOf(record, entry as EditedFile)
  const hasBase = await $.fs.exists(base)
  const exists = await $.fs.exists(entry.path)
  const plain = { ...entry, added: 0, removed: 0, hunks: [] }
  if (!hasBase && !entry.isNew) return { ...plain, kind: 'unknown', note: 'old text missing' }
  if (!hasBase && !exists) return { ...plain, kind: 'same', note: '' }
  const ran = await $.process.run(['git', 'diff', '--no-index', '--no-color', '--no-ext-diff', '-U3', '--',
    hasBase ? base : '/dev/null', exists ? entry.path : '/dev/null'])
  if (ran.exitCode === 0) return { ...plain, kind: 'same', note: '' }
  if (ran.exitCode !== 1) return { ...plain, kind: 'unknown', note: ran.stderr.trim().split('\n')[0] || 'diff failed' }
  const note = !exists ? 'deleted' : !hasBase ? 'new' : ''
  if (/^Binary files /m.test(ran.stdout)) return { ...plain, kind: 'changed', note: note || 'binary' }
  const hunks = parseHunks(ran.stdout)
  return {
    ...plain,
    kind: 'changed',
    note,
    hunks,
    added: hunks.reduce((n, h) => n + h.added, 0),
    removed: hunks.reduce((n, h) => n + h.removed, 0),
  }
}

// The record's entries as last read, by name, with their mtime then.
const savedEntries = new Map<string, { mtimeMs: number; saved: { path: string; new: boolean } }>()

// Reads the record and diffs each file whose text or baseline changed since it was last diffed.
async function scanOnce($: EngineInterface, force: boolean) {
  const { record } = await places($)
  const entries = await $.fs.list(`${record}/files`).catch(() => [])
  const known = new Map((await read($, files)).map(file => [file.key, file]))
  const next: EditedFile[] = []
  for (const { name, mtimeMs } of entries) {
    // The hooks write each file whole under a dot name, then rename it.
    if (name.startsWith('.') || !name.endsWith('.json')) continue
    const key = name.slice(0, -'.json'.length)
    let entry = savedEntries.get(name)
    if (!entry || entry.mtimeMs !== mtimeMs) {
      entry = { mtimeMs, saved: JSON.parse(await $.fs.read(`${record}/files/${name}`)) }
      savedEntries.set(name, entry)
    }
    const { saved } = entry
    const signature = `${await signatureOf($, saved.path)} ${await signatureOf($, `${record}/base/${key}`)}`
    const old = known.get(key)
    next.push(!force && old && old.signature === signature && old.isNew === saved.new ? old :
      await diffed($, record, { path: saved.path, key, isNew: saved.new, signature }))
  }
  next.sort((a, b) => a.path.localeCompare(b.path))
  const before = JSON.stringify(await read($, files))
  if (JSON.stringify(next) !== before) await update($, files, () => next)
}

let scanning: Promise<void> | undefined
let isAskedAgain = false

// A scan asked for while one runs runs again after it, so what a press did shows at once.
function scan($: EngineInterface, force = false): Promise<void> {
  if (scanning) {
    isAskedAgain = true
    return scanning
  }
  scanning = (async () => {
    try {
      do {
        isAskedAgain = false
        await scanOnce($, force)
      } while (isAskedAgain)
    } finally {
      scanning = undefined
    }
  })()
  return scanning
}

// Moves the baseline to the file as it is; a deleted file is kept as deleted.
async function keep($: EngineInterface, file: EditedFile) {
  const { record } = await places($)
  const base = baseOf(record, file)
  if (await $.fs.exists(file.path)) {
    await $.process.run(['/bin/mkdir', '-p', `${record}/base`])
    await $.process.run(['/bin/cp', file.path, base])
  } else {
    await $.process.run(['/bin/rm', '-f', base])
    await $.fs.write(`${record}/files/${file.key}.json`, JSON.stringify({ path: file.path, new: true }))
  }
}

// Puts the baseline back; a file the session created goes to the Trash.
async function undo($: EngineInterface, file: EditedFile) {
  const { record } = await places($)
  const base = baseOf(record, file)
  if (await $.fs.exists(base)) {
    await $.process.run(['/bin/mkdir', '-p', file.path.slice(0, file.path.lastIndexOf('/'))])
    await $.process.run(['/bin/cp', base, file.path])
  } else if (file.isNew) {
    const ran = await $.process.run(['/usr/bin/trash', file.path])
    if (ran.exitCode !== 0) $.ui.toast(`Could not move it to the Trash: ${ran.stderr.trim()}`)
  }
}

async function undoChange($: EngineInterface, file: EditedFile, hunk: Hunk) {
  const { record } = await places($)
  const fresh = await diffed($, record, file)
  const text = await $.fs.read(file.path).catch(() => undefined)
  const result = fresh.hunks.some(h => h.text === hunk.text) && text !== undefined ? reverted(text, hunk) : undefined
  if (result === undefined) {
    $.ui.toast(`${file.path.split('/').pop()} changed since; its changes are shown afresh.`)
    return
  }
  await $.fs.write(file.path, result)
}

// The open file's lines as last read, good while the file and its baseline stay as they were.
let openLines: { path: string; signature: string; lines: string[] | undefined } | undefined

// The file's lines, or none when it is deleted or cannot be read.
async function linesOf($: EngineInterface, file: EditedFile) {
  if (openLines?.path !== file.path || openLines.signature !== file.signature) {
    const text = file.note === 'deleted' ? undefined : await $.fs.read(file.path).catch(() => undefined)
    const lines = text?.split('\n')
    if (lines?.at(-1) === '') lines.pop()
    openLines = { path: file.path, signature: file.signature, lines }
  }
  return openLines.lines
}

// Opens the file's changes in Cursor: its text before them beside the file as it is. The copy of the
// old text has the file's name, so Cursor colours it as the file.
async function diffInCursor($: EngineInterface, file: EditedFile) {
  const { record } = await places($)
  const base = baseOf(record, file)
  const folder = `${record}/before/${file.key}`
  const before = `${folder}/${file.path.split('/').pop()}`
  await $.process.run(['/bin/mkdir', '-p', folder])
  if (await $.fs.exists(base)) await $.process.run(['/bin/cp', base, before])
  else await $.fs.write(before, '')
  const ran = await $.process.run([cursor, '--diff', before, file.path]).catch(() => undefined)
  if (ran?.exitCode !== 0) $.ui.toast(`Cursor did not open it: ${ran?.stderr.trim().split('\n')[0] || 'its command did not run'}`)
}

function fenced(text: string) {
  const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map(run => run.length))
  const fence = '`'.repeat(Math.max(3, longest + 1))
  return `${fence}diff\n${text}\n${fence}`
}

function describe(pending: Comment[]) {
  const out = ['The user left these comments on your changes in the Claude Edits pane; each quotes the change, or the one line, it is on as they saw it.']
  for (const comment of pending) {
    const lines = comment.start === comment.end ? `line ${comment.start}` : `lines ${comment.start}-${comment.end}`
    out.push('', `${comment.path}, ${lines}:`, fenced(comment.quote), `Comment: ${comment.text}`)
  }
  return out.join('\n')
}

function showPending($: EngineInterface, pending: Comment[]) {
  $.ui.status(pending.length ?
    `${pending.length} comment${pending.length > 1 ? 's' : ''} on changes go with your next message` : undefined)
}

async function changeComments($: EngineInterface, fn: (list: Comment[]) => Comment[]) {
  await update($, comments, fn)
  showPending($, await read($, comments))
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({ name: 'edits', description: 'Show the files this session edited, to review, keep or undo' })
    void $.ui.open({ id: paneId, title: 'Claude Edits' })
    showPending($, await read($, comments))
    await scan($, true)
    $.clock.every(1500, () => void scan($))
    return started
  })

  on('command.run', { command: 'edits' }, async $ => {
    await $.ui.open({ id: paneId, title: 'Claude Edits' })
    void scan($)
    return { text: 'Claude Edits pane opened.' }
  })

  on('prompt.submit', async ($, e, next) => {
    const pending = await read($, comments)
    if (!pending.length || e.command || e.isMeta) return next(e)
    const result = await next({ ...e, context: [...(e.context ?? []), describe(pending)] })
    await changeComments($, () => [])
    return result
  })

  on('ui.render', { component: 'Pane', requestId: paneId }, async ($, e) => {
    if (e.surface === 'mobile') {
      const { Text } = $.ui.resolve(e)
      return <Text dimColor>Claude Edits shows on the desktop and in the terminal.</Text>
    }
    const { Box, Text, Button, Input } = $.ui.resolve(e)
    const { cwd, home } = await places($)
    const list = await read($, files)
    const pending = await read($, comments)
    const open = await read($, openFile)
    const writing = await read($, commenting)
    const asking = await read($, confirming)
    const showsOthers = await read($, areOthersOpen)
    const isTerminal = e.surface === 'terminal'
    // Claude's own files (its settings, skills, mods and memory) and temporary ones stand apart from
    // the work's, folded until asked for.
    const isOther = (file: EditedFile) =>
      file.path.startsWith(`${home}/.claude/`) || /^\/(private\/)?(tmp|var\/folders)\//.test(file.path)
    const isListed = (file: EditedFile) => file.kind !== 'same'
    const work = list.filter(file => !isOther(file))
    const others = list.filter(file => isOther(file) && isListed(file))
    const changed = work.filter(file => file.kind === 'changed')
    const listed = [...work.filter(isListed), ...(showsOthers ? others : [])]
    const shown = (path: string) => path.startsWith(`${cwd}/`) ? path.slice(cwd.length + 1) :
      path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path
    const act = (fn: () => Promise<unknown>) => async () => {
      await fn()
      await scan($)
    }
    // What a press asked to see of a file holds while the file stays as it was diffed.
    const viewOf = (file: EditedFile, view: OpenView): OpenView =>
      view.path === file.path && view.signature === file.signature ? view :
        { path: file.path, signature: file.signature, shown: {}, page: 0 }
    const look = (file: EditedFile, change: (view: OpenView) => Partial<OpenView>) =>
      update($, openView, now => ({ ...viewOf(file, now), ...change(viewOf(file, now)) }))
    const openOne = listed.find(file => file.path === open && file.hunks.length)
    const openLinesNow = openOne && await linesOf($, openOne)
    const view = openOne && viewOf(openOne, await read($, openView))
    const roomForRows = Math.floor((maxNodes - 60 - listed.length * 12 - pending.length * 8) / 6)
    const openPages = openOne && view ?
      pages(parts(openOne.hunks, openLinesNow, view.shown), Math.max(40, Math.min(maxPageRows, roomForRows))) : []
    const pageAt = Math.min(view?.page ?? 0, openPages.length - 1)

    // A Button shows its label alone on the desktop, so what is coloured stands beside it.
    const fileRow = (file: EditedFile) => {
      if (file.kind === 'unknown') return <Text dimColor>{`${shown(file.path)}: ${file.note}`}</Text>
      const isOpen = open === file.path
      const name = file.path.split('/').pop() ?? file.path
      const dir = shown(file.path).slice(0, -name.length).replace(/\/$/, '')
      return (
        <Box flexDirection="column" key={`file:${file.path}`}>
          <Box flexDirection="row" gap={1} flexWrap="wrap" alignItems="center">
            <Button key={`toggle:${file.path}`} plain label={`${isOpen ? '▾' : '▸'} ${name}`}
              onPress={() => update($, openFile, now => now === file.path ? '' : file.path)} />
            <Text color="diffAdded">{`+${file.added}`}</Text>
            <Text color="diffRemoved">{`−${file.removed}`}</Text>
            {file.note ? <Text dimColor>{file.note}</Text> : undefined}
            {dir ? <Text dimColor>{dir}</Text> : undefined}
          </Box>
          {isOpen && fileBody(file)}
        </Box>
      )
    }

    const fileBody = (file: EditedFile) => {
      const isTrashing = asking === file.path
      const exists = file.note !== 'deleted'
      return (
        <Box flexDirection="column" paddingLeft={2} gap={1}>
          <Box flexDirection="row" gap={1} flexWrap="wrap">
            <Button key={`keep:${file.path}`} label="Keep file" onPress={act(() => keep($, file))} />
            {file.isNew && !isTrashing ?
              <Button key={`undo:${file.path}`} label="Undo file" onPress={() => update($, confirming, () => file.path)} /> :
              isTrashing ?
                <Button key={`trash:${file.path}`} label="Move to Trash" variant="primary"
                  onPress={act(async () => {
                    await update($, confirming, () => '')
                    await undo($, file)
                  })} /> :
                <Button key={`undo:${file.path}`} label="Undo file" onPress={act(() => undo($, file))} />}
            {exists && <Button key={`diff:${file.path}`} label="Diff in Cursor" onPress={() => diffInCursor($, file)} />}
            {view?.path === file.path && Object.keys(view.shown).length > 0 &&
              <Button key={`fold:${file.path}`} label="Changes only" onPress={() => look(file, () => ({ shown: {}, page: 0 }))} />}
          </Box>
          {file.hunks.length > 0 && pageView(file)}
        </Box>
      )
    }

    // One page of the file's view: its lines flush under each other, a row of buttons where unchanged
    // lines are left out and after each change.
    const pageView = (file: EditedFile) => {
      const page = openPages[pageAt] ?? []
      // A number is a button, which a desktop draws padded and in the pane's own font: two characters
      // more hold it.
      const width = String(Math.max(1, ...page.map(part => part.kind === 'line' && part.number || 0))).length +
        (isTerminal ? 0 : 2)
      const turn = (label: string, to: number) =>
        <Box flexDirection="row" gap={1} alignItems="center">
          <Button key={`${label === 'Earlier' ? 'earlier' : 'later'}:${file.path}`} label={`${label} lines`}
            onPress={() => look(file, () => ({ page: to }))} />
          <Text dimColor>{`Part ${pageAt + 1} of ${openPages.length}`}</Text>
        </Box>
      return (
        <Box flexDirection="column">
          {pageAt > 0 && turn('Earlier', pageAt - 1)}
          {page.flatMap((part, at) => part.kind === 'line' ? lineRow(file, part, at, width) :
            [part.kind === 'gap' ? gapRow(file, part) : changeRow(file, part.index)])}
          {pageAt < openPages.length - 1 && turn('Later', pageAt + 1)}
        </Box>
      )
    }

    // Where a comment is written, under the change or the line it is on.
    const commentInput = (id: string, file: EditedFile, start: number, end: number, quote: string) => (
      <Input key={`text:${id}`} rows={3} autoFocus submitLabel="Add" placeholder="Goes to Claude with your next message"
        onSubmit={async text => {
          if (!text.trim()) return
          await changeComments($, list => [...list, {
            id: crypto.randomUUID(), path: file.path, start, end, quote, text: text.trim(),
          }])
          await update($, commenting, () => '')
        }} />
    )

    // A line, and the comment being written on it. Its number, which a removed line has none of, is
    // the button that opens the comment; one file's lines show at a time, so the number alone is the
    // button's key, and a full page stays well inside what the desktop takes as one drawing.
    const lineRow = (file: EditedFile, line: Line, at: number, width: number) => {
      const { number } = line
      const id = `${file.path}:${number}`
      return [
        <Box flexDirection="row" gap={isTerminal ? 2 : 1} alignItems="flex-start" key={`line:${file.path}:${at}`}
          {...(line.tone === 'same' ? {} : { backgroundColor: tints[line.tone] })}>
          <Box flexShrink={0} width={width} justifyContent="flex-end">
            {number !== undefined &&
              <Button key={`note:${number}`} plain dimColor label={String(number)}
                onPress={() => update($, commenting, now => now === id ? '' : id)} />}
          </Box>
          <Text mono>{line.text}</Text>
        </Box>,
        number !== undefined && writing === id &&
          commentInput(id, file, number, number, `${line.tone === 'added' ? '+' : ' '}${line.text}`),
      ]
    }

    // The unchanged lines left out, and what shows more of them: on from the lines above, back from
    // the lines below, or all.
    const gapRow = (file: EditedFile, gap: Gap) => {
      const id = `${file.path}#${gap.index}`
      const more = (side: 'down' | 'up', count: number) =>
        look(file, now => ({ shown: { ...now.shown, [`${gap.index}:${side}`]: (now.shown[`${gap.index}:${side}`] ?? 0) + count } }))
      return (
        <Box flexDirection="row" gap={1} flexWrap="wrap" alignItems="center" paddingBottom={1} key={`gap:${id}`}>
          <Text dimColor>{`${gap.hidden} unchanged line${gap.hidden > 1 ? 's' : ''}`}</Text>
          {openLinesNow && gap.hidden > moreLines &&
            <Button key={`down:${id}`} label={`${moreLines} more ↓`} onPress={() => more('down', moreLines)} />}
          {openLinesNow && gap.hidden > moreLines &&
            <Button key={`up:${id}`} label={`${moreLines} more ↑`} onPress={() => more('up', moreLines)} />}
          {openLinesNow && <Button key={`all:${id}`} label="Show all" onPress={() => more('down', gap.hidden)} />}
        </Box>
      )
    }

    // The lines a change touches and its buttons, after its last line.
    const changeRow = (file: EditedFile, i: number) => {
      const hunk = file.hunks[i]
      if (!hunk) return undefined
      const id = `${file.path}#${i}`
      const { first, last } = changedLines(hunk)
      const isDeleted = file.note === 'deleted'
      return (
        <Box flexDirection="column" paddingBottom={1} key={`change:${id}`}>
          <Box flexDirection="row" gap={1} flexWrap="wrap" alignItems="center">
            <Text dimColor>{first === last ? `Line ${first}` : `Lines ${first}–${last}`}</Text>
            {!isDeleted && <Button key={`undo:${id}`} label="Undo change" onPress={act(() => undoChange($, file, hunk))} />}
            <Button key={`comment:${id}`} label="Comment"
              onPress={() => update($, commenting, now => now === id ? '' : id)} />
          </Box>
          {writing === id && commentInput(id, file, first, last, hunk.text)}
        </Box>
      )
    }

    return (
      <Box flexDirection="column" gap={1}>
        <Box flexDirection="row" gap={1} alignItems="center">
          <Text dimColor>
            {work.length ? `${changed.length} of ${work.length} files changed` : 'No edits in this session yet.'}
          </Text>
          {changed.length > 1 &&
            <Button key="keep-all" label="Keep All" onPress={act(async () => {
              for (const file of changed) await keep($, file)
            })} />}
        </Box>
        {pending.length > 0 &&
          <Box flexDirection="column">
            <Text dimColor>Goes with your next message:</Text>
            {pending.map(comment => (
              <Box flexDirection="row" gap={1} key={`pending:${comment.id}`}>
                <Text wrap="truncate-end">{shown(comment.path)}:{comment.start} {comment.text}</Text>
                <Button key={`drop:${comment.id}`} label="Remove"
                  onPress={() => changeComments($, list => list.filter(c => c.id !== comment.id))} />
              </Box>
            ))}
          </Box>}
        {work.filter(isListed).map(fileRow)}
        {others.length > 0 &&
          <Button key="others" plain onPress={() => update($, areOthersOpen, now => !now)}
            label={`${showsOthers ? '▾' : '▸'} ${others.length} other file${others.length > 1 ? 's' : ''}: Claude's own and temporary`} />}
        {showsOthers && others.map(fileRow)}
      </Box>
    )
  })
}
