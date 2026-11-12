// The diff editor: one file's two texts inline, a bar of buttons under each change, the comment
// button and box, and under a commented line its thread: the comments and Claude's answers.

const languageByEnding = {
  cc: 'cpp',
  gn: 'python',
  gni: 'python',
  h: 'cpp',
  json5: 'json',
  mm: 'objective-c',
  mojom: 'cpp',
};
const languageByName = { DEPS: 'python' };
const barHeight = 24;
// What is said of a comment of the user's nothing has followed yet.
const stateWords = { draft: 'not sent yet', sent: 'sent, Claude has not taken it yet', taken: 'Claude has it' };

export function loaded() {
  return new Promise(resolve => {
    window.require.config({ paths: { vs: new URL('../../vs', location.href).pathname } });
    window.require(['vs/editor/editor.main'], () => {
      const dark = matchMedia('(prefers-color-scheme: dark)');
      const themed = () => monaco.editor.setTheme(dark.matches ? 'vs-dark' : 'vs');
      dark.addEventListener('change', themed);
      themed();
      resolve();
    });
  });
}

// A change's line ranges in the two texts, counted from 0, the end line not included: what the
// server splices by.
export function spans(change) {
  const span = (start, end) => (end > 0 ? [start - 1, end] : [start, start]);
  const [leftStart, leftEnd] = span(change.originalStartLineNumber, change.originalEndLineNumber);
  const [rightStart, rightEnd] = span(change.modifiedStartLineNumber, change.modifiedEndLineNumber);
  return { leftStart, leftEnd, rightStart, rightEnd };
}

export class DiffView {
  // handlers: act(action, spans) for a change's button, comment({side, start, end, quote, text}),
  // uncomment(id) for a comment not sent yet, resolve(id) for a thread.
  constructor(element, handlers) {
    this.handlers = handlers;
    this.editor = monaco.editor.createDiffEditor(element, {
      automaticLayout: true,
      contextmenu: false,
      domReadOnly: true,
      folding: false,
      fontSize: 12,
      glyphMargin: false,
      hideUnchangedRegions: { enabled: false },
      ignoreTrimWhitespace: false,
      minimap: { enabled: false },
      originalEditable: false,
      readOnly: true,
      renderGutterMenu: false,
      renderIndicators: false,
      renderMarginRevertIcon: false,
      renderSideBySide: false,
      scrollBeyondLastLine: false,
      stickyScroll: { enabled: false },
      unusualLineTerminators: 'off',
    });
    this.shown = this.editor.getModifiedEditor();
    this.marks = this.shown.createDecorationsCollection();
    this.currentEdge = this.shown.createDecorationsCollection();
    this.zoneIds = [];
    // The threads' zones, what they were drawn from, and what follows their boxes' heights.
    this.threadZones = [];
    this.threadsDrawn = '';
    this.threadSizes = new ResizeObserver(entries => this.sizeThreads(entries));
    // The threads shown, and the reply being written in one of them: {thread, text}.
    this.threads = [];
    this.reply = null;
    // Whether Claude's command waits for comments now: a comment's words say where it goes.
    this.isWatched = false;
    this.bars = [];
    this.changes = [];
    // The current change, the one the keys act on: its place in changes, -1 with none.
    this.current = -1;
    this.editor.onDidUpdateDiff(() => this.drawBars());
    // The change the cursor is put into becomes the current one.
    this.shown.onDidChangeCursorPosition(() => {
      if (!this.afterDiff) {
        this.pickAtCursor();
      }
    });
    this.shown.onDidChangeCursorSelection(() => {
      clearTimeout(this.offerTimer);
      this.dropWidget('offer');
      this.offerTimer = setTimeout(() => this.offerComment(), 250);
    });
  }

