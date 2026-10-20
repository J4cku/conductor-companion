#!/usr/bin/python3
"""Phone bridge for local agent sessions.

Serves the phone web app and a small token-protected API: pair a device, list sessions (the menu bar plugin's
view of Conductor), read a session's transcript, toggle away mode, and reply. Replies are files in inbox/; the
~/.claude/hooks/phone-relay.sh hook delivers them into the session while away mode holds its turn open.

Files under ~/.claude/agent-status/phone/:
  config.json   {"lan": bool, "port": int}; the server restarts itself when it changes (launchd brings it back)
  pairing.json  one-time pairing offer written by the menu bar plugin: {"secret", "code", "expires"}
  devices.json  paired devices by sha256(token): {"name", "paired_at", "last_seen"}
  away          present = away mode on
  waiting/<sid>.json  written by the hook while it holds a turn or a question for the phone
  inbox/<sid>.json    a reply for the hook to deliver
  sent/<sid>.jsonl    what the phone sent and how it was delivered, merged into the transcript
  uploads/<sid>/      files the phone attached to a chat; the agent is told the path and opens it with its Read tool
  apns.json     push setup, absent = no push: {"keyId", "teamId", "keyPath", "bundleId", "environment"}
  push.json     push tokens by device digest and kind: {"<digest>": {"alert": {"token", "environment", "at"}, ...}}

A reply reaches a chat one of two ways. A held chat (waiting/) takes it from inbox/ through the hook. Any other chat
whose agent process is alive gets it as a Claude Code cross-session message: a throwaway headless Claude (Haiku,
SendMessage as its only tool, no hooks, same bypass mode as Conductor's chats) sends it, which wakes an idle chat.
"""
import base64
import binascii
import hashlib
import http.server
import importlib.util
import json
import os
import re
import secrets
import select
import socket
import socketserver
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = os.path.expanduser('~/.claude/agent-status')
PHONE = os.path.join(ROOT, 'phone')
CONFIG = os.path.join(PHONE, 'config.json')
PAIRING = os.path.join(PHONE, 'pairing.json')
DEVICES = os.path.join(PHONE, 'devices.json')
AWAY = os.path.join(PHONE, 'away')
WAITING = os.path.join(PHONE, 'waiting')
INBOX = os.path.join(PHONE, 'inbox')
SENT = os.path.join(PHONE, 'sent')
APP = os.path.join(PHONE, 'app.html')
SESSION_REGISTRY = os.path.expanduser('~/.claude/sessions')
CLAUDE_BINARIES = os.path.expanduser('~/Library/Application Support/com.conductor.app/agent-binaries/claude')
PLUGIN = os.path.join(ROOT, 'plugins', 'agent-status.py')
CONDUCTOR_DB = os.path.expanduser('~/Library/Application Support/com.conductor.app/conductor.db')
UPLOADS = os.path.join(PHONE, 'uploads')
APNS = os.path.join(PHONE, 'apns.json')
PUSH = os.path.join(PHONE, 'push.json')

TAILSCALE = '/Applications/Tailscale.app/Contents/MacOS/Tailscale'
QRENCODE = '/opt/homebrew/bin/qrencode'
GIT = '/usr/bin/git'
GH = '/opt/homebrew/bin/gh'
CURL = '/usr/bin/curl'
OPENSSL = '/usr/bin/openssl'
DEFAULT_PORT = 47823
MAX_PAIRING_FAILURES = 5
MAX_REPLY = 8000
MAX_BODY = 64 * 1024
MAX_ATTACHMENT = 20 * 1024 * 1024
MAX_PATCH = 200 * 1024
MAX_UNTRACKED = 500
TOOL_TIMEOUT = 10
ACCENTS = {'wc3': '#FFD200', 'dbz': '#FF9A1F', 'pkmn': '#FFCB05'}
ATTACHMENT_TYPES = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp', 'application/pdf': '.pdf',
                    'application/json': '.json', 'text/markdown': '.md', 'text/csv': '.csv', 'text/html': '.html'}  # and any other text/*
GIT_STATUS = {'A': 'added', 'M': 'modified', 'D': 'deleted', 'R': 'renamed', 'C': 'copied', 'T': 'modified', 'U': 'conflicted'}
PUSH_KINDS = ('alert', 'liveactivity', 'pushtostart')
AT_MAC_IDLE = 180  # seconds without keyboard or mouse input after which you count as away from the Mac
NOT_RUNNING = "This chat's agent isn't running (Conductor stops idle ones). Open it in Conductor once, then try again."

spec = importlib.util.spec_from_file_location('agent_status', PLUGIN)
agent_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_status)

lock = threading.Lock()
cache = {}
watching = {}  # device digest → open live streams: the app is on screen there, so it needs no notifications
pairing_failures = {'count': 0}
cost_lock = threading.Lock()
turn_costs = {}  # session id -> {'last': sent_at of the newest result row read, 'total': its total_cost_usd, 'steps': [(sent_at, usd)]}


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, value):
    tmp = f'{path}.{os.getpid()}'
    with open(tmp, 'w') as f:
        json.dump(value, f)
    os.replace(tmp, path)


def config():
    return {'lan': False, 'port': DEFAULT_PORT, **read_json(CONFIG, {})}


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def device_for(token):
    if not token:
        return None
    key = digest(token)
    with lock:
        devices = read_json(DEVICES, {})
        device = devices.get(key)
        if device and time.time() - device.get('last_seen', 0) > 60:
            device['last_seen'] = time.time()
            write_json(DEVICES, devices)
    return device


def pair(body):
    """Trade the one-time pairing secret (from the QR code) or the 6-digit code for a device token."""
    with lock:
        offer = read_json(PAIRING, None)
        if not offer or offer['expires'] < time.time():
            return 410, {'error': 'No pairing offer is open. Choose "Pair a phone" in the menu bar on your Mac.'}
        supplied_secret, supplied_code = body.get('secret', ''), str(body.get('code', '')).strip()
        if not (secrets.compare_digest(supplied_secret, offer['secret']) or (supplied_code and secrets.compare_digest(supplied_code, offer['code']))):
            pairing_failures['count'] += 1
            if pairing_failures['count'] >= MAX_PAIRING_FAILURES:
                os.remove(PAIRING)
                pairing_failures['count'] = 0
            return 403, {'error': 'That code does not match. Check the code in the menu bar.'}
        os.remove(PAIRING)
        pairing_failures['count'] = 0
        token = secrets.token_urlsafe(32)
        devices = read_json(DEVICES, {})
        devices[digest(token)] = {'name': str(body.get('device') or 'Phone')[:40], 'paired_at': time.time(), 'last_seen': time.time()}
        write_json(DEVICES, devices)
    return 200, {'token': token, 'computer': computer_name()}


