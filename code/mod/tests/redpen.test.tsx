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
// `numstat` is what git counts for a file against its baseline; `hashes`, where given, the hashes of its
// baseline, of its text now and of the text the session's last edit left (its after/<key> mark). `marks`
// are the record's after/<key> marks, by path, with when each was written; `surfaces`, where the session draws.
type Hashes = { base: string; now: string; after: string; taken?: string }
function session(on: On, edited: { path: string; isNew?: boolean; onDisk?: boolean; isGenerated?: boolean; numstat: string; hashes?: Hashes }[]) {
  const disk = new Set<string>([record])
  const entries = new Map<string, string>()
  const counts = new Map<string, string>()
  const hashOf = new Map<string, string>()
  // The folders git says are in a work tree; none unless a test adds them.
  const repos = new Set<string>()
  edited.forEach(({ path, isNew = false, onDisk = true, numstat, hashes }, i) => {
    entries.set(`${record}/files/k${i}.json`, JSON.stringify({ path, new: isNew }))
    if (!isNew) disk.add(`${record}/base/k${i}`)
    if (onDisk) disk.add(path)
    counts.set(path, numstat)
    counts.set(`${record}/base/k${i}`, numstat)
    if (hashes) {
      entries.set(`${record}/after/k${i}`, hashes.after)
      if (hashes.taken) entries.set(`${record}/review/taken/k${i}`, hashes.taken)
      hashOf.set(`${record}/base/k${i}`, hashes.base)
      hashOf.set(path, hashes.now)
    }
  })
  const ran: string[] = []
  const opened: string[] = []
  const marks = new Map<string, number>()
  const surfaces: ('terminal' | 'desktop')[] = ['desktop']
  // What review.py --ask-overview prints (the question for an overview, or nothing); the questions the mod asked
  // a fork of the session, and the fork's replies, in turn; what review.py --outline was given, and its
  // refusals, in turn; how many comments review.py --count-comments says wait; and the files' times, by
  // path, for those a test rewrites.
  const asking = { text: '' }
  // The question review.py --ask-commit prints, and what review.py --commit-message was given, in turn.
  const committing = { text: '' }
  const commitDrafts: string[] = []
  const waiting = { count: 0 }
  const forked: string[] = []
  const replies: string[] = []
  const outlines: string[] = []
  const refusals: string[] = []

  const times = new Map<string, number>()
  on('model.fork', (_, e) => {
    forked.push(e.prompt)
    const usage = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }
    return { value: { isAnswered: true as const, text: replies.shift() ?? '{"items": []}', usage } }
  })
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
  on('fs.write', (_, e) => {
    entries.set(e.path, e.text)
    return { value: undefined }
  })
  on('fs.exists', (_, e) => ({ value: disk.has(e.path) }))
  on('fs.stat', (_, e) => ({ value: { kind: 'file' as const, size: 1, mtimeMs: times.get(e.path) ?? 1, isLink: false } }))
  on('process.run', (_, e) => {
    ran.push(e.argv.join(' '))
    const done = (exitCode: number, stdout = '') =>
      ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
    if (e.argv[0] === 'git' && e.argv[3] === 'rev-parse') return done(repos.has(e.argv[2] ?? '') ? 0 : 128)
    if (e.argv[0] === 'git') {
      const [before = '', after = ''] = e.argv.slice(-2)
      const numstat = counts.get(after) ?? counts.get(before) ?? ''
      return numstat ? done(1, `${numstat}\t${before} => ${after}\n`) : done(0)
    }
    if (e.argv[0] === 'shasum') return done(0, e.argv.slice(3).map(path => `${hashOf.get(path) ?? '0'}  ${path}\n`).join(''))
    if (e.argv[2] === '--generated') {
      const asked = e.argv.slice(3)
      return done(0, edited.filter(file => file.isGenerated && asked.includes(file.path)).map(file => `${file.path}\n`).join(''))
    }
    if (e.argv[2] === '--ask-overview') return done(0, asking.text)
    if (e.argv[2] === '--ask-commit') return done(0, committing.text)
    if (e.argv[2] === '--commit-message') {
      commitDrafts.push(e.init?.stdin ?? '')
      return done(0)
    }
    if (e.argv[2] === '--count-comments') return done(0, `${waiting.count}\n`)
    if (e.argv[2] === '--outline') {
      outlines.push(e.init?.stdin ?? '')
      const refusal = refusals.shift()
      return refusal ? { value: { exitCode: 1, stdout: '', stderr: `${refusal}\n`, isStdoutTruncated: false, isStderrTruncated: false } } : done(0)
    }
    if (e.argv[1] === '/h/.claude/skills/redpen/code/review.py') return done(0, `${page}\n`)
    return done(0)
  })
  // Writes the record's overview, as review.py --outline does, and the page's comments waiting for the
  // next message, as the page's server does; and marks the last of them, which count more comments
  // waiting, as the server does when no command of Claude's waits.
  const written = (place: string, data: unknown) => {
    entries.set(place, JSON.stringify(data))
    times.set(place, (times.get(place) ?? 1) + 1)
  }
  const writeOverview = (data: unknown) => written(`${record}/review/outline.json`, data)
  const writeComments = (data: unknown) => written(`${record}/review/comments.json`, data)
  const markComments = (count: number) => {
    waiting.count = count
    written(`${record}/review/unwatched-comment`, 'time')
  }
  const watcher = () => entries.get(`${record}/review/mod-watcher`)
  // The page's Commit opened: its ask for commit messages.
  const askCommit = () => {
    disk.add(`${record}/review/commit-ask.json`)
    written(`${record}/review/commit-ask.json`, { time: 'now' })
  }
  return { ran, opened, marks, surfaces, clock, asking, forked, replies, outlines, refusals, writeOverview, writeComments,
    markComments, watcher, repos, committing, commitDrafts, askCommit }
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

