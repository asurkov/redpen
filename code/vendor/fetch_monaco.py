#!/usr/bin/env python3
"""Fetch the Monaco editor the review page loads, into monaco/ beside this script.

Usage: fetch_monaco.py [--archive FILE]

Downloads the monaco-editor package of the version below from the npm registry, checks it against the
registry's SHA-512 of it, and keeps its minified build (min/vs) and its licence files. --archive reads
a package file already at hand instead of downloading one; it is checked the same.
"""
import argparse, base64, hashlib, io, pathlib, shutil, sys, tarfile, tempfile, urllib.request

version = '0.57.0'
# dist.integrity of https://registry.npmjs.org/monaco-editor/<version>, without its "sha512-".
sha512 = '5BkI9KGoqrNvBGUe15/QlZq3OooZ8WLg1AxTpaqHRCP3HNpzPPZKE2EDz8M7c+VRmCeUw1Brp4cx/PWm3kI/5A=='
url = f'https://registry.npmjs.org/monaco-editor/-/monaco-editor-{version}.tgz'
here = pathlib.Path(__file__).resolve().parent
target = here / 'monaco'
# What is kept of the package: its path there, and its path in monaco/. A folder ends in "/".
kept = {'package/LICENSE': 'LICENSE', 'package/ThirdPartyNotices.txt': 'ThirdPartyNotices.txt', 'package/min/vs/': 'vs/'}


def placed(name):
    """Where a package file goes in monaco/; None for a file not kept."""
    for source, made in kept.items():
        if name == source:
            return made
        if source.endswith('/') and name.startswith(source):
            rest = pathlib.PurePosixPath(name[len(source):])
            return None if '..' in rest.parts else made + str(rest)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--archive')
    args = parser.parse_args()
    data = pathlib.Path(args.archive).read_bytes() if args.archive else urllib.request.urlopen(url, timeout=120).read()
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != sha512:
        sys.exit(f'not monaco-editor {version} as the registry has it: the SHA-512 differs')
    with tempfile.TemporaryDirectory(dir=here, prefix='.monaco-') as folder:
        made = pathlib.Path(folder) / 'monaco'
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for member in archive:
                name = placed(member.name)
                if name and member.isfile():
                    (made / name).parent.mkdir(parents=True, exist_ok=True)
                    (made / name).write_bytes(archive.extractfile(member).read())
        (made / 'VERSION').write_text(version + '\n')
        if target.exists():
            shutil.rmtree(target)
        made.rename(target)
    print(f'monaco-editor {version} is in {target}')


if __name__ == '__main__':
    main()
