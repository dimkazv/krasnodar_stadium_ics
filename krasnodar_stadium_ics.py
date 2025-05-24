#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from icalendar import Calendar, Event, Alarm
from lxml import html
import requests

from datetime import datetime, timedelta
import argparse
import logging
import hashlib


def get_matches_grouped_by_months():
    url = "https://fckrasnodar.ru/team/calendar/"

    logging.info(url)

    headers = {"User-Agent": "curl/7.68.0"}

    page = requests.get(url, headers=headers, allow_redirects=True)

    if page.status_code == 404:
        return None

    tree = html.fromstring(page.content)
    
    months = tree.xpath(
        "//dl[@class='matches-list']/."
    )

    events = []

    for m in months:
        month_and_year = m.xpath("dt/text()")[0].split('\xa0')
        month = kirillic_month_to_number(month_and_year[0])
        year = int(month_and_year[1])

        matches_in_month = m.xpath(
            ".//strong[text()[contains(., '«Краснодар»')] or text()[contains(., '«OZON АРЕНА»')]]/../.."
        )
        for match in matches_in_month:
            day = int(match.xpath("span[@class='date']/text()")[0])
            time = match.xpath("span[@class='day-of-week']")[0].xpath("time/text()")[0]
            team_home = match.xpath(".//span[contains(@class, 'team home')]/strong/text()")[0]
            team_guest = match.xpath(".//span[contains(@class, 'team guest')]/strong/text()")[0]
            print(day, time, team_home, team_guest)
            e = create_calendar_event(year, month, day, time, team_home, team_guest)
            events.append(e)


    return events


def create_calendar_event(year, month, day, time, team_home, team_guest):
    time = time.split(':')
    hour = int(time[0])
    minute = int(time[1])

    start = datetime(year, month, day, hour, minute, 0)
    end = start + timedelta(hours=2)

    event = Event()
    event.add("summary", f"Матч {team_home} - {team_guest}")
    event.add("dtstart", start)
    event.add("dtend", end)

    # UID is REQUIRED https://tools.ietf.org/html/rfc5545#section-3.6.1
    uid = hashlib.sha512(
        f"{year}{month}{day}{hour}{minute}".encode("ascii")
    ).hexdigest()
    event.add("uid", uid)

    # Add alarm that triggers 1 day before the event
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", f"Матч {team_home} - {team_guest}")
    alarm.add("trigger", timedelta(days=-1))
    event.add_component(alarm)

    return event

def kirillic_month_to_number(month):
    return {
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
    }[month.lower()]


def parse_args():
    parser = argparse.ArgumentParser(
        description="This script fetches data about production calendar and generates .ics file with it."
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

    for e in events:
        cal.add_component(e)

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


if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.log_level)

    events = get_matches_grouped_by_months()

    cal = generate_calendar(events)

    with open(args.output_file, "w") as f:
        f.write(cal.to_ical().decode("utf-8"))
