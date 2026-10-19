#!/usr/bin/python3
# <xbar.title>Agent Status</xbar.title>
# <xbar.desc>Claude Code and Codex sessions in the menu bar, Warcraft III or Dragon Ball flavoured.</xbar.desc>
# <swiftbar.type>streamable</swiftbar.type>
# <swiftbar.runInBash>false</swiftbar.runInBash>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
"""Menu bar view of agent sessions.

Conductor's database says which sessions are working, idle or unread, and with which model; the session files
~/.claude/hooks/agent-status.sh writes add what only hooks see: a session blocked on you and the tool it is running.
Sessions outside Conductor are known from their hook file alone.

SwiftBar writes a clicked item's stdin= text back to this process: dismiss:<session>, clear, next, theme:<name>.
"""
import base64
import math
import os
import re
import json
import secrets
import select
import socket
import sqlite3
import struct
import subprocess
import sys
import time
import zlib
from collections import namedtuple

HOME = os.path.expanduser('~/.claude/agent-status')
SESSIONS = os.path.join(HOME, 'sessions')
STATS = os.path.join(HOME, 'stats')
THEME_FILE = os.path.join(HOME, 'theme')
PHONE = os.path.join(HOME, 'phone')  # the phone bridge: phone/server.py and ~/.claude/hooks/phone-relay.sh
MUTE_FILE = os.path.join(HOME, 'mute')  # read by wc3.sh: empty = muted until turned back on, an epoch = muted until then
CONDUCTOR_DB = os.path.expanduser('~/Library/Application Support/com.conductor.app/conductor.db')

QUIET_AFTER = 15 * 60  # outside Conductor nothing marks an interrupted turn, so a silent working session is presumed idle
HIDE_QUIET_AFTER = 60 * 60
HIDE_DONE_AFTER = 30 * 60  # outside Conductor; inside, a finished session shows until you read it
UNREAD_GRACE = 20  # Conductor marks a finished turn unread a moment after the Stop hook fires
UNREAD_FOR = 6 * 3600  # older unread Conductor sessions are not news
FORGET_AFTER = 12 * 3600
KEEP_STATS_DAYS = 7
DB_EVERY = 2.0
FRAME = 0.3
IDLE = 1.0
NEXT_KEY = 'CTRL+OPTION+W'

ORDER = ['waiting', 'failed', 'working', 'done', 'quiet']
FINISHED = {'done', 'failed', 'quiet'}  # clicking one opens its workspace and turns it in
NEEDS_YOU = ['waiting', 'failed', 'done']  # what the idle-worker hotkey cycles through, oldest first

Hook = namedtuple('Hook', 'state since beat name workspace doing')
Row = namedtuple('Row', 'status unread agent model effort context asked updated workspace name place')
Session = namedtuple('Session', 'id state since name workspace doing rank context place')

memory = {'dismissed': {}, 'cursor': None, 'db_at': 0.0, 'db_ids': None, 'db_rows': {}}

# Sprites are 16x16 grids of RGBA tuples, written out at 2x with a 144 dpi tag so they render at 16pt.
N = 16
CLEAR = None
INK = (0, 0, 0, 255)


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def palette(*hex_colors):
    return [rgb(c) for c in hex_colors]


def canvas() -> list:
    return [[CLEAR] * N for _ in range(N)]


def neighbours(x, y):
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def outline(img, cells, edge):
    for y in range(N):
        for x in range(N):
            if (x, y) not in cells and any(n in cells for n in neighbours(x, y)):
                img[y][x] = edge
    return img


def mask(rows):
    return {(x, y) for y, row in enumerate(rows) for x, c in enumerate(row) if c == 'X'}


def ellipse(rx, ry, cx=7.5, cy=7.5):
    return {(x, y) for y in range(N) for x in range(N) if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1}


def bevel(cells, pal, inner=()):
    """Flat shape lit from the top left; the rim of `inner` is engraved."""
    hi, base, lo, edge = pal
    img = canvas()
    for x, y in cells:
        if (x - 1, y) not in cells or (x, y - 1) not in cells:
            img[y][x] = hi
        elif (x + 1, y) not in cells or (x, y + 1) not in cells:
            img[y][x] = lo
        elif (x, y) in inner and any(n not in inner for n in neighbours(x, y)):
            img[y][x] = lo
        else:
            img[y][x] = base
    return outline(img, cells, edge)


