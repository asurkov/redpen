import { expect, test } from 'claude-code/testing'

// A file Claude changed in one place and added a line to, against its baseline, in a store and a
// working folder that live in memory; git, cp and Cursor answered by hand.
const record = '/h/.redpen/code/s1'
const file = '/w/src/a.txt'
const base = 'one\ntwo\nthree\n'
const changed = 'one\nTWO\nthree\nfour\n'
const changedDiff = '@@ -1,3 +1,4 @@\n one\n-two\n+TWO\n three\n+four\n'

const cursor = '/Applications/Cursor.app/Contents/Resources/app/bin/cursor'

const paneSite = {
  plugin: 'edit-review',
  component: 'Pane',
  requestId: 'edit-review',
  props: {
    title: 'Claude Edits',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 40 },
    view: {},
  },
} as const

type Drawing = { findAll: (query: { type?: string }) => Promise<{ key: string | undefined; props: Record<string, unknown>; text: string; children: unknown[] }[]> }

// What an element described among a parent's children shows: a button its label.
const textOf = (part: unknown): string => {
  if (typeof part === 'string') return part
  const { props, children } = (part ?? {}) as { props?: { label?: string }; children?: unknown[] }
  return props?.label ?? (children ?? []).map(textOf).join('')
}

// The open file's lines as drawn: each one's tint, number and text.
const rows = async (ui: Drawing) => (await ui.findAll({ type: 'Box' })).filter(box => box.key?.startsWith('line:'))
  .map(box => [box.props.backgroundColor ?? '', ...box.children.map(textOf)].join('|'))

// What the desktop counts against its 2,000: every element and every text.
const nodes = async (ui: Drawing) => (await ui.findAll({}))
  .reduce((n, element) => n + 1 + element.children.filter(child => typeof child === 'string').length, 0)

