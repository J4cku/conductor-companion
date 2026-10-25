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

SwiftBar writes a clicked item's stdin= text back to this process: dismiss:<session>, clear, next, theme:<name>,
sound:<on|hour|morning|off>, volume:<level>.
"""
import base64
import math
import os
import re
import json
import select
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zlib
from collections import namedtuple
from datetime import datetime

HOME = os.path.expanduser('~/.claude/agent-status')
SESSIONS = os.path.join(HOME, 'sessions')
STATS = os.path.join(HOME, 'stats')
THEME_FILE = os.path.join(HOME, 'theme')
MUTE_FILE = os.path.join(HOME, 'mute')  # read by wc3.sh: empty = muted until turned back on, an epoch = muted until then
VOLUME_FILE = os.path.join(HOME, 'volume')  # read by wc3.sh: afplay's volume, 1 = the clip as recorded
SOUNDS_DIR = os.path.expanduser('~/.claude/sounds')
VOLUMES = (('Loud', 1.0), ('Normal', 0.5), ('Quiet', 0.25), ('Whisper', 0.1))
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
USAGE_EVERY = 300  # seconds between checks of Claude's plan usage
WARN_AT = 80  # a usage window this full shows its percentage next to the menu bar icon
CODEX_SESSIONS = os.path.expanduser('~/.codex/sessions')

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
AMBER = palette('#ffe2a6', '#ffa31a', '#c46400', '#3a1c00')
EMBER = palette('#ffc2a0', '#ff6a1f', '#b53800', '#2a0d00')


def coin(rx, pal=GOLD):
    return bevel(ellipse(rx, 6.5), pal, ellipse(rx - 2.4, 4.1) if rx > 3 else ())


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


def star_spots(n):
    """Where an n-star Dragon Ball's stars go: a ring around the centre, the seventh in the middle."""
    ring = min(n, 6)
    radius = {2: 2.4, 3: 2.8, 4: 3.1, 5: 3.7, 6: 4.1, 7: 4.2}[n]
    spots = [(round(7.5 + radius * math.cos(math.radians(-90 + 360 * k / ring + (45 if n == 4 else 0)))),
              round(7.5 + radius * math.sin(math.radians(-90 + 360 * k / ring + (45 if n == 4 else 0))))) for k in range(ring)]
    return spots + ([(8, 8)] if n == 7 else [])


def dragon_ball(stars=1, glow=False):
    """An n-star Dragon Ball; `glow` lights its rim, as when the radar finds it."""
    img = sphere(DRAGON)
    if glow:
        paint(img, {(x, y) for y in range(N) for x in range(N) if img[y][x] == DRAGON[3]}, rgb('#fff36a'))
    if stars == 1:
        return paint(img, STAR, rgb('#d42020'))
    plus = {(x + dx, y + dy) for x, y in star_spots(stars) for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))}
    return paint(img, plus, rgb('#d42020'))


POKE_RED = palette('#ff8a70', '#e3350d', '#a3200a', '#1a1a1a')
POKE_WHITE = palette('#ffffff', '#f0f0f0', '#bdbdbd', '#1a1a1a')
GREAT_BLUE = palette('#8fb4ff', '#2f6fe0', '#1c449a', '#1a1a1a')
ULTRA_BLACK = palette('#6a6a6a', '#2c2c2c', '#141414', '#1a1a1a')
MASTER_PURPLE = palette('#c79bff', '#7e33c9', '#4e1585', '#1a1a1a')
BALL_TOPS = {'poke': POKE_RED, 'great': GREAT_BLUE, 'ultra': ULTRA_BLACK, 'master': MASTER_PURPLE}


def ball_marking(kind, u, v):
    """The marks on a ball's top half, in its own (rotated) coordinates: Great Ball's red wings, Ultra Ball's yellow H,
    Master Ball's pink bumps and white M."""
    if kind == 'great' and abs(u) >= 3.3 and -4.6 < v < -1.6:
        return POKE_RED[1]
    if kind == 'ultra' and 1.5 <= abs(u) <= 2.9 and v < -1.0:
        return rgb('#ffd21f')
    if kind == 'master':
        if math.hypot(abs(u) - 3.3, v + 2.6) < 1.15:
            return rgb('#ff6fae')
        if abs(u) < 1.6 and -4.8 < v < -2.9 and (abs(u) > 0.9 or v < -4.0 or abs(u) < 0.3):
            return rgb('#ffffff')
    return None
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


