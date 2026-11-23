"""Post messages to a Google Chat space through an incoming webhook."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

MAX_TEXT = 3900  # Chat rejects text over 4096 characters


class Chat:
    """Sends text messages; messages sharing a thread key land in one thread."""

    def __init__(self, webhook_url: str | None, dry_run: bool = False) -> None:
        self.url = webhook_url
        self.dry_run = dry_run or not webhook_url
        self.sent: list[tuple[str, str]] = []
        self._last = 0.0

    def post(self, text: str, thread: str) -> bool:
        """Return True once the message is delivered (or printed in a dry run)."""
        text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT].rstrip() + "…"
        thread = re.sub(r"[^A-Za-z0-9_.-]", "-", thread)[:120]
        if self.dry_run:
            print(f"\n--- would post (thread {thread}) ---\n{text}\n---", flush=True)
            self.sent.append((thread, text))
            return True

        sep = "&" if "?" in self.url else "?"
        url = f"{self.url}{sep}messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
        body = json.dumps({"text": text, "thread": {"threadKey": thread}}).encode()
        for attempt in range(4):
            # Webhooks allow roughly one message per second per space.
            wait = 1.1 - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json; charset=UTF-8"}
                )
                with urllib.request.urlopen(req, timeout=20):
                    self.sent.append((thread, text))
                    return True
            except urllib.error.HTTPError as exc:
                # Never print the exception itself: it can carry the webhook URL.
                print(f"Chat post failed: HTTP {exc.code}", flush=True)
                if exc.code < 500 and exc.code != 429:
                    return False
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"Chat post failed: {type(exc).__name__}", flush=True)
            time.sleep(2 ** attempt)
        return False