def sphere(pal, r=6.5, cx=7.5, cy=7.5):
    hi, base, lo, edge = pal
    cells = ellipse(r, r, cx, cy)
    lx, ly = cx - r * 0.4, cy - r * 0.4
    img = canvas()
    for x, y in cells:
        d = math.hypot(x - lx, y - ly) / r
        img[y][x] = hi if d < 0.45 else base if d < 1.15 else lo
    img[round(ly)][round(lx)] = rgb('#ffffff')
    return outline(img, cells, edge)


def paint(img, cells, color):
    for x, y in cells:
        img[y][x] = color
    return img


def template(cells):
    return paint(canvas(), cells, INK)


def grey(img):
    def fade(px):
        if px is CLEAR:
            return CLEAR
        g = int(0.3 * px[0] + 0.59 * px[1] + 0.11 * px[2])
        return (g // 2 + 64,) * 3 + (px[3],)
    return [[fade(px) for px in row] for row in img]


def png(img, scale=2):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b''.join(
        b'\x00' + b''.join(bytes(px or (0, 0, 0, 0)) * scale for px in row)
        for row in img for _ in range(scale)
    )
    ppm = round(72 * scale / 0.0254)
    return base64.b64encode(
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', N * scale, N * scale, 8, 6, 0, 0, 0))
        + chunk(b'pHYs', struct.pack('>IIB', ppm, ppm, 1))
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    ).decode()


EXCLAIM = mask([
    '................',
    '................',
    '......XXXX......',
    '......XXXX......',
    '......XXXX......',
    '......XXXX......',
    '......XXXX......',
    '.......XX.......',
    '.......XX.......',
    '.......XX.......',
    '................',
    '................',
    '.......XX.......',
    '.......XX.......',
])
QUESTION = mask([
    '................',
    '................',
    '.....XXXXXX.....',
    '....XXXXXXXX....',
    '....XX....XX....',
    '..........XX....',
    '.........XXX....',
    '........XXX.....',
    '.......XXX......',
    '.......XX.......',
    '................',
    '................',
    '.......XX.......',
    '.......XX.......',
])
STAR = mask([
    '................',
    '................',
    '................',
    '................',
    '................',
    '.......XX.......',
    '.....XXXXXX.....',
    '......XXXX......',
    '......XXXX......',
    '.....XX..XX.....',
])

GOLD = palette('#fff3a0', '#ffd200', '#c98a00', '#3a2400')
BLOOD = palette('#ff9a8a', '#e02020', '#8a0c0c', '#2a0000')
DRAGON = palette('#ffe9a8', '#ffb21a', '#e2700a', '#5a2400')
KI = palette('#e8fbff', '#5ad1ff', '#1f7fe0', '#0b2a6b')
RAGE = palette('#ffd0c0', '#ff5a3a', '#c01a10', '#3a0500')
STEEL = palette('#f4f4f4', '#c9cdd2', '#8c9198', '#2b2f36')


def coin(rx):
    return bevel(ellipse(rx, 6.5), GOLD, ellipse(rx - 2.4, 4.1) if rx > 3 else ())


def ki(pal):
    img = sphere(pal, r=5)
    for x, y in ((2, 3), (13, 2), (12, 13), (3, 12)):
        img[y][x] = pal[0]
    return img


def radar(blip):
    """Dragon Radar: steel bezel with a knob, green grid screen, a blinking ball."""
    cx, cy, r = 7.5, 8.5, 6
    knob = {(7, 2), (8, 2)}
    body = ellipse(r, r, cx, cy) | knob
    img = bevel(body, STEEL)
    for x, y in ellipse(r - 1.4, r - 1.4, cx, cy):
        on_grid = x == 7 or y == 8 or abs(math.hypot(x - cx, y - cy) - 2.6) < 0.5
        img[y][x] = rgb('#239a35') if on_grid else rgb('#43d14f')
    core = {(10, 5), (11, 5), (10, 6), (11, 6)}
    if blip > 1:
        paint(img, {(10, 4), (11, 4), (9, 5), (12, 5), (9, 6), (12, 6), (10, 7), (11, 7)}, rgb('#ff9a1f'))
    if blip:
        paint(img, core, rgb('#fff36a'))
    return img


def dragon_ball():
    return paint(sphere(DRAGON), STAR, rgb('#d42020'))


POKE_RED = palette('#ff8a70', '#e3350d', '#a3200a', '#1a1a1a')
POKE_WHITE = palette('#ffffff', '#f0f0f0', '#bdbdbd', '#1a1a1a')
BLACK = rgb('#1a1a1a')
SPARKLE = rgb('#ffe14d')
BUBBLE = mask([
    '................',
    '...XXXXXXXXXX...',
    '..XXXXXXXXXXXX..',
    '..XXXXXXXXXXXX..',
    '..XXXXXXXXXXXX..',
    '..XXXXXXXXXXXX..',
    '..XXXXXXXXXXXX..',
    '..XXXXXXXXXXXX..',
    '..XXXXXXXXXXXX..',
    '..XXXXXXXXXXXX..',
    '...XXXXXXXXXX...',
    '.....XXX........',
    '....XX..........',
])
BANG = mask([
    '................',
    '................',
    '.......XX.......',
    '.......XX.......',
    '.......XX.......',
    '.......XX.......',
    '.......XX.......',
    '................',
    '.......XX.......',
    '.......XX.......',
])


def pokeball(angle=0.0, shift=0.0, r=6.5, cx=7.5, cy=7.5):
    """Poké Ball tilted by `angle` degrees, lit from the top left."""
    a = math.radians(angle)
    cx += shift
    lx, ly = cx - r * 0.4, cy - r * 0.4
    cells, img = set(), canvas()
    for y in range(N):
        for x in range(N):
            dx, dy = x - cx, y - cy
            if dx * dx + dy * dy > r * r:
                continue
            u = dx * math.cos(a) + dy * math.sin(a)
            v = -dx * math.sin(a) + dy * math.cos(a)
            hub = math.hypot(u, v)
            if hub < 1.5:
                img[y][x] = rgb('#ffffff')
            elif hub < 2.5 or abs(v) < 0.8:
                img[y][x] = BLACK
            else:
                hi, base, lo, _ = POKE_RED if v < 0 else POKE_WHITE
                d = math.hypot(x - lx, y - ly) / r
                img[y][x] = hi if d < 0.45 else base if d < 1.15 else lo
            cells.add((x, y))
    return outline(img, cells, BLACK)


def caught():
    return paint(pokeball(r=5.5, cx=8.5, cy=8.5), {(2, 0), (1, 1), (2, 1), (3, 1), (2, 2), (14, 2), (1, 13)}, SPARKLE)


def broke_free():
    """The lid pops off and light escapes."""
    ball = pokeball(r=5.5, cy=10.0)
    img = canvas()
    for y in range(N):
        for x in range(N):
            if ball[y][x] is not None:
                img[y - 3 if y < 10 else y][x] = BLACK if y == 9 else ball[y][x]
    glow = {(x, y) for x in range(4, 12) for y in range(7, 10)} | {(2, 6), (13, 6), (1, 8), (14, 8), (2, 10), (13, 10)}
    return paint(img, {c for c in glow if img[c[1]][c[0]] is None}, rgb('#fff6a8'))


def trainer_alert():
    return paint(bevel(BUBBLE, POKE_WHITE), BANG, POKE_RED[1])


def pokeball_template():
    hub = {(x, y) for x in range(N) for y in range(N) if 1.5 <= math.hypot(x - 7.5, y - 7.5) < 2.5}
    band = {(x, y) for x in range(N) for y in (7, 8)} - ellipse(1.5, 1.5)
    return template(ellipse(6.5, 6.5) - hub - band)




def far_seer(session_id):
    # the same pick as wc3.sh's status line: codepoint sum of the session id
    return ['Thrall', "Drek'Thar", 'Rehgar', 'Nazgrel', 'Zuluhed'][sum(map(ord, session_id)) % 5]


def upkeep(food):
    return 'High' if food > 80 else 'Low' if food > 50 else 'No'


def wc3_unit(tier, session_id):
    return {'recruit': 'Recruit', 'peon': 'Peon', 'grunt': 'Grunt', 'farseer': f'Far Seer {far_seer(session_id)}',
            'brewmaster': 'Brewmaster Chen', 'outsider': 'Shadow Hunter'}[tier]


def wc3_food(context):
    return f'Food {context}/100, {upkeep(context)} Upkeep'


def wc3_resources(gold, lumber, food):
    return '    '.join([f'Gold {gold:,}', f'Lumber {lumber:,}'] + ([wc3_food(food)] if food is not None else []))


def dbz_unit(tier, _session_id):
    return {'recruit': 'Saibaman', 'peon': 'Krillin', 'grunt': 'Vegeta', 'farseer': 'Goku',
            'brewmaster': 'Master Roshi', 'outsider': 'Piccolo'}[tier]


def dbz_food(context):
    return f'Ki {100 - context}% left'


def dbz_resources(gold, lumber, food):
    power = "IT'S OVER 9000!" if lumber > 9000 else f'{lumber:,}'
    return '    '.join([f'Power level {power}', f'Wishes {gold:,}'] + ([dbz_food(food)] if food is not None else []))


def pkmn_unit(tier, _session_id):
    return {'recruit': 'Magikarp', 'peon': 'Pikachu', 'grunt': 'Charizard', 'farseer': 'Mewtwo',
            'brewmaster': 'Snorlax', 'outsider': 'Porygon'}[tier]


def pkmn_food(context):
    return f'HP {100 - context}/100'


def pkmn_resources(gold, lumber, food):
    return '    '.join([f'Caught {gold:,}', f'Moves used {lumber:,}'] + ([pkmn_food(food)] if food is not None else []))


THEMES = {
    'wc3': {
        'name': 'Warcraft III',
        'color': '#8a6200,#ffd200',
        'font': 'Copperplate-Bold',
        'frames': [coin(6.5), coin(4.5), coin(1.5), coin(4.5)],
        'icons': {'waiting': bevel(EXCLAIM, GOLD), 'failed': bevel(EXCLAIM, BLOOD), 'done': bevel(QUESTION, GOLD)},
        'idle': template(ellipse(6.5, 6.5) - (ellipse(4.9, 4.9) - ellipse(3.8, 3.8))),
        'unit': wc3_unit,
        'food': wc3_food,
        'resources': wc3_resources,
        'next': 'Next idle worker',
        'empty': 'Ready to work.',
        'labels': {'waiting': 'awaiting orders', 'failed': 'under attack', 'working': 'at work', 'done': 'work complete', 'quiet': 'idle workers'},
        'quotes': {
            'waiting': ['Something need doing?', 'What you want?', 'Yes, me lord?', 'Ready to work!'],
            'failed': ['We are under attack!', 'Our base is under attack!'],
            'working': ['Work, work.', 'Zug zug.', 'Dabu.', 'Me busy. Leave me alone!', 'Okie dokie.'],
            'done': ['Work complete.', "Job's done!", 'More work?'],
            'quiet': ['Idle worker.'],
        },
    },
    'dbz': {
        'name': 'Dragon Ball',
        'color': '#b84d00,#ff9a1f',
        'font': 'AvenirNext-HeavyItalic',
        'frames': [radar(0), radar(1), radar(2), radar(1)],
        'icons': {'waiting': dragon_ball(), 'failed': ki(RAGE), 'done': ki(KI)},
        'idle': template(ellipse(6.5, 6.5) - STAR),
        'unit': dbz_unit,
        'food': dbz_food,
        'resources': dbz_resources,
        'next': 'Track the next Dragon Ball',
        'empty': 'The Dragon Balls are scattered.',
        'labels': {'waiting': 'awaiting your wish', 'failed': "yamcha'd", 'working': 'powering up', 'done': 'wish granted', 'quiet': 'in the time chamber'},
        'quotes': {
            'waiting': ['State your wish.', 'Lend me your energy!', 'Speak, and it shall be granted.'],
            'failed': ["Yamcha'd.", 'Not even a Senzu bean can fix this.'],
            'working': ['Kaaa... meee... haaa... meee...', 'Powering up!', "It's over 9000!", 'Training at 100x gravity.', 'This is not even my final form.'],
            'done': ['HAAAAAA!', 'Your wish has been granted.', 'Senzu bean, anyone?'],
            'quiet': ['Meditating in the Hyperbolic Time Chamber.'],
        },
    },
    'pkmn': {
        'name': 'Pokémon',
        'color': '#2a5db0,#ffcb05',
        'font': 'ArialRoundedMTBold',
        'frames': [pokeball(), pokeball(-25, -0.6), pokeball(), pokeball(25, 0.6), pokeball(), pokeball()],
        'icons': {'waiting': trainer_alert(), 'failed': broke_free(), 'done': caught()},
        'idle': pokeball_template(),
        'unit': pkmn_unit,
        'food': pkmn_food,
        'resources': pkmn_resources,
        'next': 'Next wild encounter',
        'empty': 'No Pokémon in your party.',
        'labels': {'waiting': 'awaiting your move', 'failed': 'fainted', 'working': 'in battle', 'done': 'caught', 'quiet': 'in the PC box'},
        'quotes': {
            'waiting': ['What will you do?', 'A wild question appeared!', 'The trainer wants to battle!'],
            'failed': ['Oh no! It broke free!', 'Your Pokémon fainted!', "It's not very effective..."],
            'working': ['Wobble... wobble...', "It's super effective!", 'A critical hit!', 'Gotta catch ’em all!'],
            'done': ['Gotcha! It was caught!', 'New data added to the Pokédex.', 'You defeated the Elite Four!'],
            'quiet': ['It is fast asleep.'],
        },
    },
}
for _theme in THEMES.values():
    _theme['icons']['quiet'] = grey(_theme['frames'][0])
    _theme['icons']['working'] = _theme['frames'][0]
    _theme['frames'] = [png(f) for f in _theme['frames']]
    _theme['icons'] = {k: png(v) for k, v in _theme['icons'].items()}
    _theme['idle'] = png(_theme['idle'])


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def cached(key, ttl, compute):
    hit = memory.get(key)
    if hit is None or time.time() - hit[0] > ttl:
        memory[key] = hit = (time.time(), compute())
    return hit[1]


def phone_config():
    return {'lan': False, 'port': 47823, **read_json(os.path.join(PHONE, 'config.json'), {})}


TAILSCALE = '/Applications/Tailscale.app/Contents/MacOS/Tailscale'


def server_up():
    def probe():
        try:
            socket.create_connection(('127.0.0.1', phone_config()['port']), timeout=0.1).close()
            return True
        except OSError:
            return False
    return cached('server_up', 5, probe)


def tailscale_state():
    """'off' (signed out or not running), 'ready' (signed in, not serving us) or 'serving'."""
    def look():
        def run(*args):
            return subprocess.run([TAILSCALE, *args], capture_output=True, text=True, timeout=5,
                                  env={**os.environ, 'TAILSCALE_BE_CLI': '1'}).stdout or '{}'  # else it hangs outside a shell
        try:
            status, serve = json.loads(run('status', '--json')), run('serve', 'status', '--json')
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return 'off'
        if status.get('BackendState') != 'Running':
            return 'off'
        return 'serving' if f":{phone_config()['port']}" in serve else 'ready'
    return cached('tailscale', 15, look)


def phone_menu(now):
    away = os.path.exists(os.path.join(PHONE, 'away'))
    held = len([n for n in os.listdir(os.path.join(PHONE, 'waiting')) if n.endswith('.json')]) if os.path.isdir(os.path.join(PHONE, 'waiting')) else 0
    offer, devices, tailscale = read_json(os.path.join(PHONE, 'pairing.json'), None), read_json(os.path.join(PHONE, 'devices.json'), {}), tailscale_state()
    page = f"http://127.0.0.1:{phone_config()['port']}/pair"
    out = [
        'Phone' + (f': away · {held} waiting for you' if away and held else ': away' if away else ''),
        line("--Away mode (agents' questions wait for your phone)", 'stdin=phone:away', 'checked=true' if away else ''),
        line('--Pair a phone…', 'stdin=phone:pair'),
    ]
    if offer and offer['expires'] > now:
        out.append(line(f"--Show the pairing QR code · {offer['code']} · until {time.strftime('%H:%M', time.localtime(offer['expires']))}", f'href={page}', 'size=12'))
    out += [
        '-----',
        line('--Reach from anywhere (Tailscale, phone needs Tailscale too)' if tailscale != 'off' else '--Reach from anywhere: sign in to Tailscale first',
             'stdin=phone:tailscale', 'checked=true' if tailscale == 'serving' else ''),
        line('--Reachable over this Wi-Fi (plain HTTP)', 'stdin=phone:lan', 'checked=true' if phone_config()['lan'] else ''),
    ]
    if devices:
        out.append(line('--Paired devices (⌥ to revoke)', 'size=11'))
        for key, device in devices.items():
            out.append(line(f"--{device['name']} · seen {ago(now - device.get('last_seen', now))} ago"))
            out.append(line(f"--Revoke {device['name']}", 'alternate=true', f'stdin=phone:revoke:{key[:16]}'))
    out.append(line(f"--Server {'running' if server_up() else 'not running'}", 'size=11'))
    return out


def phone_command(arg, now):
    if arg == 'away':
        path = os.path.join(PHONE, 'away')
        remove(path) if os.path.exists(path) else open(path, 'a').close()
    elif arg == 'pair':
        offer = {'secret': secrets.token_urlsafe(18), 'code': f'{secrets.randbelow(10 ** 6):06d}', 'expires': now + 600}
        with open(os.path.join(PHONE, 'pairing.json'), 'w') as f:
            json.dump(offer, f)
        subprocess.Popen(['/usr/bin/open', f"http://127.0.0.1:{phone_config()['port']}/pair"])
    elif arg == 'tailscale':
        state = tailscale_state()
        if state == 'off':
            subprocess.Popen(['/usr/bin/open', '-a', 'Tailscale'])
        else:
            port = str(phone_config()['port'])
            try:
                done = subprocess.run([TAILSCALE, 'serve', '--https=443', 'off'] if state == 'serving' else [TAILSCALE, 'serve', '--bg', port],
                                      capture_output=True, text=True, timeout=30, env={**os.environ, 'TAILSCALE_BE_CLI': '1'})
                consent = re.search(r'https://login\.tailscale\.com/\S+', done.stdout + done.stderr)
            except subprocess.TimeoutExpired as waited:  # serve waits while the tailnet's HTTPS certificates are switched on
                consent = re.search(r'https://login\.tailscale\.com/\S+', str(waited.stdout or '') + str(waited.stderr or ''))
            if consent:
                subprocess.Popen(['/usr/bin/open', consent.group(0)])
        memory.pop('tailscale', None)
    elif arg == 'lan':
        config = phone_config()
        with open(os.path.join(PHONE, 'config.json'), 'w') as f:
            json.dump({**config, 'lan': not config['lan']}, f)
        memory.pop('server_up', None)
    elif arg.startswith('revoke:'):
        devices = read_json(os.path.join(PHONE, 'devices.json'), {})
        with open(os.path.join(PHONE, 'devices.json'), 'w') as f:
            json.dump({k: v for k, v in devices.items() if not k.startswith(arg[7:])}, f)


def muted_until(now):
    """None while sounds play, 0 when muted until turned back on, else the epoch they come back at."""
    try:
        with open(MUTE_FILE) as f:
            text = f.read().strip()
    except FileNotFoundError:
        return None
    if not text:
        return 0
    return float(text) if float(text) > now else None


def tomorrow_morning(now):
    t = time.localtime(now + 86400)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 9, 0, 0, 0, 0, -1))


