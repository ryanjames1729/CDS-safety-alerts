"""Run with: python3 -m unittest discover -s tests"""

from __future__ import annotations

import copy
import json
import tomllib
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from safety_alerts import forecast, messages, ncdot, nws, parkway
from safety_alerts.chat import Chat
from safety_alerts.engine import Monitor
from safety_alerts.util import SourceError, locate, parse_interval

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLES = json.loads((FIXTURES / "nws_samples.json").read_text())  # real NWS alerts
with open(ROOT / "config.toml", "rb") as fh:
    CONFIG = tomllib.load(fh)

LAT, LON = CONFIG["site"]["latitude"], CONFIG["site"]["longitude"]
NOW = datetime(2026, 1, 13, 15, 0, tzinfo=timezone.utc)  # Tuesday 10 AM EST


def box(center_lat: float, center_lon: float, half: float = 0.1) -> dict:
    a, b, c, d = center_lon - half, center_lat - half, center_lon + half, center_lat + half
    return {"type": "Polygon", "coordinates": [[[a, b], [c, b], [c, d], [a, d], [a, b]]]}


OVER_CAMPUS = box(LAT, LON)
NORTH_OF_CAMPUS = box(LAT + 0.3, LON)


def feature(
    event: str,
    alert_id: str,
    vtec: str | None = None,
    geometry: dict | None = None,
    ends: datetime | None = None,
    description: str = "",
    references: list[str] | None = None,
    severity: str = "Severe",
    message_type: str = "Alert",
) -> dict:
    ends = ends or NOW + timedelta(hours=1)
    return {
        "geometry": geometry,
        "properties": {
            "id": alert_id,
            "event": event,
            "status": "Actual",
            "messageType": message_type,
            "severity": severity,
            "description": description,
            "instruction": "Take shelter now.",
            "onset": (NOW - timedelta(minutes=5)).isoformat(),
            "ends": ends.isoformat(),
            "expires": ends.isoformat(),
            "parameters": {"VTEC": [vtec]} if vtec else {},
            "references": [{"identifier": r} for r in references or []],
        },
    }


def alerts(*features: dict) -> list[nws.Alert]:
    return [a for a in map(nws.parse_alert, copy.deepcopy(list(features))) if a]


class FakeChat(Chat):
    def __init__(self) -> None:
        super().__init__(None, dry_run=True)
        self.fail = False

    def post(self, text: str, thread: str) -> bool:
        if self.fail:
            return False
        self.sent.append((thread, text))
        return True


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def monitor(started: bool = True) -> tuple[Monitor, FakeChat, Clock]:
    chat, clock = FakeChat(), Clock(NOW)
    return Monitor(copy.deepcopy(CONFIG), {"started": started}, chat, clock), chat, clock


TORNADO = "/O.NEW.KGSP.TO.W.0012.260113T1455Z-260113T1600Z/"
TORNADO_CON = "/O.CON.KGSP.TO.W.0012.000000T0000Z-260113T1600Z/"
TORNADO_CAN = "/O.CAN.KGSP.TO.W.0012.000000T0000Z-260113T1600Z/"
ADVISORY = "/O.NEW.KGSP.WW.Y.0003.260113T2300Z-260114T1700Z/"
ADVISORY_CON = "/O.CON.KGSP.WW.Y.0003.260113T2300Z-260114T1700Z/"
WHAT_2IN = "* WHAT...Snow expected. Total snow accumulations up to 2 inches.\n\n* WHERE...Buncombe County."
WHAT_5IN = "* WHAT...Heavy snow expected. Total snow accumulations of 3 to 5\ninches.\n\n* WHERE...Buncombe County."


