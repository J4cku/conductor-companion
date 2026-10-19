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

A reply reaches a chat one of two ways. A held chat (waiting/) takes it from inbox/ through the hook. Any other chat
whose agent process is alive gets it as a Claude Code cross-session message: a throwaway headless Claude (Haiku,
SendMessage as its only tool, no hooks, same bypass mode as Conductor's chats) sends it, which wakes an idle chat.
"""
import hashlib
import http.server
import importlib.util
import json
import os
import secrets
import socket
import socketserver
import sqlite3
import sys
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

TAILSCALE = '/Applications/Tailscale.app/Contents/MacOS/Tailscale'
QRENCODE = '/opt/homebrew/bin/qrencode'
DEFAULT_PORT = 47823
MAX_PAIRING_FAILURES = 5
MAX_REPLY = 8000

spec = importlib.util.spec_from_file_location('agent_status', PLUGIN)
agent_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_status)

lock = threading.Lock()
cache = {}
pairing_failures = {'count': 0}


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
    try:
        import subprocess
        return subprocess.run(['/usr/sbin/scutil', '--get', 'ComputerName'], capture_output=True, text=True, timeout=2).stdout.strip() or socket.gethostname()
    except Exception:
        return socket.gethostname()


def cached(key, ttl, compute):
    hit = cache.get(key)
    if hit is None or time.time() - hit[0] > ttl:
        cache[key] = hit = (time.time(), compute())
    return hit[1]


def run(*args):
    import subprocess
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout
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
    theme = agent_status.THEMES[agent_status.theme_key()]
    out = []
    for s in agent_status.load(now):
        quotes = theme['quotes'][s.state]
        out.append({
            'id': s.id, 'state': s.state, 'name': s.name, 'workspace': s.workspace, 'doing': s.doing,
            'unit': theme['unit'](s.rank, s.id), 'since': s.since, 'context': s.context, 'place': s.place,
            'quote': quotes[agent_status.zlib.crc32(f'{s.id}{s.state}'.encode()) % len(quotes)],
            'held': waiting(s.id), 'queued': os.path.exists(os.path.join(INBOX, f'{s.id}.json')),
        })
    return {
        'computer': computer_name(), 'away': os.path.exists(AWAY), 'now': now,
        'theme': {'name': theme['name'], 'labels': theme['labels'], 'icons': theme['icons']},
        'sessions': out,
    }


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
                           "cast(strftime('%s', s.updated_at) as integer) from sessions s join workspaces w on w.id = s.workspace_id where s.id = ?", (sid,))
    if not rows:
        return None
    name, named, pr_title, branch, directory, chat, status, unread, model, effort, updated = rows[0]
    theme = agent_status.THEMES[agent_status.theme_key()]
    state = live_states().get(sid) or db_state(status, unread)
    return {'id': sid, 'name': workspace_title(name, named, pr_title, branch, directory, chat), 'place': directory, 'state': state,
            'unit': theme['unit'](agent_status.tier(model, effort), sid), 'since': updated or time.time()}


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
        return 409, {'error': "This chat's agent isn't running (Conductor stops idle ones). Open it in Conductor once, then try again."}
    log_sent(sid, {'at': at, 'text': text, 'status': 'sending', 'via': 'message'})
    threading.Thread(target=relay, args=(sid, name, text, device['name'], at), daemon=True).start()
    return 200, {'status': 'sending', 'detail': 'Sending… the chat picks it up within a few seconds.'}


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

    def body(self):
        length = min(int(self.headers.get('Content-Length') or 0), 64 * 1024)
        try:
            return json.loads(self.rfile.read(length) or b'{}')
        except json.JSONDecodeError:
            return {}

    def device(self):
        auth = self.headers.get('Authorization', '')
        return device_for(auth[7:] if auth.startswith('Bearer ') else '')

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
        if method == 'GET' and url.path == '/api/workspaces':
            return self.send(200, workspaces())
        if method == 'POST' and url.path == '/api/away':
            if self.body().get('on'):
                open(AWAY, 'a').close()
            elif os.path.exists(AWAY):
                os.remove(AWAY)
            return self.send(200, {'away': os.path.exists(AWAY)})
        if len(parts) == 4 and parts[:2] == ['api', 'sessions'] and parts[3] == 'messages' and method == 'GET':
            limit = int(urllib.parse.parse_qs(url.query).get('limit', ['60'])[0])
            limit = min(limit, 200)
            entries = sorted(transcript(parts[2], limit) + phone_log(parts[2]), key=lambda e: e.get('at') or '')[-limit:]
            return self.send(200, {'entries': entries, 'held': waiting(parts[2]), 'session': session_meta(parts[2]),
                                   'reachable': bool(waiting(parts[2]) or peer_name(parts[2]))})
        if len(parts) == 4 and parts[:2] == ['api', 'sessions'] and parts[3] == 'reply' and method == 'POST':
            return self.send(*reply(parts[2], self.body(), device))
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
    for d in (WAITING, INBOX, SENT):
        os.makedirs(d, exist_ok=True)
    settings = config()
    host = '0.0.0.0' if settings['lan'] else '127.0.0.1'
    server = Server((host, settings['port']), Handler)
    threading.Thread(target=restart_on_config_change, args=(server, settings), daemon=True).start()
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
