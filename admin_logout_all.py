"""
admin_logout_all.py — Force logout every active session
========================================================
Usage:
    python admin_logout_all.py
    python admin_logout_all.py --url https://example.com
    python admin_logout_all.py --url http://localhost:8000

Reads your session cookie from config.env (ADMIN_SESSION_COOKIE)
or prompts you to paste it from the browser.
"""

import sys
import os
import argparse
import http.cookiejar
import urllib.request
import urllib.error
import json
import getpass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_URL = 'https://example.com'
COOKIE_NAME = 'dri_session'


def _load_env_cookie() -> str:
    """Try to read ADMIN_SESSION_COOKIE from config.env."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.env')
    if not os.path.isfile(env_path):
        return ''
    with open(env_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('ADMIN_SESSION_COOKIE='):
                return line.split('=', 1)[1].strip()
    return ''


def logout_all(base_url: str, session_token: str) -> dict:
    url = base_url.rstrip('/') + '/api/admin/logout-all'
    req = urllib.request.Request(
        url,
        method='POST',
        headers={
            'Cookie':       f'{COOKIE_NAME}={session_token}',
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get('detail', body)
        except Exception:
            detail = body
        raise SystemExit(f'[ERROR] HTTP {e.code}: {detail}')
    except urllib.error.URLError as e:
        raise SystemExit(f'[ERROR] Could not reach {url}: {e.reason}')


def session_info(base_url: str, session_token: str) -> dict:
    url = base_url.rstrip('/') + '/api/admin/session-info'
    req = urllib.request.Request(
        url,
        method='GET',
        headers={'Cookie': f'{COOKIE_NAME}={session_token}'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            detail = json.loads(body).get('detail', body)
        except Exception:
            detail = body
        raise SystemExit(f'[ERROR] HTTP {e.code}: {detail}')
    except urllib.error.URLError as e:
        raise SystemExit(f'[ERROR] Could not reach {url}: {e.reason}')


def main():
    parser = argparse.ArgumentParser(description='CGrep Admin — Force logout all sessions')
    parser.add_argument('--url', default=DEFAULT_URL, help=f'Server URL (default: {DEFAULT_URL})')
    parser.add_argument('--cookie', default='', help='Session cookie value (or set ADMIN_SESSION_COOKIE in config.env)')
    parser.add_argument('--info', action='store_true', help='Only show session info, do not logout')
    args = parser.parse_args()

    # Resolve session token
    token = args.cookie or _load_env_cookie()
    if not token:
        print('Paste your dri_session cookie value (from browser DevTools → Application → Cookies):')
        token = getpass.getpass('dri_session token: ').strip()
    if not token:
        raise SystemExit('[ERROR] No session token provided.')

    base_url = args.url

    # Show current session info first
    print(f'\n[*] Connecting to {base_url} ...')
    info = session_info(base_url, token)
    print(f'[*] Logged in as     : {info.get("username")}')
    print(f'[*] Session expires  : {info.get("session_expires_at")}')
    print(f'[*] Last logout-all  : {info.get("global_invalidated_at") or "never"}')
    print(f'[*] Session TTL      : {info.get("session_ttl_hours")} hours')

    if args.info:
        return

    print()
    confirm = input('Type YES to force logout ALL active sessions: ').strip()
    if confirm != 'YES':
        print('[!] Aborted.')
        return

    result = logout_all(base_url, token)
    print(f'\n[+] SUCCESS: {result.get("status")}')
    print(f'[+] All tokens issued before {result.get("invalidated_before")} are now invalid.')
    print('[+] Every user (including you) will be redirected to login on next request.')


if __name__ == '__main__':
    main()
