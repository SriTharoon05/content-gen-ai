"""Provision existing OAuth credentials without writing plaintext temporary files.

Default is names/presence only. --apply sends only these allowlisted credentials
to the configured Cloudflare Worker through Wrangler's stdin bulk interface.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = dotenv_values(root / 'backend' / '.env')
    names = ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'META_APP_ID', 'META_APP_SECRET', 'OAUTH_ENCRYPTION_KEY')
    values = {name: source[name] for name in names if source.get(name)}
    print(json.dumps({name: bool(source.get(name)) for name in names}))
    if not args.apply:
        return
    if not all(values.get(name) for name in names[:4]):
        raise SystemExit('Missing provider credentials; nothing was sent.')
    executable = shutil.which('npx.cmd') or shutil.which('npx')
    if not executable:
        raise SystemExit('npx is required.')
    result = subprocess.run([executable, 'wrangler', 'secret', 'bulk'], cwd=root / 'cloudflare',
                            input=json.dumps(values), text=True, encoding='utf-8', errors='replace', capture_output=True)
    # Never echo CLI diagnostics that could accidentally contain stdin.
    if result.returncode:
        raise SystemExit('Secret provisioning failed; check Wrangler authentication.')
    print('OAuth credentials provisioned. No secret values were printed or written to temporary files.')


if __name__ == '__main__':
    main()