class ParseRealAlerts(unittest.TestCase):
    def test_vtec_gives_a_stable_event_key(self):
        live = nws.parse_alert(SAMPLES["tornado_continued"])
        gone = nws.parse_alert(SAMPLES["tornado_cancelled"])
        self.assertEqual(live.key, "KMLB.TO.W.0007")
        self.assertEqual(gone.key, live.key)
        self.assertFalse(live.ended)
        self.assertTrue(gone.ended)
        self.assertTrue(nws.parse_alert(SAMPLES["winter_storm_expired"]).ended)

    def test_alert_without_vtec_is_keyed_by_id(self):
        sps = nws.parse_alert(SAMPLES["special_weather_statement"])
        self.assertFalse(sps.has_vtec)
        self.assertEqual(sps.key, sps.id)

    def test_tiers_from_config(self):
        tier = lambda name: nws.rate(nws.parse_alert(SAMPLES[name]), CONFIG).tier
        self.assertEqual(tier("tornado_continued"), 1)
        self.assertEqual(tier("flash_flood_new"), 1)
        self.assertEqual(tier("winter_weather_advisory"), 2)
        self.assertEqual(tier("special_weather_statement"), 2)

    def test_tests_and_exercises_are_skipped(self):
        sample = copy.deepcopy(SAMPLES["tornado_continued"])
        sample["properties"]["status"] = "Test"
        self.assertIsNone(nws.parse_alert(sample))

    def test_unlisted_events_fall_back_to_severity(self):
        severe, minor = alerts(
            feature("Volcano Warning", "a", severity="Severe"), feature("Beach Hazards Statement", "b", severity="Minor")
        )
        self.assertEqual(nws.rate(severe, CONFIG).tier, 2)
        self.assertEqual(nws.rate(minor, CONFIG).tier, 3)

    def test_real_alerts_render(self):
        tz = ZoneInfo("America/New_York")
        for name, sample in SAMPLES.items():
            alert = nws.parse_alert(sample)
            text = messages.new_alert(alert, nws.rate(alert, CONFIG), CONFIG, NOW, tz, [])
            self.assertIn(alert.event.upper(), text, name)
            self.assertLess(len(text), 3900, name)
        storm = nws.parse_alert(SAMPLES["severe_thunderstorm"])
        text = messages.new_alert(storm, nws.rate(storm, CONFIG), CONFIG, NOW, tz, [])
        self.assertIn("*Hazard:* 60 mph wind gusts and quarter size hail.", text)
        self.assertIn("Wind gusts to 60 MPH", text)
        advisory = nws.parse_alert(SAMPLES["winter_weather_advisory"])
        text = messages.new_alert(advisory, nws.rate(advisory, CONFIG), CONFIG, NOW, tz, [])
        self.assertIn("*What:* Lake effect snow expected.", text)
        self.assertIn("*Impacts:* Plan on slippery road conditions.", text)


class Geometry(unittest.TestCase):
    def test_point_in_polygon_and_distance(self):
        self.assertTrue(locate(LAT, LON, OVER_CAMPUS)[0])
        inside, miles = locate(LAT, LON, NORTH_OF_CAMPUS)
        self.assertFalse(inside)
        self.assertAlmostEqual(miles, 0.2 * 69.0, delta=0.5)  # edge is 0.2 degrees north
        self.assertIsNone(locate(LAT, LON, None))


