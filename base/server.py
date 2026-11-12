#!/usr/bin/env python3
"""redpen's server: one process on one port for every part's reviews, with one index of them all.

Usage: server.py --serve [--port N]     the server itself, until stopped
       server.py --restart [--port N]   restart it
       server.py --stop [--port N]      stop it
       server.py --install-agent        print a LaunchAgent that keeps it running, to install by hand

A part's command starts the server when none runs (start()), apart from itself, so the server outlives
the command and the session that ran it. It answers on 127.0.0.1:7878: / is the index, /code/… goes to
code/review.py, and the rest to prose/docdiff.py, whose reviews keep the addresses they had, /<name>/….

A part answers through functions of its script:

  respond(h, method)   answers a request, h.path being the path within the part
  listed_reviews()     the part's section of the index: {title, note, rows}, a row being {name, href,
                       when} and, where the part has them, what, link, counts and where
  framed(path)         optional: whether a GET of path is also answered under the name localhost

The server runs a part's code as it is on disk: once the part's script, one of its plugins or another
module of the base changes, the next request loads it again (see live()). Only a change to this file
needs a restart.
"""
import argparse, html, http.server, importlib.util, json, os, pathlib, plistlib, shlex, shutil, signal
import subprocess, sys, threading, time, traceback, urllib.parse, urllib.request

root = pathlib.Path(__file__).resolve().parent.parent
# Fixed, so a review's address outlives restarts.
default_port = 7878
# What a part's script loads this file as (see shared() there).
module_name = 'redpen_base_server'
scripts = {'prose': root / 'prose/docdiff.py', 'code': root / 'code/review.py'}
# Where each part's requests are: prose's reviews keep the addresses they had.
mounts = {'prose': '', 'code': '/code'}
# The LaunchAgent and the log keep docdiff's names until the reviews' stores move: the agent installed
# so far is docdiff's, and it writes there.
agent_label = 'com.asurkov.docdiff'
log = pathlib.Path(os.environ.get('DOCDIFF_HOME') or pathlib.Path.home() / '.docdiff').expanduser() / 'server.log'

# Each part's code as last loaded: its sources and the module (see live()).
loaded = {}
loading = threading.Lock()

index_style = '''
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--link:#0969da}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;--link:#4493f8}}
body{background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0}
main{max-width:920px;margin:0 auto;padding:16px;overflow-wrap:anywhere} a{color:var(--link)}
h1{font-size:22px} h2{font-size:17px;margin:28px 0 0} .n{color:var(--muted);font-size:13px} p.n{margin:0}
.reviews{list-style:none;padding:0;margin:0} .reviews li{padding:12px 0;border-bottom:1px solid var(--line)}
.reviews .name{font-weight:600}
'''


def stamp():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def sources(part):
    """A part's script, its plugins and the base's other modules, each with its time: when these
    change, live() loads the code again."""
    script = scripts[part]
    shared = [f for f in sorted((root / 'base').glob('*.py')) if f.name != 'server.py']
    return [(str(f), f.stat().st_mtime_ns) for f in [script, *sorted((script.parent / 'plugins').glob('*.py')), *shared]]


def live(part):
    """A part's code as it is on disk now: its script as a module, loaded again once one of its
    sources() has changed, or one came or went, so new code runs without a restart. Code that does
    not load goes to the log, and the code before it goes on; None while there is none."""
    now = sources(part)
    with loading:
        was = loaded.get(part)
        if not was or was[0] != now:
            # The base's modules a part has loaded (see shared() there) are loaded again with it.
            for name in [name for name in sys.modules if name.startswith('redpen_base_') and name != module_name]:
                del sys.modules[name]
            spec = importlib.util.spec_from_file_location(f'redpen_{part}', now[0][0])
            module = importlib.util.module_from_spec(spec)
            # A script's plugins find it there.
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                traceback.print_exc()
                module = was and was[1]
            loaded[part] = (now, module)
        return loaded[part][1]


def parted(path):
    """The part a request's path is in, and the path within that part."""
    part = next((p for p, mount in mounts.items() if mount and (path + '/').startswith(mount + '/')), 'prose')
    return part, path[len(mounts[part]):] or '/'


def send(h, status, body=b'', kind='text/plain; charset=utf-8'):
    h.send_response(status)
    h.send_header('Content-Type', kind)
    h.send_header('Content-Length', str(len(body)))
    h.end_headers()
    if h.command != 'HEAD':
        h.wfile.write(body)