def pokeball(angle=0.0, shift=0.0, r=6.5, cx=7.5, cy=7.5, kind='poke'):
    """Poké Ball (or Great, Ultra, Master Ball by `kind`) tilted by `angle` degrees, lit from the top left."""
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
                hi, base, lo, _ = BALL_TOPS[kind] if v < 0 else POKE_WHITE
                d = math.hypot(x - lx, y - ly) / r
                img[y][x] = (v < 0 and ball_marking(kind, u, v)) or (hi if d < 0.45 else base if d < 1.15 else lo)
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
# How full Claude's plan is (the fuller of its 5-hour and weekly windows) picks the icon: (from %, name, working frames).
THEMES['wc3']['tiers'] = [(at, name, [coin(rx, pal) for rx in (6.5, 4.5, 1.5, 4.5)])
                          for at, name, pal in ((0, 'No upkeep', GOLD), (50, 'Low upkeep', AMBER), (70, 'High upkeep', EMBER), (90, 'Out of gold', BLOOD))]
THEMES['pkmn']['tiers'] = [(at, name, [pokeball(angle, shift, kind=kind) for angle, shift in ((0, 0), (-25, -0.6), (0, 0), (25, 0.6), (0, 0), (0, 0))])
                           for at, name, kind in ((0, 'Poké Ball', 'poke'), (40, 'Great Ball', 'great'), (70, 'Ultra Ball', 'ultra'), (90, 'Master Ball', 'master'))]
THEMES['dbz']['tiers'] = [(at, f'{stars}-star ball', [dragon_ball(stars, glow) for glow in (False, False, True, True)])
                          for stars, at in zip(range(1, 8), (0, 15, 30, 45, 60, 75, 90))]
for _theme in THEMES.values():
    _theme['tiers'] = [(at, name, [png(f) for f in frames]) for at, name, frames in _theme['tiers']]
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


def volume():
    try:
        with open(VOLUME_FILE) as f:
            return float(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 1.0


def stop_sounds():
    """Silences the clip playing right now, not just the next ones."""
    subprocess.run(['/usr/bin/pkill', '-f', f'afplay .*{SOUNDS_DIR}/'], capture_output=True)


def preview_volume(level):
    """One line from the current theme at the new volume, so you hear what you picked."""
    folder = os.path.join(SOUNDS_DIR, theme_key(), 'Stop')
    clips = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(('.wav', '.mp3'))] if os.path.isdir(folder) else []
    if clips:
        stop_sounds()
        subprocess.Popen(['/usr/bin/afplay', '-v', str(level), clips[int(time.time()) % len(clips)]])


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


usage = {'claude': None}  # filled by watch_claude_usage


def fetch_claude_usage():
    """Claude's 5-hour and weekly plan windows, from the endpoint Claude Code's /usage reads, signed in with Claude Code's own
    Keychain token. Undocumented: if it changes or you're signed out, the menu just leaves the line out."""
    raw = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    token = (json.loads(raw).get('claudeAiOauth') or {}).get('accessToken') if raw else None
    if not token:
        return None
    request = urllib.request.Request('https://api.anthropic.com/api/oauth/usage', headers={
        'Authorization': f'Bearer {token}', 'anthropic-beta': 'oauth-2025-04-20', 'User-Agent': 'conductor-companion'})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    windows = []
    for key, label in (('five_hour', '5h'), ('seven_day', 'week')):
        window = body.get(key) or {}
        if window.get('utilization') is not None:
            resets = datetime.fromisoformat(window['resets_at']).timestamp() if window.get('resets_at') else None
            windows.append((label, float(window['utilization']), resets))
    return windows or None


def watch_claude_usage():
    while True:
        try:
            usage['claude'] = fetch_claude_usage()
        except Exception:  # offline, signed out or the endpoint changed: leave the line out rather than show stale numbers
            usage['claude'] = None
        time.sleep(USAGE_EVERY)


def newest_codex_log():
    """The most recently written Codex session log (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl), from the last two days' folders."""
    days = []
    for year in sorted(os.listdir(CODEX_SESSIONS))[-2:] if os.path.isdir(CODEX_SESSIONS) else []:
        for month in sorted(os.listdir(os.path.join(CODEX_SESSIONS, year)))[-2:]:
            days += [os.path.join(CODEX_SESSIONS, year, month, day) for day in sorted(os.listdir(os.path.join(CODEX_SESSIONS, year, month)))[-2:]]
    logs = [entry for day in sorted(days)[-2:] for entry in os.scandir(day) if entry.name.endswith('.jsonl')]
    return max(logs, key=lambda entry: entry.stat().st_mtime).path if logs else None


def codex_usage(now):
    """Codex's plan windows as its newest session log last reported them; a window that has since reset counts as empty."""
    def look():
        try:
            path = newest_codex_log()
            if not path:
                return None
            with open(path, 'rb') as f:
                f.seek(max(0, os.path.getsize(path) - 262144))
                tail = f.read().decode('utf-8', 'replace')
        except OSError:
            return None
        limits = None
        for raw in reversed(tail.splitlines()):
            if '"rate_limits"' in raw:
                try:
                    limits = json.loads(raw)['payload']['rate_limits']
                    break
                except (ValueError, KeyError, TypeError):
                    continue
        windows = []
        for key in ('primary', 'secondary'):
            window = (limits or {}).get(key) or {}
            if window.get('used_percent') is None:
                continue
            minutes = window.get('window_minutes') or 0
            label = '5h' if minutes == 300 else 'week' if minutes == 10080 else f'{round(minutes / 60)}h'
            resets = window.get('resets_at')
            windows.append((label, float(window['used_percent']) if not resets or resets > now else 0.0, resets))
        return windows or None
    return cached('codex_usage', 60, look)


