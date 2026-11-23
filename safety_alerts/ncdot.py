"""NCDOT DriveNC: traffic events and Snow & Ice road conditions for the county.

API reference: https://www.drivenc.gov/developers/doc (needs a developer key;
limited to ten calls a minute).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from .util import SourceError, http_get

EVENTS_URL = "https://www.drivenc.gov/api/v2/get/event"
CONDITIONS_URL = "https://www.drivenc.gov/api/v3/get/roadconditions"
MAP_URL = "https://www.drivenc.gov"

# Field names in the Snow & Ice feed, with the labels shown in Chat.
CONDITION_FIELDS = [
    ("Overall for Public Display", "Overall"),
    ("Interstates", "Interstates"),
    ("US/NC Routes", "US/NC routes"),
    ("Secondary Roads", "Secondary roads"),
]
_NO_REPORT = {"", "no report", "none", "n/a"}


@dataclass
class RoadEvent:
    id: str
    road: str
    direction: str
    description: str
    kind: str  # roadwork, closures or accidentsAndIncidents
    original_kind: str
    subtype: str
    full_closure: bool
    severity: str
    lanes: str
    comment: str
    reported: int  # Unix time

    @property
    def is_closure(self) -> bool:
        return self.full_closure or self.kind == "closures"

    @property
    def planned(self) -> bool:
        """Scheduled roadwork, as opposed to a weather or incident closure."""
        return "roadwork" in (self.kind, self.original_kind)

    @property
    def where(self) -> str:
        skip = {"", "unknown", "none"}
        return self.road if self.direction.lower() in skip else f"{self.road} {self.direction}"


def _get_list(url: str, key: str, user_agent: str) -> list[dict]:
    try:
        raw = http_get(f"{url}?{urlencode({'key': key, 'format': 'json'})}", user_agent, accept="application/json")
    except SourceError as exc:
        if "HTTP Error 400" in str(exc):
            raise SourceError("NCDOT rejected the request (HTTP 400): check the NCDOT_API_KEY secret") from exc
        raise
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SourceError(f"{url} returned invalid JSON") from exc
    if not isinstance(data, list) or not data or not all(isinstance(item, dict) for item in data):
        # The statewide lists are never legitimately empty; treat that as a fault
        # rather than as "every road is clear".
        detail = data.get("Message") or data.get("message") if isinstance(data, dict) else None
        raise SourceError(f"{url} returned no usable list{': ' + str(detail) if detail else ''}")
    return data


def _in_county(item: dict, county: str) -> bool:
    return str(item.get("County") or "").strip().lower() == county.lower()


def parse_events(items: list[dict], county: str) -> list[RoadEvent]:
    if not any("ID" in item and "EventType" in item for item in items):
        raise SourceError("NCDOT events feed is missing the expected fields")
    events = []
    for item in items:
        if "ID" not in item or not _in_county(item, county):
            continue
        text = lambda name: str(item.get(name) or "").strip()
        events.append(
            RoadEvent(
                id=str(item["ID"]),
                road=text("RoadwayName") or "Road",
                direction=text("DirectionOfTravel"),
                # Descriptions end with an internal reference such as "Id: 876".
                description=re.sub(r"\s+", " ", re.sub(r"\s*Id:\s*\d+\s*$", "", text("Description"))),
                kind=text("EventType"),
                original_kind=text("OriginalEventType"),
                subtype=text("EventSubType"),
                full_closure=item.get("IsFullClosure") is True,
                severity=text("Severity") or "Unknown",
                lanes=text("LanesAffected"),
                comment=text("Comment"),
                reported=int(item.get("Reported") or item.get("StartDate") or 0),
            )
        )
    return events


def parse_conditions(items: list[dict], county: str) -> dict[str, str]:
    """Return {label: condition} for the county; empty when NCDOT has no report."""
    if not any("County" in item and CONDITION_FIELDS[0][0] in item for item in items):
        raise SourceError("NCDOT road conditions feed is missing the expected fields")
    rows = [item for item in items if _in_county(item, county)]
    if not rows:
        raise SourceError(f"NCDOT road conditions feed has no entry for {county} County")
    row = max(rows, key=lambda item: item.get("LastUpdated") or 0)
    found = {}
    for name, label in CONDITION_FIELDS:
        value = str(row.get(name) or "").strip()
        if value.lower() not in _NO_REPORT:
            found[label] = value
    return found


def is_ignored(event: RoadEvent, cfg: dict) -> bool:
    return event.road in cfg["ncdot"].get("ignore_roads", [])


def is_notable(event: RoadEvent, cfg: dict) -> bool:
    """Worth its own message: a weather or incident closure, or an incident at a listed severity.

    Scheduled roadwork closures are left to the summaries unless the config says otherwise.
    """
    if is_ignored(event, cfg):
        return False
    if event.is_closure:
        return not event.planned or bool(cfg["ncdot"].get("post_roadwork_closures"))
    return event.kind == "accidentsAndIncidents" and event.severity in cfg["ncdot"]["incident_severities"]


def summarise(events: list[RoadEvent], cfg: dict) -> tuple[list[RoadEvent], list[RoadEvent]]:
    """Split into (weather and incident items, scheduled roadwork closures)."""
    kept = [e for e in events if not is_ignored(e, cfg)]
    severities = cfg["ncdot"]["incident_severities"]
    unplanned = [
        e for e in kept if (e.is_closure and not e.planned) or (e.kind == "accidentsAndIncidents" and e.severity in severities)
    ]
    planned = [e for e in kept if e.is_closure and e.planned]
    return unplanned, planned


def fetch(cfg: dict, key: str) -> tuple[list[RoadEvent], dict[str, str]]:
    agent, county = cfg["nws"]["user_agent"], cfg["ncdot"]["county"]
    events = parse_events(_get_list(EVENTS_URL, key, agent), county)
    conditions = parse_conditions(_get_list(CONDITIONS_URL, key, agent), county)
    return events, conditions