def index():
    """The index: each part's reviews as the part lists them (listed_reviews()), the last active
    first. A part that cannot be listed says so in its place, and the others show."""
    e, sections = html.escape, []
    for part, mount in mounts.items():
        try:
            listed = live(part).listed_reviews()
        except Exception as x:
            traceback.print_exc()
            listed = {'title': part, 'note': f'not readable: {x!r}', 'rows': []}
        rows = []
        for row in sorted(listed['rows'], key=lambda row: row['when'], reverse=True):
            what = e(row.get('what') or '')
            if row.get('link'):
                what = f'<a href="{e(row["link"])}" target="_blank" rel="noopener">{what}</a>'
            notes = [*row.get('counts', []), time.strftime('active %b %-d, %H:%M', time.localtime(row['when']))]
            rows.append(f'<li><a class="name" href="{e(mount[1:] + "/" if mount else "")}{e(row["href"])}">{e(row["name"])}</a>'
                        f'{" · " + what if what else ""}<div class="n">{e(" · ".join(notes))}</div>'
                        + (f'<div class="n">{e(row["where"])}</div>' if row.get('where') else '') + '</li>')
        sections.append(f'<h2>{e(listed["title"])}</h2><p class="n">{e(listed["note"])}</p>'
                        f'<ul class="reviews">{"".join(rows) or "<li class=n>None yet.</li>"}</ul>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>redpen · reviews</title>'
            f'<style>{index_style}</style></head><body><main><h1>Reviews</h1>{"".join(sections)}</main></body></html>')


def respond(h, method):
    """Answers a request: the index, or what the request's part answers. Only a page of this server
    gets an answer: under any other name for this address (a page elsewhere that points a name of its
    own at it, the port being fixed) the server answers nothing, and it takes a POST from its own
    pages alone. The one other name is localhost, for the GETs a part frames (see framed() in
    prose/docdiff.py)."""
    port, url = h.server.server_address[1], urllib.parse.urlsplit(h.path)
    part, path = parted(url.path)
    host = h.headers.get('Host')
    if host != f'127.0.0.1:{port}':
        framed = method != 'POST' and host == f'localhost:{port}' and getattr(live(part), 'framed', None)
        if not framed or not framed(path):
            return h.send_error(403)
    if method == 'POST' and h.headers.get('Origin') != f'http://127.0.0.1:{port}':
        return h.send_error(403)
    if url.path in ('/', '/index.html'):
        return send(h, 200, index().encode(), 'text/html; charset=utf-8')
    # Every page asks for one; none, rather than a 404 in the log each time.
    if url.path == '/favicon.ico':
        return send(h, 204, kind='image/x-icon')
    # What running() asks for.
    if url.path == '/.redpen.json':
        return send(h, 200, json.dumps({'name': 'redpen', 'pid': os.getpid(), 'port': port}).encode(), 'application/json')
    # A part has no index of its own.
    if mounts[part] and path == '/':
        h.send_response(302)
        h.send_header('Location', '/')
        h.send_header('Content-Length', '0')
        return h.end_headers()
    module = live(part)
    if not module:
        return h.send_error(500, f'{part} does not load: see the log')
    h.path = path + (f'?{url.query}' if url.query else '')
    module.respond(h, method)


class Handler(http.server.SimpleHTTPRequestHandler):
    """The server's requests, each answered by respond(). A part may hand one on to
    SimpleHTTPRequestHandler's do_GET() or do_HEAD(), with path and directory set, for a file of a
    folder."""
    cached = False

    def send_header(self, keyword, value):
        self.cached |= keyword.lower() == 'cache-control'
        super().send_header(keyword, value)

    def end_headers(self):
        # Caching is off unless the answer sets it, so a reload after a rebuild shows the new page.
        if not self.cached:
            self.send_header('Cache-Control', 'no-store')
        self.cached = False
        super().end_headers()

    def do_GET(self):
        respond(self, 'GET')

    def do_HEAD(self):
        respond(self, 'HEAD')

    def do_POST(self):
        respond(self, 'POST')

    def log_request(self, code='-', size='-'):
        # Open pages ask every second or two: refusals and failures alone go to the log.
        if str(getattr(code, 'value', code))[:1] in '45':
            super().log_request(code, size)