test("the page's Commit asks, and a fork drafts the commit messages for it, nothing in the chat", async ($, on) => {
  const { clock, forked, replies, committing, commitDrafts, askCommit } = session(on, [{ path: file, numstat: '2\t1' }])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  committing.text = 'Draft the commit message of each work tree.\n'
  replies.push('Here: {"/w": "Change a"}')
  await clock.advance(1500)
  await clock.settle()
  expect(forked).toEqual([])
  askCommit()
  await clock.advance(1500)
  await clock.settle()
  expect(forked).toEqual(['Draft the commit message of each work tree.'])
  expect(commitDrafts).toEqual(['{"/w": "Change a"}'])
  // Asked once: no second draft till the page asks again.
  await clock.advance(1500)
  await clock.settle()
  expect(forked.length).toBe(1)
})

test("the page's Describe, waiting for the next message, is answered by a fork at once", async ($, on) => {
  const { clock, asking, forked, outlines, writeComments } = session(on, [{ path: file, numstat: '2\t1' }])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  asking.text = 'The user asked for an overview.\n'
  // Comments with no Describe among them: nothing is asked.
  writeComments([{ id: 'c1', text: 'Why?' }])
  await clock.advance(1500)
  expect(forked).toEqual([])
  writeComments([{ id: 'c1', text: 'Why?' }, { id: 'd1', describe: true }])
  await clock.advance(1500)
  await clock.settle()
  expect(forked).toEqual(['The user asked for an overview.'])
  expect(outlines).toEqual(['{"items": []}'])
  // Read again only once the file changes: no second ask meanwhile.
  await clock.advance(1500)
  expect(forked.length).toBe(1)
})

test("a comment no command waits for reaches the idle session by the mod's prompt, a busy one's as its turn ends", async ($, on) => {
  const { clock, markComments, watcher } = session(on, [{ path: file, numstat: '2\t1' }])
  const submitted: string[] = []
  on('prompt.submit', (_, e) => {
    submitted.push(e.text)
    return { text: e.text }
  })
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  // Marked before this start: it goes with the next message, as it would have.
  markComments(1)
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  await clock.advance(1500)
  expect([submitted, watcher()]).toEqual([[], 'idle'])
  // Idle: sent on at the next beat, once.
  markComments(1)
  await clock.advance(1500)
  await clock.settle()
  expect(submitted).toEqual(['1 new comment on the review page.'])
  await clock.advance(1500)
  expect(submitted.length).toBe(1)
  // A turn runs: the page says so, and the comments wait for its end.
  await $.turn.start({ text: '', turnId: 't1' })
  markComments(2)
  await clock.advance(1500)
  expect([submitted.length, watcher()]).toEqual([1, 'busy'])
  await $.turn.complete({ answer: '', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })
  await clock.advance(1500)
  await clock.settle()
  expect([submitted.at(-1), watcher()]).toEqual(['2 new comments on the review page.', 'idle'])
  // A subagent's turn is no turn of the session's; comments a typed message took meanwhile send nothing.
  await $.turn.start({ text: '', turnId: 't2', agentId: 'a1' })
  markComments(0)
  await clock.advance(1500)
  await clock.settle()
  expect([submitted.length, watcher()]).toEqual([2, 'idle'])
})

