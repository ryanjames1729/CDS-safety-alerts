"""National Weather Service alerts: fetch, normalise and classify."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .util import SourceError, http_get_json, locate, parse_time

API = "https://api.weather.gov"

# /O.NEW.KGSP.TO.W.0012.261123T0100Z-261123T0145Z/
_VTEC = re.compile(r"/([OTEX])\.([A-Z]{3})\.([A-Z]{4})\.([A-Z]{2})\.([A-Z])\.(\d{4})\.")
_OVER = {"CAN", "EXP", "UPG"}


@dataclass
class Alert:
    id: str
    key: str  # stable across updates to the same event
    event: str
    severity: str
    headline: str
    description: str
    instruction: str
    onset: datetime | None
    ends: datetime | None
    geometry: dict | None
    params: dict
    references: list[str] = field(default_factory=list)
    upgraded_from: list[str] = field(default_factory=list)
    ended: bool = False  # a cancellation / expiration notice
    has_vtec: bool = False


@dataclass
class Rating:
    tier: int | None  # 1 act now, 2 heads up, 3 brief only, None ignored
    ping: bool
    inside: bool | None  # campus inside polygon; None when the alert has no polygon
    miles: float | None  # distance from campus to the polygon edge


def _first(params: dict, name: str) -> str:
    value = params.get(name)
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value) if isinstance(value, str) else ""


def parse_alert(feature: dict) -> Alert | None:
    props = feature.get("properties") or {}
    alert_id = props.get("id") or feature.get("id")
    event = props.get("event")
    if not alert_id or not event or props.get("status") != "Actual":
        return None  # skips tests, exercises and drafts

    params = props.get("parameters") or {}
    key, upgraded, live = "", [], False
    codes = [m.groups() for m in map(_VTEC.search, params.get("VTEC") or []) if m]
    codes = [c for c in codes if c[0] != "T"]  # T = test product
    for _, action, office, phenomenon, significance, number in codes:
        code = f"{office}.{phenomenon}.{significance}.{number}"
        if action == "UPG":
            upgraded.append(code)
        elif action not in _OVER and not live:
            key, live = code, True
        elif not key:
            key = code

    ended = props.get("messageType") == "Cancel" or (bool(codes) and not live)
    return Alert(
        id=alert_id,
        key=key or alert_id,
        event=event,
        severity=props.get("severity") or "Unknown",
        headline=_first(params, "NWSheadline") or props.get("headline") or "",
        description=props.get("description") or "",
        instruction=props.get("instruction") or "",
        onset=parse_time(props.get("onset") or props.get("effective")),
        ends=parse_time(props.get("ends") or props.get("expires")),
        geometry=feature.get("geometry"),
        params=params,
        references=[r.get("identifier") for r in props.get("references") or [] if r.get("identifier")],
        upgraded_from=[c for c in upgraded if c != key],
        ended=ended,
        has_vtec=bool(codes),
    )


def fetch_alerts(cfg: dict) -> list[Alert]:
    zones = ",".join(cfg["nws"]["zones"])
    data = http_get_json(f"{API}/alerts/active?zone={zones}", cfg["nws"]["user_agent"])
    features = data.get("features")
    if not isinstance(features, list):
        raise SourceError(f"NWS alerts response had no feature list: {data.get('detail') or data.get('title')}")
    seen, alerts = set(), []
    for feature in features:
        alert = parse_alert(feature)
        if alert and alert.id not in seen:
            seen.add(alert.id)
            alerts.append(alert)
    return alerts


def rate(alert: Alert, cfg: dict) -> Rating:
    tiers, site = cfg["tiers"], cfg["site"]
    spot = locate(site["latitude"], site["longitude"], alert.geometry)
    inside, miles = (spot[0], spot[1]) if spot else (None, None)

    if alert.event in tiers.get("ignore", []):
        return Rating(None, False, inside, miles)
    if alert.event in tiers["act_now"]:
        tier = 1
    elif alert.event in tiers["heads_up"]:
        tier = 2
    else:
        tier = 2 if alert.severity in ("Extreme", "Severe") else 3

    ping = tier == 1 and (inside is not False or alert.event in tiers.get("always_ping", []))
    return Rating(tier, ping, inside, miles)