test('review a change, comment on it, diff it in Cursor, undo it', async ($, on) => {
  const disk = new Map<string, string>([
    [`${record}/files/k1.json`, JSON.stringify({ path: file, new: false })],
    [`${record}/base/k1`, base],
    [file, changed],
  ])
  const writes = new Map<string, number>()
  const write = (path: string, text: string) => {
    disk.set(path, text)
    writes.set(path, (writes.get(path) ?? 0) + 1)
  }
  const opened: string[] = []
  let status: string | undefined
  let context: readonly string[] | undefined

  on('env.get', () => ({ value: '/h' }))
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/w' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'edits' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', (_, e) => {
    status = e.text
    return { value: undefined }
  })
  on('ui.toast', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('fs.list', (_, e) => ({
    value: [...disk.keys()].filter(p => p.startsWith(`${e.path}/`))
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
  }))
  on('fs.read', (_, e) => {
    const text = disk.get(e.path)
    if (text === undefined) throw new Error(`ENOENT ${e.path}`)
    return { value: text }
  })
  on('fs.write', (_, e) => {
    write(e.path, e.text)
    return { value: undefined }
  })
  on('fs.exists', (_, e) => ({ value: disk.has(e.path) }))
  on('fs.stat', (_, e) => {
    if (!disk.has(e.path)) throw new Error(`ENOENT ${e.path}`)
    return { value: { kind: 'file' as const, size: disk.get(e.path)!.length, mtimeMs: writes.get(e.path) ?? 0, isLink: false } }
  })
  on('process.run', (_, e) => {
    const [command, ...args] = e.argv
    const done = (exitCode: number, stdout = '') =>
      ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
    if (command === 'git') {
      const [a = '', b = ''] = args.slice(-2)
      if (disk.get(a) === disk.get(b)) return done(0)
      if (disk.get(a) === base && disk.get(b) === changed) return done(1, `--- a\n+++ b\n${changedDiff}`)
      throw new Error(`no fixture for ${a} ${b}`)
    }
    if (command === '/bin/cp') write(args[1]!, disk.get(args[0]!)!)
    if (command === cursor) opened.push(args.join(' '))
    return done(0)
  })
  on('prompt.submit', (_, e) => {
    context = e.context
    return { text: e.text, context: e.context }
  })

  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...paneSite, surface })
    expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('1 of 1 files changed')
    expect((await ui.find({ key: `toggle:${file}` }))?.text).toBe('▸ a.txt')
    // The counts stand beside the name, in the colours of the lines they count.
    expect((await ui.find({ key: `file:${file}` }))?.text).toBe('▸ a.txt+2−1src')
    expect((await ui.find({ type: 'Text', text: '+2' }))?.props.color).toBe('diffAdded')
    expect((await ui.find({ type: 'Text', text: '−1' }))?.props.color).toBe('diffRemoved')
    await ui.unmount()
  }

  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  await ui.press({ key: `toggle:${file}` })
  expect(await rows(ui)).toEqual([
    '|1|one',
    'diffRemovedDimmed||two',
    'diffAddedDimmed|2|TWO',
    '|3|three',
    'diffAddedDimmed|4|four',
  ])
  expect((await ui.find({ type: 'Text', text: /^Lines? \d/ }))?.text).toBe('Lines 2–4')

  await ui.press({ key: `comment:${file}#0` })
  await ui.input({ key: `text:${file}#0`, text: 'Why upper case?' })
  expect(status).toBe('1 comment on changes go with your next message')
  expect((await ui.find({ type: 'Text', text: /Why upper case/ }))?.text).toBe('src/a.txt:2 Why upper case?')

  await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'composer' } })
  expect(context?.[0]).toContain(`${file}, lines 2-4:`)
  expect(context?.[0]).toContain('Comment: Why upper case?')
  expect(status).toBeUndefined()

  // A line's number opens a comment on that line; a removed line has no number to press.
  const numbers = async () => (await ui.findAll({ type: 'Button' })).filter(button => button.key?.startsWith('note:'))
    .map(button => button.props.label)
  expect(await numbers()).toEqual(['1', '2', '3', '4'])
  await ui.press({ key: 'note:4' })
  expect(await ui.find({ key: `text:${file}:4` })).toBeDefined()
  await ui.press({ key: 'note:4' })
  expect(await ui.find({ key: `text:${file}:4` })).toBeUndefined()
  await ui.press({ key: 'note:4' })
  await ui.input({ key: `text:${file}:4`, text: 'Is this line needed?' })
  expect(await ui.find({ key: `text:${file}:4` })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /Is this line/ }))?.text).toBe('src/a.txt:4 Is this line needed?')
  await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'composer' } })
  expect(context?.[0]).toContain(`${file}, line 4:\n\`\`\`diff\n+four\n\`\`\`\nComment: Is this line needed?`)

  // Cursor gets the old text under the file's own name, beside the file.
  await ui.press({ key: `diff:${file}` })
  expect(opened).toEqual([`--diff ${record}/before/k1/a.txt ${file}`])
  expect(disk.get(`${record}/before/k1/a.txt`)).toBe(base)

  await ui.press({ key: `undo:${file}#0` })
  expect(disk.get(file)).toBe(base)
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('0 of 1 files changed')
  await ui.unmount()
})

test('Keep moves the baseline to the file as it is', async ($, on) => {
  const disk = new Map<string, string>([
    [`${record}/files/k1.json`, JSON.stringify({ path: file, new: false })],
    [`${record}/base/k1`, base],
    [file, changed],
  ])
  on('env.get', () => ({ value: '/h' }))
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/w' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'edits' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('fs.list', (_, e) => ({
    value: [...disk.keys()].filter(p => p.startsWith(`${e.path}/`))
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
  }))
  on('fs.read', (_, e) => ({ value: disk.get(e.path) ?? '' }))
  on('fs.exists', (_, e) => ({ value: disk.has(e.path) }))
  let stamp = 0
  on('fs.stat', (_, e) => ({ value: { kind: 'file' as const, size: (disk.get(e.path) ?? '').length, mtimeMs: stamp, isLink: false } }))
  on('process.run', (_, e) => {
    const [command, ...args] = e.argv
    if (command === '/bin/cp') {
      disk.set(args[1]!, disk.get(args[0]!)!)
      stamp++
    }
    const same = command !== 'git' || disk.get(args.at(-2)!) === disk.get(args.at(-1)!)
    return { value: { exitCode: same ? 0 : 1, stdout: same ? '' : changedDiff, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })

  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  await ui.press({ key: `toggle:${file}` })
  await ui.press({ key: `keep:${file}` })
  expect(disk.get(`${record}/base/k1`)).toBe(changed)
  expect(disk.get(file)).toBe(changed)
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('0 of 1 files changed')
  await ui.unmount()
})

