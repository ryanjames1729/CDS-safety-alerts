"""Wording of everything posted to Chat."""

from __future__ import annotations

import re
from datetime import datetime

from .forecast import Outlook, describe_spans
from .ncdot import MAP_URL, RoadEvent
from .nws import Alert, Rating
from .parkway import Section
from .util import fmt_clock, fmt_when, truncate, unwrap

_STARRED = re.compile(r"\*\s*([A-Z][A-Z ]+?)\.\.\.(.*?)(?=\n\s*\*\s*[A-Z][A-Z ]+?\.\.\.|\Z)", re.S)
_TAGGED = re.compile(r"^(HAZARD|IMPACT|IMPACTS)\.\.\.(.*?)(?=\n\s*\n|\Z)", re.S | re.M)
_THREATS = [
    ("tornadoDetection", "Tornado: {}"),
    ("tornadoDamageThreat", "Tornado damage threat: {}"),
    ("thunderstormDamageThreat", "Damage threat: {}"),
    ("flashFloodDetection", "Flash flooding: {}"),
    ("flashFloodDamageThreat", "Flood damage threat: {}"),
    ("maxWindGust", "Wind gusts to {}"),
    ("maxHailSize", "Hail to {} in"),
]


def forecast_link(cfg: dict) -> str:
    site = cfg["site"]
    return f"https://forecast.weather.gov/MapClick.php?lat={site['latitude']}&lon={site['longitude']}"


def _where(rating: Rating) -> str:
    if rating.inside is None:
        return ""
    if rating.inside:
        return "*Campus is INSIDE the warning area.*"
    return f"Campus is outside the warning area (about {max(1, round(rating.miles))} mi from its edge)."


def _timing(alert: Alert, now: datetime, tz) -> str:
    until = f"until {fmt_when(alert.ends, now, tz)}"
    if alert.onset and alert.onset > now:
        return f"From {fmt_when(alert.onset, now, tz)} {until}."
    return f"In effect {until}."


def _details(alert: Alert) -> list[str]:
    text, lines = alert.description.strip(), []
    starred = {name.strip(): unwrap(body) for name, body in _STARRED.findall(text)}
    if starred:
        for name in ("WHAT", "WHEN", "IMPACTS", "IMPACT"):
            if starred.get(name):
                lines.append(f"• *{name.title()}:* {truncate(starred[name], 450)}")
    else:
        paragraphs = [p for p in unwrap(text).split("\n\n") if p]
        if paragraphs and not re.match(r"[A-Z]+\.\.\.", paragraphs[0]):
            lines.append(truncate(paragraphs[0], 450))
        for name, body in _TAGGED.findall(text):
            lines.append(f"• *{name.title()}:* {truncate(unwrap(body), 300)}")
    threats = []
    for param, label in _THREATS:
        value = alert.params.get(param)
        value = value[0] if isinstance(value, list) and value else value
        if value and str(value) not in ("0.00", "0"):
            text = str(value)
            shout = text.isupper() and not any(ch.isdigit() for ch in text)
            threats.append(label.format(text.title() if shout else text))
    if threats:
        lines.append("• " + " · ".join(threats))
    return lines


def new_alert(alert: Alert, rating: Rating, cfg: dict, now: datetime, tz, upgraded: list[str]) -> str:
    icon = "🚨" if rating.tier == 1 else "⚠️"
    mention = cfg["chat"].get("mention", "") if rating.ping else ""
    lines = [f"{icon} *{alert.event.upper()}* · {cfg['site']['county']} {mention}".rstrip()]
    if _where(rating):
        lines.append(_where(rating))
    lines.append(_timing(alert, now, tz))
    if upgraded:
        lines.append(f"Upgraded from {', '.join(upgraded)}.")
    lines += _details(alert)
    if alert.instruction.strip():
        lines.append(f"*What to do:* {truncate(unwrap(alert.instruction).replace("\n\n", " "), 500)}")
    lines.append(f"<{forecast_link(cfg)}|NWS forecast and full alert text for campus>")
    return "\n".join(lines)


