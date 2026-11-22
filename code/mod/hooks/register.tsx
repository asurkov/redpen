import { atom, read, update } from 'claude-code'
import type { EngineInterface, McpToolResult, Register } from 'claude-code'

import type { EditedFile } from '../types'

// The files this session edited, from the record redpen's recorder keeps in
// ~/.redpen/code/<session>/ (files/<key>.json and base/<key>), each counted against its baseline: its
// text before the session's first edit to it, or as last accepted on the review page. The review
// itself (Accept and Reject, history, comments) is redpen's page, which Open review brings up.

const paneId = 'redpen'
const files = atom({ plugin: 'redpen', key: 'files' } as const, [], { shape: 'files-3' })
const areOthersOpen = atom({ plugin: 'redpen', key: 'areOthersOpen' } as const, false)
const redpenTab = atom({ plugin: 'redpen', key: 'redpenTab' } as const, { id: '', url: '' })

type Places = { record: string; cwd: string; home: string }

async function places($: EngineInterface): Promise<Places> {
  const home = (await $.env.get('HOME')) ?? ''
  return { record: `${home}/.redpen/code/${await $.session.id()}`, cwd: await $.session.cwd(), home }
}

async function signatureOf($: EngineInterface, path: string) {
  const stat = await $.fs.stat(path).catch(() => undefined)
  return stat ? `${stat.mtimeMs}:${stat.size}` : '-'
}

// Counts the file's lines added and removed against its baseline and says what the list shows for it.
async function counted($: EngineInterface, record: string, entry: Pick<EditedFile, 'path' | 'key' | 'isNew' | 'signature' | 'isGenerated'>): Promise<EditedFile> {
  const base = `${record}/base/${entry.key}`
  const hasBase = await $.fs.exists(base)
  const exists = await $.fs.exists(entry.path)
  const plain = { ...entry, added: 0, removed: 0 }
  if (!hasBase && !entry.isNew) return { ...plain, kind: 'unknown', note: 'old text missing' }
  if (!hasBase && !exists) return { ...plain, kind: 'same', note: '' }
  const ran = await $.process.run(['git', 'diff', '--no-index', '--numstat', '--no-ext-diff', '--',
    hasBase ? base : '/dev/null', exists ? entry.path : '/dev/null'])
  if (ran.exitCode === 0) return { ...plain, kind: 'same', note: '' }
  if (ran.exitCode !== 1) return { ...plain, kind: 'unknown', note: ran.stderr.trim().split('\n')[0] || 'diff failed' }
  const note = !exists ? 'deleted' : !hasBase ? 'new' : ''
  const [added = '', removed = ''] = ran.stdout.split('\t')
  if (added === '-') return { ...plain, kind: 'changed', note: note || 'binary' }
  return { ...plain, kind: 'changed', note, added: Number(added) || 0, removed: Number(removed) || 0 }
}

// The record's entries as last read, by name, with their mtime then.
const savedEntries = new Map<string, { mtimeMs: number; saved: { path: string; new: boolean } }>()

// Whether a file is one its project generates, as redpen's review.py says (its .gitattributes'
// linguist-generated, lockfiles, a generator's banner), by path, with when it said so: asked again
// after a minute, so an edited .gitattributes shows.
const generatedVerdicts = new Map<string, { isGenerated: boolean; at: number }>()
const verdictMs = 60_000

async function markGenerated($: EngineInterface, paths: string[]) {
  const now = Date.now()
  const asked = paths.filter(path => (generatedVerdicts.get(path)?.at ?? 0) < now - verdictMs)
  if (!asked.length) return
  const { home } = await places($)
  const ran = await $.process.run(['python3', `${home}/.claude/skills/redpen/code/review.py`, '--generated', ...asked])
    .catch(() => undefined)
  // Unsaid, a file counts as the work's.
  const said = new Set(ran?.exitCode === 0 ? ran.stdout.split('\n').filter(Boolean) : [])
  for (const path of asked) generatedVerdicts.set(path, { isGenerated: said.has(path), at: now })
}

