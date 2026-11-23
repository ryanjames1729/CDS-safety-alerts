"""Shared helpers: HTTP, time parsing and formatting, small geometry."""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


class SourceError(Exception):
    """A data source could not be read or returned something unusable."""


def http_get(url: str, user_agent: str, accept: str | None = None, timeout: int = 20, retries: int = 2) -> bytes:
    headers = {"User-Agent": user_agent}
    if accept:
        headers["Accept"] = accept
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise SourceError(f"GET {url.split('?')[0]} failed: {last}")


def http_get_json(url: str, user_agent: str, accept: str = "application/geo+json") -> dict:
    raw = http_get(url, user_agent, accept=accept)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SourceError(f"{url.split('?')[0]} returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SourceError(f"{url.split('?')[0]} returned unexpected JSON")
    return data


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_interval(value: str) -> tuple[datetime, datetime] | None:
    """Parse an NWS grid 'validTime' such as 2026-11-22T18:00:00+00:00/PT6H."""
    start_text, _, duration_text = value.partition("/")
    start = parse_time(start_text)
    match = _DURATION.match(duration_text)
    if not start or not match or not duration_text or duration_text == "P":
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return start, start + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def fmt_clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d} {suffix}" if dt.minute else f"{hour} {suffix}"


def fmt_when(dt: datetime | None, now: datetime, tz) -> str:
    """'3:45 PM today', '6 AM tomorrow' or '6 AM Wed Nov 25', in local time."""
    if dt is None:
        return "further notice"
    local, today = dt.astimezone(tz), now.astimezone(tz).date()
    if local.date() == today:
        day = "today"
    elif local.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = f"{local:%a %b} {local.day}"
    return f"{fmt_clock(local)} {day}"


def unwrap(text: str | None) -> str:
    """Join hard-wrapped lines within paragraphs; keep paragraph breaks."""
    if not text:
        return ""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return "\n\n".join(re.sub(r"\s+", " ", p).strip() for p in paragraphs if p.strip())


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


# --- geometry (lon/lat pairs, GeoJSON order) --------------------------------

def _rings(geometry: dict | None) -> list[list[list[float]]]:
    """Outer rings of a GeoJSON Polygon or MultiPolygon."""
    if not geometry:
        return []
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        return [coords[0]] if coords else []
    if geometry.get("type") == "MultiPolygon":
        return [poly[0] for poly in coords if poly]
    return []


def _in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _miles_to_ring(lon: float, lat: float, ring: list[list[float]]) -> float:
    # Local flat-earth projection in miles, centred on the point; fine at county scale.
    kx = 69.172 * math.cos(math.radians(lat))
    ky = 69.0
    pts = [((p[0] - lon) * kx, (p[1] - lat) * ky) for p in ring]
    best = math.inf
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        dx, dy = x2 - x1, y2 - y1
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def locate(lat: float, lon: float, geometry: dict | None) -> tuple[bool, float] | None:
    """Return (inside, miles_to_edge) for a point against an alert polygon.

    None when the alert has no polygon (it applies to the whole zone/county).
    """
    rings = [r for r in _rings(geometry) if len(r) >= 3]
    if not rings:
        return None
    inside = any(_in_ring(lon, lat, r) for r in rings)
    return inside, min(_miles_to_ring(lon, lat, r) for r in rings)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