def computer_name():
    def look():
        try:
            return subprocess.run(['/usr/sbin/scutil', '--get', 'ComputerName'], capture_output=True, text=True, timeout=2).stdout.strip() or socket.gethostname()
        except Exception:
            return socket.gethostname()
    return cached('computer', 60, look)


def cached(key, ttl, compute):
    hit = cache.get(key)
    if hit is None or time.time() - hit[0] > ttl:
        cache[key] = hit = (time.time(), compute())
    return hit[1]


def run(*args):
    import subprocess
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, env=tool_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ''


def tailscale_url():
    """https://<this mac>.<tailnet>.ts.net when Tailscale is up and serving this port, else None."""
    def look():
        try:
            status = json.loads(run(TAILSCALE, 'status', '--json') or '{}')
            serve = json.loads(run(TAILSCALE, 'serve', 'status', '--json') or '{}')
        except json.JSONDecodeError:
            return None
        host = (status.get('Self') or {}).get('DNSName', '').rstrip('.')
        proxied = f":{config()['port']}" in json.dumps(serve)
        return f'https://{host}' if status.get('BackendState') == 'Running' and host and proxied else None
    return cached('tailscale', 15, look)


def public_url():
    """The address the phone should use: Tailscale from anywhere, else this Wi-Fi, else only this Mac."""
    settings = config()
    if tailscale_url():
        return tailscale_url()
    if settings['lan']:
        ip = cached('lan_ip', 30, lambda: run('/usr/sbin/ipconfig', 'getifaddr', 'en0').strip())
        return f"http://{ip}:{settings['port']}"
    return f"http://127.0.0.1:{settings['port']}"


def pairing_page():
    offer, url = read_json(PAIRING, None), public_url()
    live = offer and offer['expires'] > time.time()
    reach = ('from anywhere over Tailscale (the phone needs Tailscale too)' if url.startswith('https://')
             else 'only on this Wi-Fi' if '127.0.0.1' not in url else 'nowhere yet: turn on Wi-Fi or Tailscale access in the menu')
    body = (f'<img src="/pair/qr.png?{int(offer["expires"])}" alt="QR code"><p class="code">{offer["code"]}</p>'
            f'<p>Scan with the iPhone camera, or open <b>{url}</b> and type the code.<br>Expires {time.strftime("%H:%M", time.localtime(offer["expires"]))}.</p>'
            if live else '<p class="code">Paired ✓</p><p>Or the offer expired. Choose “Pair a phone…” in the menu bar for a new one.</p>')
    return f'''<!doctype html><meta charset="utf-8"><title>Pair a phone</title>
<style>body{{font:16px -apple-system,sans-serif;background:#15161a;color:#e9eaee;text-align:center;padding:40px}}
img{{width:360px;height:360px;image-rendering:pixelated;background:#fff;padding:16px;border-radius:16px}}
.code{{font-size:40px;letter-spacing:.25em;font-weight:700;margin:18px 0 6px}} p{{color:#b9bcc6}} b{{color:#ffcb05}}</style>
<h1>Pair a phone with {computer_name()}</h1>{body}<p>Reachable {reach}.</p>
<script>{"setTimeout(()=>location.reload(),3000)" if live else ""}</script>'''


def waiting(sid):
    """The question the hook is holding for the phone, if its hook process is still alive."""
    path = os.path.join(WAITING, f'{os.path.basename(sid)}.json')
    held = read_json(path, None)
    if held is None:
        return None
    try:
        os.kill(int(held['pid']), 0)
    except (OSError, ValueError, KeyError):
        try:
            os.remove(path)  # the hook was killed (interrupt, timeout) before it could tidy up
        except FileNotFoundError:
            pass
        return None
    return held


def sessions():
    now = time.time()
    key = agent_status.theme_key()
    theme = agent_status.THEMES[key]
    listed = agent_status.load(now)
    spent = costs([s.id for s in listed], now)
    out = []
    for s in listed:
        quotes = theme['quotes'][s.state]
        out.append({
            'id': s.id, 'state': s.state, 'name': s.name, 'workspace': s.workspace, 'doing': s.doing,
            'unit': theme['unit'](s.rank, s.id), 'since': s.since, 'context': s.context, 'place': s.place,
            'quote': quotes[agent_status.zlib.crc32(f'{s.id}{s.state}'.encode()) % len(quotes)],
            'held': waiting(s.id), 'queued': os.path.exists(os.path.join(INBOX, f'{s.id}.json')),
            'cost': spent[s.id][0] if spent.get(s.id) else None,
        })
    day = time.strftime('%Y-%m-%d', time.localtime(now))
    return {
        'computer': computer_name(), 'away': os.path.exists(AWAY), 'now': now,
        'theme': {'name': theme['name'], 'labels': theme['labels'], 'icons': theme['icons'], 'frames': theme['frames'], 'accent': ACCENTS.get(key, '#FFD200')},
        'sessions': out,
        'today': {'cost': round(sum(c[1] for c in spent.values() if c), 4), 'turns': agent_status.tally(day, 'gold'), 'tools': agent_status.tally(day, 'lumber')},
    }


def local_midnight(now):
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def read_turn_costs(db, sid):
    """What each finished turn of a chat cost, reading only result rows newer than the last call.

    A result row's total_cost_usd is cumulative for the chat's Claude process and starts again from zero when Conductor
    restarts that process, so a drop opens a new segment whose first total counts in full.
    """
    seen = turn_costs.setdefault(sid, {'last': '', 'total': 0.0, 'steps': []})
    for at, total in db.execute("select sent_at, json_extract(content, '$.total_cost_usd') from session_messages "
                                "where session_id = ? and sent_at > ? and content like '{\"type\":\"result\"%' order by sent_at", (sid, seen['last'])):
        seen['last'] = at
        if total is None:
            continue
        total = float(total)
        seen['steps'].append((at, total - seen['total'] if total >= seen['total'] else total))
        seen['total'] = total
    return seen['steps']


def costs(ids, now):
    """(lifetime USD, USD since local midnight) by Conductor session id, for `ids` and every chat updated today.
    None for a chat without a finished Claude turn (Codex chats, sessions outside Conductor)."""
    midnight = local_midnight(now)
    since = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(midnight))
    out = {}
    with cost_lock:
        try:
            db = sqlite3.connect(f'file:{CONDUCTOR_DB}?mode=ro', uri=True, timeout=1)
            try:
                active = [sid for (sid,) in db.execute("select id from sessions where cast(strftime('%s', updated_at) as integer) >= ?", (int(midnight),))]
                for sid in set(ids) | set(active):
                    steps = read_turn_costs(db, sid)
                    out[sid] = (round(sum(usd for _, usd in steps), 4), sum(usd for at, usd in steps if at >= since)) if steps else None
            finally:
                db.close()
        except sqlite3.Error as error:
            sys.stderr.write(f'costs: {error}\n')
    return out