// Reads the record and counts each file whose text or baseline changed since it was last counted.
async function scanOnce($: EngineInterface, force: boolean) {
  const { record } = await places($)
  const entries = await $.fs.list(`${record}/files`).catch(() => [])
  const known = new Map((await read($, files)).map(file => [file.key, file]))
  const next: EditedFile[] = []
  const saved: { key: string; path: string; isNew: boolean }[] = []
  for (const { name, mtimeMs } of entries) {
    // The hooks write each file whole under a dot name, then rename it.
    if (name.startsWith('.') || !name.endsWith('.json')) continue
    const key = name.slice(0, -'.json'.length)
    let entry = savedEntries.get(name)
    if (!entry || entry.mtimeMs !== mtimeMs) {
      entry = { mtimeMs, saved: JSON.parse(await $.fs.read(`${record}/files/${name}`)) }
      savedEntries.set(name, entry)
    }
    saved.push({ key, path: entry.saved.path, isNew: entry.saved.new })
  }
  await markGenerated($, saved.map(entry => entry.path)).catch(() => undefined)
  for (const { key, path, isNew } of saved) {
    const signature = `${await signatureOf($, path)} ${await signatureOf($, `${record}/base/${key}`)}`
    const isGenerated = generatedVerdicts.get(path)?.isGenerated ?? false
    const old = known.get(key)
    next.push(!force && old && old.signature === signature && old.isNew === isNew && old.isGenerated === isGenerated ? old :
      await counted($, record, { path, key, isNew, signature, isGenerated }))
  }
  next.sort((a, b) => a.path.localeCompare(b.path))
  const before = JSON.stringify(await read($, files))
  if (JSON.stringify(next) !== before) await update($, files, () => next)
}

let scanning: Promise<void> | undefined
let isAskedAgain = false

// A scan asked for while one runs runs again after it.
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

// The desktop's Browser pane and the view of its side panes, as /mcp lists them.
const browserServer = 'Claude Browser'
const viewServer = 'ccd_view'

// The JSON object an MCP result's text opens with; the Browser pane's tools follow it with a sentence.
function openingJson(result: McpToolResult | undefined): Record<string, unknown> | undefined {
  const text = result?.content.map(block => block.text ?? '').join('') ?? ''
  try {
    return JSON.parse(text.slice(0, text.lastIndexOf('}') + 1))
  } catch {
    return undefined
  }
}

// Opens this session's redpen page, the whole review (Accept and Reject, history, comments on any
// line), in the desktop's Browser pane: in the tab it opened there before while that tab is open, else
// in a new one. From the terminal, or with no Browser pane to reach, in the default browser. Given a
// file, at that file: the page opens what a link into it names, #<path>.
async function openRedpen($: EngineInterface, surface: string, path?: string) {
  const { home } = await places($)
  const ran = await $.process.run(['python3', `${home}/.claude/skills/redpen/code/review.py`,
    '--session', await $.session.id()]).catch(() => undefined)
  const url = ran?.stdout.trim().split('\n').pop() ?? ''
  if (!url.startsWith('http://')) {
    $.ui.toast(`redpen did not start: ${ran?.stderr.trim().split('\n')[0] || 'its script did not run'}`)
    return
  }
  const linked = path ? `${url}#${path.split('/').map(encodeURIComponent).join('/')}` : url
  const browser = surface === 'desktop' ? await $.mcp.connect(browserServer).catch(() => undefined) : undefined
  if (!browser?.isConnected) {
    await $.process.run(['/usr/bin/open', linked])
    return
  }
  const call = (tool: string, args: Record<string, unknown>) =>
    $.mcp.call(browser.server, tool, args).catch(() => undefined)
  const tab = await read($, redpenTab)
  const tabs = (openingJson(await call('tabs_context', {}))?.tabs ?? []) as { tabId: string }[]
  const isOpen = tab.url === url && tabs.some(open => open.tabId === tab.id)
  const shown = isOpen ? await call('tabs_select', { tabId: tab.id }) : await call('preview_start', { url: linked })
  // In the tab open already, a new link is the same page's: it opens the file where the page stands.
  if (isOpen && path && shown && !shown.isError) await call('navigate', { url: linked, tabId: tab.id })
  if (!shown || shown.isError) {
    await $.process.run(['/usr/bin/open', linked])
    $.ui.toast('The Browser pane did not open it; it is open in your browser.')
    return
  }
  if (!isOpen) await update($, redpenTab, () => ({ id: String(openingJson(shown)?.tabId ?? ''), url }))
  // The page opens behind whatever the side shows; the app's own view server brings the pane to the front.
  const view = await $.mcp.connect(viewServer).catch(() => undefined)
  if (view?.isConnected) await $.mcp.call(view.server, 'show_pane', { pane: 'preview' }).catch(() => undefined)
}