  // Shows the two texts of the file at path. changeActions: [action, label, key] for each change's
  // bar, the key where a key carries the action out on the current change; sides: the names of the
  // two texts, {left, right}; place: 'kept' to stay where the view is, 'last' for the last change, a
  // line's number for that line, else the first change comes into view.
  show({ path, left, right, changeActions, canSplice, sides, threads, place }) {
    const was = { top: this.viewTop(), position: this.shown.getPosition(), hadCurrent: this.current >= 0 };
    const language = languageOf(path);
    const old = this.editor.getModel();
    this.dropWidget('offer');
    this.dropWidget('box');
    this.changeActions = canSplice ? changeActions : [];
    this.sides = sides;
    // No change is current until the editor has compared the two texts.
    this.current = -1;
    this.afterDiff = () => this.settle(place, was);
    this.editor.setModel({
      original: monaco.editor.createModel(left, language),
      modified: monaco.editor.createModel(right, language),
    });
    old?.original.dispose();
    old?.modified.dispose();
    if (place === 'kept') {
      this.scrollTo(was.top);
    }
    // The editor dropped the threads' zones with the texts it showed.
    this.threadsDrawn = '';
    this.reply = null;
    this.mark(threads);
  }

  // The view's place in the text: its first line and where that line's top is against the view's.
  // Unlike the scroll offset it holds while the bars and the removed lines above it come and go.
  viewTop() {
    const line = this.shown.getVisibleRanges()[0]?.startLineNumber || 1;
    return { line, offset: this.shown.getTopForLineNumber(line) - this.shown.getScrollTop() };
  }

  scrollTo({ line, offset }) {
    this.shown.setScrollTop(this.shown.getTopForLineNumber(line) - offset);
  }

  // Puts the view in its place once the changes are known; the change there becomes the current one.
  settle(place, was) {
    if (place === 'kept') {
      if (was.position) {
        this.shown.setPosition(was.position);
      }
      this.scrollTo(was.top);
      // The current change is gone, accepted say: the next one takes its place if it is in view,
      // or with none further the one before.
      const line = this.shown.getPosition().lineNumber;
      const next = this.changes.findIndex(change => lastLineOf(change) >= line);
      const near = this.changes[next >= 0 ? next : this.changes.length - 1];
      if (was.hadCurrent && near && !holds(near, line) && this.isInView(near)) {
        this.shown.setPosition({ lineNumber: firstLineOf(near), column: 1 });
      }
      this.pickAtCursor();
    } else if (place === 'last') {
      this.goTo(this.changes.length - 1);
    } else if (typeof place === 'number') {
      this.shown.setPosition({ lineNumber: place, column: 1 });
      this.shown.revealLineInCenter(place);
      this.pickAtCursor();
    } else {
      this.goTo(0);
    }
  }

  // Whether the page's line count of the text matches the editor's, so a change's ranges mean the
  // same lines to the server.
  static splits(text) {
    const model = monaco.editor.createModel(text, 'plaintext');
    const isSame = model.getLineCount() === text.split('\n').length;
    model.dispose();
    return isSame;
  }

  // Steps to the next or previous change: from the current one, or with none from the cursor. Says
  // whether there was one that way; while the editor compares the texts, that there was.
  go(direction) {
    if (this.afterDiff) {
      return true;
    }
    const line = this.shown.getPosition().lineNumber;
    let index;
    if (this.current >= 0) {
      index = this.current + (direction === 'next' ? 1 : -1);
    } else if (direction === 'next') {
      index = this.changes.findIndex(change => lastLineOf(change) >= line);
    } else {
      index = this.changes.findLastIndex(change => firstLineOf(change) < line);
    }
    if (!this.changes[index]) {
      return false;
    }
    this.goTo(index);
    return true;
  }

  // Makes the change at index the current one and brings it into view, the cursor on its first line.
  goTo(index) {
    const change = this.changes[index];
    if (change) {
      const first = firstLineOf(change);
      this.shown.setPosition({ lineNumber: first, column: 1 });
      // In view already with its bar and the lines next to it, it stays where it is; else it comes
      // near the top.
      this.shown.revealRangeNearTopIfOutsideViewport(new monaco.Range(Math.max(1, first - 1), 1, lastLineOf(change) + 1, 1));
    }
    this.pick(change ? index : -1);
  }

  // Draws the change at index as the current one, or none with -1; the cursor stays where it is.
  pick(index) {
    const change = this.changes[index];
    this.current = index;
    this.bars.forEach((bar, place) => bar.classList.toggle('current', place === index));
    this.currentEdge.set(change?.modifiedEndLineNumber > 0 ? [{
      range: new monaco.Range(change.modifiedStartLineNumber, 1, change.modifiedEndLineNumber, 1),
      options: { isWholeLine: true, linesDecorationsClassName: 'currentEdge' },
    }] : []);
  }