def conductor_query(sql, args=()):
    db = sqlite3.connect(f'file:{CONDUCTOR_DB}?mode=ro', uri=True, timeout=1)
    try:
        return db.execute(sql, args).fetchall()
    finally:
        db.close()


def workspace_title(name, named, pr_title, branch, directory, chat=None):
    return agent_status.display_name(chat, name, named, pr_title, directory)


def live_states():
    """The menu bar's view (it knows which sessions wait for you), by session id."""
    return {s.id: s.state for s in agent_status.load(time.time())}


def db_state(status, unread):
    if status in ('working', 'waiting'):
        return 'working'
    if status == 'error':
        return 'failed'
    return 'done' if unread else 'idle'


def workspaces():
    """Every non-archived workspace, grouped by repository in the sidebar's order."""
    live = live_states()
    chats = {}
    for sid, wid, status, unread, title, agent, model, updated in conductor_query(
            "select s.id, s.workspace_id, s.status, s.unread_count, s.title, s.agent_type, s.model, cast(strftime('%s', s.updated_at) as integer) "
            "from sessions s join workspaces w on w.id = s.workspace_id where w.state != 'archived' and s.is_hidden = 0"):
        chats.setdefault(wid, []).append({'id': sid, 'title': title, 'agent': agent, 'model': model, 'updated': updated or 0,
                                          'state': live.get(sid) or db_state(status, unread), 'held': waiting(sid)})
    repos, rank = {}, {'waiting': 0, 'failed': 1, 'working': 2, 'done': 3, 'quiet': 4, 'idle': 5}
    for rid, repo, order, wid, directory, name, named, pr_title, branch, derived, pinned, active, updated in conductor_query(
            "select r.id, r.name, coalesce(r.display_order, 0), w.id, w.directory_name, w.workspace_name, w.user_set_workspace_name, w.pr_title, w.branch, "
            "coalesce(nullif(w.manual_status, ''), w.derived_status), w.pinned_at is not null, w.active_session_id, cast(strftime('%s', w.updated_at) as integer) "
            "from workspaces w join repos r on r.id = w.repository_id where w.state != 'archived' and coalesce(r.hidden, 0) = 0"):
        sessions = sorted(chats.get(wid, []), key=lambda c: -c['updated'])
        top = min((c['state'] for c in sessions), key=rank.get, default='idle')
        entry = repos.setdefault(rid, {'id': rid, 'name': repo, 'order': order, 'workspaces': []})
        entry['workspaces'].append({
            'id': wid, 'title': workspace_title(name, named, pr_title, branch, directory, next((c['title'] for c in sessions if c['id'] == active), None)),
            'directory': directory, 'branch': branch,
            'status': derived or '', 'pinned': bool(pinned), 'state': top, 'held': any(c['held'] for c in sessions),
            'session': active if any(c['id'] == active for c in sessions) else (sessions[0]['id'] if sessions else None),
            'chats': len(sessions), 'updated': max([updated or 0] + [c['updated'] for c in sessions]),
        })
    for entry in repos.values():
        entry['workspaces'].sort(key=lambda w: (not w['pinned'], -w['updated']))
    return {'repos': sorted(repos.values(), key=lambda r: (r['order'], r['name'].lower()))}


def session_meta(sid):
    rows = conductor_query("select w.workspace_name, w.user_set_workspace_name, w.pr_title, w.branch, w.directory_name, s.title, s.status, s.unread_count, "
                           "s.model, case s.agent_type when 'codex' then s.codex_thinking_level else s.claude_effort_level end, "
                           "cast(strftime('%s', s.updated_at) as integer), w.id from sessions s join workspaces w on w.id = s.workspace_id where s.id = ?", (sid,))
    if not rows:
        return None
    name, named, pr_title, branch, directory, chat, status, unread, model, effort, updated, wid = rows[0]
    theme = agent_status.THEMES[agent_status.theme_key()]
    state = live_states().get(sid) or db_state(status, unread)
    spent = costs([sid], time.time()).get(sid)
    return {'id': sid, 'name': workspace_title(name, named, pr_title, branch, directory, chat), 'place': directory, 'state': state,
            'unit': theme['unit'](agent_status.tier(model, effort), sid), 'since': updated or time.time(),
            'workspace': wid, 'cost': spent[0] if spent else None}


def messages(sid, limit):
    """A chat's transcript merged with what the phone sent it, plus the question held for the phone and whether a reply can reach it."""
    entries = sorted(transcript(sid, limit) + phone_log(sid), key=lambda e: e.get('at') or '')[-limit:]
    held = waiting(sid)
    return {'entries': entries, 'held': held, 'session': session_meta(sid), 'reachable': bool(held or peer_name(sid))}


def transcript(sid, limit):
    """The session's conversation as Conductor stored it: your messages as text, the agent's as SDK JSON."""
    db = sqlite3.connect(f'file:{CONDUCTOR_DB}?mode=ro', uri=True, timeout=1)
    try:
        rows = db.execute('select role, content, sent_at from session_messages where session_id = ? and sent_at is not null '
                          'order by sent_at desc limit 600', (sid,)).fetchall()
    finally:
        db.close()
    entries = []
    for role, content, at in reversed(rows):
        if role == 'user':
            entries.append({'role': 'user', 'text': content, 'at': at})
            continue
        try:
            message = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            continue
        kind = message.get('type')
        blocks = (message.get('message') or {}).get('content')
        if kind == 'assistant' and isinstance(blocks, list):
            for block in blocks:
                if block.get('type') == 'text' and block.get('text', '').strip():
                    entries.append({'role': 'assistant', 'text': block['text'], 'at': at})
                elif block.get('type') == 'tool_use':
                    entries.append({'role': 'tool', 'text': tool_summary(block), 'at': at})
        elif kind == 'user' and isinstance(blocks, str) and blocks.strip():
            entries.append({'role': 'note', 'text': blocks, 'at': at})
    return entries[-limit:]


def tool_summary(block):
    name, args = block.get('name', 'tool'), block.get('input') or {}
    detail = args.get('description') or args.get('command') or args.get('file_path') or args.get('pattern') or args.get('url') or args.get('query') or ''
    if 'AskUserQuestion' in name:
        detail = ' / '.join(q.get('question', '') for q in args.get('questions', []))
    return f"{name.split('__')[-1]}: {str(detail)[:160]}".rstrip(': ')


def peer_name(sid):
    """The cross-session address of the chat's live Claude process, from Claude Code's session registry."""
    for entry in os.scandir(SESSION_REGISTRY) if os.path.isdir(SESSION_REGISTRY) else ():
        if not entry.name.endswith('.json'):
            continue
        meta = read_json(entry.path, {})
        if meta.get('sessionId') != sid or not meta.get('name'):
            continue
        try:
            os.kill(int(meta['pid']), 0)
        except (OSError, ValueError, KeyError):
            continue
        return meta['name']
    return None