class AlertLifecycle(unittest.TestCase):
    def test_tornado_warning_from_issue_to_cancellation(self):
        mon, chat, clock = monitor()
        mon.process_alerts(alerts(feature("Tornado Warning", "t1", TORNADO, OVER_CAMPUS)))
        self.assertEqual(len(chat.sent), 1)
        thread, text = chat.sent[0]
        self.assertIn("🚨 *TORNADO WARNING*", text)
        self.assertIn("<users/all>", text)
        self.assertIn("Campus is INSIDE", text)
        self.assertIn("until 11 AM today", text)

        mon.process_alerts(alerts(feature("Tornado Warning", "t1", TORNADO, OVER_CAMPUS)))
        self.assertEqual(len(chat.sent), 1, "same alert must not repost")

        clock.now += timedelta(minutes=10)
        mon.process_alerts(alerts(feature("Tornado Warning", "t2", TORNADO_CON, OVER_CAMPUS, references=["t1"])))
        self.assertEqual(len(chat.sent), 2)
        self.assertEqual(chat.sent[1][0], thread, "update goes in the same thread")
        self.assertIn("Update: Tornado Warning", chat.sent[1][1])
        self.assertNotIn("<users/all>", chat.sent[1][1])

        clock.now += timedelta(minutes=10)
        mon.process_alerts(
            alerts(feature("Tornado Warning", "t3", TORNADO_CAN, OVER_CAMPUS, message_type="Cancel"))
        )
        self.assertEqual(len(chat.sent), 3)
        self.assertEqual(chat.sent[2][0], thread)
        self.assertIn("no longer in effect", chat.sent[2][1])
        self.assertEqual(mon.state["events"], {})

    def test_polygon_missing_campus_does_not_ping_unless_tornado(self):
        mon, chat, _ = monitor()
        mon.process_alerts(
            alerts(
                feature("Severe Thunderstorm Warning", "s1", "/O.NEW.KGSP.SV.W.0040.260113T1455Z-260113T1600Z/", NORTH_OF_CAMPUS),
                feature("Tornado Warning", "t1", TORNADO, NORTH_OF_CAMPUS),
            )
        )
        storm, tornado = chat.sent[0][1], chat.sent[1][1]
        self.assertIn("Campus is outside the warning area (about 14 mi", storm)
        self.assertNotIn("<users/all>", storm)
        self.assertIn("<users/all>", tornado)

    def test_ping_when_storm_polygon_moves_over_campus(self):
        mon, chat, _ = monitor()
        vtec = "/O.NEW.KGSP.SV.W.0040.260113T1455Z-260113T1600Z/"
        mon.process_alerts(alerts(feature("Severe Thunderstorm Warning", "s1", vtec, NORTH_OF_CAMPUS)))
        mon.process_alerts(alerts(feature("Severe Thunderstorm Warning", "s2", vtec.replace("NEW", "CON"), OVER_CAMPUS)))
        self.assertIn("Campus is now inside the warning area", chat.sent[1][1])
        self.assertIn("<users/all>", chat.sent[1][1])

    def test_advisory_is_quiet_and_rewording_is_not_reposted(self):
        mon, chat, clock = monitor()
        ends = NOW + timedelta(hours=26)
        mon.process_alerts(alerts(feature("Winter Weather Advisory", "w1", ADVISORY, ends=ends, description=WHAT_2IN)))
        self.assertIn("⚠️ *WINTER WEATHER ADVISORY*", chat.sent[0][1])
        self.assertNotIn("<users/all>", chat.sent[0][1])

        clock.now += timedelta(hours=6)
        mon.process_alerts(alerts(feature("Winter Weather Advisory", "w2", ADVISORY_CON, ends=ends, description=WHAT_2IN)))
        self.assertEqual(len(chat.sent), 1, "reissue with the same forecast stays quiet")

        mon.process_alerts(alerts(feature("Winter Weather Advisory", "w3", ADVISORY_CON, ends=ends, description=WHAT_5IN)))
        self.assertEqual(len(chat.sent), 2)
        self.assertIn("Expected conditions have been revised", chat.sent[1][1])
        self.assertIn("3 to 5 inches", chat.sent[1][1])

        later = ends + timedelta(hours=3)
        mon.process_alerts(alerts(feature("Winter Weather Advisory", "w4", ADVISORY_CON, ends=later, description=WHAT_5IN)))
        self.assertIn("In effect until 3 PM tomorrow.\n• End time has changed", chat.sent[2][1])

    def test_upgrade_replaces_watch_without_an_all_clear(self):
        mon, chat, _ = monitor()
        ends = NOW + timedelta(hours=30)
        mon.process_alerts(alerts(feature("Winter Storm Watch", "a1", "/O.NEW.KGSP.WS.A.0002.260114T0000Z-260114T1800Z/", ends=ends)))
        upgrade = feature("Winter Storm Warning", "a2", "/O.NEW.KGSP.WS.W.0002.260114T0000Z-260114T1800Z/", ends=ends)
        upgrade["properties"]["parameters"]["VTEC"].insert(0, "/O.UPG.KGSP.WS.A.0002.260114T0000Z-260114T1800Z/")
        mon.process_alerts(alerts(upgrade))
        self.assertEqual(len(chat.sent), 2)
        self.assertIn("🚨 *WINTER STORM WARNING*", chat.sent[1][1])
        self.assertIn("Upgraded from Winter Storm Watch.", chat.sent[1][1])
        self.assertIn("<users/all>", chat.sent[1][1])
        self.assertEqual(list(mon.state["events"]), ["KGSP.WS.W.0002"])

    def test_one_missed_poll_does_not_end_an_alert(self):
        mon, chat, clock = monitor()
        mon.process_alerts(alerts(feature("Tornado Warning", "t1", TORNADO, OVER_CAMPUS)))
        mon.process_alerts([])
        self.assertEqual(len(chat.sent), 1)
        mon.process_alerts([])
        self.assertIn("no longer listed by NWS", chat.sent[1][1])

    def test_alert_past_its_end_time_closes_immediately(self):
        mon, chat, clock = monitor()
        mon.process_alerts(alerts(feature("Tornado Warning", "t1", TORNADO, OVER_CAMPUS)))
        clock.now += timedelta(hours=2)
        mon.process_alerts([])
        self.assertIn("(expired)", chat.sent[1][1])

    def test_failed_post_is_retried_on_the_next_poll(self):
        mon, chat, _ = monitor()
        chat.fail = True
        mon.process_alerts(alerts(feature("Tornado Warning", "t1", TORNADO, OVER_CAMPUS)))
        self.assertEqual(mon.state["events"], {})
        self.assertTrue(mon.post_failed)
        chat.fail = False
        mon.process_alerts(alerts(feature("Tornado Warning", "t1", TORNADO, OVER_CAMPUS)))
        self.assertEqual(len(chat.sent), 1)

    def test_statement_updates_follow_references(self):
        mon, chat, _ = monitor()
        mon.process_alerts(alerts(feature("Special Weather Statement", "p1", severity="Moderate")))
        later = NOW + timedelta(hours=3)
        mon.process_alerts(alerts(feature("Special Weather Statement", "p2", references=["p1"], ends=later, severity="Moderate")))
        self.assertEqual(len(mon.state["events"]), 1)
        self.assertEqual(chat.sent[1][0], chat.sent[0][0])

    def test_brief_only_alerts_are_tracked_silently(self):
        mon, chat, _ = monitor()
        mon.process_alerts(alerts(feature("Frost Advisory", "f1", severity="Minor")))
        self.assertEqual(chat.sent, [])
        self.assertEqual(len(mon.active), 1)
        mon.process_alerts([])
        mon.process_alerts([])
        self.assertEqual(chat.sent, [])