  pickAtCursor() {
    const line = this.shown.getPosition().lineNumber;
    this.pick(this.changes.findIndex(change => holds(change, line)));
  }

  isInView(change) {
    const line = firstLineOf(change);
    return this.shown.getVisibleRanges().some(range => range.startLineNumber <= line && line <= range.endLineNumber);
  }

  // Carries out the current change's action that has the key, if there is such a one.
  actByKey(key) {
    const change = this.changes[this.current];
    const [action] = this.changeActions.find(([, , actionKey]) => actionKey === key) || [];
    if (change && action) {
      this.handlers.act(action, spans(change));
    }
  }

  // Opens the comment box on the selected text, or else on the current change; says whether there
  // was either. A box that is open comes back into view and takes the keys again.
  startComment() {
    const box = this.widgets?.box;
    if (box) {
      this.shown.revealLineInCenterIfOutsideViewport(box.getPosition().position.lineNumber);
      focusLaidOut(box.getDomNode().querySelector('textarea'));
      return true;
    }
    const change = this.changes[this.current];
    const target = this.selectionTarget() || (change && this.changeTarget(change));
    if (target) {
      this.openBox(target);
    }
    return Boolean(target);
  }

  // Whether the keys go to the text shown, not to a field over it.
  hasTextFocus() {
    return this.shown.hasTextFocus();
  }

  // Shows the file's threads, those with a place in the text shown: their lines marked, and under
  // them what was said, in a zone. A poll that found nothing new leaves them alone.
  mark(threads) {
    const model = this.shown.getModel();
    const drawing = JSON.stringify([threads, this.isWatched, this.reply?.thread, this.currentThread, this.openable?.()]);
    if (!model || drawing === this.threadsDrawn) {
      return;
    }
    this.threadsDrawn = drawing;
    this.threads = threads;
    const placed = threads.map(thread => ({ thread, lines: this.placeOf(thread, model) })).filter(({ lines }) => lines);
    this.marks.set(placed.filter(({ lines }) => !lines.isRemoved).map(({ thread, lines }) => {
      const kind = thread.entries.at(-1).by === 'claude' ? 'answered' : 'commented';
      return {
        range: new monaco.Range(lines.start, 1, lines.end, 1),
        options: {
          isWholeLine: true,
          className: `${kind}Line`,
          linesDecorationsClassName: `${kind}Edge`,
          overviewRuler: { color: kind === 'answered' ? '#8a8a8a' : '#d9a520', position: monaco.editor.OverviewRulerLane.Center },
        },
      };
    }));
    this.threadSizes.disconnect();
    this.shown.changeViewZones(accessor => {
      this.threadZones.forEach(zone => accessor.removeZone(zone.id));
      this.threadZones = placed.map(({ thread, lines }) => {
        const zone = {
          afterLineNumber: lines.end,
          // Under the change's bar, when both stand after one line.
          ordinal: 10002,
          heightInPx: 34 + 36 * thread.entries.length,
          domNode: this.threadNode(thread, lines),
          suppressMouseDown: true,
        };
        zone.id = accessor.addZone(zone);
        return zone;
      });
    });
    this.threadZones.forEach(zone => this.threadSizes.observe(zone.domNode.firstChild));
    const input = this.threadZones.map(zone => zone.domNode.querySelector('textarea')).find(Boolean);
    if (input) {
      focusLaidOut(input);
    }
  }

  // The keys of the files the page can open: set by the page.
  openable() {
    return [];
  }

  // Opens the reply of a thread, or with nothing closes the one open; what was typed is kept while
  // the threads are drawn again around it.
  replyIn(thread) {
    this.reply = thread ? { thread, text: '' } : null;
    this.mark(this.threads);
  }

