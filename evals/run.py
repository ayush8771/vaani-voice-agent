"""
evals/run.py — the multilingual evaluation set, run against the real LLM.

Every case is a scripted conversation (English, Hindi or Hinglish) with the
outcome it must produce: the booking written, the reply language, the tools
called, or that nothing was booked. Each case gets a fresh database.

    python -m evals.run                         # all cases, model from .env
    python -m evals.run --lang hi               # one language
    python -m evals.run --model sarvam-30b --base-url https://api.sarvam.ai/v1
    python -m evals.run --case hi-04 --verbose  # one case, print the dialogue

Results are printed and saved to evals/results/<date>_<model>.md.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TTS_ENABLED", "false")
os.environ.setdefault("RAG_AUTOBUILD", "false")
os.environ.setdefault("OTP_DEV_ECHO", "true")

from config import settings  # noqa: E402

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _resolve_date(spec: str) -> str:
    import availability as av
    today = av.today()
    if spec.startswith("+"):
        return (today + timedelta(days=int(spec[1:]))).isoformat()
    target = WEEKDAYS.index(spec)
    return (today + timedelta(days=(target - today.weekday()) % 7)).isoformat()


def _reply_lang(reply: str) -> str:
    import i18n
    return i18n.detect(reply)


def run_case(case: dict, verbose: bool = False) -> dict:
    import brain
    from database import connection, init_db

    db = Path(tempfile.mkdtemp()) / "eval.db"
    settings.db_path = str(db)
    init_db()

    state, reply, transcript = {}, "", []
    started = time.time()
    turn_times = []
    for turn in case["turns"]:
        t = time.time()
        reply, _audio, state = brain.get_ai_response(turn, state, stt_language=case.get("code"), speak=False)
        turn_times.append(time.time() - t)
        transcript.append((turn, reply))
    elapsed = time.time() - started

    with connection() as con:
        booked = con.execute("SELECT date, time, people, name FROM bookings WHERE status='confirmed'").fetchall()
        tools = [r[0] for r in con.execute("SELECT tool FROM tool_calls ORDER BY id")]

    expect, failures = case["expect"], []
    if "booking" in expect:
        b = expect["booking"]
        want = (_resolve_date(b["date"]), b["time"], b["people"], b["name"])
        if booked != [want]:
            failures.append(f"booking {booked} != {[want]}")
    if expect.get("no_booking") and booked:
        failures.append(f"unexpected booking {booked}")
    for tool in expect.get("tools", []):
        if tool not in tools:
            failures.append(f"tool {tool} not called (called: {tools})")
    if "reply_any" in expect and not any(s.lower() in reply.lower() for s in expect["reply_any"]):
        failures.append(f"reply lacks any of {expect['reply_any']}")
    want_lang = expect.get("reply_lang")
    if want_lang:
        got = _reply_lang(reply)
        # an English-only fixed reply (e.g. a phone number) is fine for Hinglish
        if got != want_lang and not (want_lang == "hinglish" and got == "en"):
            failures.append(f"reply language {got} != {want_lang}")

    if verbose:
        for said, answered in transcript:
            print(f"    YOU  : {said}\n    VAANI: {answered}")
    return {"id": case["id"], "lang": case["lang"], "passed": not failures, "failures": failures,
            "seconds": elapsed, "slowest_turn": max(turn_times), "turns": len(case["turns"]),
            "final_reply": reply}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run Vaani's multilingual evaluation set")
    parser.add_argument("--lang", choices=["en", "hi", "hinglish", "kn"])
    parser.add_argument("--case")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.model:
        settings.llm_model = args.model
    if args.base_url:
        settings.llm_base_url = args.base_url.rstrip("/")
    logging.basicConfig(level=logging.ERROR)

    cases = json.loads((ROOT / "evals" / "cases.json").read_text(encoding="utf-8"))
    if args.lang:
        cases = [c for c in cases if c["lang"] == args.lang]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]

    import brain
    brain.warm_up_llm()

    results = []
    for case in cases:
        r = run_case(case, verbose=args.verbose)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['id']:7} {r['seconds']:6.1f}s  {'; '.join(r['failures'])}", flush=True)

    lines = [f"# Vaani evaluation — {settings.llm_model}", "",
             "| Language | Passed | Pass rate | Avg seconds / conversation | Slowest turn (s) |",
             "|---|---|---|---|---|"]
    for lang in ("en", "hi", "hinglish", "kn", None):
        subset = [r for r in results if lang is None or r["lang"] == lang]
        if not subset:
            continue
        passed = sum(r["passed"] for r in subset)
        lines.append(f"| {lang or '**All**'} | {passed}/{len(subset)} | {passed / len(subset):.0%} | "
                     f"{sum(r['seconds'] for r in subset) / len(subset):.1f} | "
                     f"{max(r['slowest_turn'] for r in subset):.1f} |")
    failed = [r for r in results if not r["passed"]]
    if failed:
        lines += ["", "## Failures", ""] + [f"- **{r['id']}**: {'; '.join(r['failures'])}" for r in failed]
    report = "\n".join(lines)
    print("\n" + report)

    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(exist_ok=True)
    name = f"{time.strftime('%Y-%m-%d')}_{settings.llm_model.replace(':', '-').replace('/', '-')}.md"
    (out_dir / name).write_text(report + "\n", encoding="utf-8")
    print(f"\nSaved evals/results/{name}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