def serve(port):
    """Serves on port until stopped. A port in use is waited for: the LaunchAgent's server, while one
    a command started still runs, takes over when that one stops."""
    # A part's script that loads this file gets this copy of it.
    sys.modules[module_name] = sys.modules[__name__]
    waiting = False
    while True:
        try:
            server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError as e:
            if not waiting:
                print(f'{stamp()} port {port}: {e.strerror}; waiting for it', flush=True)
            waiting = True
            time.sleep(5)
    # The parts' locks, kept here as a part's code comes and goes.
    server.locks = {}
    print(f'{stamp()} pid {os.getpid()} serves redpen on http://127.0.0.1:{port}/', flush=True)
    server.serve_forever()


def running(port):
    """The server on port, {name, pid, port}; None if none of ours answers."""
    try:
        got = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/.redpen.json', timeout=2).read())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) and got.get('name') == 'redpen' else None


def start(port):
    """Starts the server apart from this process, so it outlives the command and the shell that ran
    it, its output in the log; returns once it answers."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, 'a') as out:
        p = subprocess.Popen([sys.executable, '-I', os.path.abspath(__file__), '--serve', '--port', str(port)],
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        got = running(port)
        if got:
            # Another one came up first (the LaunchAgent's, another command's): this one is not needed.
            if got['pid'] != p.pid:
                p.terminate()
            return
        if p.poll() is not None:
            break
        time.sleep(0.1)
    p.terminate()
    sys.exit(f'no redpen server came up on port {port} (is something else on it?): see {log}')


def stop(port):
    """Stops the server on port, if one runs (under the LaunchAgent, launchd starts it again); returns
    what ran."""
    got = running(port)
    if got:
        os.kill(got['pid'], signal.SIGTERM)
        for _ in range(50):
            if (running(port) or {}).get('pid') != got['pid']:
                break
            time.sleep(0.1)
    return got


def restart(port):
    """Restarts the server: launchd does, under the LaunchAgent; else it is stopped and started.
    Needed only after a change to this file: the parts' code is loaded again on its own."""
    was = running(port)
    if port == default_port and not subprocess.run(['launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/{agent_label}'],
                                                   capture_output=True).returncode:
        for _ in range(100):
            if (running(port) or was or {}).get('pid') != (was or {}).get('pid'):
                return
            time.sleep(0.1)
        sys.exit(f'launchd restarted {agent_label}, and nothing new answers on port {port}: see {log}')
    stop(port)
    start(port)


def agent():
    """The LaunchAgent that keeps the server running, as a plist: started at login, started again when
    it stops, its output in the log. Its PATH has pandoc's and git's folders, as launchd's own has no
    Homebrew."""
    tools = [os.path.dirname(t) for t in map(shutil.which, ('pandoc', 'git')) if t]
    return plistlib.dumps({
        'Label': agent_label,
        'ProgramArguments': [shutil.which('python3') or sys.executable, '-I', os.path.abspath(__file__), '--serve'],
        'EnvironmentVariables': {'PATH': ':'.join(dict.fromkeys([*tools, '/usr/bin', '/bin', '/usr/sbin', '/sbin']))},
        'RunAtLoad': True, 'KeepAlive': True,
        'StandardOutPath': str(log), 'StandardErrorPath': str(log),
    }).decode()


def main():
    parser = argparse.ArgumentParser(description="redpen's server, for every part's reviews.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--serve', action='store_true', help='run the server here')
    action.add_argument('--restart', action='store_true')
    action.add_argument('--stop', action='store_true')
    action.add_argument('--install-agent', action='store_true',
                        help='print a LaunchAgent that keeps the server running, to install by hand')
    parser.add_argument('--port', type=int, default=default_port)
    args = parser.parse_args()
    if args.serve:
        return serve(args.port)
    if args.restart:
        restart(args.port)
        return print(f'restarted: http://127.0.0.1:{args.port}/')
    if args.stop:
        got = stop(args.port)
        return print(f'stopped pid {got["pid"]}' if got else f'no redpen server on port {args.port}')
    print(agent(), end='')
    plist, me = f'~/Library/LaunchAgents/{agent_label}.plist', f'python3 -I {shlex.quote(os.path.abspath(__file__))}'
    print(f'\nTo install: save it as {plist}, stop the server a command started, and load it:\n'
          f'  {me} --install-agent > {plist}\n'
          f'  {me} --stop\n'
          f'  launchctl bootstrap gui/$(id -u) {plist}\n'
          f'To remove: launchctl bootout gui/$(id -u)/{agent_label} && rm {plist}', file=sys.stderr)


if __name__ == '__main__':
    main()