  // Gives each thread's zone the height its box has now.
  sizeThreads(entries) {
    this.shown.changeViewZones(accessor => {
      for (const { target } of entries) {
        const zone = this.threadZones.find(candidate => candidate.domNode.firstChild === target);
        const height = target.offsetHeight + 8;
        if (zone && height > 8 && zone.heightInPx !== height) {
          zone.heightInPx = height;
          accessor.layoutZone(zone.id);
        }
      }
    });
  }

  // Where a thread stands in the text shown: its lines there, moved with the first line it quotes
  // when the text changed above them; for removed lines, the line they stood after. Nothing for a
  // thread on another text.
  placeOf(thread, model) {
    const count = model.getLineCount();
    const within = line => Math.max(1, Math.min(line, count));
    if (thread.side !== this.sides.right) {
      const isHere = thread.anchorSide === this.sides.right && thread.anchor >= 1;
      return isHere ? { start: within(thread.anchor), end: within(thread.anchor), isRemoved: true } : null;
    }
    if (!(thread.start >= 1)) {
      return null;
    }
    const quoted = (thread.quote || '').split('\n').find(line => line.trim())?.trim();
    const has = line => line >= 1 && line <= count && model.getLineContent(line).includes(quoted);
    let moved = 0;
    if (quoted && !has(thread.start)) {
      for (let far = 1; far < count && !moved; far++) {
        moved = has(thread.start - far) ? -far : has(thread.start + far) ? far : 0;
      }
    }
    return { start: within(thread.start + moved), end: within(thread.end + moved) };
  }