def claude_binary():
    versions = sorted(os.listdir(CLAUDE_BINARIES), key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split('.')])
    return os.path.join(CLAUDE_BINARIES, versions[-1], 'claude')


def log_sent(sid, record):
    with lock, open(os.path.join(SENT, f'{os.path.basename(sid)}.jsonl'), 'a') as f:
        f.write(json.dumps(record) + '\n')


def relay(sid, name, text, device, at):
    import subprocess
    message = (f'Message from the user, sent from their phone ({device}). Treat it exactly as if they had typed it in this chat:\n\n{text}\n\n'
               "Conductor does not display this message, so begin your reply with one line quoting it, prefixed 'From your phone:'.")
    prompt = (f'Call the SendMessage tool exactly once, with to={json.dumps(name)} and message set to the text between the markers, copied '
              'exactly, line breaks included. Do not add, drop or rephrase anything. Then stop without other output.\n'
              f'<<<MESSAGE\n{message}\nMESSAGE>>>')
    command = [claude_binary(), '-p', prompt, '--model', 'haiku', '--permission-mode', 'bypassPermissions', '--tools', 'SendMessage',
               '--strict-mcp-config', '--settings', json.dumps({'disableAllHooks': True}), '--no-session-persistence', '--name', 'phone',
               '--output-format', 'json']
    try:
        done = subprocess.run(command, cwd=PHONE, capture_output=True, text=True, timeout=180,
                              env={**os.environ, 'CONDUCTOR_WORKSPACE_ID': '', 'CLAUDECODE': ''})
        result = json.loads(done.stdout or '{}')
        ok = done.returncode == 0 and not result.get('is_error')
        log_sent(sid, {'at': at, 'status': 'delivered' if ok else 'failed', 'detail': (result.get('result') or done.stderr or '')[-300:]})
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as error:
        log_sent(sid, {'at': at, 'status': 'failed', 'detail': str(error)[-300:]})


def reply(sid, body, device):
    text = str(body.get('text', '')).strip()[:MAX_REPLY]
    if not text:
        return 400, {'error': 'Empty message.'}
    at = time.time()
    if waiting(sid):
        write_json(os.path.join(INBOX, f'{os.path.basename(sid)}.json'), {'text': text, 'device': device['name'], 'at': at})
        log_sent(sid, {'at': at, 'text': text, 'status': 'delivered', 'via': 'away'})
        return 200, {'status': 'delivering', 'detail': 'Answered — the agent continues now.'}
    name = peer_name(sid)
    if name is None:
        return 409, {'error': NOT_RUNNING}
    log_sent(sid, {'at': at, 'text': text, 'status': 'sending', 'via': 'message'})
    threading.Thread(target=relay, args=(sid, name, text, device['name'], at), daemon=True).start()
    return 200, {'status': 'sending', 'detail': 'Sending… the chat picks it up within a few seconds.'}


def safe_filename(name, mime):
    base = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.basename(str(name or ''))).strip('._-')[:80] or 'attachment'
    if not os.path.splitext(base)[1]:
        base += ATTACHMENT_TYPES.get(mime, '.txt')
    return base


def attach(sid, body, device):
    """Save a file sent from the phone and reply with its path, so the agent opens it with its Read tool."""
    if not re.fullmatch(r'[A-Za-z0-9-]+', sid):
        return 404, {'error': 'Unknown chat.'}
    mime = str(body.get('mime', '')).split(';')[0].strip().lower()
    if mime not in ATTACHMENT_TYPES and not mime.startswith('text/'):
        return 415, {'error': 'Attach an image (PNG, JPEG, GIF, WebP), a PDF or a text file.'}
    data = ''.join(str(body.get('data', '')).split())
    if data.startswith('data:'):
        data = data.partition(',')[2]
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return 400, {'error': 'The file data is not valid base64.'}
    if not raw:
        return 400, {'error': 'The file is empty.'}
    if len(raw) > MAX_ATTACHMENT:
        return 413, {'error': 'Attachments can be at most 20 MB.'}
    if not (waiting(sid) or peer_name(sid)):
        return 409, {'error': NOT_RUNNING}
    folder = os.path.join(UPLOADS, sid)
    os.makedirs(folder, exist_ok=True)
    now = time.time()
    path = os.path.join(folder, f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}{int(now % 1 * 1000):03d}-{safe_filename(body.get('name'), mime)}")
    with open(path, 'wb') as f:
        f.write(raw)
    caption = str(body.get('caption', '')).strip()[:MAX_REPLY - 600]
    text = (f'{caption}\n\n' if caption else '') + f'I attached a file from my phone: {path} (open it with your Read tool).'
    status, result = reply(sid, {'text': text}, device)
    if status != 200:
        os.remove(path)
    return status, result


def phone_log(sid):
    """What the phone sent to this chat, each with its latest delivery status."""
    records = {}
    try:
        with open(os.path.join(SENT, f'{os.path.basename(sid)}.jsonl')) as f:
            for row in f:
                record = json.loads(row)
                records.setdefault(record['at'], {}).update(record)
    except FileNotFoundError:
        pass
    return [{'role': 'phone', 'text': r.get('text', ''), 'status': r.get('status'), 'detail': r.get('detail', ''),
             'at': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(r['at']))} for r in records.values() if r.get('text')]


def tool_env():
    """launchd gives us a bare PATH and no HOME; gh needs both, and git must not take index.lock from under an agent.
    The Tailscale app's binary only acts as the CLI when told to (or when run from a shell); otherwise it hangs."""
    return {**os.environ, 'HOME': os.path.expanduser('~'), 'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'GIT_OPTIONAL_LOCKS': '0',
            'TAILSCALE_BE_CLI': '1'}


def git(root, *args):
    return subprocess.run([GIT, '-C', root, '--literal-pathspecs', *args], capture_output=True, timeout=TOOL_TIMEOUT, env=tool_env())


def workspace_checkout(wid):
    """(path, branch, target branch, remote) of a Conductor workspace whose folder still exists, else None."""
    rows = conductor_query("select w.workspace_path, w.branch, coalesce(nullif(w.intended_target_branch, ''), nullif(r.default_branch, ''), 'main'), "
                           "coalesce(nullif(r.remote, ''), 'origin') from workspaces w join repos r on r.id = w.repository_id where w.id = ?", (wid,))
    return rows[0] if rows and rows[0][0] and os.path.isdir(rows[0][0]) else None


def merge_base(root, target, remote):
    """The ref the workspace branches from and its merge-base with HEAD; HEAD itself when neither ref exists."""
    for ref in (f'{remote}/{target}', target):
        done = git(root, 'merge-base', 'HEAD', ref)
        if done.returncode == 0:
            return ref, done.stdout.decode().strip()
    return 'HEAD', 'HEAD'


