"""A doc's copy on the repo's git remote: where the remote has the file, the doc's bar offers the link to
it there (share()), and a Markdown doc copied from the bar has its links to such files point there. For
the working tree, "there" is the branch's upstream, or else the remote's default branch; for commits
(--head), the head itself, as a permalink, once a branch of the remote holds it. All of it is read off the
clone, which knows the remote as of its last fetch or push: nothing is asked of the network. GitHub,
GitLab and Bitbucket each address a file their own way; another host's way is not guessed.
"""
import pathlib, re, urllib.parse

# A host by a word of its name: what it is called, and its address of a file at a ref of a repo.
HOSTS = (('github', 'GitHub', '{}/blob/{}/{}'), ('gitlab', 'GitLab', '{}/-/blob/{}/{}'), ('bitbucket', 'Bitbucket', '{}/src/{}/{}'))
# A remote's URL, over ssh (git@host:owner/repo.git, ssh://git@host:22/owner/repo) or https: its host and
# the repo's path there.
URL = re.compile(r'(?:\w+://)?(?:[^@/]+@)?([^:/]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$')


def share(run, name, memo):
    """The link to the file on the remote, where the remote has it: an address of the doc, with the
    Markdown's own anchors, as these hosts render it (see docdiff's load_plugins). Its note says when
    the working tree's file reads otherwise than the one there."""
    if 'git_remote' not in memo:
        memo['git_remote'] = published(run)
    if not memo['git_remote']:
        return None
    place, base, shape, ref, at = memo['git_remote']
    repo = pathlib.Path(run['repo'])
    # ls-tree, not cat-file: a blobless clone (a PR's) has the trees, and must fetch no blob for this.
    if not docdiff.git(repo, 'ls-tree', ref, '--', name, check=False).stdout.strip():
        return None
    differs = not run.get('head') and docdiff.git(repo, 'diff', '--quiet', ref, '--', name, check=False).returncode == 1
    return [{'id': 'remote', 'order': 40, 'label': f'Link on {place}', 'short': 'link', 'address': place, 'anchors': True,
             'icon': docdiff.ICONS['github' if place == 'GitHub' else 'link'],
             'note': ' · '.join([urllib.parse.urlsplit(base).path[1:], at[:12] if run.get('head') else at,
                                 *(['differs from this copy'] if differs else [])]),
             'text': shape.format(base, urllib.parse.quote(at), urllib.parse.quote(name))}]


def published(run):
    """Where the remote has the run's files: (the host's name, the repo's address there, its address of a
    file, the clone's ref that holds what the remote has, that ref as an address names it); None with no
    remote, with nothing of the run's on it, or on a host whose addresses are not known."""
    repo = pathlib.Path(run['repo'])
    git = lambda *a: docdiff.git(repo, *a, check=False).stdout.decode().rstrip('\n')
    remotes = git('remote').split()
    if run.get('head'):
        # A commit is there once a branch of the remote holds it; the link is to that commit.
        ref = at = git('rev-parse', '--verify', '--quiet', f'{run["head"]}^{{commit}}')
        holds = git('for-each-ref', '--count=1', '--contains', ref, '--format=%(refname)', 'refs/remotes') if ref else ''
        remote = next((r for r in remotes if holds.startswith(f'refs/remotes/{r}/')), None)
    else:
        branch = git('symbolic-ref', '--quiet', 'HEAD')
        remote, at, ref = ((git('for-each-ref', '--format=%(upstream:remotename)\t%(upstream:remoteref)\t%(upstream)', branch)
                            if branch else '').split('\t') + ['', ''])[:3]
        at = at.removeprefix('refs/heads/')
        if remote not in remotes:
            # No upstream: the default branch of origin, or of the only remote.
            remote = 'origin' if 'origin' in remotes else remotes[0] if len(remotes) == 1 else None
            ref = remote and next(filter(None, (git('rev-parse', '--verify', '--quiet', '--symbolic-full-name', f'refs/remotes/{remote}/{b}')
                                                for b in ('HEAD', 'main', 'master'))), None)
            at = (ref or '').removeprefix(f'refs/remotes/{remote}/')
    m = URL.match(git('remote', 'get-url', remote)) if remote and ref else None
    # A host is a name with a dot: an ssh alias or a path says nothing of where the repo is.
    host = m and '.' in m.group(1) and next((h for h in HOSTS if h[0] in m.group(1).lower()), None)
    return (host[1], f'https://{m.group(1)}/{m.group(2)}', host[2], ref, at) if host else None
