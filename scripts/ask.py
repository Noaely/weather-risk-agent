"""Send questions to a running backend and print answer, tool calls, latency and token usage.

    .venv/Scripts/python scripts/ask.py "Which Midwest hubs are most exposed to winter disruption?"
    .venv/Scripts/python scripts/ask.py --examples     # the client's 4 example questions + a follow-up
    .venv/Scripts/python scripts/ask.py --examples --quiet   # use before a demo to warm the prompt cache
"""

from __future__ import annotations

import argparse
import json

import httpx

EXAMPLES = [
    "Which hubs in the Midwest are most exposed to winter disruption?",
    "Compare Miami and Houston in terms of hurricane and flood exposure.",
    "What percentage of days in Denver last year had snowfall?",
    "Why is the Dallas hub's weather disruption risk high?",
]
FOLLOW_UP = "And how does Kansas City compare on flood?"  # asked in the same session as the first question


def ask(client: httpx.Client, url: str, q: str, session_id: str | None, quiet: bool) -> str:
    r = client.post(f"{url}/chat", json={"message": q, "session_id": session_id})
    r.raise_for_status()
    d = r.json()
    calls = ", ".join(f"{c['name']}({json.dumps(c['input'])})" for c in d["tool_calls"] if c["name"] != "submit_answer")
    u = d["usage"]
    print(f"\nQ: {q}\n   {d['latency_ms'] / 1000:.1f}s | {d['rounds']} model calls | model {d['model']} | "
          f"tokens in {u.get('input_tokens')} (+cache read {u.get('cache_read_input_tokens')}, "
          f"cache write {u.get('cache_creation_input_tokens')}) out {u.get('output_tokens')}")
    print(f"   tools: {calls or '-'}")
    if d["error"]:
        print(f"   ERROR: {d['error']}")
    elif not quiet:
        a = d["answer"]
        g = d["grounding"]
        print(f"   grounding: scores[] ok={g['ok']} {g['ungrounded'] or ''} | "
              f"numbers in text not found in any tool result: {g['text_unverified'] or 'none'}")
        print("   ANSWER:\n" + "\n".join("     " + line for line in a["answer"].splitlines()))
        print(f"   confidence: {a['confidence']}")
        print(f"   scores: {[(s['hub_id'], s['name'], s['value'], s['unit']) for s in a['scores']]}")
        print(f"   assumptions: {a['assumptions']}")
        print(f"   data_sources: {a['data_sources']}")
    return d["session_id"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("question", nargs="*")
    p.add_argument("--examples", action="store_true")
    p.add_argument("--quiet", action="store_true", help="print only timing and tools")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    args = p.parse_args()

    with httpx.Client(timeout=180) as client:
        print(client.get(f"{args.url}/health").json())
        if args.examples:
            first = ask(client, args.url, EXAMPLES[0], None, args.quiet)
            ask(client, args.url, FOLLOW_UP, first, args.quiet)   # follow-up: same session
            for q in EXAMPLES[1:]:
                ask(client, args.url, q, None, args.quiet)
        session = None
        for q in args.question:
            session = ask(client, args.url, q, session, args.quiet)


if __name__ == "__main__":
    main()