def untracked(root, *paths):
    out = git(root, 'ls-files', '--others', '--exclude-standard', '-z', '--', *paths).stdout.decode(errors='replace')
    return [p for p in out.split('\0') if p]


def line_count(path):
    """Lines in a new file, 0 for binary or large ones (as --numstat counts binaries)."""
    try:
        if os.path.getsize(path) > 2 * 1024 * 1024:
            return 0
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return 0
    if b'\0' in data[:8000]:
        return 0
    return data.count(b'\n') + (1 if data and not data.endswith(b'\n') else 0)


def name_status(root, base):
    """{path: (status, the path it was renamed or copied from, else None)} for tracked files changed since `base`."""
    out = {}
    tokens = git(root, 'diff', '--name-status', '-z', '-M', base).stdout.decode(errors='replace').split('\0')
    i = 0
    while i < len(tokens) and tokens[i]:
        code = tokens[i][0]
        if code in 'RC':
            out[tokens[i + 2]] = (GIT_STATUS[code], tokens[i + 1])
            i += 3
        else:
            out[tokens[i + 1]] = (GIT_STATUS.get(code, 'modified'), None)
            i += 2
    return out


def changed_files(root, base):
    """Every file the workspace changed since `base`, committed or not, untracked included."""
    counts = {}
    tokens = git(root, 'diff', '--numstat', '-z', '-M', base).stdout.decode(errors='replace').split('\0')
    i = 0
    while i < len(tokens) and tokens[i]:
        added, removed, path = tokens[i].split('\t', 2)
        if not path:  # a rename: the old and the new path follow
            path, i = tokens[i + 2], i + 3
        else:
            i += 1
        counts[path] = (int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0)
    statuses = name_status(root, base)
    files = [{'path': p, 'added': counts.get(p, (0, 0))[0], 'removed': counts.get(p, (0, 0))[1], 'status': statuses.get(p, ('modified', None))[0]}
             for p in sorted(set(counts) | set(statuses))]
    files += [{'path': p, 'added': line_count(os.path.join(root, p)), 'removed': 0, 'status': 'added'} for p in untracked(root)[:MAX_UNTRACKED]]
    return sorted(files, key=lambda f: f['path'])


def check_outcome(check):
    if check.get('__typename') == 'StatusContext':
        state = check.get('state')
        return 'passed' if state == 'SUCCESS' else 'failed' if state in ('FAILURE', 'ERROR') else 'pending'
    if check.get('status') != 'COMPLETED':
        return 'pending'
    return 'passed' if check.get('conclusion') in ('SUCCESS', 'NEUTRAL', 'SKIPPED') else 'failed'


def pull_request(wid, root):
    """The branch's GitHub pull request via gh, cached a minute per workspace; None without one or when gh fails."""
    def look():
        try:
            done = subprocess.run([GH, 'pr', 'view', '--json', 'number,title,url,state,isDraft,reviewDecision,statusCheckRollup'],
                                  cwd=root, capture_output=True, text=True, timeout=TOOL_TIMEOUT, env=tool_env())
            pr = json.loads(done.stdout) if done.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None
        if not isinstance(pr, dict):
            return None
        checks = {'passed': 0, 'failed': 0, 'pending': 0}
        for check in pr.get('statusCheckRollup') or []:
            checks[check_outcome(check)] += 1
        return {'number': pr.get('number'), 'title': pr.get('title'), 'url': pr.get('url'), 'state': pr.get('state'),
                'isDraft': bool(pr.get('isDraft')), 'reviewDecision': pr.get('reviewDecision') or None, 'checks': checks}
    return cached(f'pr:{wid}', 60, look)


def workspace_changes(wid):
    found = workspace_checkout(wid)
    if found is None:
        return 404, {'error': 'No such workspace on this Mac.'}
    root, branch, target, remote = found
    try:
        ref, base = merge_base(root, target, remote)
        files = changed_files(root, base)
    except subprocess.TimeoutExpired:
        return 504, {'error': 'git took too long.'}
    return 200, {'branch': branch, 'base': ref, 'mergeBase': base, 'files': files,
                 'totals': {'files': len(files), 'added': sum(f['added'] for f in files), 'removed': sum(f['removed'] for f in files)},
                 'pr': pull_request(wid, root)}


def workspace_diff(wid, path):
    """One file's unified diff against the same base as workspace_changes; untracked files diff against nothing."""
    found = workspace_checkout(wid)
    if found is None:
        return 404, {'error': 'No such workspace on this Mac.'}
    root, _, target, remote = found
    if '\0' in path:
        return 400, {'error': 'That path is not inside the workspace.'}
    relative = os.path.normpath(path or '.')
    real_root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(real_root, relative))
    if os.path.isabs(relative) or relative == '.' or relative.split(os.sep)[0] == '..' or os.path.commonpath([real_root, full]) != real_root:
        return 400, {'error': 'That path is not inside the workspace.'}
    try:
        _, base = merge_base(root, target, remote)
        source = name_status(root, base).get(relative, (None, None))[1]  # a renamed file diffs against its old path
        patch = git(root, 'diff', '-M', base, '--', *([source] if source else []), relative).stdout
        if not patch and untracked(root, relative):
            patch = git(root, 'diff', '--no-index', '--', '/dev/null', relative).stdout
    except subprocess.TimeoutExpired:
        return 504, {'error': 'git took too long.'}
    return 200, {'path': relative, 'patch': patch[:MAX_PATCH].decode(errors='replace'), 'truncated': len(patch) > MAX_PATCH}


def task_url(prompt, root):
    return f"conductor://prompt={urllib.parse.quote(prompt, safe='')}&path={urllib.parse.quote(root, safe='')}"