def updated_alert(alert: Alert, rating: Rating, cfg: dict, now: datetime, tz, changes: list[str], ping: bool) -> str:
    mention = cfg["chat"].get("mention", "") if ping else ""
    lines = [f"🔄 *Update: {alert.event}* {mention}".rstrip()]
    if _where(rating):
        lines.append(_where(rating))
    lines.append(_timing(alert, now, tz))
    lines += [f"• {c}" for c in changes]
    if alert.headline:
        lines.append(f"_{truncate(unwrap(alert.headline), 300)}_")
    lines += _details(alert)[:3]
    return "\n".join(lines)


def ended_alert(event: str, cfg: dict, reason: str) -> str:
    return (
        f"✅ *{event}* is no longer in effect for {cfg['site']['county']} ({reason}).\n"
        "Other alerts may still be active; see the latest messages in this space."
    )


def parkway_change(changes: list[tuple[Section, str]], as_of: str) -> str:
    lines = ["🚧 *Blue Ridge Parkway status change*"]
    for section, before in changes:
        note = f" — {truncate(section.note, 160)}" if section.note else ""
        was = f" (was: {before})" if before else ""
        lines.append(f"• {section.mileposts}, {truncate(section.crossroads, 90)}: *{section.status}*{was}{note}")
    if as_of:
        lines.append(f"_NPS road status as of {as_of}_")
    return "\n".join(lines)


def road_event(event: RoadEvent) -> str:
    title = "Road closure" if event.is_closure else "Road incident"
    lines = [f"🚧 *{title} — {event.where}* (NCDOT)", truncate(event.description, 500)]
    facts = [f"Lanes: {event.lanes}"] if event.lanes else []
    if event.severity.lower() not in ("unknown", "none", ""):
        facts.append(f"Severity: {event.severity}")
    if facts:
        lines.append("• " + " · ".join(facts))
    if event.comment:
        lines.append(f"• {truncate(event.comment, 300)}")
    lines.append(f"<{MAP_URL}|DriveNC map>")
    return "\n".join(lines)


def road_event_cleared(where: str, summary: str) -> str:
    return f"✅ *Cleared or reopened:* {where} — NCDOT no longer lists this as a closure or major incident.\n_{summary}_"


def road_conditions_change(now: dict[str, str], before: dict[str, str], county: str) -> str:
    lines = [f"🧊 *NCDOT road conditions changed — {county}*"]
    for label in dict.fromkeys([*now, *before]):
        new, old = now.get(label, "No report"), before.get(label, "No report")
        lines.append(f"• {label}: *{new}*" + (f" (was: {old})" if new != old else ""))
    lines.append(f"<{MAP_URL}|DriveNC map>")
    return "\n".join(lines)


def _road_lines(roads: dict | None, cfg: dict, now: datetime) -> list[str]:
    """`roads` is {"events": [...], "conditions": {...}}, or None when unavailable."""
    if roads is None:
        return ["🚗 *NCDOT roads:* unavailable"]
    conditions = roads["conditions"]
    summary = " · ".join(f"{label}: {value}" for label, value in conditions.items()) or "no report"
    lines = [f"🚗 *NCDOT road conditions:* {summary}"]
    events = [e for e in roads["events"] if e.is_closure or e.severity in cfg["ncdot"]["incident_severities"]]
    if not events:
        return lines + ["🚧 *NCDOT closures and major incidents:* none"]
    cutoff = now.timestamp() - cfg["ncdot"]["list_days"] * 86400
    recent = sorted((e for e in events if e.reported >= cutoff), key=lambda e: -e.reported)
    older = len(events) - len(recent)
    lines.append(f"🚧 *NCDOT closures and major incidents:* {len(events)}")
    for event in recent[:6]:
        lines.append(f"• {event.where}: {truncate(event.description, 140)}")
    hidden = older + max(0, len(recent) - 6)
    if hidden:
        lines.append(f"• plus {hidden} more, mostly long-standing (<{MAP_URL}|DriveNC map>)")
    return lines