test('unchanged lines around a change show when asked for', async ($, on) => {
  // A form feed, which the engine reads as a line's end, in a line that did not change.
  const lines = Array.from({ length: 60 }, (_, i) => i === 39 ? 'l40\f' : `l${i + 1}`)
  const was = `${lines.join('\n')}\n`
  const now = `${lines.map(line => line === 'l30' ? 'L30' : line).join('\n')}\n`
  const hunk = '@@ -27,7 +27,7 @@\n l27\n l28\n l29\n-l30\n+L30\n l31\n l32\n l33'
  const disk = new Map<string, string>([
    [`${record}/files/k1.json`, JSON.stringify({ path: file, new: false })],
    [`${record}/base/k1`, was],
    [file, now],
  ])
  on('env.get', () => ({ value: '/h' }))
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/w' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'edits' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('fs.list', (_, e) => ({
    value: [...disk.keys()].filter(p => p.startsWith(`${e.path}/`))
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
  }))
  on('fs.read', (_, e) => ({ value: disk.get(e.path) ?? '' }))
  on('fs.exists', (_, e) => ({ value: disk.has(e.path) }))
  on('fs.stat', (_, e) => ({ value: { kind: 'file' as const, size: (disk.get(e.path) ?? '').length, mtimeMs: 0, isLink: false } }))
  on('process.run', () =>
    ({ value: { exitCode: 1, stdout: `--- a\n+++ b\n${hunk}\n`, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))

  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  await ui.press({ key: `toggle:${file}` })
  const gaps = async () => (await ui.findAll({ type: 'Text', text: /unchanged line/ })).map(gap => gap.text)
  expect(await rows(ui)).toEqual([
    '|27|l27', '|28|l28', '|29|l29', 'diffRemovedDimmed||l30', 'diffAddedDimmed|30|L30',
    '|31|l31', '|32|l32', '|33|l33',
  ])
  expect(await gaps()).toEqual(['26 unchanged lines', '27 unchanged lines'])
  expect(await ui.find({ key: `fold:${file}` })).toBeUndefined()

  await ui.press({ key: `up:${file}#0` })
  expect((await rows(ui)).slice(0, 2)).toEqual(['|7|l7', '|8|l8'])
  expect(await gaps()).toEqual(['6 unchanged lines', '27 unchanged lines'])
  expect(await ui.find({ key: `up:${file}#0` })).toBeUndefined()

  await ui.press({ key: `all:${file}#0` })
  expect((await rows(ui))[0]).toBe('|1|l1')
  expect(await gaps()).toEqual(['27 unchanged lines'])

  // The file's ends count as lines to go on from: down from its first, up from its last.
  await ui.press({ key: `fold:${file}` })
  expect(await rows(ui)).toHaveLength(8)
  await ui.press({ key: `down:${file}#0` })
  expect((await rows(ui)).slice(0, 2)).toEqual(['|1|l1', '|2|l2'])
  expect(await gaps()).toEqual(['6 unchanged lines', '27 unchanged lines'])
  await ui.press({ key: `up:${file}#1` })
  expect((await rows(ui)).at(-1)).toBe('|60|l60')
  expect(await gaps()).toEqual(['6 unchanged lines', '7 unchanged lines'])
  await ui.press({ key: `fold:${file}` })
  await ui.press({ key: `all:${file}#0` })

  await ui.press({ key: `down:${file}#1` })
  expect(await rows(ui)).toHaveLength(54)
  expect(await rows(ui)).toContain('|40|l40�')
  expect(await gaps()).toEqual(['7 unchanged lines'])

  await ui.press({ key: `all:${file}#1` })
  expect(await rows(ui)).toHaveLength(61)
  expect((await rows(ui)).at(-1)).toBe('|60|l60')
  expect(await gaps()).toEqual([])
  await ui.unmount()
})

