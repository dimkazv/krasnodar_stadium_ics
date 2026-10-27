#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from icalendar import Calendar, Event, Alarm
from lxml import html
import requests

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import argparse
import hashlib
import logging
import re
import sys

CALENDAR_URL = "https://fckrasnodar.ru/team/calendar/"
REQUEST_TIMEOUT = 30

# Stadium (location) names that mark a match as played in Krasnodar.
STADIUM_MARKERS = ("«Краснодар»", "«Ozon Арена»")

# The club's schedule is published in Moscow time (no DST).
TIMEZONE = ZoneInfo("Europe/Moscow")

MATCH_DURATION = timedelta(hours=2)
MORNING_REMINDER_TIME = time(10, 0)

MONTHS = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}


def fetch_calendar_page():
    headers = {"User-Agent": "curl/7.68.0"}

    logging.info(CALENDAR_URL)
    response = requests.get(CALENDAR_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    return response.content


def parse_month_header(dt_text):
    """'Декабрь 2026' -> (2026, 12). Raises ValueError on unexpected format."""
    parts = dt_text.split()  # also splits on non-breaking space
    if len(parts) != 2:
        raise ValueError(f"unexpected month header: {dt_text!r}")

    return int(parts[1]), kirillic_month_to_number(parts[0])


def parse_match(match, year, month):
    day = int(match.xpath("span[@class='date']/text()")[0])
    start_time = match.xpath("span[@class='day-of-week']/time/text()")[0].strip()
    team_home = match.xpath(".//span[contains(@class, 'team home')]/strong/text()")[0]
    team_guest = match.xpath(".//span[contains(@class, 'team guest')]/strong/text()")[0]
    match_id = match.xpath("a[@class='info']/@href")

    return create_calendar_event(
        year,
        month,
        day,
        start_time,
        team_home,
        team_guest,
        match_id=_extract_match_id(match_id[0]) if match_id else None,
    )


def _extract_match_id(href):
    found = re.search(r"match_id=(\d+)", href)
    return found.group(1) if found else None


def get_matches_grouped_by_months(page_content):
    tree = html.fromstring(page_content)

    events = []

    for m in tree.xpath("//dl[@class='matches-list']"):
        # Some <dt> nodes may contain an empty string – skip them safely
        month_header = "".join(m.xpath("dt/text()")).strip()
        if not month_header:
            logging.warning("Skipping month block with empty dt text")
            continue

        try:
            year, month = parse_month_header(month_header)
        except (ValueError, KeyError):
            logging.warning("Skipping month block with unexpected dt format: %r", month_header)
            continue

        for match in m.xpath("dd"):
            location = "".join(match.xpath("span[@class='location']/strong/text()"))
            if not any(marker in location for marker in STADIUM_MARKERS):
                continue

            try:
                event = parse_match(match, year, month)
            except (IndexError, ValueError):
                logging.exception("Skipping unparsable match in %s.%s", month, year)
                continue

            logging.debug("Parsed %s", event["summary"])
            events.append(event)

    return events


def create_alarm(description, trigger):
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", description)
    alarm.add("trigger", trigger)
    return alarm


def create_calendar_event(year, month, day, start_time, team_home, team_guest, match_id=None):
    hour, minute = (int(part) for part in start_time.split(":"))

    start = datetime(year, month, day, hour, minute, tzinfo=TIMEZONE)
    end = start + MATCH_DURATION
    title = f"Матч {team_home} - {team_guest}"

    event = Event()
    event.add("summary", title)
    event.add("dtstart", start)
    event.add("dtend", end)

    # UID is REQUIRED https://tools.ietf.org/html/rfc5545#section-3.6.1
    # The match id survives rescheduling; fall back to a start-time hash.
    if match_id:
        uid = f"{match_id}@krasnodar-stadium-ics"
    else:
        uid = hashlib.sha256(start.isoformat().encode("ascii")).hexdigest()
    event.add("uid", uid)

    # 1 day before the match
    event.add_component(create_alarm(title, timedelta(days=-1)))

    # Morning of the match day (10:00). Relative to the start, so that it
    # does not depend on the time zone of the viewer's calendar app.
    morning = datetime.combine(start.date(), MORNING_REMINDER_TIME, tzinfo=TIMEZONE)
    if morning < start:
        event.add_component(
            create_alarm(f"Сегодня в {start_time}: {title}", morning - start)
        )

    # 5 minutes before the start
    event.add_component(create_alarm(title, timedelta(minutes=-5)))

    return event


def kirillic_month_to_number(month):
    return MONTHS[month.lower()]


def parse_args():
    parser = argparse.ArgumentParser(
        description="This script fetches the FC Krasnodar schedule and generates an .ics file with matches played at the Krasnodar stadium."
    )

    default_output_file = "test.ics"
    parser.add_argument(
        "-o",
        dest="output_file",
        metavar="out",
        default=default_output_file,
        help="output file (default: {0})".format(default_output_file),
    )

    parser.add_argument("--log-level", metavar="level", default="INFO")

    return parser.parse_args()


def generate_calendar(events):
    cal = Calendar()
    cal.add("prodid", "-//My calendar product//mxm.dk//")
    cal.add("version", "2.0")
    cal.add("NAME", "Календарь матчей стадиона Краснодар")
    cal.add("X-WR-CALNAME", "Календарь матчей стадиона Краснодар")

    # Sorted, so that the file only changes when the schedule does.
    for e in sorted(events, key=lambda e: e["dtstart"].dt):
        cal.add_component(e)

    # Emits VTIMEZONE for the TZID used by the events.
    cal.add_missing_timezones()

    return cal


def setup_logging(log_level):
    logging_level = getattr(logging, log_level.upper(), None)

    if not isinstance(logging_level, int):
        raise ValueError("Invalid log level: {0}".format(log_level))

    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="[%d/%m/%Y:%H:%M:%S %z]",
    )


def main():
    args = parse_args()
    setup_logging(args.log_level)

    events = get_matches_grouped_by_months(fetch_calendar_page())

    # Do not overwrite a good calendar with an empty one if the site layout changes.
    if not events:
        logging.error("No matches found, refusing to write an empty calendar")
        return 1

    logging.info("Found %d matches", len(events))

    cal = generate_calendar(events)

    with open(args.output_file, "wb") as f:
        f.write(cal.to_ical())

    return 0


if __name__ == "__main__":
    sys.exit(main())