_ALERTS_DOWN = "🔔 *Active alerts:* unavailable (NWS feed error)"


def _sections(parts: list[list[str]]) -> str:
    """Join groups of lines with a blank line between groups."""
    return "\n\n".join("\n".join(lines) for lines in parts if lines)


def _alert_lines(active: list[tuple[Alert, Rating]], now: datetime, tz) -> list[str]:
    if not active:
        return ["🔔 *Active alerts:* none"]
    lines = ["🔔 *Active alerts:*"]
    for alert, rating in sorted(active, key=lambda pair: pair[1].tier or 9):
        icon = {1: "🚨", 2: "⚠️"}.get(rating.tier, "•")
        timing = _timing(alert, now, tz).rstrip(".")
        lines.append(f"{icon} {alert.event} — {timing[0].lower()}{timing[1:]}")
    return lines


def _parkway_lines(sections: list[Section] | None, as_of: str, cfg: dict) -> list[str]:
    park = cfg["parkway"]
    label = f"🏞️ *Blue Ridge Parkway (MP {park['milepost_min']:g}–{park['milepost_max']:g}):*"
    if sections is None:
        return [f"{label} status unavailable"]
    closed = [s for s in sections if s.state == "closed"]
    if not closed:
        lines = [f"{label} all {len(sections)} sections open"]
    else:
        lines = [f"{label} {len(closed)} of {len(sections)} sections closed"]
        for s in closed:
            note = f" ({truncate(s.note, 80)})" if s.note else ""
            lines.append(f"• {s.mileposts}, {truncate(s.crossroads, 80)}{note}")
    if as_of:
        lines.append(f"_NPS status as of {as_of}_")
    return lines


def _forecast_lines(outlook: Outlook | None, show_hours: list[int], tz) -> list[str]:
    if outlook is None:
        return ["🌡️ *Campus forecast:* unavailable"]
    lines, shown = ["🌡️ *Campus forecast*"], set()
    for hour in outlook.hours:
        local = hour.start.astimezone(tz)
        if local.hour in show_hours and local.hour not in shown:
            shown.add(local.hour)
            temp = f"{hour.temp:.0f}°F" if hour.temp is not None else "—"
            wind = f", wind {hour.wind}" if hour.wind else ""
            lines.append(f"• {fmt_clock(local)} {local:%a}: {temp}, {hour.summary}, precip {hour.pop:.0f}%{wind}")
    low = f"{outlook.low:.0f}°F" if outlook.low is not None else "—"
    gust = f"{outlook.gust_mph:.0f} mph" if outlook.gust_mph is not None else "—"
    last = outlook.hours[-1].start.astimezone(tz)
    lines.append(
        f"📊 *Through {fmt_clock(last)} {last:%a}:* low {low} · snow {outlook.snow_in:.1f} in · "
        f"ice {outlook.ice_in:.2f} in · peak gust {gust}"
    )
    if outlook.wintry_hours:
        lines.append(f"❄️ *Wintry or freezing precipitation possible:* {describe_spans(outlook.wintry_hours, tz)}")
    return lines


