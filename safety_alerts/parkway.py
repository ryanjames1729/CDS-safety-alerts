"""Blue Ridge Parkway road status, read from the NPS road closures page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from .util import SourceError, http_get

_MILEPOSTS = re.compile(r"(\d{1,3}(?:\.\d+)?)(?:\s*-\s*(\d{1,3}(?:\.\d+)?))?\s*$")
_AS_OF = re.compile(r"Road status as of\s+(.{5,80}?\d{4})", re.I)


@dataclass
class Section:
    start: float
    end: float
    crossroads: str
    status: str
    note: str

    @property
    def key(self) -> str:
        return f"{self.start:g}-{self.end:g}"

    @property
    def mileposts(self) -> str:
        return f"MP {self.start:g}" if self.start == self.end else f"MP {self.start:g}–{self.end:g}"

    @property
    def state(self) -> str:
        """closed, open (includes ungated stretches) or other."""
        text = self.status.lower()
        if "closed" in text:
            return "closed"
        if "open" in text or "ungated" in text:
            return "open"
        return "other"


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        self.text.append(data)
        if self._cell is not None:
            self._cell.append(data)


def parse_page(html: str, mp_min: float, mp_max: float) -> tuple[list[Section], str]:
    """Return the sections overlapping [mp_min, mp_max] and the page's 'as of' text."""
    parser = _Rows()
    parser.feed(html)
    sections, total = [], 0
    for row in parser.rows:
        if len(row) < 3:
            continue
        match = _MILEPOSTS.search(row[0])
        if not match:
            continue
        start = float(match.group(1))
        end = float(match.group(2)) if match.group(2) else start
        if end < start or end > 470:
            continue
        total += 1
        # A stretch must lie partly inside the range (merely touching an end does
        # not count); a single access point counts if it is within the range.
        within = mp_min <= start <= mp_max if start == end else end > mp_min and start < mp_max
        if within:
            note = row[3] if len(row) > 3 else ""
            sections.append(Section(start, end, row[1], row[2], note))
    if total < 20:
        raise SourceError(f"Parkway page layout not recognised (found {total} road sections)")
    if not sections:
        raise SourceError(f"Parkway page lists no sections between MP {mp_min:g} and {mp_max:g}")
    as_of = _AS_OF.search(re.sub(r"\s+", " ", " ".join(parser.text)))
    return sections, (as_of.group(1).strip() if as_of else "")


def fetch_sections(cfg: dict) -> tuple[list[Section], str]:
    park = cfg["parkway"]
    raw = http_get(park["url"], cfg["nws"]["user_agent"], accept="text/html")
    return parse_page(raw.decode("utf-8", errors="replace"), park["milepost_min"], park["milepost_max"])
