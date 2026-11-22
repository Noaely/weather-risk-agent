"""Smoke-test a running deployment (local production mode or the live URL) before sharing the link.

    python scripts/smoke_test.py --url https://<your-service>.onrender.com --code <ACCESS_CODE>
    python scripts/smoke_test.py --url http://127.0.0.1:8003 --code <code> --free   # skip the paid step

Free checks: the UI is served, /health, the access code is enforced on /access AND on /chat called
directly (no UI), and no API key appears in the shipped JavaScript. Paid step (~$0.02): one question and
a follow-up in the same session. Exit code 1 if anything fails.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import httpx

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--code", required=True, help="the ACCESS_CODE set on the host")
    p.add_argument("--free", action="store_true", help="skip the paid question + follow-up")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    base = args.url.rstrip("/")
    c = httpx.Client(timeout=180)

    print(f"Smoke test {base}")
    t = time.perf_counter()
    h = c.get(f"{base}/health")
    first = time.perf_counter() - t
    body = h.json() if h.status_code == 200 else {}
    check("/health responds", h.status_code == 200, f"{first:.1f}s for the first request (includes any cold start)")
    check("database loaded, period 2016-2025", body.get("status") == "ok" and body.get("period") == "2016-2025",
          f"status={body.get('status')}, period={body.get('period')}, hubs={body.get('hubs')}")
    check("API key is set on the host", body.get("api_key_set") is True)
    check("access code required", body.get("access_code_required") is True)
    print(f"        rate limiter keys on: {body.get('client_ip_source')}  "
          "(true-client-ip = per-visitor; socket = all visitors share one limit)")

    page = c.get(f"{base}/")
    check("UI served at /", page.status_code == 200 and 'id="root"' in page.text)
    leaked = []
    for src in re.findall(r'src="(/assets/[^"]+\.js)"', page.text):
        js = c.get(f"{base}{src}").text
        if re.search(r"sk-ant-[A-Za-z0-9_-]{10,}", js):
            leaked.append(src)
    check("no API key in the shipped JavaScript", not leaked, ", ".join(leaked))

    q = {"message": "Which hubs in the Midwest are most exposed to winter disruption?"}
    check("/chat without code -> 401 (direct call, no UI)", c.post(f"{base}/chat", json=q).status_code == 401)
    check("/chat with wrong code -> 401",
          c.post(f"{base}/chat", json=q, headers={"X-Access-Code": "definitely-wrong"}).status_code == 401)
    check("/access with the right code -> 200",
          c.post(f"{base}/access", headers={"X-Access-Code": args.code}).status_code == 200)

    if not args.free:
        hdr = {"X-Access-Code": args.code}
        t = time.perf_counter()
        r1 = c.post(f"{base}/chat", json=q, headers=hdr)
        d1 = r1.json() if r1.status_code == 200 else {}
        check("question answered", r1.status_code == 200 and d1.get("answer") is not None,
              f"{time.perf_counter() - t:.1f}s, error={d1.get('error') or r1.text[:120] if not d1.get('answer') else None}")
        if d1.get("answer"):
            g = d1.get("grounding") or {}
            check("answer grounded", g.get("ok") and not g.get("text_unverified"), str(g.get("text_unverified")))
            t = time.perf_counter()
            r2 = c.post(f"{base}/chat", headers=hdr, json={"message": "And how does Kansas City compare on flood?",
                                                            "session_id": d1["session_id"]})
            d2 = r2.json() if r2.status_code == 200 else {}
            check("follow-up answered in the same session",
                  r2.status_code == 200 and d2.get("session_id") == d1["session_id"] and d2.get("answer") is not None,
                  f"{time.perf_counter() - t:.1f}s")
            if d2.get("answer"):
                print("        follow-up answer: " + d2["answer"]["answer"][:160].replace("\n", " ") + "…")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f" — FAILED: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