def theme_key():
    try:
        with open(THEME_FILE) as f:
            key = f.read().strip()
    except FileNotFoundError:
        key = ''
    return key if key in THEMES else 'wc3'


def remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def tier(model, effort):
    """The unit wc3.sh would field for this model; low effort is peon work whatever the model."""
    model = model or ''
    if not model:
        return 'recruit'
    if effort == 'low' or 'haiku' in model:
        return 'peon'
    for family, unit in (('sonnet', 'grunt'), ('opus', 'farseer'), ('fable', 'brewmaster'), ('gpt', 'outsider'), ('codex', 'outsider')):
        if family in model:
            return unit
    return 'grunt'


def read_hooks(now):
    hooks = {}
    for entry in os.scandir(SESSIONS):
        if '.' in entry.name:  # the hook's half-written temp file
            continue
        try:
            beat = entry.stat().st_mtime
            with open(entry.path) as f:
                fields = f.read().rstrip('\n').split('\x1f')
        except FileNotFoundError:
            continue
        if now - beat > FORGET_AFTER:
            remove(entry.path)
            continue
        state, since, name, workspace, doing = (fields + [''] * 5)[:5]
        hooks[entry.name] = Hook(state, float(since or beat), beat, name or entry.name[:8], workspace, doing)
    return hooks


