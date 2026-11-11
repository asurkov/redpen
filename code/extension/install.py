#!/usr/bin/env python3
"""Pack the Claude Edits extension beside this script into a .vsix and install it into Cursor.

Usage: install.py [--uninstall] [--profile NAME]

Cursor loads it on the next window reload. Each Cursor profile has its own extensions, so it goes into
the default profile and every other one; --profile names one profile instead. CURSOR names Cursor's
command-line tool (default: the one inside /Applications/Cursor.app).
"""
import argparse, json, os, pathlib, subprocess, sys, tempfile, zipfile

here = pathlib.Path(__file__).resolve().parent
cursor = os.environ.get('CURSOR', '/Applications/Cursor.app/Contents/Resources/app/bin/cursor')
manifest = json.loads((here / 'package.json').read_text())
storage = pathlib.Path.home() / 'Library/Application Support/Cursor/User/globalStorage/storage.json'
extension_id = f"{manifest['publisher']}.{manifest['name']}"

content_types = '''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json"/>
  <Default Extension=".js" ContentType="application/javascript"/>
  <Default Extension=".svg" ContentType="image/svg+xml"/>
  <Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>
'''

vsix_manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{manifest['name']}" Version="{manifest['version']}" Publisher="{manifest['publisher']}"/>
    <DisplayName>{manifest['displayName']}</DisplayName>
    <Description xml:space="preserve">{manifest['description']}</Description>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{manifest['engines']['vscode']}"/>
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
'''


def profiles():
    """Each Cursor profile's --profile arguments, the default profile's being none."""
    try:
        named = json.loads(storage.read_text()).get('userDataProfiles', [])
    except (OSError, ValueError):
        named = []
    return [[]] + [['--profile', p['name']] for p in named]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--uninstall', action='store_true')
    ap.add_argument('--profile')
    args = ap.parse_args()
    targets = [['--profile', args.profile]] if args.profile else profiles()
    if args.uninstall:
        sys.exit(max(subprocess.call([cursor, '--uninstall-extension', extension_id, *t]) for t in targets))
    files = ['package.json', 'extension.js', 'media/icon.svg']
    with tempfile.TemporaryDirectory() as tmp:
        vsix = pathlib.Path(tmp) / f"{manifest['name']}-{manifest['version']}.vsix"
        with zipfile.ZipFile(vsix, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', content_types)
            z.writestr('extension.vsixmanifest', vsix_manifest)
            for name in files:
                z.write(here / name, f'extension/{name}')
        status = max(subprocess.call([cursor, '--install-extension', str(vsix), '--force', *t]) for t in targets)
    if status == 0:
        print('Reload the Cursor window (Developer: Reload Window) to load it.')
    sys.exit(status)


if __name__ == '__main__':
    main()
