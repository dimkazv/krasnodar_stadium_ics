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
from time import sleep

CALENDAR_URL = "https://fckrasnodar.ru/team/calendar/"
REQUEST_TIMEOUT = 30

# National team matches (other than the club's own) come from the Russian
# Football Union: its match pages state the stadium.
RFS_CALENDAR_URL = "https://www.rfs.ru/nationals/calendar/matches"
RFS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; krasnodar-stadium-ics; +https://github.com/dimkazv/krasnodar_stadium_ics)"
}
# The site sits behind a WAF that blocks bursts of requests, so stay polite.
RFS_MAX_MATCH_PAGES = 30
RFS_REQUEST_DELAY = 1.0
RFS_NOT_STARTED = "Матч не начался"

# Stadium (location) names that mark a match as played in Krasnodar.
STADIUM_MARKERS = ("«Краснодар»", "«Ozon Арена»")
STADIUM_CITY = "Краснодар"

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

# Month names as they appear in a date ("17 ноября 2026").
MONTHS_GENITIVE = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
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

    match_id = _extract_match_id(match_id[0]) if match_id else None

    return create_calendar_event(
        year,
        month,
        day,
        start_time,
        team_home,
        team_guest,
        uid=f"{match_id}@krasnodar-stadium-ics" if match_id else None,
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


def _text(node):
    return " ".join(node.text_content().split())


def rfs_upcoming_match_urls(calendar_content):
    """URLs of matches in the RFS calendar that have no score yet."""
    tree = html.fromstring(calendar_content)

    urls = []
    for item in tree.xpath("//li[contains(@class, 'schedule-matches-list-item')]"):
        scores = [_text(n) for n in item.xpath(".//*[@class='match-score__number']")]
        links = item.xpath(".//a[contains(@href, '/match/')]/@href")
        if scores == ["-", "-"] and links and links[0] not in urls:
            urls.append(links[0])

    return urls


def is_krasnodar_stadium(place_text):
    """True only for 'Стадион: «Краснодар» (Россия, Краснодар)' and its aliases."""
    found = re.search(r"Стадион:\s*(«[^»]+»)\s*\(([^)]*)\)", place_text.replace("​", ""))
    if not found:
        return False

    name, location = found.groups()
    return name in STADIUM_MARKERS and location.split(",")[-1].strip() == STADIUM_CITY


def parse_rfs_match(page_content, url):
    """Event for a not yet started match at the Krasnodar stadium, else None."""
    tree = html.fromstring(page_content)

    def first_text(xpath):
        nodes = tree.xpath(xpath)
        return _text(nodes[0]) if nodes else ""

    if first_text("//*[@id='js-match-status']") != RFS_NOT_STARTED:
        return None

    if not is_krasnodar_stadium(first_text("//*[contains(@class, 'match-header-info-place')]")):
        return None

    # "17 ноября 2026, вторник. 19:00 мск"
    when = re.search(
        r"(\d{1,2})\s+([а-я]+)\s+(\d{4}),[^.]*\.\s*(\d{1,2}:\d{2})\s*мск",
        first_text("//*[contains(@class, 'widget-match__top--center')]"),
    )
    month = MONTHS_GENITIVE.get(when.group(2)) if when else None
    teams = [_text(n) for n in tree.xpath("//*[contains(@class, 'widget-match__team-name')]")]
    match_id = re.search(r"/match/(\d+)", url)

    if not (when and month and len(teams) == 2 and all(teams) and match_id):
        logging.warning("Skipping RFS match with unexpected page layout: %s", url)
        return None

    return create_calendar_event(
        int(when.group(3)),
        month,
        int(when.group(1)),
        when.group(4),
        teams[0],
        teams[1],
        uid=f"rfs-{match_id.group(1)}@krasnodar-stadium-ics",
    )


def fetch_rfs(url):
    logging.info(url)
    response = requests.get(url, headers=RFS_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    return response.content


def get_rfs_matches():
    urls = rfs_upcoming_match_urls(fetch_rfs(RFS_CALENDAR_URL))
    if len(urls) > RFS_MAX_MATCH_PAGES:
        logging.warning("Checking only the first %d of %d RFS matches", RFS_MAX_MATCH_PAGES, len(urls))
        urls = urls[:RFS_MAX_MATCH_PAGES]

    events = []
    for url in urls:
        sleep(RFS_REQUEST_DELAY)
        try:
            event = parse_rfs_match(fetch_rfs(url), url)
        except requests.RequestException as e:
            # Most likely the WAF: do not keep hammering the site.
            logging.warning("Stopping RFS checks after a failed request: %s", e)
            break

        if event is not None:
            logging.info("RFS match at the stadium: %s", event["summary"])
            events.append(event)

    return events


def create_alarm(description, trigger):
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", description)
    alarm.add("trigger", trigger)
    return alarm


def create_calendar_event(year, month, day, start_time, team_home, team_guest, uid=None):
    hour, minute = (int(part) for part in start_time.split(":"))

    start = datetime(year, month, day, hour, minute, tzinfo=TIMEZONE)
    end = start + MATCH_DURATION
    title = f"Матч {team_home} - {team_guest}"

    event = Event()
    event.add("summary", title)
    event.add("dtstart", start)
    event.add("dtend", end)

    # UID is REQUIRED https://tools.ietf.org/html/rfc5545#section-3.6.1
    # The caller passes a source-specific id that survives rescheduling;
    # fall back to a start-time hash.
    event.add("uid", uid or hashlib.sha256(start.isoformat().encode("ascii")).hexdigest())

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

    logging.info("Found %d club matches", len(events))

    # The extra source is best effort: the club's own matches must not depend on it.
    try:
        rfs_events = get_rfs_matches()
    except Exception as e:
        logging.warning("RFS matches skipped: %r", e)
        rfs_events = []

    # Only one match can be played at the stadium at a time.
    known_starts = {e["dtstart"].dt for e in events}
    for e in rfs_events:
        if e["dtstart"].dt in known_starts:
            logging.info("Skipping RFS match already in the calendar: %s", e["summary"])
            continue
        events.append(e)

    cal = generate_calendar(events)

    with open(args.output_file, "wb") as f:
        f.write(cal.to_ical())

    return 0


if __name__ == "__main__":
    sys.exit(main())