test("a file someone else changed since all of the session's changes to it were accepted is not counted", async ($, on) => {
  session(on, [
    { path: file, numstat: '2\t1' },
    // The session's last edit is still pending: its own.
    { path: '/w/mine.txt', numstat: '1\t1', hashes: { base: 'h0', now: 'h1', after: 'h1' } },
    // All of the session's accepted, then another session edited it: theirs alone.
    { path: '/w/theirs.txt', numstat: '3\t2', hashes: { base: 'h1', now: 'h2', after: 'h1' } },
    // Their change taken by the page, at the text the session left, and changed again since: theirs too.
    { path: '/w/again.txt', numstat: '1\t1', hashes: { base: 'h3', now: 'h4', after: 'h1', taken: 'h1' } },
  ])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('2 of 4 files changed')
  expect(await ui.find({ key: 'file:/w/mine.txt' })).toBeDefined()
  expect(await ui.find({ key: 'file:/w/theirs.txt' })).toBeUndefined()
  expect(await ui.find({ key: 'file:/w/again.txt' })).toBeUndefined()
  await ui.unmount()
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

test("a skill kept in its own repository is the work's; side-session notes and Codex's folder are the agents' own", async ($, on) => {
  const skill = '/h/.claude/skills/redpen/code/review.py'
  const notes = '/h/Library/Application Support/Claude/side-session-notes/local_1/plan.md'
  const codex = '/h/.codex/config.toml'
  const { repos } = session(on, [skill, notes, codex].map(path => ({ path, numstat: '2\t1' })))
  repos.add('/h/.claude/skills/redpen/code')
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('1 of 1 files changed')
  expect(await ui.find({ key: `file:${skill}` })).toBeDefined()
  expect(await ui.find({ key: `file:${notes}` })).toBeUndefined()
  expect(await ui.find({ key: `file:${codex}` })).toBeUndefined()
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
  expect(ran).toContain(`/usr/bin/open ${page}`)
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
    expect((await band.find({ key: 'title' }))?.text).toBe('1 file changed')
    expect((await band.find({ key: 'redpen' }))?.props.label).toBe('Review')
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
  expect((await band.find({ key: 'title' }))?.text).toBe('1 file changed')
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

test('a turn that edited the work asks for its overview and opens the review as it ends', async ($, on) => {
  const { marks, surfaces, clock, asking, forked } = session(on, [
    { path: file, numstat: '2\t1' },
    { path: '/h/.claude/settings.json', numstat: '1\t0' },
  ])
  // review.py asks: the turn's end asks a fork of the session, whether the page opens or not.
  asking.text = 'Write the overview as JSON.\n'
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
  expect([opens(), forked.length]).toEqual([1, 1])
  // No edit, or only one to Claude's own files: nothing opens, nothing is asked.
  await turn([])
  await turn(['k1'])
  expect([opens(), forked.length]).toEqual([1, 1])
  // A turn that showed something in the Browser pane keeps it there; its overview is asked for.
  await turn(['k0'], {}, () => $.tool.call({ tool: 'mcp__Claude_Browser__navigate', url: 'http://localhost:3000/' }))
  expect([opens(), forked.length]).toEqual([1, 2])
  // An interrupted turn opens nothing and asks nothing.
  await turn(['k0'], { reason: 'aborted' })
  expect([opens(), forked.length]).toEqual([1, 2])
  // A subagent's edits open the review as the main turn that ran it ends, not as its own turn does.
  await turn([], {}, async () => {
    await turn(['k0'], { agentId: 'a1' })
    expect([opens(), forked.length]).toEqual([1, 2])
  })
  expect([opens(), forked.length]).toEqual([2, 3])
  // In the terminal, the row above the prompt says so; nothing opens, and the overview is asked for.
  surfaces.splice(0, 1, 'terminal')
  await turn(['k0'])
  expect([opens(), forked.length]).toEqual([2, 4])
})

test('the row says what is new, closes with ×, and comes back when the session edits again', async ($, on) => {
  const { marks, clock } = session(on, [
    { path: file, numstat: '2\t1' },
    { path: '/w/src/b.txt', numstat: '1\t0' },
  ])
  on('mcp.connect', (_, e) => ({ value: { isConnected: true as const, server: e.server } }))
  on('mcp.call', () => ({ value: { content: [{ type: 'text', text: '{"tabs":[],"tabId":"tab-1"}' }], isError: false } }))
  // a.txt was edited before the session started (a resume): not new.
  marks.set(`${record}/after/k0`, 1)
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const row = async (act?: string) => {
    const band = await $.ui.mount({ ...bandSite, surface: 'desktop' })
    if (act) await band.press({ key: act })
    const title = (await band.find({ key: 'title' }))?.text
    const more = (await band.find({ key: 'more' }))?.text
    const text = title && [title, more].filter(Boolean).join(' ')
    await band.unmount()
    return text
  }
  // The session edits a file: the next scan has it.
  const edit = async (key: string, at: number) => {
    marks.set(`${record}/after/${key}`, at)
    await clock.advance(1500)
  }

  expect(await row()).toBe('2 files changed')
  await edit('k1', 5)
  expect(await row()).toBe('b.txt')
  // × closes it, and it stays closed while nothing new is edited.
  await row('close')
  expect(await row()).toBeUndefined()
  await clock.advance(1500)
  expect(await row()).toBeUndefined()
  // A new edit brings it back, saying what is new since it was closed.
  await edit('k0', 9)
  expect(await row()).toBe('a.txt')
  // Opening the review is looking: nothing is new, and the row stays.
  await row('redpen')
  expect(await row()).toBe('2 files changed')
})

test('Accept all in the row has review.py accept everything pending', async ($, on) => {
  const { ran } = session(on, [{ path: file, numstat: '2\t1' }])
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const band = await $.ui.mount({ ...bandSite, surface: 'desktop' })
  expect((await band.find({ key: 'acceptAll' }))?.props.label).toBe('Accept all')
  await band.press({ key: 'acceptAll' })
  expect(ran).toContain('python3 /h/.claude/skills/redpen/code/review.py --accept-all --session s1')
  await band.unmount()
})

test('opening the review asks Claude for the overview it lacks, and the row names what is new by it', async ($, on) => {
  const { marks, clock, asking, forked, replies, outlines, refusals, ran, writeOverview } = session(on, [
    { path: file, numstat: '2\t1' },
    { path: '/w/src/b.txt', numstat: '1\t0' },
  ])
  on('mcp.connect', (_, e) => ({ value: { isConnected: true as const, server: e.server } }))
  on('mcp.call', () => ({ value: { content: [{ type: 'text', text: '{"tabs":[],"tabId":"tab-1"}' }], isError: false } }))
  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const row = async (act?: string) => {
    const band = await $.ui.mount({ ...bandSite, surface: 'desktop' })
    if (act) await band.press({ key: act })
    const title = (await band.find({ key: 'title' }))?.text
    const more = (await band.find({ key: 'more' }))?.text
    const text = title && [title, more].filter(Boolean).join(' ')
    await band.unmount()
    return text
  }

  // review.py says no overview is needed: nothing is asked.
  await row('redpen')
  expect(ran).toContain('python3 /h/.claude/skills/redpen/code/review.py --ask-overview --session s1')
  expect(forked).toEqual([])
  // It says one is: a fork of the session answers its question, and review.py keeps the JSON in the reply.
  asking.text = 'Write the overview as JSON.\n'
  replies.push('Here it is:\n```json\n{"items": [{"title": "A"}]}\n```')
  await row('redpen')
  await clock.settle()
  expect(forked).toEqual(['Write the overview as JSON.'])
  expect(outlines).toEqual(['{"items": [{"title": "A"}]}'])
  // One refused is asked again once, with the refusal and the answer; refused again, the ask is dropped.
  refusals.push('x.txt is not among the files', 'still not')
  await row('redpen')
  await clock.settle()
  expect(forked.length).toBe(3)
  expect(forked[2]).toContain('Your answer was refused: x.txt is not among the files')
  expect(forked[2]).toContain('{"items": []}')
  expect(ran).toContain('python3 /h/.claude/skills/redpen/code/review.py --drop-ask --session s1')

  // Edits, then the overview Claude wrote after them: the row names them by its item.
  marks.set(`${record}/after/k1`, 5)
  marks.set(`${record}/after/k0`, 6)
  writeOverview({ time: new Date(10).toISOString(), items: [{ title: 'Prev and next fixed', blocks: [{ key: 'k0', path: file }] }] })
  await clock.advance(1500)
  expect(await row()).toBe('Prev and next fixed +1')
  // a.txt edited after the overview was written: by its name until a new one says what it is.
  marks.set(`${record}/after/k0`, 20)
  await clock.advance(1500)
  expect(await row()).toBe('a.txt +1')
})