  // A thread's zone: what was said, the oldest first, then Reply and Resolve.
  threadNode(thread, lines) {
    // As a change's bar: the box is a child of the zone's node, lifted over the text layer.
    const zone = document.createElement('div');
    zone.style.zIndex = '10';
    const box = zone.appendChild(document.createElement('div'));
    const lastAnswer = thread.entries.findLastIndex(entry => entry.by === 'claude');
    box.className = `thread${lastAnswer === thread.entries.length - 1 ? ' answered' : ''}${thread.id === this.currentThread ? ' current' : ''}`;
    // The editor must not take the thread's keys and clicks for its own.
    for (const name of ['keydown', 'keyup', 'keypress', 'mousedown', 'pointerdown']) {
      box.addEventListener(name, event => event.stopPropagation());
    }
    thread.entries.forEach((entry, index) => {
      const row = box.appendChild(document.createElement('div'));
      row.className = `entry ${entry.by} ${entry.state || ''}`;
      const head = row.appendChild(document.createElement('div'));
      head.className = 'head';
      const who = head.appendChild(document.createElement('b'));
      who.textContent = entry.by === 'claude' ? 'Claude' : 'You';
      const said = [new Date(entry.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })];
      if (entry.by === 'user' && index > lastAnswer) {
        said.push(entry.state === 'draft' && !this.isWatched ? 'goes with your next message' : stateWords[entry.state]);
      }
      head.append(said.join(' · '), spring());
      if (entry.state === 'draft') {
        button(head, '✕', () => this.handlers.uncomment(entry.id), 'quiet').title = 'Delete the comment';
      }
      row.appendChild(document.createElement('div')).textContent = entry.text;
      // The change Claude made in answer: a way to it, while the page lists its file.
      if (entry.change) {
        const { key, path, line } = entry.change;
        const name = `${path.slice(path.lastIndexOf('/') + 1)}${line ? `:${line}` : ''}`;
        const way = row.appendChild(document.createElement('div'));
        way.className = 'change';
        if (this.openable().includes(key)) {
          button(way, `Changed ${name} →`, () => this.handlers.open(entry.change), 'quiet').title = path;
        } else {
          way.textContent = `Changed ${name}`;
        }
      }
    });
    const foot = document.createElement('div');
    foot.className = 'foot';
    if (this.reply?.thread === thread.id) {
      const input = box.appendChild(document.createElement('textarea'));
      input.placeholder = 'Reply to Claude';
      input.value = this.reply.text;
      input.addEventListener('input', () => {
        this.reply.text = input.value;
      });
      const send = () => {
        if (input.value.trim()) {
          const { side, start, end, quote, anchor, anchorSide } = thread;
          this.handlers.comment({ side, start, end, quote, anchor, anchorSide, text: input.value, thread: thread.id });
          this.replyIn(null);
        }
      };
      input.addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          send();
        } else if (event.key === 'Escape') {
          this.replyIn(null);
        }
      });
      foot.append(this.isWatched ? 'Send now, at the bottom, hands it to Claude' : 'Goes with your next message', spring());
      button(foot, 'Cancel', () => this.replyIn(null), 'quiet');
      button(foot, 'Add reply', send, 'main');
    } else if (thread.entries.some(entry => entry.state !== 'draft')) {
      button(foot, 'Reply', () => this.replyIn(thread.id), 'quiet');
      button(foot, 'Resolve', () => this.handlers.resolve(thread.id), 'quiet').title = 'Take the thread off the page';
    }
    if (foot.childNodes.length) {
      box.appendChild(foot);
    }
    return zone;
  }

  drawBars() {
    const changes = this.editor.getLineChanges();
    this.changes = changes || [];
    const zones = this.changes.map(change => this.bar(change));
    this.bars = zones.map(zone => zone.firstChild);
    this.shown.changeViewZones(accessor => {
      this.zoneIds.forEach(id => accessor.removeZone(id));
      this.zoneIds = this.changes.map((change, index) => accessor.addZone({
        afterLineNumber: change.modifiedEndLineNumber || change.modifiedStartLineNumber,
        // Under the editor's own zone, the removed lines, when both stand after one line.
        ordinal: 10001,
        heightInPx: barHeight,
        domNode: zones[index],
        suppressMouseDown: true,
      }));
    });
    if (!changes) {
      // The editor has not compared the texts yet: it says so again once it has.
      return;
    }
    if (this.afterDiff) {
      this.afterDiff();
      this.afterDiff = null;
    } else {
      this.pick(Math.min(this.current, this.changes.length - 1));
    }
  }

  bar(change) {
    // The editor sets the zone node's own display, so the row of buttons is a child of it; the
    // z-index lifts it over the text layer, which else takes its clicks.
    const zone = document.createElement('div');
    zone.style.zIndex = '10';
    const bar = zone.appendChild(document.createElement('div'));
    bar.className = 'changeBar';
    bar.style.height = `${barHeight}px`;
    for (const name of ['mousedown', 'pointerdown']) {
      bar.addEventListener(name, event => event.stopPropagation());
    }
    for (const [action, label, key] of this.changeActions) {
      const made = button(bar, label, () => this.handlers.act(action, spans(change)));
      if (key) {
        made.dataset.key = key;
      }
    }
    button(bar, 'Comment', () => this.openBox(this.changeTarget(change)), 'quiet').dataset.key = 'r';
    return zone;
  }

  // What a comment on the whole change is on: its lines in the text shown, or, for removed lines
  // alone, in the other text.
  changeTarget(change) {
    if (change.modifiedEndLineNumber > 0) {
      const [start, end] = [change.modifiedStartLineNumber, change.modifiedEndLineNumber];
      return { side: this.sides.right, start, end, quote: linesOf(this.shown.getModel(), start, end), anchor: end };
    }
    const [start, end] = [change.originalStartLineNumber, change.originalEndLineNumber];
    const quote = linesOf(this.editor.getOriginalEditor().getModel(), start, end);
    return { side: this.sides.left, start, end, quote, anchor: Math.max(1, change.modifiedStartLineNumber), isRemoved: true };
  }

  // What a comment on the selected text is on; nothing with no text selected.
  selectionTarget() {
    const selection = this.shown.getSelection();
    if (!selection || selection.isEmpty()) {
      return null;
    }
    // A selection that ends at a line's start does not take that line.
    const end = selection.endColumn === 1 && selection.endLineNumber > selection.startLineNumber
      ? selection.endLineNumber - 1 : selection.endLineNumber;
    return {
      side: this.sides.right,
      start: selection.startLineNumber,
      end,
      quote: this.shown.getModel().getValueInRange(selection),
      anchor: selection.getPosition().lineNumber,
    };
  }

  offerComment() {
    const target = this.widgets?.box ? null : this.selectionTarget();
    if (!target) {
      return;
    }
    const node = document.createElement('div');
    node.className = 'commentWidget';
    button(node, 'Comment', () => this.openBox(target));
    const { lineNumber, column } = this.shown.getSelection().getPosition();
    this.addWidget('offer', node, { lineNumber, column }, true);
  }

  openBox(target) {
    this.dropWidget('offer');
    this.dropWidget('box');
    const node = document.createElement('div');
    node.className = 'commentWidget';
    const box = node.appendChild(document.createElement('div'));
    box.className = 'commentBox';
    const head = box.appendChild(document.createElement('div'));
    head.className = 'head';
    const lines = target.start === target.end ? `line ${target.start}` : `lines ${target.start}–${target.end}`;
    head.append(`Comment for Claude on ${target.isRemoved ? 'the removed ' : ''}${lines}`, spring());
    button(head, '✕', () => this.dropWidget('box'), 'quiet').title = 'Close';
    const input = box.appendChild(document.createElement('textarea'));
    input.placeholder = 'What should change here?';
    const foot = box.appendChild(document.createElement('div'));
    foot.className = 'foot';
    foot.append(this.isWatched ? 'Send now, at the bottom, hands it to Claude' : 'Goes with your next message', spring());
    const send = () => {
      if (input.value.trim()) {
        this.handlers.comment({
          side: target.side,
          start: target.start,
          end: target.end,
          quote: target.quote,
          text: input.value,
          // Removed lines are not in the text shown: the comment stands at the line they followed.
          ...(target.isRemoved ? { anchor: target.anchor, anchorSide: this.sides.right } : {}),
        });
        this.dropWidget('box');
      }
    };
    button(foot, 'Add comment', send, 'main');
    // The editor must not take the box's keys and clicks for its own.
    for (const name of ['keydown', 'keyup', 'keypress', 'mousedown', 'pointerdown', 'wheel']) {
      box.addEventListener(name, event => event.stopPropagation());
    }
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        send();
      } else if (event.key === 'Escape') {
        this.dropWidget('box');
      }
    });
    this.addWidget('box', node, { lineNumber: target.anchor, column: 1 }, false);
    focusLaidOut(input);
  }

  addWidget(name, node, position, suppressMouseDown) {
    const { ABOVE, BELOW } = monaco.editor.ContentWidgetPositionPreference;
    const widget = {
      suppressMouseDown,
      getId: () => `redpen.${name}`,
      getDomNode: () => node,
      getPosition: () => ({ position, preference: [BELOW, ABOVE] }),
    };
    this.widgets = { ...this.widgets, [name]: widget };
    this.shown.addContentWidget(widget);
  }

  dropWidget(name) {
    const widget = this.widgets?.[name];
    if (widget) {
      this.shown.removeContentWidget(widget);
      this.widgets[name] = null;
    }
  }
}

