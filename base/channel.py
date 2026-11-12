#!/usr/bin/env python3
"""redpen's channel from a review page to Claude: the asks a page sends, each handed to Claude once.

Usage: channel.py --wait LOG [LOG ...]   print the logs' asks nobody was handed, or else wait for the
                                         next ones; Claude keeps it running in the background

A part keeps the asks of one review in a log, a .jsonl file with an ask per line: prose/'s
revisit.jsonl, code/'s sent.jsonl. An ask is what the part made it, with a time and an id from here.
Beside a log, in its folder, the channel keeps:

  handed.jsonl    one line per ask handed over: {"ask": its id, "time", "by"}
  watcher         the time, written every second by the command waiting on the log
  .channel.lock   locked while an ask is added or handed over

An ask is handed over once, and whoever does it marks it in handed.jsonl, by:

  wait        the waiting command printed it
  prompt      a part's hook sent it along with the user's next message
  shell       a command of the kind this one replaced was watching: it reads the lines added to the log
              and only touches the watcher (see watching())
  clipboard   nobody was watching, and the page copied it for the user to paste

A line with no id is from before the channel, and nobody's to hand over.
"""
import argparse, contextlib, fcntl, json, os, pathlib, sys, time

# A watcher older than this many seconds is nobody's: the command that wrote it has stopped.
fresh_seconds = 5


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def records(place):
    """A .jsonl file's records; a line that does not parse is skipped, and a missing file has none."""
    try:
        lines = place.read_bytes().splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            found.append(record)
    return found


def add_line(place, record):
    """Adds a record to a .jsonl file as one whole line in one write, so a reader of the lines (a
    shell's wc and tail too) never has half of one."""
    line = (json.dumps(record, ensure_ascii=False) + '\n').encode('utf-8', 'replace')
    fd = os.open(place, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


@contextlib.contextmanager
def locked(log):
    """Holds the lock of a log's folder, across processes: asks are added, marked and handed over under
    it. Taking it twice in one process waits forever, so waiting(), mark() and append() leave it to
    their caller."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log.with_name('.channel.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def waiting(log):
    """The log's asks nobody was handed, the oldest first."""
    handed = {line.get('ask') for line in records(log.with_name('handed.jsonl'))}
    return [ask for ask in records(log) if isinstance(ask.get('id'), str) and ask['id'] not in handed]


def mark(log, asks, by):
    """Records that the asks were handed over, and by what: they wait no more."""
    for ask in asks:
        add_line(log.with_name('handed.jsonl'), {'ask': ask['id'], 'time': stamp(), 'by': by})


def append(log, record):
    """Adds an ask to the log: the record with the time, unless it has one, and an id."""
    ask = {'time': stamp(), **record, 'id': f'{time.time_ns():x}'}
    add_line(log, ask)
    return ask


def watching(log):
    """What watches the log now: 'wait', this file's command, which takes the asks nobody was handed;
    'shell', a command of the kind it replaced, which touches the watcher and writes nothing in it;
    None, nobody."""
    watcher = log.with_name('watcher')
    try:
        if time.time() - watcher.stat().st_mtime >= fresh_seconds:
            return None
        written = float(watcher.read_text())
    except OSError:
        return None
    except ValueError:
        return 'shell'
    return 'wait' if time.time() - written < fresh_seconds else 'shell'


def beat(log):
    """Says that this command watches the log now: the time, put in the watcher by a rename, as a
    watcher read empty is taken for a touched one."""
    fresh = log.with_name(f'.watcher-{os.getpid()}')
    fresh.write_text(repr(time.time()))
    os.replace(fresh, log.with_name('watcher'))


def asked(log, record, unwatched=None):
    """Adds an ask to the log, for a page; returns it and what watches the log (see watching()). A
    waiting command takes it from there. With a shell's command watching, it is marked as that one's;
    with nobody, as handed over by `unwatched` when the page has such a way (the clipboard), and else
    it waits for whoever hands over next."""
    with locked(log):
        watcher = watching(log)
        ask = append(log, record)
        if watcher != 'wait' and (watcher or unwatched):
            mark(log, [ask], watcher or unwatched)
    return ask, watcher


def hand_over(log, by, tell):
    """Tells each ask nobody was handed, then marks them; returns how many. An ask told and not
    marked, as when the telling fails, is told again the next time."""
    if not log.is_file():
        return 0
    with locked(log):
        asks = waiting(log)
        for ask in asks:
            tell(ask)
        sys.stdout.flush()
        mark(log, asks, by)
    return len(asks)


def wait(logs, tell):
    """Hands over the logs' asks nobody was handed, or else waits for the next ones, and returns: what
    a command does that Claude keeps in the background and starts again each time it ends. While it
    waits, the logs' watchers say so."""
    sizes = {}
    while True:
        handed = 0
        for log in logs:
            beat(log)
            size = log.stat().st_size if log.is_file() else 0
            if sizes.get(log) != size:
                sizes[log] = size
                handed += hand_over(log, 'wait', tell)
        if handed:
            return
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Wait for the asks of review pages, and print them.")
    parser.add_argument('--wait', nargs='+', type=pathlib.Path, required=True, metavar='LOG')
    logs = [log.expanduser() for log in parser.parse_args().wait]
    for log in logs:
        if not log.parent.is_dir():
            sys.exit(f'No such folder: {log.parent}')
    wait(logs, lambda ask: print(json.dumps(ask, ensure_ascii=False)))


if __name__ == '__main__':
    main()