DB_QUERY = '''
    select s.id, s.status, s.unread_count, s.agent_type, s.model, s.claude_effort_level, s.codex_thinking_level, s.title,
           s.context_used_percent, strftime('%s', s.last_user_message_at), strftime('%s', s.updated_at),
           w.id, w.workspace_name, w.user_set_workspace_name, w.pr_title, w.branch, w.directory_name
    from sessions s join workspaces w on w.id = s.workspace_id
    where s.is_hidden = 0 and w.state != 'archived'
      and (s.status in ('working', 'waiting')
           or (s.unread_count > 0 and cast(strftime('%s', s.updated_at) as integer) > ?)
           or s.id in ({}))
'''


UNNAMED_CHATS = {'', 'claude', 'codex', 'untitled', 'start chat', 'new chat'}


def display_name(chat, name, named, pr_title, directory):
    """The chat's own name, else the workspace's (yours, then its PR title, then its folder). Never the branch."""
    if chat and chat.strip().lower() not in UNNAMED_CHATS:
        return chat
    if named and name:
        return name
    return pr_title or directory


def conductor(now, ids):
    """Conductor's view of the hooked sessions plus any it has working or unread, by session id."""
    ids = frozenset(ids)
    if now - memory['db_at'] < DB_EVERY and ids == memory['db_ids']:
        return memory['db_rows']
    try:
        db = sqlite3.connect(f'file:{CONDUCTOR_DB}?mode=ro', uri=True, timeout=0.2)
        try:
            found = db.execute(DB_QUERY.format(','.join('?' * len(ids)) or "''"), [int(now - UNREAD_FOR), *ids]).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return memory['db_rows']
    rows = {}
    for sid, status, unread, agent, model, claude_effort, codex_effort, chat, context, asked, updated, workspace, name, named, pr_title, branch, directory in found:
        rows[sid] = Row(status, unread or 0, agent, model, codex_effort if agent == 'codex' else claude_effort,
                        None if context is None else round(context), float(asked or updated or now), float(updated or now), workspace,
                        display_name(chat, name, named, pr_title, directory), directory)
    memory.update(db_at=now, db_ids=ids, db_rows=rows)
    return rows