def when(epoch, now):
    if time.localtime(epoch)[:3] == time.localtime(now)[:3]:
        return time.strftime('%H:%M', time.localtime(epoch))
    return time.strftime('%a %H:%M' if epoch - now < 7 * 86400 else '%b %d', time.localtime(epoch))


def usage_windows(now):
    return [('Claude', usage['claude'], 'https://claude.ai/settings/usage'), ('Codex', codex_usage(now), 'https://chatgpt.com/codex/settings/usage')]


def claude_tier(theme):
    """The theme's icon tier for how full Claude's plan is, or None while that's unknown."""
    if not usage['claude']:
        return None
    fullest = max(used for _, used, _ in usage['claude'])
    return next(tier for tier in reversed(theme['tiers']) if fullest >= tier[0])


def usage_lines(now, tier):
    out = []
    for name, windows, href in usage_windows(now):
        if windows:
            text = '    '.join(f'{label} {used:.0f}%' + (f' · resets {when(resets, now)}' if resets else '') for label, used, resets in windows)
            text += f'    · {tier[1]}' if name == 'Claude' and tier else ''
            hot = max(used for _, used, _ in windows) >= WARN_AT
            out.append(line(f'{name}   {text}', f'href={href}', 'size=12', 'color=#c2410c,#fb923c' if hot else ''))
    return out


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
    tier = claude_tier(theme)
    if top is None:
        icon = f"image={tier[2][0]}" if tier else f"templateImage={theme['idle']}"
    elif top == 'working':
        frames = tier[2] if tier else theme['frames']
        icon = f"image={frames[tick % len(frames)]}"
    else:
        icon = f"image={theme['icons'][top]}"
    today = time.strftime('%Y-%m-%d', time.localtime(now))
    food = max((s.context for s in sessions if s.state in ('working', 'waiting') and s.context is not None), default=None)
    fullest = max((used for _, windows, _ in usage_windows(now) for _, used, _ in windows or []), default=0)
    title = ' '.join(str(part) for part in (counts['working'] or '', f'{fullest:.0f}%' if fullest >= WARN_AT else '') if part)
    out = [
        line(title, icon, *style, 'dropdown=false'),
        '---',
        line(theme['resources'](tally(today, 'gold'), tally(today, 'lumber'), food), *style, 'size=13'),
        line(' · '.join(f"{counts[state]} {theme['labels'][state]}" for state in ORDER if counts[state]) or theme['empty'], 'size=12'),
        *usage_lines(now, tier),
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
    loudness = next((name.lower() for name, level in VOLUMES if abs(volume() - level) < 0.01), 'loud')
    status = f'Playing · {loudness}' if silence is None else 'Muted' if silence == 0 else f"Muted until {time.strftime('%a %H:%M', time.localtime(silence))}"
    out += [
        'Sounds',  # fixed title: SwiftBar 2.1.1 greys out a submenu whose own title changes (fixed after it, swiftbar#518)
        line(f'--{status}', 'size=11'),
        '-----',
        line('--On', 'stdin=sound:on', 'checked=true' if silence is None else ''),
        line('--Mute for 1 hour', 'stdin=sound:hour'),
        line('--Mute until tomorrow 9:00', 'stdin=sound:morning'),
        line('--Mute until I turn them on', 'stdin=sound:off', 'checked=true' if silence == 0 else ''),
        '-----',
    ]
    out += [line(f'--Volume: {name}', f'stdin=volume:{level}', 'checked=true' if abs(volume() - level) < 0.01 else '') for name, level in VOLUMES]
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
    elif verb == 'sound' and arg == 'on':
        remove(MUTE_FILE)
    elif verb == 'sound':
        until = {'hour': f'{int(now) + 3600}', 'morning': f'{int(tomorrow_morning(now))}', 'off': ''}[arg]
        with open(MUTE_FILE, 'w') as f:
            f.write(until + '\n')
        stop_sounds()
    elif verb == 'volume' and arg in {str(level) for _, level in VOLUMES}:
        with open(VOLUME_FILE, 'w') as f:
            f.write(arg + '\n')
        if muted_until(now) is None:
            preview_volume(float(arg))


def main():
    os.makedirs(SESSIONS, exist_ok=True)
    os.makedirs(STATS, exist_ok=True)
    prune_stats(time.time())
    threading.Thread(target=watch_claude_usage, daemon=True).start()
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
