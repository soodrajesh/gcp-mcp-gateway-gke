"""Turn the agent's JSON-lines event log (stdin) into a readable transcript."""
import json, sys

for line in sys.stdin:
    try:
        e = json.loads(line)
    except ValueError:
        continue
    ev = e.get("event")
    if ev == "start":
        print(f"agent starts as OAuth client '{e['client']}' -> gateway {e['gateway']}  (model {e['model']})")
    elif ev == "token_obtained":
        print("  1. token obtained from the IdP")
    elif ev == "tools_available":
        print(f"  2. the gateway offers {e['count']} tools to this identity: {', '.join(e['names'])}")
    elif ev == "question":
        print(f"  3. question: {e['text']}")
    elif ev == "tool_call":
        print(f"  -> model asks for {e['tool']}({', '.join(f'{k}={v!r}' for k, v in e['args'].items())})")
    elif ev == "tool_result":
        print(f"  <- {'REFUSED/ERROR ' if e['refused'] else ''}{e['tool']}: {e['preview'][:150]}")
    elif ev == "answer":
        print(f"\nANSWER ({e.get('steps', '?')} model steps):\n{e['text']}")
