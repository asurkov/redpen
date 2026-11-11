// The diff editor: one file's two texts inline, a bar of buttons under each change, and the comment
// button and box.

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

export function loaded() {
  return new Promise(resolve => {
    window.require.config({ paths: { vs: '/vs' } });
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
  // handlers: act(action, spans) for a change's button, comment({side, start, end, quote, text}).
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
    this.zoneIds = [];
    this.changes = [];
    this.editor.onDidUpdateDiff(() => this.drawBars());
    this.shown.onDidChangeCursorSelection(() => {
      clearTimeout(this.offerTimer);
      this.dropWidget('offer');
      this.offerTimer = setTimeout(() => this.offerComment(), 250);
    });
  }

  // Shows the two texts of the file at path. changeActions: [action, label] pairs for each change's
  // bar; sides: the names of the two texts, {left, right}; place: 'kept' to stay where the view is,
  // else the first change comes into view.
  show({ path, left, right, changeActions, canSplice, sides, comments, place }) {
    const scrollTop = this.shown.getScrollTop();
    const language = languageOf(path);
    const old = this.editor.getModel();
    this.dropWidget('offer');
    this.dropWidget('box');
    this.changeActions = canSplice ? changeActions : [];
    this.sides = sides;
    this.afterDiff = () => (place === 'kept' ? this.shown.setScrollTop(scrollTop) : this.editor.revealFirstDiff());
    this.editor.setModel({
      original: monaco.editor.createModel(left, language),
      modified: monaco.editor.createModel(right, language),
    });
    old?.original.dispose();
    old?.modified.dispose();
    if (place === 'kept') {
      this.shown.setScrollTop(scrollTop);
    }
    this.mark(comments);
  }

  // Whether the page's line count of the text matches the editor's, so a change's ranges mean the
  // same lines to the server.
  static splits(text) {
    const model = monaco.editor.createModel(text, 'plaintext');
    const isSame = model.getLineCount() === text.split('\n').length;
    model.dispose();
    return isSame;
  }

  go(direction) {
    this.editor.goToDiff(direction);
  }

  reveal(line) {
    this.shown.revealLineInCenter(line);
  }

  // Marks the lines the waiting comments are on, those on the text shown.
  mark(comments) {
    const lineCount = this.shown.getModel()?.getLineCount() || 0;
    this.marks.set(comments
      .filter(comment => comment.side === this.sides.right && comment.start >= 1 && comment.start <= lineCount)
      .map(comment => ({
        range: new monaco.Range(comment.start, 1, Math.min(comment.end, lineCount), 1),
        options: {
          isWholeLine: true,
          className: 'commentedLine',
          linesDecorationsClassName: 'commentedEdge',
          hoverMessage: { value: `Comment for Claude: ${comment.text}` },
          overviewRuler: { color: '#d9a520', position: monaco.editor.OverviewRulerLane.Center },
        },
      })));
  }

  drawBars() {
    this.changes = this.editor.getLineChanges() || [];
    this.shown.changeViewZones(accessor => {
      this.zoneIds.forEach(id => accessor.removeZone(id));
      this.zoneIds = this.changes.map(change => accessor.addZone({
        afterLineNumber: change.modifiedEndLineNumber || change.modifiedStartLineNumber,
        // Under the editor's own zone, the removed lines, when both stand after one line.
        ordinal: 10001,
        heightInPx: barHeight,
        domNode: this.bar(change),
        suppressMouseDown: true,
      }));
    });
    this.afterDiff?.();
    this.afterDiff = null;
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
    for (const [action, label] of this.changeActions) {
      button(bar, label, () => this.handlers.act(action, spans(change)));
    }
    button(bar, 'Comment', () => this.openBox(this.changeTarget(change)), 'quiet');
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

  offerComment() {
    const selection = this.shown.getSelection();
    if (!selection || selection.isEmpty() || this.widgets?.box) {
      return;
    }
    // A selection that ends at a line's start does not take that line.
    const end = selection.endColumn === 1 && selection.endLineNumber > selection.startLineNumber
      ? selection.endLineNumber - 1 : selection.endLineNumber;
    const target = {
      side: this.sides.right,
      start: selection.startLineNumber,
      end,
      quote: this.shown.getModel().getValueInRange(selection),
      anchor: selection.getPosition().lineNumber,
    };
    const node = document.createElement('div');
    node.className = 'commentWidget';
    button(node, 'Comment', () => this.openBox(target));
    this.addWidget('offer', node, { lineNumber: selection.getPosition().lineNumber, column: selection.getPosition().column }, true);
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
    foot.append('Goes with your next message', spring());
    const send = () => {
      if (input.value.trim()) {
        this.handlers.comment({ side: target.side, start: target.start, end: target.end, quote: target.quote, text: input.value });
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

function linesOf(model, start, end) {
  return model.getValueInRange(new monaco.Range(start, 1, end, model.getLineMaxColumn(end)));
}

function languageOf(path) {
  const name = path.slice(path.lastIndexOf('/') + 1);
  const ending = name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : '';
  const known = monaco.languages.getLanguages().find(language => language.extensions?.includes(`.${ending}`));
  return languageByName[name] || languageByEnding[ending] || known?.id || 'plaintext';
}