class Parkway(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES / "parkway_roadclosures.html").read_text(encoding="utf-8")
        self.sections, self.as_of = parkway.parse_page(html, 355.0, 406.0)

    def test_parses_real_page(self):
        self.assertEqual(len(self.sections), 19)
        self.assertEqual(self.as_of, "7:28 P.M, Sunday, November 22, 2026")
        closed = [s.mileposts for s in self.sections if s.state == "closed"]
        self.assertEqual(closed, ["MP 355.3", "MP 355.3–364.5", "MP 367.6"])
        asheville = next(s for s in self.sections if s.start == 384.7)
        self.assertIn("Hendersonville Road", asheville.crossroads)
        self.assertEqual(asheville.state, "open")

    def test_configured_range_stops_at_the_access_roads(self):
        park = CONFIG["parkway"]
        html = (FIXTURES / "parkway_roadclosures.html").read_text(encoding="utf-8")
        sections, _ = parkway.parse_page(html, park["milepost_min"], park["milepost_max"])
        self.assertEqual(
            [s.mileposts for s in sections],
            [
                "MP 375.7",
                "MP 375.7–381.9",
                "MP 377.4",
                "MP 381.9–382.8",
                "MP 382.8–384.1",
                "MP 384.1–384.7",
                "MP 384.7–388.8",
                "MP 388.8–393.6",
            ],
        )
        self.assertFalse([s for s in sections if s.state == "closed"], "Mitchell and Craggy closures are out of range")

    def test_unrecognised_page_is_an_error_not_an_all_clear(self):
        with self.assertRaises(SourceError):
            parkway.parse_page("<html><body>Page moved</body></html>", 355.0, 406.0)

    def test_only_status_changes_are_posted(self):
        mon, chat, _ = monitor()
        mon.process_parkway(self.sections, self.as_of)
        mon.process_parkway(self.sections, self.as_of)
        self.assertEqual(chat.sent, [])
        changed = copy.deepcopy(self.sections)
        target = next(s for s in changed if s.start == 384.7)
        target.status, target.note = "Closed", "Weather Closure"
        mon.process_parkway(changed, self.as_of)
        self.assertEqual(len(chat.sent), 1)
        self.assertIn("MP 384.7–388.8", chat.sent[0][1])
        self.assertIn("*Closed* (was: Open) — Weather Closure", chat.sent[0][1])
        mon.process_parkway(self.sections, self.as_of)
        self.assertIn("*Open* (was: Closed)", chat.sent[1][1])


