#!/usr/bin/python3
"""Away mode for agent sessions: while ~/.claude/agent-status/phone/away exists, hold an agent's question
(AskUserQuestion, PreToolUse) open until the phone answers, then hand the answer to the agent. Run through
phone-relay.sh, which returns at once when away mode is off.

Finished turns are never held: the phone reaches idle chats directly (see phone/server.py). Anything typed in
Conductor wins: a message queued for this chat there releases the hold, and the question shows in Conductor.

The phone server reads waiting/<sid>.json to show the question and writes inbox/<sid>.json with the answer.
"""
import json
import os
import sqlite3
import sys
import time

PHONE = os.path.expanduser('~/.claude/agent-status/phone')
AWAY = os.path.join(PHONE, 'away')
CONDUCTOR_DB = os.path.expanduser('~/Library/Application Support/com.conductor.app/conductor.db')
HOLD = 55 * 60  # under the hook's 3600s timeout, so the question reaches Conductor when nobody answers


def take(path):
    try:
        with open(path) as f:
            reply = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    os.remove(path)
    return reply


def typed_in_conductor(sid):
    """A message you typed in Conductor for this chat, still queued behind the hold."""
    try:
        db = sqlite3.connect(f'file:{CONDUCTOR_DB}?mode=ro', uri=True, timeout=1)
        try:
            return db.execute("select 1 from session_messages where session_id = ? and role = 'user' and sent_at is null and cancelled_at is null limit 1",
                              (sid,)).fetchone() is not None
        finally:
            db.close()
    except sqlite3.Error:
        return False


def main():
    hook = json.load(sys.stdin)
    sid = os.path.basename(hook.get('session_id') or '')
    if not sid or hook.get('agent_id') or not os.path.exists(AWAY) or 'AskUserQuestion' not in hook.get('tool_name', ''):
        return
    inbox, marker = os.path.join(PHONE, 'inbox', f'{sid}.json'), os.path.join(PHONE, 'waiting', f'{sid}.json')
    take(inbox)  # an answer must be to this question, not something queued earlier
    with open(marker, 'w') as f:
        json.dump({'kind': 'question', 'questions': (hook.get('tool_input') or {}).get('questions', []), 'pid': os.getpid(),
                   'since': time.time(), 'workspace': os.environ.get('CONDUCTOR_WORKSPACE_NAME', '')}, f)
    try:
        deadline = time.time() + HOLD
        while time.time() < deadline and os.path.exists(AWAY) and not typed_in_conductor(sid):
            reply = take(inbox)
            if reply:
                print(json.dumps({'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': f"The user answered this question from their phone instead of in the dialog: {reply['text']}\n\n"
                                                "Conductor does not display this message, so begin your reply with one line quoting it, prefixed 'From your phone:'.",
                }}))
                return
            time.sleep(1)
    finally:
        if os.path.exists(marker):
            os.remove(marker)


if __name__ == '__main__':
    main()