export function button(parent, label, act, kind) {
  const made = parent.appendChild(document.createElement('button'));
  made.textContent = label;
  made.className = kind || '';
  made.addEventListener('click', act);
  return made;
}

// The editor lays a widget out a frame or two after it is added, and until then its input cannot
// take the focus.
function focusLaidOut(input, triesLeft = 30) {
  input.focus();
  if (document.activeElement !== input && triesLeft > 0) {
    requestAnimationFrame(() => focusLaidOut(input, triesLeft - 1));
  }
}

function spring() {
  const made = document.createElement('span');
  made.className = 'spring';
  return made;
}

// A change's first and last line in the text shown; removed lines alone count as the line they
// stand after.
function firstLineOf(change) {
  return Math.max(1, change.modifiedStartLineNumber);
}

function lastLineOf(change) {
  return Math.max(firstLineOf(change), change.modifiedEndLineNumber);
}

function holds(change, line) {
  return firstLineOf(change) <= line && line <= lastLineOf(change);
}

function linesOf(model, start, end) {
  return model.getValueInRange(new monaco.Range(start, 1, end, model.getLineMaxColumn(end)));
}

function languageOf(path) {
  const name = path.slice(path.lastIndexOf('/') + 1);
  const ending = name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : '';
  const known = monaco.languages.getLanguages().find(language => language.extensions?.includes(`.${ending}`));
  return languageByName[name] || languageByEnding[ending] || known?.id || 'plaintext';
}