def road_item(event_id: int, county: str = "Buncombe", **changes) -> dict:
    """An event shaped like the samples in NCDOT's API documentation."""
    item = {
        "ID": event_id,
        "RoadwayName": "I-26",
        "DirectionOfTravel": "Eastbound",
        "Description": "Vehicle Crash on I-26 Eastbound near Exit 37. All lanes closed.",
        "Reported": int(NOW.timestamp()) - 600,
        "StartDate": int(NOW.timestamp()) - 600,
        "LanesAffected": "All lanes closed",
        "EventType": "closures",
        "EventSubType": "vehicleCrash",
        "IsFullClosure": True,
        "Severity": "Major",
        "Comment": "detour is available. ",
        "County": county,
        "OriginalEventType": "accidentsAndIncidents",
    }
    return {**item, **changes}


def condition_item(county: str = "Buncombe", **changes) -> dict:
    item = {
        "Id": 27,
        "LocationDescription": county,
        "Overall for Public Display": "No Report",
        "Interstates": None,
        "US/NC Routes": None,
        "Secondary Roads": None,
        "AreaName": county,
        "LastUpdated": 1742322224,
        "County": county,
    }
    return {**item, **changes}


MINOR_CRASH = dict(EventType="accidentsAndIncidents", IsFullClosure=False, Severity="Minor", LanesAffected="1 Right lane closed")


class Roads(unittest.TestCase):
    def test_only_the_county_is_kept(self):
        events = ncdot.parse_events([road_item(1), road_item(2, county="Moore")], "Buncombe")
        self.assertEqual([e.id for e in events], ["1"])
        self.assertEqual(events[0].where, "I-26 Eastbound")
        self.assertTrue(events[0].is_closure)

    def test_no_report_means_no_conditions(self):
        rows = [condition_item(), condition_item("Bertie")]
        self.assertEqual(ncdot.parse_conditions(rows, "Buncombe"), {})
        rows[0].update({"Interstates": "Clear", "Secondary Roads": "Snow/Ice Covered"})
        self.assertEqual(
            ncdot.parse_conditions(rows, "Buncombe"), {"Interstates": "Clear", "Secondary roads": "Snow/Ice Covered"}
        )

    def test_unexpected_responses_are_errors_not_all_clear(self):
        with self.assertRaises(SourceError):
            ncdot.parse_events([{"unexpected": 1}], "Buncombe")
        with self.assertRaises(SourceError):
            ncdot.parse_conditions([condition_item("Bertie")], "Buncombe")

    def test_existing_closures_are_noted_quietly_then_new_ones_post(self):
        mon, chat, _ = monitor()
        parse = lambda *items: ncdot.parse_events(list(items), "Buncombe")
        mon.process_roads(parse(road_item(1)), {})
        self.assertEqual(chat.sent, [], "closures already there when NCDOT is first connected")

        mon.process_roads(parse(road_item(1), road_item(2, RoadwayName="US-25"), road_item(3, **MINOR_CRASH)), {})
        self.assertEqual(len(chat.sent), 1, "minor crash is left out")
        thread, text = chat.sent[0]
        self.assertIn("🚧 *Road closure — US-25 Eastbound* (NCDOT)", text)
        self.assertIn("Lanes: All lanes closed · Severity: Major", text)

        mon.process_roads(parse(road_item(1)), {})
        self.assertEqual(chat.sent[1][0], thread)
        self.assertIn("Cleared or reopened:* US-25 Eastbound", chat.sent[1][1])
        self.assertEqual(list(mon.state["ncdot"]["events"]), ["1"])

    def test_condition_changes_post_once(self):
        mon, chat, _ = monitor()
        mon.process_roads([], {})
        icy = {"Overall": "Snow/Ice Covered", "Secondary roads": "Snow/Ice Covered"}
        mon.process_roads([], icy)
        mon.process_roads([], icy)
        self.assertEqual(len(chat.sent), 1)
        self.assertIn("• Overall: *Snow/Ice Covered* (was: No report)", chat.sent[0][1])
        mon.process_roads([], {})
        self.assertIn("• Overall: *No report* (was: Snow/Ice Covered)", chat.sent[1][1])

    def test_brief_lists_recent_closures_and_counts_old_ones(self):
        old = int(NOW.timestamp()) - 40 * 86400
        events = ncdot.parse_events(
            [road_item(1), road_item(2, RoadwayName="SR-1103", Reported=old), road_item(3, **MINOR_CRASH)], "Buncombe"
        )
        roads = {"events": events, "conditions": {"Overall": "Clear"}}
        text = messages.brief("morning", CONFIG, NOW, ZoneInfo("America/New_York"), [], None, None, "", roads)
        self.assertIn("*NCDOT road conditions:* Overall: Clear", text)
        self.assertIn("*NCDOT closures and major incidents:* 2", text)
        self.assertIn("• I-26 Eastbound: Vehicle Crash", text)
        self.assertIn("• plus 1 more, mostly long-standing", text)
        self.assertNotIn("SR-1103", text)
        without = messages.brief("morning", CONFIG, NOW, ZoneInfo("America/New_York"), [], None, None, "")
        self.assertNotIn("NCDOT", without)


