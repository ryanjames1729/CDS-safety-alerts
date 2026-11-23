"""Decides what to post: compares what the sources say now with what was already reported."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import forecast, messages, ncdot, nws, parkway
from .chat import Chat
from .nws import Alert, Rating
from .util import SourceError, now_utc, parse_time

HEALTH_THREAD = "monitor-health"
_WHAT = re.compile(r"\*\s*WHAT\.\.\.(.*?)(?=\n\s*\*\s*[A-Z]|\Z)", re.S)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _gist(alert: Alert) -> str:
    """Fingerprint of the expected conditions, so reworded reissues stay quiet."""
    match = _WHAT.search(alert.description)
    text = re.sub(r"\s+", " ", match.group(1)).strip().lower() if match else ""
    return hashlib.sha1(text.encode()).hexdigest()[:12] if text else ""


class Monitor:
    def __init__(self, cfg: dict, state: dict, chat: Chat, clock=now_utc, ncdot_key: str = "") -> None:
        self.cfg, self.state, self.chat, self.clock = cfg, state, chat, clock
        self.ncdot_key = ncdot_key if cfg.get("ncdot", {}).get("enabled") else ""
        # False = NCDOT not in use, None = unreachable, else {"events", "conditions"}
        self.roads: dict | None | bool = False
        self.tz = ZoneInfo(cfg["site"]["timezone"])
        self.active: list[tuple[Alert, Rating]] | None = None  # None = NWS unreachable
        self.sections: list[parkway.Section] | None = None
        self.as_of = ""
        self.post_failed = False

    # --- plumbing -----------------------------------------------------------

    def _post(self, text: str, thread: str) -> bool:
        ok = self.chat.post(text, thread)
        self.post_failed = self.post_failed or not ok
        return ok

    def _health(self, name: str, label: str, error: Exception | None) -> None:
        health = self.state.setdefault("health", {})
        entry, now = health.setdefault(name, {}), self.clock()
        if error is None:
            if not entry.get("notified") or self._post(messages.source_up(label), HEALTH_THREAD):
                health.pop(name, None)
            return
        print(f"{label} source error: {error}", flush=True)
        first = parse_time(entry.setdefault("first_fail", _iso(now))) or now
        limit = self.cfg["health"]["notify_after_minutes"]
        if not entry.get("notified") and now - first >= timedelta(minutes=limit):
            if self._post(messages.source_down(label, limit), HEALTH_THREAD):
                entry["notified"] = True

    # --- NWS alerts ---------------------------------------------------------

    def poll_alerts(self, quiet: bool = False) -> None:
        try:
            alerts = nws.fetch_alerts(self.cfg)
        except SourceError as exc:
            self.active = None
            self._health("nws", "National Weather Service", exc)
            return
        self._health("nws", "National Weather Service", None)
        self.process_alerts(alerts, quiet)

    def process_alerts(self, alerts: list[Alert], quiet: bool = False) -> None:
        now, cfg = self.clock(), self.cfg
        events: dict = self.state.setdefault("events", {})

        # Alerts without a VTEC code are tied to their earlier versions by reference.
        owner = {alert_id: key for key, rec in events.items() for alert_id in rec["ids"]}
        current: dict[str, tuple[Alert, Rating]] = {}
        ended: set[str] = set()
        for alert in alerts:
            if not alert.has_vtec:
                alert.key = next((owner[r] for r in [alert.id, *alert.references] if r in owner), alert.key)
            if alert.ended:
                ended.add(alert.key)
                continue
            rating = nws.rate(alert, cfg)
            if rating.tier is None:
                continue
            held = current.get(alert.key)
            if held is None or (rating.inside and not held[1].inside):
                current[alert.key] = (alert, rating)
        self.active = list(current.values())

        for key, (alert, rating) in current.items():
            record = events.get(key)
            fresh = {
                "event": alert.event,
                "tier": rating.tier,
                "ends": _iso(alert.ends),
                "inside": rating.inside,
                "gist": _gist(alert),
                "misses": 0,
            }
            if record is None:
                replaced = [k for k in alert.upgraded_from if k in events]
                if rating.tier <= 2 and not quiet:
                    names = [events[k]["event"] for k in replaced]
                    text = messages.new_alert(alert, rating, cfg, now, self.tz, names)
                    if not self._post(text, key):
                        continue  # not recorded, so the next poll tries again
                events[key] = {**fresh, "ids": [alert.id]}
                for old in replaced:
                    events.pop(old, None)
                continue

            record["misses"] = 0
            if alert.id in record["ids"]:
                continue
            changes, ping = [], False
            if record.get("ends") != fresh["ends"]:
                changes.append("End time has changed")
            if rating.inside and record.get("inside") is False:
                changes.append("Campus is now inside the warning area")
                ping = rating.tier == 1
            elif rating.inside is False and record.get("inside"):
                changes.append("Campus is no longer inside the warning area")
            if fresh["gist"] and fresh["gist"] != record.get("gist"):
                changes.append("Expected conditions have been revised")
            if rating.tier < record["tier"]:
                changes.append("Raised in priority")
            # Storm-based warnings are reissued as the storm moves; always relay those.
            relay = bool(changes) or (rating.tier == 1 and alert.geometry is not None)
            if rating.tier <= 2 and relay and not quiet:
                text = messages.updated_alert(alert, rating, cfg, now, self.tz, changes, ping)
                if not self._post(text, key):
                    continue
            record.update(fresh)
            record["ids"] = (record["ids"] + [alert.id])[-40:]

        for key in [k for k in events if k not in current]:
            record = events[key]
            ends = parse_time(record.get("ends"))
            if key in ended:
                reason = "cancelled or expired by NWS"
            elif ends and now >= ends:
                reason = "expired"
            else:
                # Guard against a one-off feed glitch before declaring an alert over.
                record["misses"] = record.get("misses", 0) + 1
                if record["misses"] < 2:
                    continue
                reason = "no longer listed by NWS"
            if record["tier"] <= 2 and not quiet:
                if not self._post(messages.ended_alert(record["event"], cfg, reason), key):
                    continue
            del events[key]

    # --- Blue Ridge Parkway -------------------------------------------------

    def poll_parkway(self, quiet: bool = False) -> None:
        if not self.cfg["parkway"].get("enabled"):
            return
        try:
            sections, as_of = parkway.fetch_sections(self.cfg)
        except SourceError as exc:
            self.sections = None
            self._health("parkway", "Blue Ridge Parkway road status", exc)
            return
        self._health("parkway", "Blue Ridge Parkway road status", None)
        self.process_parkway(sections, as_of, quiet)

    def process_parkway(self, sections: list[parkway.Section], as_of: str, quiet: bool = False) -> None:
        self.sections, self.as_of = sections, as_of
        before = (self.state.get("parkway") or {}).get("sections")
        if before is not None and not quiet:
            changes = []
            for section in sections:
                old = before.get(section.key)
                if old is None:
                    if section.state == "closed":
                        changes.append((section, ""))
                elif old["state"] != section.state:
                    changes.append((section, old["status"]))
            if changes:
                day = self.clock().astimezone(self.tz).date().isoformat()
                if not self._post(messages.parkway_change(changes, as_of), f"parkway-{day}"):
                    return  # keep the old snapshot so the change is retried
        self.state["parkway"] = {
            "as_of": as_of,
            "sections": {s.key: {"state": s.state, "status": s.status} for s in sections},
        }

    # --- NCDOT roads --------------------------------------------------------

    def poll_roads(self, quiet: bool = False) -> None:
        if not self.ncdot_key:
            return
        try:
            events, conditions = ncdot.fetch(self.cfg, self.ncdot_key)
        except SourceError as exc:
            self.roads = None
            self._health("ncdot", "NCDOT DriveNC", exc)
            return
        self._health("ncdot", "NCDOT DriveNC", None)
        self.process_roads(events, conditions, quiet)

    def process_roads(self, events: list[ncdot.RoadEvent], conditions: dict[str, str], quiet: bool = False) -> None:
        self.roads = {"events": events, "conditions": conditions}
        saved = self.state.get("ncdot")
        quiet = quiet or saved is None  # first sight of this source: note what exists, post nothing
        saved = self.state.setdefault("ncdot", {"events": {}, "conditions": {}})
        known: dict = saved["events"]

        notable = {e.id: e for e in events if ncdot.is_notable(e, self.cfg)}
        for event_id, event in notable.items():
            if event_id in known:
                continue
            if not quiet and not self._post(messages.road_event(event), f"ncdot-{event_id}"):
                continue
            known[event_id] = {"where": event.where, "summary": event.description[:200]}
        listed = {e.id: e for e in events}
        for event_id in [i for i in known if i not in notable]:
            gone, still = known[event_id], listed.get(event_id)
            # Still closed but no longer something we post about (a settings
            # change, say): forget it without announcing a reopening.
            silent = quiet or (still is not None and still.is_closure)
            text = messages.road_event_cleared(gone["where"], gone["summary"])
            if silent or self._post(text, f"ncdot-{event_id}"):
                del known[event_id]

        if conditions != saved["conditions"]:
            day = self.clock().astimezone(self.tz).date().isoformat()
            text = messages.road_conditions_change(conditions, saved["conditions"], self.cfg["site"]["county"])
            if quiet or self._post(text, f"ncdot-conditions-{day}"):
                saved["conditions"] = conditions

    # --- briefs -------------------------------------------------------------

    def due_briefs(self) -> list[str]:
        local, briefs, due = self.clock().astimezone(self.tz), self.cfg["briefs"], []
        sent = self.state.setdefault("briefs", {})
        for kind in ("morning", "evening"):
            hour, minute = (int(part) for part in briefs[f"{kind}_time"].split(":"))
            start = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            late = timedelta(minutes=briefs["max_late_minutes"])
            if (
                local.weekday() in briefs[f"{kind}_days"]
                and start <= local < start + late
                and sent.get(kind) != local.date().isoformat()
            ):
                due.append(kind)
        return due

    def send_brief(self, kind: str, forced: bool = False) -> None:
        now = self.clock()
        today = now.astimezone(self.tz).date().isoformat()
        try:
            outlook = forecast.fetch_outlook(self.cfg, self.state, now, 16 if kind == "evening" else 14)
        except SourceError as exc:
            print(f"Forecast unavailable: {exc}", flush=True)
            outlook = None

        if not forced:
            # Scheduled briefs only go out when there is something to flag.
            alerting = any(rating.tier <= 2 for _, rating in self.active or [])
            if not alerting and outlook is None:
                return  # cannot judge yet; try again on the next run
            if not alerting and not outlook.notable:
                self.state.setdefault("briefs", {})[kind] = today
                print(f"{kind.title()} brief: nothing to flag, not sent.", flush=True)
                return

        text = messages.brief(
            kind, self.cfg, now, self.tz, self.active, outlook, self.sections, self.as_of, self.roads
        )
        if kind == "status":
            self._post(text, f"status-{now:%Y%m%dT%H%M%S}")
        elif self._post(text, f"brief-{kind}-{today}"):
            self.state.setdefault("briefs", {})[kind] = today

    # --- one pass -----------------------------------------------------------

    def run(self, full: bool = True) -> None:
        """One polling pass. `full` adds the slower-changing sources and briefs."""
        if not self.state.get("started"):
            self.poll_alerts(quiet=True)
            self.poll_parkway(quiet=True)
            self.poll_roads(quiet=True)
            text = messages.startup(self.cfg, self.clock(), self.tz, self.active, self.sections, self.as_of)
            if self._post(text, "monitor-start"):
                self.state["started"] = True
            return
        self.poll_alerts()
        if full:
            self.poll_parkway()
            self.poll_roads()
            for kind in self.due_briefs():
                self.send_brief(kind)