test('a long change shows in parts, each within what the desktop draws', async ($, on) => {
  const long = Array.from({ length: 600 }, (_, i) => `line ${i + 1}`)
  const longDiff = `@@ -0,0 +1,600 @@\n${long.map(line => `+${line}`).join('\n')}\n`
  const others = Array.from({ length: 60 }, (_, i) => `/w/src/other${i}.txt`)
  const disk = new Map<string, string>([
    [`${record}/files/k1.json`, JSON.stringify({ path: file, new: true })],
    [file, `${long.join('\n')}\n`],
  ])
  others.forEach((path, i) => {
    disk.set(`${record}/files/o${i}.json`, JSON.stringify({ path, new: false }))
    disk.set(`${record}/base/o${i}`, 'a\n')
    disk.set(path, 'b\n')
  })
  on('env.get', () => ({ value: '/h' }))
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/w' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'edits' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('fs.list', (_, e) => ({
    value: [...disk.keys()].filter(p => p.startsWith(`${e.path}/`))
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
  }))
  on('fs.read', (_, e) => ({ value: disk.get(e.path) ?? '' }))
  on('fs.exists', (_, e) => ({ value: disk.has(e.path) }))
  on('fs.stat', (_, e) => ({ value: { kind: 'file' as const, size: (disk.get(e.path) ?? '').length, mtimeMs: 0, isLink: false } }))
  on('process.run', (_, e) => ({
    value: {
      exitCode: 1, stdout: `--- a\n+++ b\n${e.argv.at(-1) === file ? longDiff : '@@ -1 +1 @@\n-a\n+b\n'}`, stderr: '',
      isStdoutTruncated: false, isStderrTruncated: false,
    },
  }))

  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  await ui.press({ key: `toggle:${file}` })
  const part = async () => (await ui.find({ type: 'Text', text: /^Part \d+ of \d+$/ }))?.text
  const drawn: string[] = []
  let partCount = 1
  for (; ; partCount++) {
    drawn.push(...await rows(ui))
    expect(await nodes(ui)).toBeLessThan(2000)
    if (!await ui.find({ key: `later:${file}` })) break
    // The change's buttons stand after its last line.
    expect(await ui.find({ key: `comment:${file}#0` })).toBeUndefined()
    await ui.press({ key: `later:${file}` })
  }
  expect(partCount).toBeGreaterThan(2)
  expect(drawn).toHaveLength(600)
  expect(drawn[0]).toBe('diffAddedDimmed|1|line 1')
  expect(drawn.at(-1)).toBe('diffAddedDimmed|600|line 600')
  expect(await ui.find({ key: `comment:${file}#0` })).toBeDefined()
  expect(await part()).toBe(`Part ${partCount} of ${partCount}`)

  await ui.press({ key: `earlier:${file}` })
  expect(await part()).toBe(`Part ${partCount - 1} of ${partCount}`)
  await ui.unmount()
})

test("Claude's own files and the temporary ones stay folded until asked for", async ($, on) => {
  const own = '/h/.claude/skills/review/SKILL.md'
  const temporary = '/private/tmp/notes/a.txt'
  const disk = new Map<string, string>([file, own, temporary].flatMap((path, i) => [
    [`${record}/files/k${i}.json`, JSON.stringify({ path, new: false })],
    [`${record}/base/k${i}`, base],
    [path, changed],
  ] as [string, string][]))
  on('env.get', () => ({ value: '/h' }))
  on('session.id', () => ({ value: 's1' }))
  on('session.cwd', () => ({ value: '/w' }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', () => ({ value: { command: 'edits' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('fs.list', (_, e) => ({
    value: [...disk.keys()].filter(p => p.startsWith(`${e.path}/`))
      .map(p => ({ name: p.slice(e.path.length + 1), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
  }))
  on('fs.read', (_, e) => ({ value: disk.get(e.path) ?? '' }))
  on('fs.exists', (_, e) => ({ value: disk.has(e.path) }))
  on('fs.stat', (_, e) => ({ value: { kind: 'file' as const, size: (disk.get(e.path) ?? '').length, mtimeMs: 0, isLink: false } }))
  on('process.run', () =>
    ({ value: { exitCode: 1, stdout: `--- a\n+++ b\n${changedDiff}`, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))

  await $.session.start({ cwd: '/w', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...paneSite, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('1 of 1 files changed')
  expect(await ui.find({ key: `toggle:${file}` })).toBeDefined()
  expect(await ui.find({ key: `toggle:${own}` })).toBeUndefined()
  expect(await ui.find({ key: `toggle:${temporary}` })).toBeUndefined()
  expect((await ui.find({ key: 'others' }))?.text).toBe("▸ 2 other files: Claude's own and temporary")

  await ui.press({ key: 'others' })
  expect((await ui.find({ type: 'Text', text: /files changed/ }))?.text).toBe('1 of 1 files changed')
  expect(await ui.find({ key: `toggle:${temporary}` })).toBeDefined()
  await ui.press({ key: `toggle:${own}` })
  expect(await rows(ui)).toHaveLength(5)
  await ui.unmount()
})