class Forecast(unittest.TestCase):
    def test_interval_parsing(self):
        start, end = parse_interval("2026-11-22T18:00:00+00:00/PT6H")
        self.assertEqual(end - start, timedelta(hours=6))
        start, end = parse_interval("2026-11-22T18:00:00+00:00/P1DT2H")
        self.assertEqual(end - start, timedelta(hours=26))
        self.assertIsNone(parse_interval("garbage"))

    def test_accumulation_is_prorated_to_the_window(self):
        series = {"values": [{"validTime": "2026-01-13T12:00:00+00:00/PT6H", "value": 50.8}]}
        total = forecast._total(series, NOW, NOW + timedelta(hours=14))  # window starts 15:00
        self.assertAlmostEqual(total / 25.4, 1.0)

    def test_wintry_hours(self):
        hour = lambda temp, pop, text: forecast.Hour(NOW, temp, pop, text, "5 mph")
        self.assertTrue(hour(31, 40, "Rain Likely").wintry)
        self.assertTrue(hour(36, 20, "Chance Snow Showers").wintry)
        self.assertFalse(hour(31, 10, "Mostly Clear").wintry)
        self.assertFalse(hour(45, 80, "Slight Chance Rain Showers").wintry)


class Briefs(unittest.TestCase):
    def test_morning_brief_is_due_once_per_weekday(self):
        mon, _, clock = monitor()
        tz = ZoneInfo("America/New_York")
        clock.now = datetime(2026, 1, 13, 4, 59, tzinfo=tz)
        self.assertEqual(mon.due_briefs(), [])
        clock.now = datetime(2026, 1, 13, 5, 20, tzinfo=tz)  # scheduler running late
        self.assertEqual(mon.due_briefs(), ["morning"])
        mon.state["briefs"]["morning"] = "2026-01-13"
        self.assertEqual(mon.due_briefs(), [])
        clock.now = datetime(2026, 1, 17, 5, 20, tzinfo=tz)  # Saturday
        self.assertEqual(mon.due_briefs(), [])
        clock.now = datetime(2026, 1, 18, 19, 5, tzinfo=tz)  # Sunday evening
        self.assertEqual(mon.due_briefs(), ["evening"])

    def outlook(self, snow: float) -> forecast.Outlook:
        hours = [forecast.Hour(NOW + timedelta(hours=i), 45, 5, "Mostly Sunny", "5 mph") for i in range(14)]
        return forecast.Outlook(hours, 45, snow, 0.0, 12)

    def test_scheduled_briefs_stay_silent_on_an_ordinary_day(self):
        mon, chat, _ = monitor()
        mon.active = []
        with mock.patch.object(forecast, "fetch_outlook", return_value=self.outlook(snow=0.0)):
            mon.send_brief("morning")
            mon.send_brief("evening")
        self.assertEqual(chat.sent, [])
        self.assertEqual(mon.state["briefs"], {"morning": "2026-01-13", "evening": "2026-01-13"})

    def test_scheduled_brief_goes_out_when_snow_is_forecast(self):
        mon, chat, _ = monitor()
        mon.active = []
        with mock.patch.object(forecast, "fetch_outlook", return_value=self.outlook(snow=1.5)):
            mon.send_brief("morning")
        self.assertEqual(len(chat.sent), 1)
        self.assertIn("Morning safety brief", chat.sent[0][1])
        self.assertIn("snow 1.5 in", chat.sent[0][1])

    def test_scheduled_brief_goes_out_when_an_alert_is_active(self):
        mon, chat, _ = monitor()
        mon.process_alerts(alerts(feature("Winter Weather Advisory", "w1", ADVISORY, ends=NOW + timedelta(hours=26))))
        with mock.patch.object(forecast, "fetch_outlook", return_value=self.outlook(snow=0.0)):
            mon.send_brief("evening")
        self.assertIn("Evening outlook", chat.sent[-1][1])

    def test_requested_update_always_posts_and_leaves_brief_state_alone(self):
        mon, chat, _ = monitor()
        mon.active = []
        with mock.patch.object(forecast, "fetch_outlook", return_value=self.outlook(snow=0.0)):
            mon.send_brief("status", forced=True)
        self.assertEqual(len(chat.sent), 1)
        self.assertIn("📋 *Conditions update — Tuesday 10 AM* (requested)", chat.sent[0][1])
        self.assertIn("*Active alerts:* none", chat.sent[0][1])
        self.assertIn("• 10 AM Tue: 45°F, Mostly Sunny", chat.sent[0][1])
        self.assertNotIn("briefs", mon.state)

    def test_brief_reports_alerts_closures_and_winter_flags(self):
        mon, _, _ = monitor()
        mon.process_alerts(alerts(feature("Winter Weather Advisory", "w1", ADVISORY, ends=NOW + timedelta(hours=26))))
        html = (FIXTURES / "parkway_roadclosures.html").read_text(encoding="utf-8")
        sections, as_of = parkway.parse_page(html, 355.0, 406.0)
        hours = [forecast.Hour(NOW + timedelta(hours=i), 30 + i, 60, "Light Snow", "5 mph") for i in range(3)]
        outlook = forecast.Outlook(hours, 30, 1.4, 0.05, 22)
        text = messages.brief("morning", CONFIG, NOW, mon.tz, mon.active, outlook, sections, as_of)
        self.assertIn("⚠️ Winter Weather Advisory — in effect until 12 PM tomorrow", text)
        self.assertIn("snow 1.4 in · ice 0.05 in · peak gust 22 mph", text)
        self.assertIn("❄️ *Wintry or freezing precipitation possible:* 10 AM–1 PM Tue", text)
        self.assertIn("3 of 19 sections closed", text)


