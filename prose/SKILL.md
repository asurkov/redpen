---
name: docdiff
description: "Show a repo's uncommitted Markdown changes (or changes since a given commit, or between two commits such as a PR) as one rendered page: each changed doc in full, additions and deletions marked inline, with next/prev navigation, opened in the built-in browser. ReSpec specs (an index.html with a respecConfig and its includes) are rendered and diffed too. Use when the user invokes /docdiff, asks to see doc changes or proposed wording rendered \"on the text\", wants an htmldiff-style view of .md edits, or wants the rendered diff of a spec PR (e.g. a WICG/webmonetization PR whose pr-preview Diff is empty), or how a Google Doc differs from its Markdown before a /gdocsync push."
argument-hint: "[repo path] [base revision] [head revision | PR] [paths…]"
---

# docdiff

Renders old = the file at a base revision (default `HEAD`) and new = the working tree (staged,
unstaged and untracked `.md` files) with pandoc, compares the two renders paragraph by paragraph (headings,
list items and tables count as paragraphs), and writes one page. Added paragraphs show as plain text with
a green bar; removed ones sit under a one-line summary with a red bar, open up to 200 words and folded
past that; a lightly edited one is word-diffed inline; consecutive added and removed paragraphs within a
section are one change, all its old text first, then all its new, each in one box with one bar,
paragraphs and list items together; only a change of one-to-one rewrites keeps each old paragraph above its new one. The page
opens with what is compared and its docs, each a link to it (with its change counts where there are
several), and both scroll away; under them, a sticky toolbar, one row where wide: the filter, ↑ prev / next
change ↓ buttons and `n`/`p` (or `j`/`k`) keys, "N of M to review" (with the doc where there are several),
the actions, and a view switch (`v` cycles): **Diff**, every change from base to new in one
view, each either still **to review** or accepted (✓), and **Old** / **New**, each version clean, for
rewrites too large to read inline; switching keeps the reader at the same place in the doc. In Diff, a
**filter** picks which changes `n`/`p` step through and the status counts: **All**, **To review** (the
default: the changes not accepted yet) or **Accepted** (what the user has signed off); beside it, **● N new
since <rev>** ([Review rounds](#review-rounds)), pressed, narrows the filter to its changes new since the
last review. It hides nothing, and a reload keeps both. `n`/`p` step from the
current change; with none, from where the reader last clicked
or selected, while that is on screen, and otherwise from the top of the screen. The page's first line says what is compared
(Uncommitted edits, Commits, GitHub PR or Google Docs vs Markdown, with the repo) and names both sides in
short, the base revision and `--head`'s with its commit's subject, or the working tree (`--new-label` names
it otherwise); its title has both in full.
Whitespace-only rewraps do not count as changes. Relative images are embedded. With `--head <rev>`, new
is the files at that revision instead of the working tree (a PR, say). A changed `.html` file under an
`index.html` that holds a `respecConfig` stands for that ReSpec spec, which shows as one doc
([ReSpec specs](#respec-specs)).

**Accept** (`a`, or the ✓ button; "accept file" on each file's bar) moves the current change into the
accepted copy, and the page reloads with it accepted, in place; on an accepted change, the same key (↺
unaccept; "unaccept file") takes it back out. Which changes are accepted is read off the accepted copy:
the changes of the copy to new are laid over the changes of base to new by position, new being their
common side, and a change with none of them on it is accepted; an accept moves the copy's changes at its
place, so a change keeps its key, and stays current, through accepts.
When the head or the base moves, accepted changes the move left alone stay accepted, and the ones it
touched come back with a blue edge, **new since last review** ([Review rounds](#review-rounds)).
Clicking a change makes it current and opens a small popup under it: ✓ (or ↺), ✎ revisit and the buttons
plugins add, such as 💬 comment ([Review comments](#review-comments)) and GitHub ↗ for a PR
([Plugins](#plugins)); selecting text pops up ✎ and 💬. **Revisit** (`r`, or the
button) takes the selected text, or the current change, and a request typed under it; Send (↵; ⇧↵ adds a line) copies a
ready prompt to the clipboard and queues the request ([Accepts and revisits](#accepts-and-revisits)).

**Reviewing commits** (`--head`: a PR, say), where there is nothing to edit or publish, the same actions
read as a review: accept is **✓ looks good** (`a`) and ↺ **not reviewed**, accepted is **reviewed**
("22 to review · 28 reviewed", the filter's **Reviewed**), and revisit is **✎ ask** (`r`), a question to
Claude about the change; 💬 comment (`c`) is the remark for the PR's author. On a change you commented on,
accept is **✓ reviewed** instead: it leaves To review without saying it looks good. With `--pr` and your
login known, a comment you wrote on GitHub counts as your 💬 does, here and in settled changes below
([GitHub PR](#github-pr)); other people's comments do not. Either one resolves the
answered threads on that change alone; ↺ not reviewed reopens those ([Review comments](#review-comments)).

Accepted (reviewed) changes show **settled**, each with its green ✓: the new text only, no strike-through
or highlight, with a thin neutral bar beside a block that changed (blue for one new since last review);
your commented wording on one has a dotted amber underline (the comment's quote, or the change's text when
the comment is on the whole change) and its 💬 beside it. A removed block stays its one-line "removed: …"
stub with its marks. Clicking one shows its diff again; **± diffs** beside the view switch, remembered per
viewer, shows them all. Changes to review always show their diffs. Each doc's line at the top counts the accepted changes that
carry comments or open questions ("· 3 with comments · 1 open question"), each count stepping through
them.

## Arguments

Read them from the invocation, in any order:

- a directory → the repo (default: the current working directory's repo). For the WM docs, that is
  `~/projects/webmonetization/docs` or `~/projects/webmonetization/wm-design-docs`.
- a revision (`HEAD~1`, a hash, a branch) → the base. Default `HEAD`. A second revision → `--head`.
- a PR (URL or number) → base and head from the PR ([Spec PRs](#spec-prs)).
- file paths → limit to those, as pathspecs relative to the repo root, shown in that order; a named file
  with no changes shows too, unchanged.

## Steps

1. Build and serve; the command returns once the page is built:

   ```bash
   python3 -I ~/.claude/skills/docdiff/docdiff.py --repo <repo> --base <rev> [--head <rev>] [--name <name>] --serve [paths…]
   ```

   It prints `<k> changed doc(s) … → <out>/index.html`, then `URL: http://127.0.0.1:7878/<name>/index.html`.
   Each review has a name: `--name`, or else the repo's name, with `--pr` the repo's name and the PR's
   number (`webmonetization-780`). Its page and state live in `~/.docdiff/<name>` (`<out>` below), out
   of reach of macOS's temp cleanup, so a rerun with the same name picks up its accepts, comments and
   revisits. `--out DIR` keeps them elsewhere, an older review's `$TMPDIR/docdiff-<repo name>` say:
   the review is then named after DIR, unless `--name` says otherwise, and `~/.docdiff/<name>` links
   to DIR. A name already taken by a review of another repo is refused: pass `--name`.
   [The umbrella server](#the-umbrella-server) serves every review under its name, so the URL stays
   the same from run to run. `--serve` starts it if it is not running, and otherwise leaves the new
   build to it, and an open page reloads into the new build. The server also notices on its own when
   the repo moves on (a commit, an edit, a new export: the revisions or the changed files' sizes and
   times differ from the build's) and rebuilds.
2. Read the `URL:` line. With `0 changed doc(s)` there is nothing to show: say so and stop.
3. Open the URL in the built-in browser (`navigate`, a new tab if the current one shows something else).
   Use http, never `file://`: a local file there renders as a static snapshot with no scripts, so the
   navigation does not work. Open it on `127.0.0.1` as printed, never `localhost`: specs render on
   `localhost`, where the server gives out a spec's sources and nothing else, and it takes posts only
   from the `127.0.0.1` page.
4. Tell the user the files and change counts in one or two lines, and that `n`/`p` step through changes,
   `a` accepts one, `r` revisits a selection and `c` comments on it; with changes new since last review,
   how many, and how many of them are to review, and that `s` shows what changed in one since then.

## Accepts and revisits

The server writes both into the review's dir, `<out>`:

- `accepted/<file>.html` — the accepted copy: the file as the user has accepted it, rendered. It starts as
  the base; each accept moves one change in, each unaccept takes one out, and the server rebuilds the page.
  `<file>.base` records the base commit. When the head or the base moves (an edit, a push, a commit, a
  new export), the copy is carried over by position ([Review rounds](#review-rounds)): accepted changes
  the move left alone stay, the ones it touched come back to review whole, and the copy never keeps text
  the new base dropped. Beside it, its round: `<file>.round.json` (`seen`, the head the copy goes with,
  and `last`, the version last reviewed, each `{id, time}`, `id` a commit or `tree:<hash>` for the working
  tree) and those versions rendered, `<file>.seen.html` and `<file>.last.html`. Before a new round rewrites
  them, they go to `<out>/backup/<time>/accepted/` (the last 20 kept). A copy from before rounds, with no
  `seen` yet, gets one on the next build, unchanged; on a base move it carries over by its changes' words
  around them once more. (`accepted.json`, the older store of accepted change keys, seeds the copy once
  with the changes that still match.)
- `revisit.jsonl` — one request per line: `time`, `file`, `section`, `quote`, `view`, `prompt`, `key` (the
  change's, for a revisit of a change) and `text` (the prompt as copied to the clipboard). One asked from a
  thread ([Review comments](#review-comments)) also has `thread`, its thread's first revisit's time: a
  follow-up (Ask more) quotes the thread's text, a revision of a drafted comment also has `revises` (the
  draft's id) and quotes the draft. Older lines have neither. When the user pastes one or says they sent
  revisits, read the lines since the last one handled, apply each to the file, and rerun docdiff; with
  `--head` (commits, a PR) a revisit is a question (its prompt starts "Ask about"): answer it, editing
  nothing. Before answering one with `thread`, read the thread so far: the revisit.jsonl lines whose
  `time` or `thread` is that time (or whose quote is its quote), and their answers in answers.jsonl, and
  answer the new question in that light. A revision asks to rework the draft it quotes: answer with the
  new draft as a blockquote. One with `comment` (the id of a comment of the user's not posted, from ✎ Ask
  Claude on it) quotes that comment's text and asks about it ("shorter", "as a question"): edit nothing,
  and give a reworked comment as a blockquote, which its card offers to use in its place. One with `remote` (a thread from elsewhere, `<source>:<comment id>`, its
  text starting "Draft a reply to this … thread") asks for a reply to that Google Docs or GitHub thread,
  its quote the thread (the text it is on, then each comment): edit nothing, answer with the reply as a
  blockquote, which the thread's card offers to copy; a revision of it has `remote` too. Its `mode`
  says what the prompt is: `draft`, notes to write the reply from, or none (an empty prompt: draft it on your own judgment); `polish`, the user's own reply, to fix
  and tighten keeping their meaning and voice, the polished reply the first blockquote (the card marks its
  edits against what they wrote). With `weigh`, also say briefly, outside the blockquotes, whether you
  would answer differently on substance, and if so give that reply as one more blockquote. A source
  with a shared store (`store()`: gdoc_comments.py's is `<export>-replies/`, beside the export) keeps
  its reply requests there, not in the page's out dir, so every page showing its comments shares the
  drafts: such a line has `store`, and its answer goes to `<store>/answers.jsonl`. Watch the store's
  revisit.jsonl for them, besides each page's.
  Open each reply with the request as sent, since the chat does not show it: the section and the quote
  as a blockquote, then the prompt, e.g. `> **Terms** · "visits a monetized document."` and
  `> monetized document or monetized page?`.
- `answers.jsonl` — Claude's answers to revisits, one per line: `{"time": <the revisit's time>, "answer":
  <Markdown>}`. After answering a revisit in chat, append the answer here (the reply without the quoted
  request), and the page shows it under the request ([Review comments](#review-comments)); until then the
  request shows as pending. Put a comment drafted for the PR in the answer as a blockquote (`> …`, a blank
  line between two), as the page offers each blockquote as a comment to make:

  ```bash
  python3 -c 'import json,sys; print(json.dumps({"time": sys.argv[1], "answer": sys.stdin.read().strip()}, ensure_ascii=False))' "<time>" >> <out>/answers.jsonl <<'EOF'
  <the answer>
  EOF
  ```
- `review.json` — the user's review comments, which revisits they resolved or read, which comments on
  GitHub they read
  ([Review comments](#review-comments)), and what was pushed to the PR ([GitHub PR](#github-pr), Pushing). When the user says they left comments, read them there.

**Watching for revisits.** While the user reviews, keep one background Bash command (`run_in_background`)
waiting for the next request. It has no time limit, unlike Monitor, and it touches `<out>/watcher` every
second: on Send the page checks that file, says "sent, Claude is watching" while it is fresh, and
otherwise copies the prompt for the user to paste. The command exits with the new request; handle it,
append the answer to `answers.jsonl`, then start the next wait at once.

```bash
sh -c 'd=$HOME/.docdiff/<name>; f=$d/revisit.jsonl; touch "$f"; n=$(($(wc -l < "$f"))); while [ $(($(wc -l < "$f"))) -le $n ]; do touch "$d/watcher"; sleep 1; done; tail -n +$((n+1)) "$f"'
```

## Review rounds

GitHub's "changes since your last review", for every file of a review. The accepted copy goes with the
head it was made for, its round's **seen** (the head commit, or for the working tree a hash of its render).
When the head moves (a push, an edit) or the base does (main merged in), the server diffs seen against the
new head and lays that over the new base-to-head diff, both having the head as their new side: each
change the move left alone keeps its ✓ if it had one, found by where it is (its text traced back through
the text the move did not touch), not by the words around it, which an edit nearby or a moved base
changes; a change the move touched comes back to To review whole, and so does one that was still to
review.

**New since last review.** Each round also keeps **last**, the version last reviewed. A change that changed
since then is new: a blue edge on it (its block's bar, or a bar beside the paragraph or item that holds an
inline change), reviewed again or not. A change in several boxes (removed blocks, then the added ones) gets
it only on the boxes the update shares text with: a TODO removed before last, next to steps the author
rewrote since, keeps its red bar. Each filter's count has its new ones beside it in blue ("To review
9 ●6", "Reviewed 45 ●3"), and **● 6 new since 04041c3** counts the ones in the filter on: pressed, it has
`n`/`p` step through those alone ("2 of 6 new to review"), and it stays pressed across filters (All: every
new change). A new change's popup has **Δ since 04041c3** (`s`): a
card under it with the blocks of last-to-now that it holds, what changed in it since you reviewed it.

**When a round starts.** last moves up to seen when the head moves on, if by then every change that was
new had been reviewed (or there was no last yet); if one was still to review, last stays, and what the next
update changes adds to what is new. So "last reviewed" is the last version you had finished, before the
author pushed again, as on GitHub, where it is the commit your review was on; no button to remember, no
blue edge that vanishes the moment the last new change is reviewed, while you may still want to reread it,
and no update can hide one you have not reviewed yet.

**From a review done before.** `--reviewed-at <rev>` (on that run only; reruns do not repeat it) makes rev
the version each changed file was last reviewed at, for a review from before rounds, or one done on GitHub:
what changed since rev is new, and what is reviewed stays as it is; a round there was goes to
`<out>/backup` first. A spec's rev renders in the page first ("rendering 04041c3, last reviewed, with
ReSpec…"), and so does any version a round names that is not rendered yet.

## ReSpec specs

`plugins/respec.py`. A spec renders in the page itself, not in the script: the first load shows the
spec's bar with "rendering with ReSpec…" while each side loads in a hidden frame, and a few seconds later
the page reloads into the diff. So the page must be open in the built-in browser, with network access
(ReSpec, xref and specref load online). Renders are kept in `<out>/rendered/<sources id>.html`, by the hash of the spec's directory
on that side, so an unchanged side never renders twice. Before diffing, the frame drops the table of
contents, the index, section numbers and permalinks, and unwraps sections, so the diff pairs headings,
paragraphs and list items. The diff sees through an algorithm's wrapper (`div.algorithm`) the same way, so
each step is an item of its own, and so is the text that opens it when no paragraph holds that text
(it shows as one in Diff); notes, examples and issues stay one block each. On "not rendered: …" in the
bar, reload the page to retry.

The spec's own scripts run in the frame, served from `localhost` (another origin than the page on
`127.0.0.1`), so a PR cannot script the page or post accepts or revisits; what the frame sends back is
stripped of scripts and handlers before it enters the page.

A revisit on a spec names the spec's `index.html` and the section; find the included source file that
holds the quote (`grep -rl`) and edit that.

### Spec PRs

For a GitHub PR, run against a clone of its repo in `~/.docdiff/clones/<repo>`, the same clone for
every PR of that repo; with none there yet, make a blobless one:

```bash
clone=~/.docdiff/clones/<repo>
[ -d "$clone" ] || git clone -q --filter=blob:none --no-checkout <repo url> "$clone"
git -C "$clone" fetch -q origin main
git -C "$clone" fetch -q origin "pull/<N>/head"
head=$(git -C "$clone" rev-parse FETCH_HEAD)
base=$(git -C "$clone" merge-base origin/main "$head")
```

then step 1 with `--repo "$clone" --base $base --head $head --pr <owner>/<repo>#<N>`: the review is
named `<repo>-<N>`. Base is the merge base, as on GitHub, so changes that landed on main since do not
show as reverted. Pass hashes, not `FETCH_HEAD`: the server re-resolves both on every rebuild. `--pr` gives each change a button
to its line on GitHub, and `--me <login>` makes your comments there your review ([GitHub PR](#github-pr)).

## Google Doc vs Markdown

For all of a repo's docs at once, `gdocsync.py --repo <repo> browse` exports them and runs docdiff as
below, with `--docs` and every mapped doc named, so one in step shows too (with its open comments, out
of the header's list): a table above the diff lists every Markdown file, its name a link to the doc
below, with a link to its Google Doc, where it stands (committed or not, and against its Google Doc) with
links to its uncommitted edits on a second docdiff page and to what a push would change, and its open
comments (`plugins/gdoc_docs.py`). A link to a docdiff page ending `#file=<name>` opens it at that doc, and `&view=<view>` (`old`, `new`)
in that view, or `review`, `acc` or `diff` (the views before the filter) in Diff with the filter To
review, Accepted or All, and ● new off; the docs list's "uncommitted edits ↗" opens it with All (`diff`), so `n`/`p`
step through every uncommitted change whatever has been accepted.
For one doc, by hand:

What a `/gdocsync` push would change in a Google Doc, with the doc's open comments on it:

```bash
python3 -I ~/.claude/skills/gdocsync/gdocsync.py --repo <repo> export [file.md…]
```

writes `~/.gdocsync/<repo name>/<repo name>-google/`, a git repo whose tag `google` holds each Google
Doc's text and whose working tree holds its Markdown's. Then step 1 with `--repo <that dir> --base google
--comments <that dir>/comments.json --new-label "Markdown of <repo>, as exported"`. Old is the Google Doc
(the header shows when it was read, from the export's commit), new the Markdown, both as the plain text a
push compares: inline styles and link targets do not show, since a push sets them from the Markdown.

The Google Docs' open comments (`plugins/gdoc_comments.py`) show as GitHub's do on a PR
([GitHub PR](#github-pr), "The PR's comments on GitHub"), named On Google Docs: each thread a
💬 where its quote reads in the old text, its card the comment and its replies, what a
push does to its text ("a push deletes its text", "a push edits its text") and Open in Google Docs ↗;
in the Review panel, in document order, and one whose quote no longer reads there (the Google Doc was
edited after it was made) in its On Google Docs group. They are as fresh as the export. With
`--refresh CMD` (`browse` passes gdocsync's export), the panel's **↻ Google Docs** runs CMD in the
background ("Google Docs being read…"), and when the export lands the page reloads into the Google Docs'
new text and comments; a failed run's last line shows there instead. The file's bar says how many are on text a push changes, beside review.py's
"💬 N comments" link to each.

A revisit there names the export's file: apply it to the same file in the real repo, export again and
rerun docdiff.

**Publishing accepted changes.** Add `--publish` with gdocsync's push of the accepted copy, then its
export:

```bash
--publish "python3 -I ~/.claude/skills/gdocsync/gdocsync.py --repo <repo> push {file} --target {target} && python3 -I ~/.claude/skills/gdocsync/gdocsync.py --repo <repo> export {file}"
```

While any file has accepted changes, the header has "⇪ publish accepted (N)". It opens a panel at the
bottom that lists each such file and its count; on Publish the server publishes them in turn, the
button saying which one ("publishing 1 of 2…"): for each, it writes the accepted copy as Markdown
(`{target}`) and runs the command. The panel then shows what each printed, and Done reloads the page
with the published changes in the old side and the rest still to review. A push refused because the
Google Doc or the Markdown changed since the export publishes nothing for that file: export again,
rerun docdiff (accepts that still apply carry over), and publish again; a file already published has
nothing accepted left, so it is not published twice. The click is the user's
go: never press it on a doc other people read. Since gdocsync follows the committed Markdown, the
Google page shows committed changes only; an edit is reviewed and committed first (below).

**Committing accepted changes.** On a page of the working tree (no `--head`), `--commit` adds
"⇪ commit accepted (N)" to the header while some file has all its changes accepted. Its panel lists those
files and the ones still waiting on changes to review, with an editable message ("Update <files>"; ↵
commits); the server commits each listed file as it reads on disk (`git add` and `git commit -- <files>`,
so nothing else staged goes in), checking first that every change in it is accepted, then runs
`--after-commit CMD` in the background (gdocsync's export, so the Google page shows the commit). A file
with changes left is not committed: a partial file would need its Markdown rebuilt from the render,
which would reformat it. The click is the user's commit: never press it for them.

On an edit session's page (a worktree of its own, on its own branch), `--commit-into PATH` then
cherry-picks the commit onto the checkout at PATH (the repo's own), so the session's reviewed edits land
there; the reply says where it landed, or, when it does not apply there (a conflict, or uncommitted edits
in its way), that it stays on the session's branch, with git's reason, and PATH is left as it was.

## The umbrella server

One server on `127.0.0.1:7878` serves every review in `~/.docdiff`, each under `/<name>/`, found by its
`.run.json`. Its index, http://127.0.0.1:7878/, lists them, the last active first: each one's name,
linked to its page, what it compares (the PR, or the repo with its revisions), its counts (to review,
reviewed or accepted, then as the page's header counts them, drafts, on GitHub and open questions) and
when it was last active. `--serve` starts the server
in the background, apart from the command, so it outlives the command and the session that ran it; its
log is `~/.docdiff/server.log`. It answers on `127.0.0.1` and, for spec renders, on `localhost`, and to
no other name for the address.

The server runs docdiff's code as it is on disk: once `docdiff.py` or a plugin changes, the next request
loads it again (code that does not load goes to the log, and the code before it goes on). Only a change
to the server itself (`umbrella()`, `Umbrella`, `live()`) needs a restart:

```bash
python3 -I ~/.claude/skills/docdiff/docdiff.py --restart
```

`--stop` stops it, and `--port N` (with `--serve`, `--restart` or `--stop`) works with another server on
port N instead, a test's say, which serves the same reviews. `DOCDIFF_HOME=<dir>` keeps the reviews, the
clones and the server's log in `<dir>` instead of `~/.docdiff`, so a test server on another port neither
sees nor writes the reviews in use, and `DOCDIFF_GITHUB_API=<url>` sends its GitHub API calls, reads and
pushes, to a stand-in at url.

**Keeping it running.** `--install-agent` prints a LaunchAgent, `com.asurkov.docdiff`, which starts the
server at login and again whenever it stops, its PATH set to find pandoc and git, its output in the same
log. It is not installed until the user says so, as it changes their Mac for good; then:

```bash
python3 -I ~/.claude/skills/docdiff/docdiff.py --install-agent > ~/Library/LaunchAgents/com.asurkov.docdiff.plist
python3 -I ~/.claude/skills/docdiff/docdiff.py --stop
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.asurkov.docdiff.plist
```

Under it, `--restart` has launchd restart the server, and `--stop` amounts to a restart, as launchd
starts it again. To remove it: `launchctl bootout gui/$(id -u)/com.asurkov.docdiff`, then delete the
plist. launchd starts the server with none of the shell's environment, so a `GITHUB_TOKEN` set there does
not reach it: to push comments to a PR or resolve its threads from it, the plist needs `GITHUB_TOKEN` under
`EnvironmentVariables`, where the token then sits in plain text.

Servers from before the umbrella server, one per review on a port of its own, run their old code until
stopped; a review's dir from one of them comes over with `--out` (or, in `~/.docdiff` already, with its
name).

## Plugins

Source-specific features are plugins: the Python modules in `plugins/` next to `docdiff.py`, each loaded
on every run and used where it applies.

- `respec.py`: ReSpec specs ([ReSpec specs](#respec-specs)).
- `gdoc_comments.py`: `--comments`, a Google Doc's comments, shown as comments made elsewhere, on any
  page (the repo's own uncommitted edits too); it titles the page "Google Docs vs Markdown" only on base
  `google`
  ([Google Doc vs Markdown](#google-doc-vs-markdown)).
- `gdoc_docs.py`: `--docs`, the repo's docs and their Google Docs listed above the diff.
- `github_pr.py`: `--pr`, a link from each change to its line in a GitHub PR, and the PR's comments read
  from GitHub ([GitHub PR](#github-pr)).
- `review.py`: review comments, and revisits with Claude's answers, on the page
  ([Review comments](#review-comments)).

A plugin sees docdiff itself as `docdiff` (`git()`, `read()` and the like) and may define the hooks
below; `load_plugins()` in `docdiff.py` has their exact shapes. `run` is the run's arguments as
`<out>/.run.json` keeps them, `repo` and `out` as full paths.

- `add_args(parser)`: its command-line arguments, kept in `.run.json` with the rest, so the server's
  rebuilds see them.
- `name(run)`: the review's name when neither `--name` nor `--out` gives one: `github_pr.py`'s is the
  repo's name and the PR's number.
- `counts(run)`: words for the review's line in the index of reviews: `review.py`'s count by who sees
  them, as the page's header does: your comments not posted (drafts where comments get posted), the open
  threads on GitHub (or in the Google Docs) with a place in the doc and your comments posted there, from
  the copy the page read last, and the open questions (or revisits).
- `PATHS` and `doc(…)`: the files it makes docs of beside `*.md`, and the doc a changed one stands for
  (a spec's `index.html` for a section file).
- `htmls(…)`: its docs rendered, both sides, or while they are not, what the file's section shows instead.
- `section(run, name)`: words for a file's bar and data for the page.
- `store(run)`, with `SOURCE`: a dir where replies drafted for this source's comments are kept,
  shared by every page that shows them (gdoc_comments.py's); without one they stay in the page's out dir.
- `title(run)`: what the page compares, for the header's first line, with a link or none: `github_pr.py`'s
  names the PR, `gdoc_comments.py`'s a Google Doc against its Markdown.
- `top(run)`: html above the docs, such as `gdoc_docs.py`'s table; with it the page is served even when
  no doc changed.
- `actions(run)`: links in a change's popup, `[(label, key, href)]` per change, made from the file's diff
  as text: per block (paragraph, list item, heading…) a list of `(change key or None, old text, new
  text)`. A link opens `href` in a new tab, and so does `key` on the current change, so the key must not
  be one of docdiff's own (`n p j k a r v`).
- `place(run, old, new, side, before, after)`: for other plugins, where text of a file's block is in its
  source; `github_pr.py`'s gives review comments their file and line.
- `remarks(run, out, refresh)`: for other plugins, comments made on the docs elsewhere, which review
  comments show, one source per plugin: `{source, open, refresh, posting, push, resolve, fetched, error,
  me, comments}` (its name, its link's words, whether ↻ can ask it again, whether your 💬 may be posted
  there, whether `push()` sends them there, whether `resolve()` resolves its threads); a thread's first
  comment has `thread`, `resolved` and `resolved_by` where the source said. `github_pr.py`'s reads the PR's
  on GitHub, `gdoc_comments.py`'s the Google Docs' from the export.
- `push(run, cs, last, dry)`: for other plugins, sends your comments to the source as your pending review,
  or with `dry` only says what it would do; `github_pr.py`'s ([GitHub PR](#github-pr), Pushing).
- `resolve(run, out, thread, resolved)`: for other plugins, resolves or reopens a thread on the source, on
  the user's click; `github_pr.py`'s ([GitHub PR](#github-pr), Resolved).
- `STYLE`, `SCRIPT`: CSS and JavaScript for the page, after docdiff's own. An element of class `panel`
  holds off the reload after a rebuild while it shows, and clicks in it leave the doc alone; so do clicks
  in an element of class `ui` (a card or side panel), which does not hold off the reload. Every review's
  page shares the server's origin: the script names what it fetches relative to the page (`fetch('review')`,
  never `/review`), and its sessionStorage and localStorage keys through `sk(key)`, which makes them the
  review's own.
- `get(run, path, query)`, `post(out, path, req)`: the server's routes, path within the review
  (`/review`, not `/<name>/review`); the page rebuilds after a post, unless the path is in the plugin's
  `IN_PLACE`.

### GitHub PR

`plugins/github_pr.py`. With `--pr <PR URL | owner/repo#N | N>` (N alone takes the repo from the clone's
`origin` remote; needs `--head`), each change's popup gets **GitHub ↗** (`g`): the change in the PR's
Files changed view at its line, where review comments go:
`https://github.com/<owner>/<repo>/pull/<N>/files#diff-<sha256 of the path>R<line>`, the anchor GitHub's
own line links use. The line is the head file's line of the change's first added word, or, when the
change only removes, `L<line>` in the base file. GitHub highlights the line but does not scroll to it,
so the button names the file and line: **GitHub ↗ terms.html:23**, with "(old)" for a base line.

To find it, the change's block (up to 40 words on each side of it) is matched word by word against the
files the PR changes, for a spec everything it changes in the spec's directory: the file holding the
longest run of the block's words wins, and aligning the two puts the change's word on a line. Tags and
punctuation do not count, so ReSpec's rewrites (`[=term=]`, `{{IDL}}`, `|var|`) cost only the words they
change; a `title` attribute counts, as ReSpec shows a note's or an example's title as its caption. A
Markdown file's words take their lines from pandoc's source positions (`gfm+sourcepos`). A change with no
run of 4 words anywhere, text ReSpec makes such as the references, links to its file without a line, or to
the PR's files when a spec has several. `place()` finds other text of a block the same way, for review
comments.

### Review comments

`plugins/review.py`. Both popups, a change's and a selection's, get **💬** (`c`): a comment on that change
or that text, typed in a panel like the revisit one: what it is on (section and quote, muted), the text,
then **Delete** apart and in red (an existing comment only), **Cancel** (Esc) and **Save** (↵; ⇧↵ adds a
line). It is kept in
`<out>/review.json` with its file, view, change key (none on a selection), quote (its text as it reads),
the text of its block around it, section, body and time; with `--pr`, also `github`: its file and line in
the PR, found as its change's GitHub ↗ button finds them, and found again when the run's head moves.

The page has three signs: ✓ reviewed, the blue edge of a change new since last review ([Review
rounds](#review-rounds)), and 💬. Everything said at one place is one mark there: your comments, your
questions to Claude (revisit threads, below) and threads from elsewhere ([GitHub PR](#github-pr), [Google
Doc vs Markdown](#google-doc-vs-markdown)). Its look says who sees the most public thing open in it: a
**solid** grey 💬 with a thread from elsewhere or a comment of yours posted there, which everyone there
reads; an **outlined amber** 💬 with a comment of yours not posted, a draft nobody there sees yet (nor in
your pending review, until you submit it); and a **grey ✎** with questions to Claude alone, which never
leave this page. It counts the comments it holds, each of yours, each open question and each comment of a
thread from elsewhere (none written for one), and its state shows on it: dashed (with …) while Claude has a
question or a reply to draft, filled green while an answer, a draft or a comment on GitHub is unread, GitHub's glyph and the
replies' count (`1↩`) with a comment of yours posted there, and a faint dot when all it holds is resolved.
Clicking it opens your one comment in its editor, or else a card of all it holds, each under its own
header, which starts with who sees it: **💬 On GitHub**, **💬 Draft · not posted** (**in your pending
review** once pushed, **edited since pushed** after that), or **✎ Asked Claude · private** (on your own
docs, where nothing is posted, **💬 Your comment**); then its state and actions: your comments (edit, copy,
delete), threads from elsewhere, then questions, the resolved ones last. The editor and the Review panel's
entries start the same way.

Each comment goes on its change in every view that has the change, and elsewhere where its quote
reads, the text before it picking among repeats: so it shows on the same text in Old or New. Where a view
has neither (removed words in New, added ones in Old), the mark
ends the paragraph or list item holding the text before it in its block (or else after it); only a view
without that block goes without the mark. When no view has its text any more (it was rewritten since),
the mark sits on its section's heading. A quote that does not read as it is is matched with spaces
ignored, as a selection across paragraphs keeps no space between them. Marks never count as changes or
take n/p. A mark on removed words sits after them, so a settled change shows it. The header's **💬** opens
the Review panel, and beside it what was said is counted by who sees it, with how many of each are unread:
"2 drafts · 6 on GitHub (3 unread) · 5 questions (4 unread) · 1 pending". On GitHub counts what has a place
in the doc, and unread there is a thread with a comment or a drafted reply you have not read ([GitHub PR](#github-pr),
Unread); a question is unread while its answer is; pending is what waits for Claude, drafted replies to
threads from elsewhere too. Each count, unread ones too, steps through its own marks in the doc's order, as
the counts of reviewed changes with comments or open questions step through those ("2 of 4 unread questions
· Monetization status"), and stays armed, underlined with an `n p` chip: `n` and `p` (`j` and `k`) then step
through its marks instead of the changes, until Esc (with no card open) or another of the header's controls,
such as next change, hands them back. The Review panel: the comments and revisits in the order they
come in the doc, each a click from its place, comments with edit, copy and delete. Copy puts one comment
on the clipboard as Markdown: its `path:line` (linked to the line on GitHub; without `--pr`, the file and
section), the quote and the comment, to paste into GitHub. **Copy drafts (N)**, shown while you have
comments not posted yet (**Copy comments (N)** on your own docs), does that for all of those, whatever the
panel's audience filter, to post by hand while ⇪ Push cannot (no token), leaving out the posted ones ([GitHub PR](#github-pr),
Posted), which pasted again would be posted twice, and the ones that read much like a comment of yours
there ("copied 2 · left out 2 already on GitHub in other words"); with a token, **⇪ Push** sends them to the PR as your
pending review (Pushing). Saving a comment shows it in place, with no
rebuild; comments outlast rebuilds, accepts and server restarts.

Revisits show the same way, one thread per 💬 (✎ in its card): the revisits of one change, or of one quote,
each request with Claude's answers from `answers.jsonl` under it. Asking again on the same change or quote
adds to its thread. One on selected text keeps only its quote, so a view without it takes the text around
it from a view that has it. A thread is **pending** (dashed) while its last request has no answer,
**new** (filled and bold, counted unread in the header) while an answer is unread, until its card is opened, and
**resolved** once the user says so: ✓ Resolve on its card or in the Review panel, or, on commits, by
accepting (✓ looks good or ✓ reviewed) the change it is on, which resolves its answered threads; a thread
whose last question has no answer yet stays open, and so does one on text outside a change or across
several. ↺ not reviewed reopens the threads its accept resolved, not ones resolved by hand. On your own
docs accepting resolves nothing. Asking again reopens a thread. A resolved thread counts no more in its 💬,
and alone it folds to a faint dot in the text, which opens its card to ↺ Reopen, and in the panel into a folded Resolved group under the open
ones (Open / Resolved / All filter, and beside it who sees them, **GitHub · Drafts · Claude**, one at a
time, pressed again for all; the header's 💬 opens the panel on Open and all, and a reload keeps both). A thread's card keeps its header, the state with ✎ Ask more and ✓
Resolve (↺ Reopen), at its top while a long thread scrolls under it; in the Review panel each entry has
them on its first line, and a long entry folds to a few lines with Show more. On commits, a card whose
thread is on one change still in To review leads with that change's accept, worded as the toolbar's (✓
looks good, or ✓ reviewed on a commented change; `a` while the card is open, and `r` asks more there) and doing the same: the
change is reviewed and its answered threads resolve, this one with them unless its last question has no
answer yet, as the button's title then says. ✓ Resolve is the smaller choice there, done asking but not
done reviewing: it resolves this thread alone and leaves the change in To review. A card on unchanged
text, across several changes or on a reviewed change has only Resolve or Reopen. **Ask more** (or `r`
while the card is open) opens the revisit panel on the thread's own text, saying which thread it goes on
from: the question joins that thread whatever text is selected, and reopens it if it was resolved.

Each drafted comment in an answer (a blockquote) is a **proposal**, shown once per thread however many
answers repeat it, with its actions under it. **💬 Use** makes it a comment on the thread's change or
quote and opens that to edit (edits go in the comment, never in the proposal); the proposal then shows ✓
used with 💬 Open comment. **✎ Revise** opens the revisit panel on the draft, in its thread ("shorter",
"as a question"); the draft in that revision's answer replaces it, which folds to a summary, still
readable. **Dismiss** folds it to a stub. **↶ Undo** takes back Use (deleting the comment, after asking
if it was edited) or Dismiss. An answer with several proposed drafts also offers 💬 Use all as one
comment. Using a draft resolves nothing: once every draft in a thread is used, dismissed or replaced, its
header says "drafts handled" and urges Resolve, where the card has no change to accept.

A comment of yours not posted has **✎ Ask Claude** in its card and panel entry: the revisit panel on the
comment's text, for "shorter", "as a question" or anything else about it. What you ask about it, and
Claude's answers, show under the comment as **✎ Asked Claude · private** (not as a question of its own),
and its mark and the drafts count say when one waits or an answer is unread; a comment with asks opens in
its card rather than its editor. A reworked draft in an answer offers **Use**, which puts it in place of
the comment's text (✓ used), and **↶ Undo**, which brings that text back; ✎ Revise and Dismiss work as
for any proposal.

`review.json` keeps each revisit's state by its time, `revisits: {<time>: {resolved, resolved_by, seen}}`,
`resolved_by` `accept` when its change's accept resolved it, `seen` how many of its answers were read, and each proposal's by the time of the revisit that first proposed it and
the draft's hash, `proposals: {"<time>#<hash>": {state: used | dismissed, comment}}`; deleting the comment
frees the draft again. `revisit.jsonl` and `answers.jsonl` are only ever appended to.

The page asks the server every 2 seconds, so a request or an answer shows without a reload. A
per-review server started before `review.py` existed has no route for any of this (💬 greys out): stop
it and rerun docdiff with `--serve`.

**Comments made elsewhere**, on GitHub or in the Google Docs, are counted on each doc's bar: "💬 N
comments" opens the Review panel on that doc's entries alone ("Only <file> · show all" shows every doc's
again, as the header's 💬 does), and each entry goes to its place in the doc. Below 840px wide the panel docks at the
bottom, so the place it goes to stays in sight above it. The text each one is on is highlighted pale
yellow, as Google Docs does, and darker while its card is open or just after going to it (the CSS Custom
Highlight API, so the page's text is not wrapped in elements). One whose quote no longer reads anywhere
(the text was edited after the comment was made) sits, dashed, at the end of the paragraph that shares
the most of its words, half of them at least; its card says "its text has changed: here at the closest
match".

**Drafting a reply** to a thread from elsewhere: its card and its panel entry have **✎ Draft a reply**,
which opens a reply box inline, under the thread and its drafts (a folded panel entry unfolds, and the box
scrolls into view; pressed again, it closes it): one text box, then **☐ weigh in**, **Polish** and
**Draft**. Draft takes the text as notes for the reply, or with none has Claude draft on its own; Polish
takes it as your reply, to fix and tighten, and waits for text. ↵ presses the one used last
(highlighted), ⇧↵ adds a line, Esc closes the box; text being typed outlasts the page drawing again.
Weigh in has Claude also say whether it would answer differently, with a reply of its own if so. The
request names the thread (`remote`) and its mode and weigh, and goes to Claude as revisits do; the card
says for a few seconds whether Claude is watching (or that it was copied to paste), and lists each
request as Notes, Your reply or No notes. Claude's answer shows in the card under **✎ Drafted replies · private**,
each draft with **Copy** (to paste as the reply there; Open in Google Docs ↗ or on GitHub ↗ goes to
it), **✎ Revise** (a box under that draft) and **Dismiss**, as a draft for a comment of yours has Use. A
polished reply shows its edits against your text, struck and inserted; Copy takes it clean. While a
draft is pending the thread's mark is dashed, and a new one fills it until its card is opened; the
header counts them with the revisits. Drafts belong to the thread, not the page: for Google Docs
comments they are kept in one store beside the export, so the Google Docs page and the local edits page
show the same ones (whether one was read, or a draft dismissed, stays per page). Nothing is posted:
replying stays yours.

**The PR's comments on GitHub** (`github_pr.py`, with `--pr`; read only: only ⇪ Push and ✓ Resolve write there, below). The page
reads the PR's review comments, its own comments and its reviews' bodies from GitHub's API on load and
from ↻ GitHub in the Review panel, which says when they were read and, offline or rate limited, that it
shows the last copy. They are kept in `<out>/github.json` with each page's ETag; a page load asks again
only when the copy is over 5 minutes old, as without a token each ask costs 3 of GitHub's 60 requests an
hour (a 304 included). `GITHUB_TOKEN`, if set, is sent and never written down. `--pr-comments FILE`
reads them from a file instead (`{review, issue, reviews}` as the API lists them), offline or for a test.
`--me <login>` names you on GitHub; without it, `GITHUB_TOKEN`'s user (`GET /user`, once, its login kept
in `github.json`). With neither, every GitHub comment is someone else's.

A line comment is placed on the diff by its `path`, `side` and `line`, the inverse of a change's GitHub
button: the words of that source line, with the words around it, are matched against the diff's blocks,
and the block whose matching run passes through the line holds it, on the change there or in unchanged
text. Each thread (a comment and its replies) goes in the 💬 at its place, counting each of its comments,
in every view where its text reads, or else its block, as your comment does; in the card it is read only: author, time, the body as
Markdown (rendered as answers are), the replies, ✓ Resolve (below) and Open on GitHub ↗. Outdated comments, ones that
could not be placed, the PR's own comments and review bodies are listed in the Review panel under
On GitHub.

**Resolved.** Whether a thread is resolved on GitHub comes from GitHub's GraphQL API (`reviewThreads`),
read with the comments when `GITHUB_TOKEN` is set, as the REST API does not give it; GraphQL has no
ETags, so it is read as often as the comments are. A thread's card and its panel entry have **✓ Resolve**,
or **↺ Reopen** on a resolved one, which resolve or reopen it on GitHub (`resolveReviewThread`,
`unresolveReviewThread`), on that click alone, and show what GitHub answers. A thread resolved there,
whoever resolved it ("resolved by MichaelKohler"), goes as a resolved question does: its mark folds to the
faint dot, and the Review panel lists it under Resolved, not Open, nor under On GitHub; the doc bar's
count leaves it out. Its only action is ↺ Reopen: ✎ Draft a reply comes back once it is reopened.
Without a token a thread's state is not known and there is no ✓ Resolve; with one, ✓ Resolve shows once
the threads have been read, never greyed out, which would read as resolved. Nothing resolves here
alone: the state is always GitHub's.

**Posted.** A 💬 of yours whose text a GitHub comment has (spacing aside, or one holding the other) was
posted: GitHub holds the real copy. Its 💬 has GitHub's glyph (a ✓ on a mark only ever means reviewed)
and, when the thread there has replies, their count (`1↩`). Its part of the card shows it as GitHub's
threads show, read only: "On GitHub · your comment, posted 7 days
ago · 1 reply", the text it is on, GitHub's copy of it, the replies, ✎ Draft a reply, ✓ Resolve (with a token)
and Open on GitHub ↗; its Review panel entry is the same, with no copy, which is for pasting a comment not posted yet. Under them, **Local copy: edit · delete** act on the copy kept here alone:
GitHub's stays as it is, and a copy edited past matching is no longer posted. Such a copy, one that still
shares most of its words (60%) with a comment of yours on GitHub, says **on GitHub in other words ↗** in
its row, card and editor, linking to that comment, where you edit it if the new wording matters.

**Yours** (your login known): a thread you wrote in, first or as a reply, is your comment on its change,
as a 💬 is: accept there reads ✓ reviewed, the change counts in "with comments", and once it is settled
its wording has the dotted amber underline. Its 💬 is solid, as every thread there is (amber is a draft's), still read only with Open on GitHub ↗, and
the Review panel names you "(you)". A 💬 is posted only by a comment of yours, and the thread holding that
comment then shows as that 💬 alone, not counted twice.

**Unread** (your login known): a thread's comments by others newer than your last look at it are unread.
Its 💬 fills green, as an unread answer's does, its panel entry says "1 new", and the header counts it in
"(N unread)" after "on GitHub"; opening its card or its panel entry reads them, and the card marks each of
them **new** while it stays open. `review.json` keeps, per thread, the time of the newest comment read,
`threads: {"GitHub:<its first comment's id>": {seen}}`, from what the page has already read: no more asks
of GitHub. A thread never opened here has all its comments by others unread.

**Pushing** (`push()` in `github_pr.py`). Without `GITHUB_TOKEN` the page only reads GitHub: comments go
there by hand, through Copy drafts (N). With one, while some of your 💬 are not on GitHub, the Review panel has
**⇪ Push N as a pending review** (N the ones new or edited since pushed; with none, **⇪ Pending
review**). It sends them, in the doc's order, to the PR as your **pending review**, which GitHub shows to
you alone until you submit it there: submitting stays your click on GitHub. Its panel first reads your
pending review on GitHub and lists what a push would do: **Add**; **Update** (edited here since pushed);
**In the review text** (a comment without a line goes in the review's own text, with its file, section
and quote); **In your pending review already**; and **Left out**, each with why. One to add that reads
much like a comment of yours on GitHub says so: a copy reworded here after it was posted no longer counts
as posted, and would go again. **Push** (↵) does it, then says what went and links to the PR's Files
changed, where you submit it. Without a pending review, a push starts one: `POST
/repos/<owner>/<repo>/pulls/<N>/reviews` with `commit_id` the head and `comments: [{path, line, side,
body}]`, no `event`. With one, a new comment is added to it (GraphQL's `addPullRequestReviewThread`, as
REST adds comments only in the request that makes a review), an edited one is updated (`PATCH
…/pulls/comments/<id>`), and the review's text rewritten (`PUT …/reviews/<id>`). Each comment keeps what
it became there in `review.json`, `pushed: {review, id, body, time}`, and the review is `push: {review,
body, url, time}`, so pushing again updates instead of duplicating. Left out: a comment GitHub has the
text of (posted); one edited on GitHub since it was pushed, or the review's text edited there (left as
they read there); one in a review you submitted (edit it there); and new ones while your pending review
is on another commit than the page's head (submit or discard it first). Deleting a comment here leaves it
in the pending review. Your pending review and its comments never count as posted: nobody else sees them until you submit it,
so their 💬 stays a draft's, in your pending review. The token is the
server's `GITHUB_TOKEN`, one that may write the PR's comments (classic
`public_repo`, or fine-grained Pull requests: read and write); the launchd server has none unless its
plist gives it one ([The umbrella server](#the-umbrella-server)).

## Proposing wording with it

When proposing edits to docs, apply them to the real files (uncommitted), run docdiff, and let the user
review the rendered page; a rejected edit is a `git restore` of that file. Never commit or stage as part
of this skill.

## Limits

- Needs `pandoc` (`brew install pandoc`), and for specs the built-in browser.
- Links between docs and section anchors are not rewritten; the page is for reading changes, not
  for following links. A click on a link into the doc or to another doc does nothing, so it never
  scrolls away from the change being read. A link to the web, anywhere on the page (a Google Doc, a
  comment on GitHub), opens in the default browser: the server opens it (`/open`, http and https only,
  from the page alone), since the page may be in an embedded browser; failing that, in a new tab. A link
  to docdiff's own pages opens in a new tab, and a modifier key follows any link as usual.
- A deleted file shows struck through as a whole; a renamed file diffs against its old path.
- Accepting and revisiting need the page served by docdiff (`--serve`); two identical changes with the
  same words around them in one file share a key, so accepting one accepts both.
