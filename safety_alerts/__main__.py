"""Command line entry point: python3 -m safety_alerts"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from collections import Counter
from pathlib import Path

from . import messages
from .chat import Chat
from .engine import Monitor


def load_state(path: Path | None) -> dict:
    if path and path.exists() and path.stat().st_size:
        try:
            state = json.loads(path.read_text())
            if isinstance(state, dict):
                return state
        except ValueError:
            print("State file unreadable; starting fresh.", flush=True)
    return {}


def save_state(path: Path | None, state: dict) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="safety_alerts", description=__doc__)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--state", help="JSON file remembering what has already been reported")
    ap.add_argument("--dry-run", action="store_true", help="print messages instead of posting them")
    ap.add_argument("--loop-seconds", type=int, default=0, help="keep polling NWS alerts for this long")
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls when looping")
    ap.add_argument(
        "--brief",
        choices=["status", "morning", "evening"],
        help="post this summary now, then exit ('status' is the on-demand conditions update)",
    )
    ap.add_argument("--guide", action="store_true", help="post the how-to-read-these-alerts message, then exit")
    ap.add_argument("--test-message", action="store_true", help="post a test message, then exit")
    args = ap.parse_args(argv)

    with open(args.config, "rb") as handle:
        cfg = tomllib.load(handle)

    webhook = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "").strip()
    chat = Chat(webhook, dry_run=args.dry_run)
    state_path = Path(args.state) if args.state else None
    if not webhook and not args.dry_run:
        print("GOOGLE_CHAT_WEBHOOK_URL is not set: printing messages only, and not saving state.", flush=True)
        state_path_out = None
    else:
        state_path_out = state_path

    if args.test_message:
        ok = chat.post(
            f"🧪 *Test message* from the {cfg['site']['name']} safety alert monitor. No action needed.",
            "monitor-test",
        )
        return 0 if ok else 1

    if args.guide:
        return 0 if chat.post(messages.guide(cfg), "monitor-guide") else 1

    state = load_state(state_path)
    ncdot_key = os.environ.get("NCDOT_API_KEY", "").strip()
    if cfg.get("ncdot", {}).get("enabled") and not ncdot_key:
        print("NCDOT_API_KEY is not set: skipping NCDOT road data.", flush=True)

    if args.brief:
        # A brief on demand reads the sources but leaves the saved state alone.
        monitor = Monitor(cfg, json.loads(json.dumps(state)), chat, ncdot_key=ncdot_key)
        monitor.poll_alerts(quiet=True)
        monitor.poll_parkway(quiet=True)
        monitor.poll_roads(quiet=True)
        monitor.send_brief(args.brief, forced=True)
    else:
        monitor = Monitor(cfg, state, chat, ncdot_key=ncdot_key)
        deadline = time.monotonic() + args.loop_seconds
        first = True
        while True:
            monitor.run(full=first)
            first = False
            save_state(state_path_out, state)
            if time.monotonic() + args.interval > deadline:
                break
            time.sleep(args.interval)

    if not args.brief:
        events = state.get("events", {})
        print(f"Done. Tracking {len(events)} active alert(s); {len(chat.sent)} message(s) sent.", flush=True)
    if isinstance(monitor.roads, dict):
        kinds = Counter(f"{e.kind}/{e.severity}{'/full closure' if e.full_closure else ''}" for e in monitor.roads["events"])
        print(f"NCDOT county events: {dict(kinds) or 'none'}", flush=True)
        print(f"NCDOT road conditions: {monitor.roads['conditions'] or 'no report'}", flush=True)
    if monitor.post_failed:
        print("One or more Chat posts failed.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