// Claude's own files (its settings, skills, mods and memory) and temporary ones stand apart from the
// work's: the pane folds them until asked for, and the row above the prompt leaves them out.
const isOther = (file: EditedFile, home: string) =>
  file.path.startsWith(`${home}/.claude/`) || /^\/(private\/)?(tmp|var\/folders)\//.test(file.path)

// The work's files and those of them changed, as the pane's head and the row above the prompt count
// them: not Claude's own, nor those the project generates.
const sortedFiles = (list: EditedFile[], home: string) => {
  const work = list.filter(file => !isOther(file, home) && !file.isGenerated)
  return { work, changed: work.filter(file => file.kind === 'changed') }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({ name: 'edits', description: 'Show the files this session edited, and open their review in redpen' })
    await scan($, true)
    $.clock.every(1500, () => void scan($))
    return started
  })

  on('command.run', { command: 'edits' }, async $ => {
    await $.ui.open({ id: paneId, title: 'redpen' })
    void scan($)
    return { text: 'redpen pane opened.' }
  })

  // One row above the prompt while the session's work has changes: their count and the way to the
  // review, which leaves the side of the window to the page. A phone has no page to open.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.surface === 'mobile' || e.props.hasSurvey) return next(e)
    const { changed, work } = sortedFiles(await read($, files), (await places($)).home)
    if (!changed.length) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    return (
      <Box flexDirection="row" gap={1} alignItems="center">
        <Text dimColor>{`redpen · ${changed.length} of ${work.length} files changed`}</Text>
        <Button key="redpen" label="Open review" onPress={press => void openRedpen($, press.surface)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: paneId }, async ($, e) => {
    if (e.surface === 'mobile') {
      const { Text } = $.ui.resolve(e)
      return <Text dimColor>redpen shows on the desktop and in the terminal.</Text>
    }
    const { Box, Text, Button } = $.ui.resolve(e)
    const { cwd, home } = await places($)
    const list = await read($, files)
    const showsOthers = await read($, areOthersOpen)
    const isListed = (file: EditedFile) => file.kind !== 'same'
    const { work, changed } = sortedFiles(list, home)
    // Generated files are listed after the work's, said so, and not counted.
    const generated = list.filter(file => !isOther(file, home) && file.isGenerated && isListed(file))
    const others = list.filter(file => isOther(file, home) && isListed(file))
    const shown = (path: string) => path.startsWith(`${cwd}/`) ? path.slice(cwd.length + 1) :
      path.startsWith(`${home}/`) ? `~${path.slice(home.length)}` : path

    const fileRow = (file: EditedFile) => {
      if (file.kind === 'unknown') return <Text key={`file:${file.path}`} dimColor>{`${shown(file.path)}: ${file.note}`}</Text>
      const name = file.path.split('/').pop() ?? file.path
      const dir = shown(file.path).slice(0, -name.length).replace(/\/$/, '')
      return (
        <Box flexDirection="row" gap={1} flexWrap="wrap" alignItems="center" key={`file:${file.path}`}>
          <Button key={`open:${file.path}`} plain label={name} onPress={press => void openRedpen($, press.surface, file.path)} />
          <Text color="diffAdded">{`+${file.added}`}</Text>
          <Text color="diffRemoved">{`−${file.removed}`}</Text>
          {file.note ? <Text dimColor>{file.note}</Text> : undefined}
          {file.isGenerated ? <Text dimColor>generated</Text> : undefined}
          {dir ? <Text dimColor>{dir}</Text> : undefined}
        </Box>
      )
    }

    return (
      <Box flexDirection="column" gap={1}>
        <Box flexDirection="row" gap={1} alignItems="center">
          <Text dimColor>
            {work.length ? `${changed.length} of ${work.length} files changed` : 'No edits in this session yet.'}
          </Text>
          {list.length > 0 &&
            <Button key="redpen" label="Open review" onPress={press => void openRedpen($, press.surface)} />}
        </Box>
        <Box flexDirection="column">
          {work.filter(isListed).map(fileRow)}
          {generated.map(fileRow)}
        </Box>
        {others.length > 0 &&
          <Button key="others" plain onPress={() => update($, areOthersOpen, now => !now)}
            label={`${showsOthers ? '▾' : '▸'} ${others.length} other file${others.length > 1 ? 's' : ''}: Claude's own and temporary`} />}
        {showsOthers && <Box flexDirection="column">{others.map(fileRow)}</Box>}
      </Box>
    )
  })
}
