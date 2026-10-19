#!/bin/sh
# Away mode off: return at once (this runs on every Stop). On: phone-relay.py holds the turn for the phone.
[ -e "$HOME/.claude/agent-status/phone/away" ] || exit 0
exec /usr/bin/python3 "$HOME/.claude/hooks/phone-relay.py"
