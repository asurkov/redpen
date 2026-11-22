import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'

// Files Claude changed, created and deleted, against their baselines, in a store and a working folder
// that live in memory; git's counts and redpen's script answered by hand.
const record = '/h/.redpen/code/s1'
const file = '/w/src/a.txt'
const page = 'http://127.0.0.1:7878/code/s/s1/'

const paneSite = {
  plugin: 'redpen',
  component: 'Pane',
  requestId: 'redpen',
  props: {
    title: 'redpen',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

// The session's record: each file's entry and, unless it is new, its baseline; and the files on disk.
// `numstat` is what git counts for a file against its baseline.
function session(on: On, edited: { path: string; isNew?: boolean; onDisk?: boolean; numstat: string }[]) {
  const disk = new Set<string>()
  const entries = new Map<string, string>()
  const counts = new Map<string, string>()
  edited.forEach(({ path, isNew = false, onDisk = true, numstat }, i) => {
    entries.set(`${record}/files/k${i}.json`, JSON.stringify({ path, new: isNew }))
    if (!isNew) disk.add(`${record}/base/k${i}`)
    if (onDisk) disk.add(path)
    counts.set(path, numstat)
    counts.set(`${record}/base/k${i}`, numstat)
  })
  const ran: string[] = []
  on('env.get', () => ({ value: '/h' }))
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/w' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'edits' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.toast', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('fs.list', (_, e) => ({
    value: [...entries.keys()].filter(p => p.startsWith(`${e.path}/`))
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
  }))
  on('fs.read', (_, e) => ({ value: entries.get(e.path) ?? '' }))
  on('fs.exists', (_, e) => ({ value: disk.has(e.path) }))
  on('fs.stat', () => ({ value: { kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false } }))
  on('process.run', (_, e) => {
    ran.push(e.argv.join(' '))
    const done = (exitCode: number, stdout = '') =>
      ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
    if (e.argv[0] === 'git') {
      const [before = '', after = ''] = e.argv.slice(-2)
      const numstat = counts.get(after) ?? counts.get(before) ?? ''
      return numstat ? done(1, `${numstat}\t${before} => ${after}\n`) : done(0)
    }
    if (e.argv[1] === '/h/.claude/skills/redpen/code/review.py') return done(0, `${page}\n`)
    return done(0)
  })
  return ran
}

test('the pane lists the edited files with their counts', async ($, on) => {
  session(on, [
    { path: file, numstat: '2\t1' },
    { path: '/w/src/new.txt', isNew: true, numstat: '3\t0' },
    { path: '/w/gone.txt', onDisk: false, numstat: '0\t4' },
    { path: '/w/logo.png', numstat: '-\t-' },
    { path: '/w/same.txt', numstat: '' },
  ])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...paneSite, surface })
    expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('4 of 5 files changed')
    // The counts stand beside the name, in the colours of the lines they count.
    expect((await ui.find({ key: `file:${file}` }))?.text).toBe('a.txt+2−1src')
    expect((await ui.find({ type: 'Text', text: '+2' }))?.props.color).toBe('diffAdded')
    expect((await ui.find({ type: 'Text', text: '−1' }))?.props.color).toBe('diffRemoved')
    expect((await ui.find({ key: 'file:/w/src/new.txt' }))?.text).toBe('new.txt+3−0newsrc')
    expect((await ui.find({ key: 'file:/w/gone.txt' }))?.text).toBe('gone.txt+0−4deleted')
    expect((await ui.find({ key: 'file:/w/logo.png' }))?.text).toBe('logo.png+0−0binary')
    expect(await ui.find({ key: 'file:/w/same.txt' })).toBeUndefined()
    expect((await ui.find({ key: 'redpen' }))?.props.label).toBe('Open review')
    await ui.unmount()
  }
})

test('with no edits the pane says so and offers nothing to open', async ($, on) => {
  session(on, [])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: 'No edits in this session yet.' })).toBeDefined()
  expect(await ui.find({ key: 'redpen' })).toBeUndefined()
  await ui.unmount()
})

test("Claude's own files and the temporary ones stay folded until asked for", async ($, on) => {
  const own = '/h/.claude/skills/review/SKILL.md'
  const temporary = '/private/tmp/notes/a.txt'
  session(on, [file, own, temporary].map(path => ({ path, numstat: '2\t1' })))
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('1 of 1 files changed')
  expect(await ui.find({ key: `file:${file}` })).toBeDefined()
  expect(await ui.find({ key: `file:${own}` })).toBeUndefined()
  expect(await ui.find({ key: `file:${temporary}` })).toBeUndefined()
  expect((await ui.find({ key: 'others' }))?.text).toBe("▸ 2 other files: Claude's own and temporary")

  await ui.press({ key: 'others' })
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('1 of 1 files changed')
  expect((await ui.find({ key: `file:${own}` }))?.text).toBe('SKILL.md+2−1~/.claude/skills/review')
  expect(await ui.find({ key: `file:${temporary}` })).toBeDefined()
  await ui.unmount()
})

test('Open review opens the page in the Browser pane, in the same tab while it is open', async ($, on) => {
  const ran = session(on, [{ path: file, numstat: '2\t1' }])
  const calls: string[] = []
  let tabs: string[] = []
  let opened = 0
  on('mcp.connect', (_, e) => ({ value: { isConnected: true as const, server: e.server } }))
  on('mcp.call', (_, e) => {
    calls.push(`${e.server} ${e.tool} ${JSON.stringify(e.args)}`)
    const text = (value: unknown) => ({ value: { content: [{ type: 'text', text: `${JSON.stringify(value)}\nA sentence after.` }], isError: false } })
    if (e.tool === 'tabs_context') return text({ browserOpen: tabs.length > 0, tabs: tabs.map(tabId => ({ tabId, origin: 'http://127.0.0.1:7878', isActive: true })) })
    if (e.tool === 'preview_start') {
      const tabId = `tab-${++opened}`
      tabs.push(tabId)
      return text({ serverId: 'p', tabId, reused: false, navOk: true })
    }
    return text({})
  })

  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  await ui.press({ key: 'redpen' })
  expect(ran).toContain('python3 /h/.claude/skills/redpen/code/review.py --session s1')
  expect(calls.filter(call => !call.includes('tabs_context'))).toEqual([
    `Claude Browser preview_start {"url":"${page}"}`,
    'ccd_view show_pane {"pane":"preview"}',
  ])

  // Pressed again, the tab it opened comes to the front, its page as the person left it.
  await ui.press({ key: 'redpen' })
  expect(calls.slice(-2)).toEqual(['Claude Browser tabs_select {"tabId":"tab-1"}', 'ccd_view show_pane {"pane":"preview"}'])
  expect(opened).toBe(1)

  // That tab closed, a new one opens.
  tabs = []
  await ui.press({ key: 'redpen' })
  expect(calls.at(-2)).toBe(`Claude Browser preview_start {"url":"${page}"}`)
  expect(opened).toBe(2)
  await ui.unmount()

  // The terminal has no Browser pane: the page opens in the default browser.
  const terminal = await $.ui.mount({ ...paneSite, surface: 'terminal' })
  await terminal.press({ key: 'redpen' })
  expect(ran.at(-1)).toBe(`/usr/bin/open ${page}`)
  expect(opened).toBe(2)
  await terminal.unmount()
})
