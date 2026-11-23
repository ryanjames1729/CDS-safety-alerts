"""NWS point forecast for campus: hourly conditions plus snow, ice and wind totals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .nws import API
from .util import SourceError, fmt_clock, http_get_json, parse_interval, parse_time

_WINTRY = re.compile(r"\b(snow|sleet|freezing|ice|icy|wintry|flurr)", re.I)


@dataclass
class Hour:
    start: datetime
    temp: float | None
    pop: float
    summary: str
    wind: str

    @property
    def wintry(self) -> bool:
        if _WINTRY.search(self.summary):
            return True
        return self.temp is not None and self.temp <= 32 and self.pop >= 30


@dataclass
class Outlook:
    hours: list[Hour]  # hourly forecast across the window
    low: float | None
    snow_in: float
    ice_in: float
    gust_mph: float | None

    @property
    def wintry_hours(self) -> list[Hour]:
        return [h for h in self.hours if h.wintry]

    @property
    def notable(self) -> bool:
        return self.snow_in >= 0.1 or self.ice_in >= 0.01 or bool(self.wintry_hours)


def grid_urls(cfg: dict, state: dict) -> dict:
    """Forecast URLs for campus, looked up once and remembered."""
    site = cfg["site"]
    where = f"{site['latitude']:.4f},{site['longitude']:.4f}"
    cached = state.get("grid") or {}
    if cached.get("where") == where and cached.get("hourly") and cached.get("grid"):
        return cached
    props = http_get_json(f"{API}/points/{where}", cfg["nws"]["user_agent"]).get("properties") or {}
    if not props.get("forecastHourly") or not props.get("forecastGridData"):
        raise SourceError("NWS point lookup returned no forecast grid for the campus location")
    state["grid"] = {"where": where, "hourly": props["forecastHourly"], "grid": props["forecastGridData"]}
    return state["grid"]


def _total(series: dict | None, start: datetime, end: datetime) -> float:
    """Sum an accumulation series over [start, end], prorating partial overlap."""
    total = 0.0
    for item in (series or {}).get("values") or []:
        span, value = parse_interval(item.get("validTime", "")), item.get("value")
        if not span or value is None:
            continue
        lo, hi = max(span[0], start), min(span[1], end)
        if hi > lo:
            total += value * ((hi - lo) / (span[1] - span[0]))
    return total


def _extreme(series: dict | None, start: datetime, end: datetime, pick) -> float | None:
    values = []
    for item in (series or {}).get("values") or []:
        span, value = parse_interval(item.get("validTime", "")), item.get("value")
        if span and value is not None and span[1] > start and span[0] < end:
            values.append(value)
    return pick(values) if values else None


def fetch_outlook(cfg: dict, state: dict, start: datetime, hours: int) -> Outlook:
    urls, agent = grid_urls(cfg, state), cfg["nws"]["user_agent"]
    end = start + timedelta(hours=hours)

    periods = (http_get_json(urls["hourly"], agent).get("properties") or {}).get("periods")
    if not periods:
        raise SourceError("NWS hourly forecast returned no periods")
    hourly = []
    for p in periods:
        begin = parse_time(p.get("startTime"))
        if not begin or begin + timedelta(hours=1) <= start or begin >= end:
            continue
        temp = p.get("temperature")
        if temp is not None and p.get("temperatureUnit") == "C":
            temp = temp * 9 / 5 + 32
        pop = (p.get("probabilityOfPrecipitation") or {}).get("value") or 0
        hourly.append(Hour(begin, temp, pop, p.get("shortForecast") or "", p.get("windSpeed") or ""))
    if not hourly:
        raise SourceError("NWS hourly forecast does not cover the requested window")

    grid = http_get_json(urls["grid"], agent).get("properties") or {}
    gust = _extreme(grid.get("windGust"), start, end, max)  # km/h
    temps = [h.temp for h in hourly if h.temp is not None]
    return Outlook(
        hours=hourly,
        low=min(temps) if temps else None,
        snow_in=_total(grid.get("snowfallAmount"), start, end) / 25.4,  # mm
        ice_in=_total(grid.get("iceAccumulation"), start, end) / 25.4,  # mm
        gust_mph=gust * 0.621371 if gust is not None else None,
    )


def describe_spans(hours: list[Hour], tz) -> str:
    """Collapse consecutive hours into ranges, e.g. '5 AM–8 AM Tue, 2 PM Tue'."""
    spans: list[list[datetime]] = []
    for hour in hours:
        if spans and hour.start - spans[-1][1] <= timedelta(hours=1):
            spans[-1][1] = hour.start
        else:
            spans.append([hour.start, hour.start])
    parts = []
    for first, last in spans:
        a, b = first.astimezone(tz), (last + timedelta(hours=1)).astimezone(tz)
        parts.append(f"{fmt_clock(a)}–{fmt_clock(b)} {a:%a}")
    return ", ".join(parts)