def brief(
    kind: str,
    cfg: dict,
    now: datetime,
    tz,
    active: list[tuple[Alert, Rating]] | None,
    outlook: Outlook | None,
    sections: list[Section] | None,
    as_of: str,
    roads: dict | None | bool = False,
) -> str:
    local = now.astimezone(tz)
    if kind == "status":
        title = f"📋 *Conditions update — {local:%A} {fmt_clock(local)}* (requested)"
        hours = [(local.hour + step) % 24 for step in (0, 2, 4, 7, 10, 13)]
    elif kind == "morning":
        title, hours = f"🌅 *Morning safety brief — {local:%A, %b} {local.day}*", [6, 8, 10, 12, 15, 18]
    else:
        title, hours = f"🌙 *Evening outlook for tomorrow morning — {local:%A, %b} {local.day}*", [21, 0, 4, 6, 8, 10]
    parts = [
        [title, f"_{cfg['site']['name']}, {cfg['site']['county']}_"],
        [_ALERTS_DOWN] if active is None else _alert_lines(active, now, tz),
        _forecast_lines(outlook, hours, tz),
    ]
    if roads is not False:  # False = NCDOT source not in use
        parts.append(_road_lines(roads, cfg, now))
    if cfg["parkway"].get("enabled"):
        parts.append(_parkway_lines(sections, as_of, cfg))
    parts.append([f"🔗 <{forecast_link(cfg)}|Full NWS forecast for campus>"])
    return _sections(parts)


def startup(cfg: dict, now: datetime, tz, active: list[tuple[Alert, Rating]] | None, sections, as_of: str) -> str:
    parts = [
        [
            f"✅ *Safety alert monitor is online* for {cfg['site']['name']}, {cfg['site']['county']}.",
            "🚨 act-now alerts ping this space · ⚠️ heads-up alerts post quietly · "
            "updates and end-of-alert notices reply in the same thread.",
        ],
        [_ALERTS_DOWN] if active is None else _alert_lines(active, now, tz),
    ]
    if cfg["parkway"].get("enabled"):
        parts.append(_parkway_lines(sections, as_of, cfg))
    return _sections(parts)


def guide(cfg: dict) -> str:
    """The 'how to read these alerts' message."""
    county = cfg["site"]["county"]
    morning, evening = (
        fmt_clock(datetime.strptime(cfg["briefs"][f"{kind}_time"], "%H:%M")) for kind in ("morning", "evening")
    )
    lines = [
        "ℹ️ *How to read these alerts*",
        "🚨 *Act now* — a warning that calls for action: tornado, severe thunderstorm, flash flood, winter storm "
        "or ice storm. Pings everyone here. Storm warnings say whether campus is inside the warning area.",
        "⚠️ *Heads up* — a watch or advisory worth knowing about. No ping.",
        "🔄 *Update* and ✅ *ended* notices reply in the same thread as the alert they belong to.",
    ]
    if cfg.get("ncdot", {}).get("enabled"):
        lines.append(f"🚧 *Road closure* — NCDOT reports a full closure or major incident in {county}.")
        lines.append("🧊 *Road conditions* — NCDOT's snow and ice report for the county has changed.")
    if cfg["parkway"].get("enabled"):
        lines.append("🚧 *Parkway* — a Blue Ridge Parkway section around Asheville has opened or closed.")
    lines += [
        f"🌅 🌙 *Briefs* — summaries at {morning} and {evening}, sent only when "
        "an alert is active or winter weather is in the forecast. No message means nothing to flag.",
        "📋 *Conditions update* — current alerts, campus forecast and roads, posted when someone asks for it.",
        "🛠️ *Monitor notice* — one of the data sources is unreachable; check it directly until it clears.",
    ]
    if cfg["chat"].get("commands"):
        lines.append("⌨️ *Commands* — type `/update` for current conditions or `/help` for this guide.")
    lines += [
        "_This supplements phone emergency alerts and a NOAA weather radio; it does not replace them. "
        "Alerts here can lag the official warning by several minutes._",
    ]
    return "\n".join(lines)


def source_down(name: str, minutes: int) -> str:
    return (
        f"🛠️ *Monitor notice:* the {name} feed has been unreachable for {minutes}+ minutes. "
        "Alerts from it may be delayed or missing; check the source directly until this clears."
    )


def source_up(name: str) -> str:
    return f"🛠️ *Monitor notice:* the {name} feed is reachable again."