def start_task(body):
    """Experimental: hand a prompt to Conductor on this Mac, which opens a new workspace for it in the repository."""
    prompt = str(body.get('prompt', '')).strip()[:MAX_REPLY]
    if not prompt:
        return 400, {'error': 'Empty prompt.'}
    rows = conductor_query('select root_path from repos where id = ?', (str(body.get('repo', '')),))
    if not rows or not rows[0][0]:
        return 404, {'error': 'No such repository.'}
    done = subprocess.run(['/usr/bin/open', task_url(prompt, rows[0][0])], capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    if done.returncode != 0:
        return 502, {'error': f'Conductor did not open: {done.stderr.strip()[:200]}'}
    return 200, {'status': 'opened'}


def log(text):
    sys.stderr.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def apns_settings():
    """phone/apns.json when it is complete and its key file exists, else None (push off)."""
    settings = read_json(APNS, None)
    if not isinstance(settings, dict) or not all(settings.get(k) for k in ('keyId', 'teamId', 'keyPath', 'bundleId')):
        return None
    settings = {'environment': 'production', **settings, 'keyPath': os.path.expanduser(settings['keyPath'])}
    return settings if os.path.isfile(settings['keyPath']) else None


def push_tokens(kind):
    """(device digest, token, environment) for every still-paired device registered for `kind`."""
    devices = read_json(DEVICES, {})
    return [(key, kinds[kind]['token'], kinds[kind].get('environment')) for key, kinds in read_json(PUSH, {}).items()
            if key in devices and kind in kinds]


def register_push(key, body):
    token, kind = str(body.get('token', '')).strip().lower(), body.get('kind')
    if kind not in PUSH_KINDS:
        return 400, {'error': f"kind must be one of {', '.join(PUSH_KINDS)}."}
    if not re.fullmatch(r'[0-9a-f]{32,512}', token):
        return 400, {'error': 'token must be the hex push token.'}
    environment = body.get('environment') if body.get('environment') in ('development', 'production') else None
    with lock:
        registry = read_json(PUSH, {})
        registry.setdefault(key, {})[kind] = {'token': token, 'environment': environment, 'at': time.time()}
        write_json(PUSH, registry)
    return 200, {'registered': kind, 'configured': apns_settings() is not None}


def push_status():
    devices = read_json(DEVICES, {})
    return {'configured': apns_settings() is not None, 'devices': sum(1 for key, kinds in read_json(PUSH, {}).items() if key in devices and kinds)}


def drop_token(key, kind, token):
    with lock:
        registry = read_json(PUSH, {})
        if registry.get(key, {}).get(kind, {}).get('token') == token:
            del registry[key][kind]
            write_json(PUSH, registry)


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def der_to_raw(der):
    """An ECDSA signature as openssl writes it (a DER SEQUENCE of the integers r and s) in JWS's 64-byte r||s form."""
    def length(i):
        if der[i] < 0x80:
            return der[i], i + 1
        size = der[i] & 0x7F
        return int.from_bytes(der[i + 1:i + 1 + size], 'big'), i + 1 + size
    if der[:1] != b'\x30':
        raise ValueError('not a DER signature')
    _, i = length(1)
    raw = b''
    for _ in range(2):
        if der[i] != 0x02:
            raise ValueError('not a DER signature')
        size, i = length(i + 1)
        number = der[i:i + size].lstrip(b'\0')
        if len(number) > 32:
            raise ValueError('not a P-256 signature')
        raw += number.rjust(32, b'\0')
        i += size
    return raw


def apns_jwt(settings):
    """The ES256 provider token APNs wants, signed with openssl; reused for 50 minutes (APNs refuses ones over an hour old)."""
    def sign():
        compact = {'separators': (',', ':')}
        unsigned = (b64url(json.dumps({'alg': 'ES256', 'kid': settings['keyId']}, **compact).encode()) + '.'
                    + b64url(json.dumps({'iss': settings['teamId'], 'iat': int(time.time())}, **compact).encode()))
        der = subprocess.run([OPENSSL, 'dgst', '-sha256', '-sign', settings['keyPath']], input=unsigned.encode(),
                             capture_output=True, timeout=TOOL_TIMEOUT, check=True).stdout
        return f'{unsigned}.{b64url(der_to_raw(der))}'
    return cached(f"jwt:{settings['teamId']}:{settings['keyId']}:{settings['keyPath']}", 50 * 60, sign)


def apns_send(settings, token, environment, payload, live):
    """POST one notification over HTTP/2 with curl, as the stdlib has no HTTP/2. Returns (HTTP status, APNs reason).
    The URL and headers (JWT, device token) go in on stdin as a curl config and the payload in a private temp file,
    so none of them show in `ps` or in an error message."""
    host = 'api.sandbox.push.apple.com' if (environment or settings['environment']) == 'development' else 'api.push.apple.com'
    config = '\n'.join([f'url = "https://{host}/3/device/{token}"',
                         f'header = "authorization: bearer {apns_jwt(settings)}"',
                         f"header = \"apns-topic: {settings['bundleId']}{'.push-type.liveactivity' if live else ''}\"",
                         f"header = \"apns-push-type: {'liveactivity' if live else 'alert'}\"", 'header = "apns-priority: 10"'])
    with tempfile.NamedTemporaryFile('w', suffix='.json') as body:
        json.dump(payload, body)
        body.flush()
        done = subprocess.run([CURL, '--http2', '-s', '--max-time', str(TOOL_TIMEOUT), '-o', '-', '-w', '\n%{http_code}', '-K', '-',
                               '--data-binary', f'@{body.name}'],
                              input=config, capture_output=True, text=True, timeout=TOOL_TIMEOUT + 5)
    answer, _, code = done.stdout.rpartition('\n')
    try:
        reason = json.loads(answer).get('reason') if answer.strip() else None
    except (json.JSONDecodeError, AttributeError):
        reason = answer.strip()[:200] or None
    return int(code) if code.isdigit() else 0, reason


def user_at_mac():
    """Someone is using this Mac: the screen is unlocked and there was keyboard or mouse input in the last few minutes."""
    if '"CGSSessionScreenIsLocked"=Yes' in run('/usr/sbin/ioreg', '-n', 'Root', '-d1'):
        return False
    idle = re.search(r'"HIDIdleTime" = (\d+)', run('/usr/sbin/ioreg', '-c', 'IOHIDSystem', '-d', '4'))
    return bool(idle) and int(idle.group(1)) / 1e9 < AT_MAC_IDLE


def quiet_devices():
    """Devices that shouldn't get notifications now: every one while you're at the Mac (unless away mode is on), and any
    phone with the app open on screen."""
    with lock:
        quiet = {key for key, streams in watching.items() if streams > 0}
    if not os.path.exists(AWAY) and user_at_mac():
        quiet |= set(read_json(DEVICES, {}))
    return quiet


def push(kind, payload, skip=frozenset()):
    """Sends to every device registered for `kind` except those in `skip`. Returns the (device, token) pairs that are done
    with: accepted by Apple, or rejected as no longer valid (those are dropped from the registry here)."""
    settings, done = apns_settings(), []
    if settings is None:
        return done
    for key, token, environment in push_tokens(kind):
        if key in skip:
            continue
        try:
            status, reason = apns_send(settings, token, environment, payload, live=kind in ('liveactivity', 'pushtostart'))
        except Exception as error:  # a failed send must not stop the others or repeat the batch
            log(f'push {kind} to {token[:8]}… failed: {type(error).__name__}')
            continue
        if status == 410 or (status == 400 and reason == 'BadDeviceToken'):
            drop_token(key, kind, token)
            done.append((key, token))
        elif status == 200:
            done.append((key, token))
        else:
            log(f'push {kind} to {token[:8]}…: {status} {reason or ""}')
    return done


def last_reply(sid):
    """The newest text the agent wrote in a chat, whitespace collapsed, for a notification body."""
    try:
        rows = conductor_query("select content from session_messages where session_id = ? and role = 'assistant' and sent_at is not null "
                               "order by sent_at desc limit 50", (sid,))
    except sqlite3.Error:
        return ''
    for (content,) in rows:
        try:
            message = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            continue
        blocks = (message.get('message') or {}).get('content') if message.get('type') == 'assistant' else None
        texts = [b['text'] for b in blocks if b.get('type') == 'text' and b.get('text', '').strip()] if isinstance(blocks, list) else []
        if texts:
            return ' '.join(texts[-1].split())
    return ''


def alert(s, title, body, category, badge, options=()):
    payload = {'aps': {'alert': {'title': title, 'body': body}, 'sound': 'default', 'badge': badge, 'thread-id': s.id,
                       'category': category, 'mutable-content': 1},
               'sessionId': s.id, 'workspace': s.workspace}
    if options:  # the app's notification extension turns these into buttons
        payload['options'] = [str(o) for o in options[:3]]  # whole labels: the tapped one is sent back as the answer
    return payload


def session_alerts(listed, before, badge, theme):
    """Notifications for what changed since the previous snapshot: a finished turn, a failure, a question held for the phone."""
    out = []
    for s, held in listed:
        state, asked = before.get(s.id, (None, None))
        if held and held.get('since') != asked:
            questions = held.get('questions') or []
            question = ' / '.join(q.get('question', '') for q in questions) or 'Has a question'
            single = len(questions) == 1 and not questions[0].get('multiSelect')  # buttons pick exactly one answer
            options = [o.get('label') if isinstance(o, dict) else o for o in (questions[0].get('options') or [])] if single else []
            out.append(alert(s, f'{s.name} asks', agent_status.clip(question, 240), 'AGENT_QUESTION', badge, [o for o in options if o]))
        elif s.state == 'done' and state in ('working', 'waiting'):
            out.append(alert(s, s.name, agent_status.clip(last_reply(s.id) or theme['quotes']['done'][0], 140), 'AGENT_REPLY', badge))
        elif s.state == 'failed' and state != 'failed':
            out.append(alert(s, s.name, agent_status.clip(last_reply(s.id) or theme['quotes']['failed'][0], 140), 'AGENT_REPLY', badge))
    return out


def live_activity(working, badge, theme, now):
    """Live Activity content for the working sessions; an `end` event once nothing works and nothing needs you."""
    top = max(working, key=lambda s: s.since, default=None)
    aps = {'timestamp': int(now), 'event': 'update', 'content-state': {
        'working': len(working), 'needsYou': badge, 'headline': top.name if top else '', 'detail': top.doing if top else '',
        'unit': theme['unit'](top.rank, top.id) if top else '', 'updatedAt': now}}
    if not working and not badge:
        aps.update({'event': 'end', 'dismissal-date': int(now) + 60})
    return {'aps': aps}


def live_activity_start(update):
    """Push-to-start: opens the Live Activity on a phone that has none, with a banner naming the busiest chat."""
    state = update['aps']['content-state']
    body = f"{state['headline']}: {state['detail']}" if state['detail'] else state['headline'] or 'An agent started working'
    return {'aps': {**update['aps'], 'event': 'start', 'attributes-type': 'AgentsActivityAttributes',
                    'attributes': {'computer': computer_name()},
                    'alert': {'title': f"{state['working']} working" if state['working'] else 'Needs you', 'body': agent_status.clip(body, 120)}}}


def watch_for_push():
    """While APNs is set up and a device registered, poll session states every 2 s and push what changed.
    The first snapshot only sets the baseline, so a restart never replays old news."""
    before, live_sent, live_at, started_at, start_owed = None, None, 0.0, 0.0, False
    while True:
        time.sleep(2)
        try:
            if apns_settings() is None or not any(push_tokens(kind) for kind in PUSH_KINDS):
                before = live_sent = None
                continue
            now = time.time()
            theme = agent_status.THEMES[agent_status.theme_key()]
            listed = [(s, waiting(s.id)) for s in agent_status.load(now)]
            badge = sum(1 for s, held in listed if s.state in ('waiting', 'failed') or held)
            working = [s for s, _ in listed if s.state == 'working']
            live = (frozenset(s.id for s in working), badge)
            previous, before = before, {s.id: (s.state, (held or {}).get('since')) for s, held in listed}  # advance first: no repeats
            if previous is None:
                live_sent = live
                continue
            alerts = session_alerts(listed, previous, badge, theme)
            quiet = quiet_devices() if alerts else set()
            for payload in alerts:
                push('alert', payload, skip=quiet)
            idle = (frozenset(), 0)
            if live == idle:
                start_owed = False
            elif live_sent == idle:
                start_owed = True  # idle → busy: the phone should get a Live Activity if it has none
            if push_tokens('liveactivity'):
                start_owed = False
                if live != live_sent and now - live_at >= 5:
                    update = live_activity(working, badge, theme, now)
                    done = push('liveactivity', update)
                    if update['aps']['event'] == 'end':  # an ended activity's token is spent; the next one comes from push-to-start
                        for key, token in done:
                            drop_token(key, 'liveactivity', token)
                    live_sent, live_at = live, now
            else:
                if start_owed and now - started_at >= 600 and push_tokens('pushtostart'):
                    done = push('pushtostart', live_activity_start(live_activity(working, badge, theme, now)), skip=quiet_devices())
                    if {token for _, token in done} & {token for _, token, _ in push_tokens('pushtostart')}:  # still registered = accepted
                        start_owed = False
                    started_at = now  # a failed start waits as long as a sent one before trying again
                live_sent = live
        except Exception as error:  # one bad tick must not end the watcher thread
            log(f'push watcher: {type(error).__name__}: {error}')


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'agent-status-phone'

    def log_message(self, format, *args):
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {self.address_string()} {format % args}\n")

    def send(self, status, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def length(self):
        """The request's Content-Length, or None when it's missing a number or negative (never read to EOF)."""
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return None
        return length if length >= 0 else None

    def body(self, limit=MAX_BODY):
        length = self.length()
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(min(length, limit)) or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def bearer(self):
        auth = self.headers.get('Authorization', '')
        return auth[7:] if auth.startswith('Bearer ') else ''

    def device(self):
        return device_for(self.bearer())

    def client_gone(self):
        try:
            return bool(select.select([self.connection], [], [], 0)[0]) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            return True

    def stream(self, query):
        """Server-Sent Events: the sessions payload whenever it changes and, with ?session=, that chat's messages payload.
        Ends when the phone is revoked, as the token is otherwise only checked when the stream opens."""
        key_of_device = digest(self.bearer())
        sid = (query.get('session') or [None])[0]
        limit = min(int((query.get('limit') or ['60'])[0]), 200)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        sent, pinged = {}, time.time()
        with lock:
            watching[key_of_device] = watching.get(key_of_device, 0) + 1
        try:
            while not self.client_gone() and key_of_device in read_json(DEVICES, {}):
                try:
                    payloads = (('snapshot', sessions()), ('transcript', messages(sid, limit) if sid else None))
                except sqlite3.Error as error:  # Conductor's database can be briefly unreadable; try again next tick
                    log(f'stream: {error}')
                    payloads = ()
                for event, payload in payloads:
                    if payload is None:
                        continue
                    key = json.dumps({k: v for k, v in payload.items() if k != 'now'}, sort_keys=True)  # `now` ticks every call
                    if key != sent.get(event):
                        sent[event] = key
                        self.wfile.write(f'event: {event}\ndata: {json.dumps(payload)}\n\n'.encode())
                if time.time() - pinged >= 15:
                    self.wfile.write(b': ping\n\n')
                    pinged = time.time()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with lock:
                watching[key_of_device] -= 1

    def route(self, method):
        url = urllib.parse.urlparse(self.path)
        parts = [p for p in url.path.split('/') if p]
        if method == 'GET' and url.path in ('/', '/index.html'):
            with open(APP, 'rb') as f:
                return self.send(200, f.read(), 'text/html; charset=utf-8')
        if method == 'GET' and url.path == '/manifest.webmanifest':
            return self.send(200, {'name': 'Agents', 'short_name': 'Agents', 'display': 'standalone', 'start_url': '/',
                                   'background_color': '#15161a', 'theme_color': '#15161a',
                                   'icons': [{'src': '/icon.png', 'sizes': '192x192', 'type': 'image/png'}]}, 'application/manifest+json')
        if method == 'GET' and url.path in ('/icon.png', '/apple-touch-icon.png'):
            sprite = agent_status.pokeball() if agent_status.theme_key() == 'pkmn' else agent_status.radar(2) if agent_status.theme_key() == 'dbz' else agent_status.coin(6.5)
            return self.send(200, agent_status.base64.b64decode(agent_status.png(sprite, scale=12)), 'image/png')
        if url.path.startswith('/pair'):
            local = self.client_address[0] in ('127.0.0.1', '::1') and self.headers.get('Host', '').split(':')[0] in ('127.0.0.1', 'localhost') \
                and not self.headers.get('X-Forwarded-For') and not self.headers.get('Tailscale-User-Login')
            if not local:
                return self.send(404, {'error': 'Not found.'})
            if url.path == '/pair':
                return self.send(200, pairing_page().encode(), 'text/html; charset=utf-8')
            offer = read_json(PAIRING, None)
            if url.path == '/pair/qr.png' and offer:
                import subprocess
                link = f"{public_url()}/#pair={offer['secret']}"
                return self.send(200, subprocess.run([QRENCODE, '-o', '-', '-t', 'PNG', '-s', '10', '-m', '1', link], capture_output=True).stdout, 'image/png')
            return self.send(404, {'error': 'No pairing offer.'})
        if method == 'POST' and url.path == '/api/pair':
            return self.send(*pair(self.body()))
        device = self.device()
        if parts[:1] == ['api'] and device is None:
            return self.send(401, {'error': 'This phone is not paired with this computer.'})
        if method == 'GET' and url.path == '/api/sessions':
            return self.send(200, sessions())
        if method == 'GET' and url.path == '/api/stream':
            return self.stream(urllib.parse.parse_qs(url.query))
        if method == 'GET' and url.path == '/api/workspaces':
            return self.send(200, workspaces())
        if len(parts) == 4 and parts[:2] == ['api', 'workspaces'] and method == 'GET' and parts[3] == 'changes':
            return self.send(*workspace_changes(parts[2]))
        if len(parts) == 4 and parts[:2] == ['api', 'workspaces'] and method == 'GET' and parts[3] == 'diff':
            return self.send(*workspace_diff(parts[2], urllib.parse.parse_qs(url.query).get('path', [''])[0]))
        if method == 'GET' and url.path == '/api/push/status':
            return self.send(200, push_status())
        if method == 'POST' and url.path == '/api/push/register':
            return self.send(*register_push(digest(self.bearer()), self.body()))
        if method == 'POST' and url.path == '/api/tasks':
            return self.send(*start_task(self.body()))
        if method == 'POST' and url.path == '/api/away':
            if self.body().get('on'):
                open(AWAY, 'a').close()
            elif os.path.exists(AWAY):
                os.remove(AWAY)
            return self.send(200, {'away': os.path.exists(AWAY)})
        if len(parts) == 4 and parts[:2] == ['api', 'sessions'] and parts[3] == 'messages' and method == 'GET':
            limit = int(urllib.parse.parse_qs(url.query).get('limit', ['60'])[0])
            return self.send(200, messages(parts[2], min(limit, 200)))
        if len(parts) == 4 and parts[:2] == ['api', 'sessions'] and parts[3] == 'reply' and method == 'POST':
            return self.send(*reply(parts[2], self.body(), device))
        if len(parts) == 4 and parts[:2] == ['api', 'sessions'] and parts[3] == 'attachments' and method == 'POST':
            limit = MAX_ATTACHMENT * 4 // 3 + MAX_BODY  # base64 plus the other fields
            if (self.length() or 0) > limit:
                return self.send(413, {'error': 'Attachments can be at most 20 MB.'})
            return self.send(*attach(parts[2], self.body(limit), device))
        return self.send(404, {'error': 'Not found.'})

    def do_GET(self):
        self.route('GET')

    def do_POST(self):
        self.route('POST')


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def restart_on_config_change(server, started_with):
    while True:
        time.sleep(2)
        if config() != started_with:
            server.shutdown()
            return


def main():
    for d in (WAITING, INBOX, SENT, UPLOADS):
        os.makedirs(d, exist_ok=True)
    settings = config()
    host = '0.0.0.0' if settings['lan'] else '127.0.0.1'
    server = Server((host, settings['port']), Handler)
    threading.Thread(target=restart_on_config_change, args=(server, settings), daemon=True).start()
    threading.Thread(target=watch_for_push, daemon=True).start()
    sys.stderr.write(f'listening on {host}:{settings["port"]}\n')
    advert = None
    if settings['lan']:  # lets the phone app find this Mac; the TXT record carries the address to use
        import subprocess
        advert = subprocess.Popen(['/usr/bin/dns-sd', '-R', computer_name(), '_agentstatus._tcp', 'local', str(settings['port']), f'url={public_url()}'],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        server.serve_forever()
    finally:
        if advert:
            advert.terminate()
    sys.exit(1)  # launchd's KeepAlive starts us again with the new config


if __name__ == '__main__':
    main()