def resolve(sid, hook, row, now):
    if row is None:  # outside Conductor the hook file is all there is
        state, since = hook.state, hook.since
        if state == 'working' and now - hook.beat > QUIET_AFTER:
            state, since = 'quiet', hook.beat
        hide = HIDE_QUIET_AFTER if state == 'quiet' else HIDE_DONE_AFTER if state in FINISHED else None
        if state not in ORDER or (hide and now - since > hide):
            return None
        return Session(sid, state, since, hook.name, hook.workspace, hook.doing, 'recruit', None, hook.name)
    hooked = hook.state if hook else None
    doing = hook.doing if hook else ''
    if row.status in ('working', 'waiting'):  # Conductor's waiting is a turn parked on a background task
        state = 'waiting' if hooked == 'waiting' else 'working'
        if row.status == 'waiting' and state == 'working':
            doing = 'Waiting on a background task'
    elif row.unread or (hooked in ('done', 'failed') and now - hook.since < UNREAD_GRACE):
        state = 'failed' if row.status == 'error' or hooked == 'failed' else 'done'
    else:
        return None
    since = hook.since if hooked == state else row.asked if state in ('working', 'waiting') else row.updated
    return Session(sid, state, since, row.name, row.workspace, doing, tier(row.model, row.effort), row.context, row.place)


