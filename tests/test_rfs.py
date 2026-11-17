import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import krasnodar_stadium_ics as ics  # noqa: E402


def match_page(
    status="Матч не начался",
    place="«Краснодар» (Россия, Краснодар)",
    when="17 ноября 2026, вторник. 19:00 мск",
    teams=("Россия", "Венесуэла"),
):
    place_html = f"Стадион: {place}<br>Вместимость: 33 395" if place else ""
    return f"""
    <html><head><meta charset="utf-8"></head><body>
    <div class="widget-match__top">
      <div class="widget-match__top--side">Футбол</div>
      <div class="widget-match__top--center">{when}</div>
    </div>
    <div class="widget-match__team-name">{teams[0]}</div>
    <div class="widget-match__team-name">{teams[1]}</div>
    <div id="js-match-status" class="widget-match__status">{status}</div>
    <div class="match-header-info-place">{place_html}</div>
    </body></html>
    """.encode()


def calendar_item(href, score=("-", "-")):
    return f"""
    <li class="schedule-matches-list-item">
      <a class="teams-content" href="{href}">
        <div class="match-score__number">{score[0]}</div>
        <div class="match-score__separator">:</div>
        <div class="match-score__number">{score[1]}</div>
      </a>
    </li>
    """


class ParseRfsMatchTest(unittest.TestCase):
    URL = "https://www.rfs.ru/match/57790"

    def parse(self, **kwargs):
        return ics.parse_rfs_match(match_page(**kwargs), self.URL)

    def test_match_at_the_stadium(self):
        event = self.parse()

        self.assertEqual(str(event["summary"]), "Матч Россия - Венесуэла")
        start = event["dtstart"].dt
        self.assertEqual((start.year, start.month, start.day, start.hour, start.minute), (2026, 11, 17, 19, 0))
        self.assertEqual(str(event["uid"]), "rfs-57790@krasnodar-stadium-ics")
        self.assertEqual(len(event.walk("VALARM")), 3)

    def test_old_and_new_stadium_names(self):
        self.assertIsNotNone(self.parse(place="«Ozon Арена» (Россия, Краснодар)"))

    def test_attendance_suffix_is_ignored(self):
        place = "«Краснодар» (Россия, Краснодар)<br>Вместимость: 33 395. Количество зрителей: 31 625"
        self.assertIsNotNone(self.parse(place=place))

    def test_other_stadium_in_other_city(self):
        self.assertIsNone(self.parse(place="«Краснодар» (Россия, Сочи)"))

    def test_other_stadium_in_krasnodar(self):
        self.assertIsNone(self.parse(place="«Академия Краснодар» (Россия, Краснодар)"))
        self.assertIsNone(self.parse(place="Футбольный манеж (Россия, Краснодар)"))

    def test_no_stadium_given(self):
        self.assertIsNone(self.parse(place=""))

    def test_played_or_not_scheduled_matches(self):
        for status in ("Матч окончен", "Матч отменён", "Матч перенесён", "Матч идёт", ""):
            with self.subTest(status=status):
                self.assertIsNone(self.parse(status=status))

    def test_unexpected_date_or_time_zone(self):
        self.assertIsNone(self.parse(when="17 ноября 2026, вторник. 19:00 мск".replace("мск", "UTC")))
        self.assertIsNone(self.parse(when="дата уточняется"))
        self.assertIsNone(self.parse(when="17 ноябрь 2026, вторник. 19:00 мск"))

    def test_missing_teams(self):
        self.assertIsNone(self.parse(teams=("Россия", "")))


class UpcomingMatchUrlsTest(unittest.TestCase):
    def test_only_matches_without_a_score(self):
        content = f"""
        <ul>
          {calendar_item("https://www.rfs.ru/match/1", ("2", "1"))}
          {calendar_item("https://www.rfs.ru/match/2")}
          {calendar_item("https://www.rfs.ru/match/2")}
          {calendar_item("https://www.rfs.ru/match/3")}
        </ul>
        """.encode()

        self.assertEqual(
            ics.rfs_upcoming_match_urls(content),
            ["https://www.rfs.ru/match/2", "https://www.rfs.ru/match/3"],
        )


class IsKrasnodarStadiumTest(unittest.TestCase):
    def test_zero_width_spaces(self):
        self.assertTrue(ics.is_krasnodar_stadium("Стадион: «​Краснодар»​ (Россия, Краснодар)"))

    def test_not_a_stadium_line(self):
        self.assertFalse(ics.is_krasnodar_stadium(""))
        self.assertFalse(ics.is_krasnodar_stadium("Краснодар"))


if __name__ == "__main__":
    unittest.main()