class Guide(unittest.TestCase):
    def test_guide_explains_each_kind_of_message(self):
        text = messages.guide(CONFIG)
        for icon in ("🚨", "⚠️", "🔄", "✅", "🚧", "🧊", "🌅", "📋", "🛠️"):
            self.assertIn(icon, text)
        self.assertIn("summaries at 5 AM and 7 PM", text)
        self.assertNotIn("/update", text)
        with_commands = copy.deepcopy(CONFIG)
        with_commands["chat"]["commands"] = True
        self.assertIn("type `/update` for current conditions", messages.guide(with_commands))
        self.assertLess(len(text), 3900)


class Startup(unittest.TestCase):
    def test_first_run_posts_one_summary_instead_of_a_burst(self):
        mon, chat, _ = monitor(started=False)
        mon.process_alerts(alerts(feature("Tornado Warning", "t1", TORNADO, OVER_CAMPUS)), quiet=True)
        self.assertEqual(chat.sent, [])
        text = messages.startup(CONFIG, NOW, mon.tz, mon.active, None, "")
        self.assertIn("🚨 Tornado Warning", text)
        self.assertIn("KGSP.TO.W.0012", mon.state["events"])


class SourceHealth(unittest.TestCase):
    def test_outage_is_reported_once_then_recovery(self):
        mon, chat, clock = monitor()
        error = SourceError("timeout")
        mon._health("nws", "National Weather Service", error)
        self.assertEqual(chat.sent, [])
        clock.now += timedelta(minutes=16)
        mon._health("nws", "National Weather Service", error)
        mon._health("nws", "National Weather Service", error)
        self.assertEqual(len(chat.sent), 1)
        self.assertIn("unreachable for 15+ minutes", chat.sent[0][1])
        mon._health("nws", "National Weather Service", None)
        self.assertIn("reachable again", chat.sent[1][1])
        self.assertEqual(mon.state["health"], {})


if __name__ == "__main__":
    unittest.main()