def load(now):
    hooks = read_hooks(now)
    rows = conductor(now, hooks)
    sessions = []
    for sid in set(hooks) | set(rows):
        s = resolve(sid, hooks.get(sid), rows.get(sid), now)
        if s and memory['dismissed'].get(sid, 0) < s.since:
            sessions.append(s)
    return sorted(sessions, key=lambda s: (ORDER.index(s.state), -s.since))


def tally(day, kind):
    try:
        return os.path.getsize(os.path.join(STATS, f'{day}.{kind}'))
    except FileNotFoundError:
        return 0


def prune_stats(now):
    cutoff = time.strftime('%Y-%m-%d', time.localtime(now - KEEP_STATS_DAYS * 86400))
    for entry in os.scandir(STATS):
        if entry.name[:10] < cutoff:
            remove(entry.path)


def ago(seconds):
    minutes = int(seconds // 60)
    if minutes < 1:
        return 'now'
    return f'{minutes}m' if minutes < 60 else f'{minutes // 60}h {minutes % 60}m'


def clip(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + '…'


def md(text):
    return re.sub(r'([\\`*_\[\]<>#~])', r'\\\1', text.replace('|', '/'))


def line(title, *params):
    return f"{title} | {' '.join(p for p in params if p)}"


def items(s, theme, now):
    quotes = theme['quotes'][s.state]
    quote = quotes[zlib.crc32(f'{s.id}{s.state}'.encode()) % len(quotes)]
    unit = theme['unit'](s.rank, s.id)
    said = s.doing if s.doing and s.state in ('working', 'waiting') else quote
    tip = f'{unit}: “{quote}”' + (f" · {theme['food'](s.context)}" if s.context is not None else '')
    return [
        line(f'**{md(clip(s.name, 48))}**' + (f' · {md(s.place)}' if s.place and s.place != s.name else '') + f'   {md(unit)} · {md(clip(said, 64))}', 'md=true',
             f"image={theme['icons'][s.state]}", f'badge="{ago(now - s.since)}"', f'tooltip="{tip}"',
             f'href=conductor://workspace?id={s.workspace}' if s.workspace else '',
             f'stdin=dismiss:{s.id}' if s.state in FINISHED else ''),
        line(f"Dismiss {clip(s.name, 48).replace('|', '/')}", 'alternate=true', f'stdin=dismiss:{s.id}'),
    ]


def render(sessions, now, tick):
    theme = THEMES[theme_key()]
    style = (f"color={theme['color']}", f"font={theme['font']}")
    counts = {state: sum(s.state == state for s in sessions) for state in ORDER}
    top = next((state for state in ORDER if counts[state] and state != 'quiet'), None)
    if top is None:
        icon = f"templateImage={theme['idle']}"
    elif top == 'working':
        icon = f"image={theme['frames'][tick % len(theme['frames'])]}"
    else:
        icon = f"image={theme['icons'][top]}"
    today = time.strftime('%Y-%m-%d', time.localtime(now))
    food = max((s.context for s in sessions if s.state in ('working', 'waiting') and s.context is not None), default=None)
    out = [
        line(counts['working'] or '', icon, *style, 'dropdown=false'),
        '---',
        line(theme['resources'](tally(today, 'gold'), tally(today, 'lumber'), food), *style, 'size=13'),
        line(' · '.join(f"{counts[state]} {theme['labels'][state]}" for state in ORDER if counts[state]) or theme['empty'], 'size=12'),
    ]
    for state in ORDER:
        group = [s for s in sessions if s.state == state]
        if group:
            out += ['---', line(theme['labels'][state].capitalize(), 'size=11')]
            for s in group:
                out += items(s, theme, now)
    out += ['---', line(theme['next'], 'stdin=next', f'shortcut={NEXT_KEY}')]
    if any(s.state in FINISHED for s in sessions):
        out.append(line('Clear finished', 'stdin=clear'))
    out.append('Theme')
    out += [line(f"--{t['name']}", f'stdin=theme:{key}', 'checked=true' if key == theme_key() else '') for key, t in THEMES.items()]
    silence = muted_until(now)
    out += [
        'Sounds: on' if silence is None else 'Sounds: muted' if silence == 0 else f"Sounds: muted until {time.strftime('%a %H:%M', time.localtime(silence))}",
        line('--On', 'stdin=sound:on', 'checked=true' if silence is None else ''),
        line('--Mute for 1 hour', 'stdin=sound:hour'),
        line('--Mute until tomorrow 9:00', 'stdin=sound:morning'),
        line('--Mute until I turn them on', 'stdin=sound:off', 'checked=true' if silence == 0 else ''),
    ]
    out += phone_menu(now)
    return '\n'.join(out), top == 'working'


def next_idle(sessions):
    """Like WC3's idle-worker key: each press opens the next session that needs you, oldest first."""
    targets = sorted((s for s in sessions if s.state in NEEDS_YOU and s.workspace), key=lambda s: (NEEDS_YOU.index(s.state), s.since))
    if not targets:
        return
    ids = [s.id for s in targets]
    pick = targets[(ids.index(memory['cursor']) + 1) % len(ids) if memory['cursor'] in ids else 0]
    memory['cursor'] = pick.id
    subprocess.Popen(['/usr/bin/open', f'conductor://workspace?id={pick.workspace}'])


def handle(command, now):
    verb, _, arg = command.partition(':')
    if verb == 'dismiss':
        memory['dismissed'][arg] = now
    elif verb == 'clear':
        for s in load(now):
            if s.state in FINISHED:
                memory['dismissed'][s.id] = now
    elif verb == 'next':
        next_idle(load(now))
    elif verb == 'theme' and arg in THEMES:
        with open(THEME_FILE, 'w') as f:
            f.write(arg + '\n')
    elif verb == 'phone':
        phone_command(arg, now)
    elif verb == 'sound' and arg == 'on':
        remove(MUTE_FILE)
    elif verb == 'sound':
        until = {'hour': f'{int(now) + 3600}', 'morning': f'{int(tomorrow_morning(now))}', 'off': ''}[arg]
        with open(MUTE_FILE, 'w') as f:
            f.write(until + '\n')


def main():
    os.makedirs(SESSIONS, exist_ok=True)
    os.makedirs(STATS, exist_ok=True)
    prune_stats(time.time())
    listening, last, tick = True, None, 0
    while True:
        now = time.time()
        frame, animated = render(load(now), now, tick)
        if frame != last:
            sys.stdout.write('~~~\n' + frame + '\n')
            sys.stdout.flush()
            last = frame
        wait = FRAME if animated else IDLE
        if listening and select.select([0], [], [], wait)[0]:
            data = os.read(0, 4096).decode()
            listening = bool(data)  # EOF: SwiftBar closed our stdin
            for command in data.splitlines():
                handle(command.strip(), time.time())
        elif not listening:
            time.sleep(wait)
        tick += 1


if __name__ == '__main__':
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
