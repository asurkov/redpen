import { expect, mock, test } from 'claude-code/testing'
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

const bandSite = {
  plugin: 'redpen',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isCollapsed: false, isWorking: false, maxRows: 5, bodyColumns: 80, scroll: { offset: 0, bodyRows: 5 }, view: {} },
} as const

// The session's record: each file's entry and, unless it is new, its baseline; and the files on disk.
// `numstat` is what git counts for a file against its baseline. `marks` are the record's after/<key>
// marks, by path, with when each was written; `surfaces`, where the session draws.
function session(on: On, edited: { path: string; isNew?: boolean; onDisk?: boolean; isGenerated?: boolean; numstat: string }[]) {
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
  const opened: string[] = []
  const marks = new Map<string, number>()
  const surfaces: ('terminal' | 'desktop')[] = ['desktop']
  on('env.get', () => ({ value: '/h' }))
  on('session.surfaces', () => ({ value: [...surfaces] }))
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/w' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'edits' } }))
  on('ui.open', (_, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  on('ui.toast', () => ({ value: undefined }))
  // The every-1.5-seconds scan waits on a clock that moves only when a test moves it.
  const clock = mock.clock(on)
  on('fs.list', (_, e) => ({
    value: [...entries.keys(), ...marks.keys()].filter(p => p.startsWith(`${e.path}/`))
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: marks.get(p) ?? 1, isLink: false })),
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
    if (e.argv[2] === '--generated') {
      const asked = e.argv.slice(3)
      return done(0, edited.filter(file => file.isGenerated && asked.includes(file.path)).map(file => `${file.path}\n`).join(''))
    }
    if (e.argv[1] === '/h/.claude/skills/redpen/code/review.py') return done(0, `${page}\n`)
    return done(0)
  })
  return { ran, opened, marks, surfaces, clock }
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
  const { ran } = session(on, [{ path: file, numstat: '2\t1' }])
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

test('a row above the prompt counts the changed files and opens the review; the pane waits for /edits', async ($, on) => {
  const { opened } = session(on, [
    { path: file, numstat: '2\t1' },
    { path: '/w/same.txt', numstat: '' },
    { path: '/h/.claude/settings.json', numstat: '1\t0' },
  ])
  const calls: string[] = []
  on('mcp.connect', (_, e) => ({ value: { isConnected: true as const, server: e.server } }))
  on('mcp.call', (_, e) => {
    calls.push(`${e.server} ${e.tool}`)
    return { value: { content: [{ type: 'text', text: '{"tabs":[],"tabId":"tab-1"}' }], isError: false } }
  })
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  expect(opened).toEqual([])
  await $.command.run({ command: 'edits', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 120 } })
  expect(opened).toEqual(['redpen'])

  for (const surface of ['terminal', 'desktop'] as const) {
    const band = await $.ui.mount({ ...bandSite, surface })
    // Claude's own files are not the work's: the row counts the work alone.
    expect((await band.find({ type: 'Text', text: /^redpen/ }))?.text).toBe('redpen · 1 of 2 files changed')
    expect((await band.find({ key: 'redpen' }))?.props.label).toBe('Open review')
    await band.unmount()
  }
  const band = await $.ui.mount({ ...bandSite, surface: 'desktop' })
  await band.press({ key: 'redpen' })
  expect(calls).toContain('Claude Browser preview_start')
  await band.unmount()

  const phone = await $.ui.mount({ ...bandSite, surface: 'mobile' })
  expect(await phone.find({ key: 'redpen' })).toBeUndefined()
  await phone.unmount()
  const survey = await $.ui.mount({ ...bandSite, surface: 'desktop', props: { ...bandSite.props, hasSurvey: true } })
  expect(await survey.find({ key: 'redpen' })).toBeUndefined()
  await survey.unmount()
})

test('with only Claude\'s own files changed there is no row above the prompt', async ($, on) => {
  session(on, [{ path: '/h/.claude/settings.json', numstat: '1\t0' }])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const band = await $.ui.mount({ ...bandSite, surface: 'desktop' })
  expect(await band.find({ key: 'redpen' })).toBeUndefined()
  await band.unmount()
})

test('generated files are listed, said so, and counted neither in the pane nor in the row', async ($, on) => {
  session(on, [
    { path: file, numstat: '2\t1' },
    { path: '/w/yarn.lock', isGenerated: true, numstat: '40\t12' },
  ])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('1 of 1 files changed')
  expect((await ui.find({ key: 'file:/w/yarn.lock' }))?.text).toBe('yarn.lock+40−12generated')
  await ui.unmount()
  const band = await $.ui.mount({ ...bandSite, surface: 'desktop' })
  expect((await band.find({ type: 'Text', text: /^redpen/ }))?.text).toBe('redpen · 1 of 1 files changed')
  await band.unmount()
})

test('a file in the pane opens the review at that file, in the tab open already', async ($, on) => {
  session(on, [{ path: '/w/src/my file.txt', numstat: '2\t1' }])
  const calls: string[] = []
  let tabs: string[] = []
  on('mcp.connect', (_, e) => ({ value: { isConnected: true as const, server: e.server } }))
  on('mcp.call', (_, e) => {
    calls.push(`${e.tool} ${JSON.stringify(e.args)}`)
    const text = (value: unknown) => ({ value: { content: [{ type: 'text', text: `${JSON.stringify(value)}\nA sentence after.` }], isError: false } })
    if (e.tool === 'tabs_context') return text({ tabs: tabs.map(tabId => ({ tabId })) })
    if (e.tool === 'preview_start') {
      tabs = ['tab-1']
      return text({ tabId: 'tab-1' })
    }
    return text({})
  })
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  const linked = `${page}#/w/src/my%20file.txt`
  await ui.press({ key: 'open:/w/src/my file.txt' })
  expect(calls).toContain(`preview_start {"url":"${linked}"}`)
  // Pressed again: the same tab, the page told the file by its link.
  await ui.press({ key: 'open:/w/src/my file.txt' })
  expect(calls.filter(call => !call.startsWith('tabs_context') && !call.startsWith('show_pane')).slice(-2))
    .toEqual(['tabs_select {"tabId":"tab-1"}', `navigate {"url":"${linked}","tabId":"tab-1"}`])
  await ui.unmount()
})

test('a turn that edited the work opens the review as it ends', async ($, on) => {
  const { marks, surfaces, clock } = session(on, [
    { path: file, numstat: '2\t1' },
    { path: '/h/.claude/settings.json', numstat: '1\t0' },
  ])
  const calls: string[] = []
  on('mcp.connect', (_, e) => ({ value: { isConnected: true as const, server: e.server } }))
  on('mcp.call', (_, e) => {
    calls.push(`${e.server} ${e.tool}`)
    return { value: { content: [{ type: 'text', text: '{"tabs":[],"tabId":"tab-1"}' }], isError: false } }
  })
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  on('tool.call', () => ({ result: { content: [] } }))
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const opens = () => calls.filter(call => call === 'Claude Browser preview_start').length
  let turns = 0
  // A turn of the main loop; edits, given, are the session's own to those files (by their keys), as
  // the recorder marks them; during, if given, runs in the turn.
  const turn = async (edits: string[], ending: { reason?: 'answer' | 'aborted'; agentId?: string } = {}, during?: () => Promise<unknown>) => {
    const turnId = `t${++turns}`
    await $.turn.start({ text: '', turnId, agentId: ending.agentId })
    for (const key of edits) marks.set(`${record}/after/${key}`, turns + 1)
    await during?.()
    const reason = ending.reason ?? 'answer'
    await $.turn.complete({ answer: '', durationMs: 1, isAborted: reason === 'aborted', turnId, reason, agentId: ending.agentId })
    await clock.settle()
  }

  await turn(['k0'])
  expect(opens()).toBe(1)
  // No edit, or only one to Claude's own files: nothing opens.
  await turn([])
  await turn(['k1'])
  expect(opens()).toBe(1)
  // A turn that showed something in the Browser pane keeps it there.
  await turn(['k0'], {}, () => $.tool.call({ tool: 'mcp__Claude_Browser__navigate', url: 'http://localhost:3000/' }))
  expect(opens()).toBe(1)
  // An interrupted turn opens nothing.
  await turn(['k0'], { reason: 'aborted' })
  expect(opens()).toBe(1)
  // A subagent's edits open the review as the main turn that ran it ends, not as its own turn does.
  await turn([], {}, async () => {
    await turn(['k0'], { agentId: 'a1' })
    expect(opens()).toBe(1)
  })
  expect(opens()).toBe(2)
  // In the terminal, the row above the prompt says so; nothing opens.
  surfaces.splice(0, 1, 'terminal')
  await turn(['k0'])
  expect(opens()).toBe(2)
})
