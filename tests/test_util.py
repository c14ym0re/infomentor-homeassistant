"""Enhetstester för den rena logiken i util.py (körs utan Home Assistant)."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
from datetime import date

MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "infomentor" / "util.py"
)
_spec = importlib.util.spec_from_file_location("infomentor_util", MODULE_PATH)
util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(util)


HUB_HTML = """
<script>
IMHome = { init: {
  "pupils":[
    {"id":"111","name":"Efternamn, Anna","switchPupilUrl":"https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/999","initials":"AE","selected":true},
    {"id":"222","name":"Efternamn, Bo","switchPupilUrl":"https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/888","initials":"BE","selected":false}
  ],
  selectedPupilName: 'Efternamn, Anna'
}};
</script>
"""

LOGIN_HTML = """
<form method="post">
  <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="VS" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="GEN" />
  <input type="hidden" name="__EVENTVALIDATION" value="EV" />
  <input type="text" name="login_ascx$txtNotandanafn" />
  <input type="password" name="login_ascx$txtLykilord" />
  <input type="submit" name="login_ascx$btnLogin" value="Logga in" />
</form>
"""


class TestNames(unittest.TestCase):
    def test_display_name_reverses(self):
        self.assertEqual(util.display_name("Efternamn, Anna"), "Anna Efternamn")

    def test_display_name_without_comma(self):
        self.assertEqual(util.display_name("Anna"), "Anna")

    def test_display_name_empty(self):
        self.assertEqual(util.display_name(""), "")

    def test_parse_names_option(self):
        text = "Efternamn, Anna = Anna\n\nEfternamn, Bo = Bosse\nOgiltig rad"
        self.assertEqual(
            util.parse_names_option(text), {"Efternamn, Anna": "Anna", "Efternamn, Bo": "Bosse"}
        )


class TestTime(unittest.TestCase):
    def test_to_date(self):
        self.assertEqual(util.to_date("2026-09-29T08:00:00"), date(2026, 9, 29))
        self.assertIsNone(util.to_date("inte-ett-datum"))

    def test_day_and_time(self):
        self.assertEqual(util.day_of("2026-09-29T08:00:00"), "2026-09-29")
        self.assertEqual(util.time_of("2026-09-29T08:00:00"), "08:00")


class TestClassification(unittest.TestCase):
    def test_is_break(self):
        for title in ("Lunch", "Rast", "Ombyte", "M-tid", "Studietid"):
            self.assertTrue(util.is_break(title), title)
        self.assertFalse(util.is_break("Matematik"))

    def test_is_pe(self):
        for title in ("Idh", "IDH", "Idrott och hälsa", "Gymnastik", "Gympa"):
            self.assertTrue(util.is_pe(title), title)
        self.assertFalse(util.is_pe("Matematik"))

    def test_is_done(self):
        self.assertTrue(util.is_done("Done"))
        self.assertTrue(util.is_done("Klar"))
        self.assertFalse(util.is_done("Due"))


class TestHtmlParsing(unittest.TestCase):
    def test_extract_pupils(self):
        pupils = util.extract_pupils(HUB_HTML)
        self.assertEqual(len(pupils), 2)
        self.assertEqual(pupils[0]["name"], "Efternamn, Anna")
        self.assertEqual(pupils[1]["id"], "222")

    def test_extract_pupils_missing_marker(self):
        self.assertEqual(util.extract_pupils("<html></html>"), [])

    def test_hidden_input(self):
        self.assertEqual(util.hidden_input(LOGIN_HTML, "__VIEWSTATE"), "VS")
        self.assertEqual(util.hidden_input(LOGIN_HTML, "__EVENTVALIDATION"), "EV")
        self.assertEqual(util.hidden_input(LOGIN_HTML, "saknas"), "")

    def test_extract_oauth_decodes_entities(self):
        html = '<input type="hidden" name="oauth_token" value="C0MB&amp;SLg" />'
        self.assertEqual(util.extract_oauth_token(html), "C0MB&SLg")
        self.assertIsNone(util.extract_oauth_token("<html></html>"))

    def test_hidden_inputs_collects_every_hidden_field(self):
        html = (
            '<input type="hidden" name="__VIEWSTATE" value="VS" />'
            '<input type="hidden" name="login_ascx$IdpListRepeater$ctl1$url" value="https://a" />'
            '<input type="hidden" name="login_ascx$IdpListRepeater$ctl1$number" value="7" />'
            '<input type="text" name="login_ascx$txtNotandanafn" value="" />'
            '<input type="hidden" name="x" value="A&amp;B" />'
        )
        fields = util.hidden_inputs(html)
        self.assertEqual(fields["__VIEWSTATE"], "VS")
        self.assertEqual(fields["login_ascx$IdpListRepeater$ctl1$url"], "https://a")
        self.assertEqual(fields["login_ascx$IdpListRepeater$ctl1$number"], "7")
        self.assertEqual(fields["x"], "A&B")  # HTML-avkodat
        self.assertNotIn("login_ascx$txtNotandanafn", fields)  # inte hidden

    def test_find_login_fields(self):
        fields = util.find_login_fields(LOGIN_HTML)
        self.assertEqual(fields["username"], "login_ascx$txtNotandanafn")
        self.assertEqual(fields["password"], "login_ascx$txtLykilord")
        self.assertEqual(fields["submit"], "login_ascx$btnLogin")

    def test_find_login_fields_falls_back(self):
        fields = util.find_login_fields("<form></form>")
        self.assertEqual(fields["username"], "login_ascx$txtNotandanafn")
        self.assertEqual(fields["password"], "login_ascx$txtLykilord")


class TestNormalization(unittest.TestCase):
    def test_normalize_lessons_skips_breaks(self):
        raw = [
            {"title": "Ma", "start": "2026-09-29T08:00:00", "end": "2026-09-29T08:50:00", "notes": {"roomInfo": "402"}},
            {"title": "Lunch", "start": "2026-09-29T11:00:00", "end": "2026-09-29T11:40:00"},
            {"title": "Idh", "start": "2026-09-29T13:00:00", "end": "2026-09-29T14:00:00"},
        ]
        lessons = util.normalize_lessons(raw)
        self.assertEqual([lesson["title"] for lesson in lessons], ["Ma", "Idh"])
        self.assertFalse(lessons[0]["is_pe"])
        self.assertTrue(lessons[1]["is_pe"])
        self.assertEqual(lessons[0]["room"], "402")

    def test_normalize_calendar_skips_task_duplicates(self):
        raw = [
            {"id": 1, "title": "Prov", "startDateFull": "2026-09-30T00:00:00", "subjects": [{"title": "Kemi"}]},
            {"id": 2, "title": "Inlämning", "url": "/task/show/2", "startDateFull": "2026-10-01"},
        ]
        events = util.normalize_calendar(raw)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["subjects"], "Kemi")

    def test_normalize_tasks(self):
        raw = [{"id": 5, "title": " Loggbok ", "dueDate": "2026-10-02", "status": "Due", "isOverdue": False}]
        tasks = util.normalize_tasks(raw)
        self.assertEqual(tasks[0]["title"], "Loggbok")
        self.assertEqual(tasks[0]["due"], "2026-10-02")

    def test_normalize_notifications_maps_child(self):
        raw = [
            {
                "id": 7,
                "appType": "News",
                "title": "Nyhet",
                "dateSent": "2026-09-28T14:00:00",
                "pupilSourceId": "16863|111|NEMANDI_SKOLI",
            }
        ]
        notes = util.normalize_notifications(raw, {"111": "Efternamn, Anna"})
        self.assertEqual(notes[0]["child"], "Efternamn, Anna")
        self.assertEqual(notes[0]["type"], "News")

    def test_normalize_attendance(self):
        raw = {
            "absentToday": True,
            "absenceTodaySessions": [
                {"title": "Ma", "formattedTimeString": "10:00-10:50", "isAbsent": True},
                {"title": "Sv", "formattedTimeString": "11:00-11:50", "isAbsent": False},
            ],
            "leaveRequests": [{"id": 1}],
        }
        attendance = util.normalize_attendance(raw)
        self.assertTrue(attendance["absent_today"])
        self.assertEqual(attendance["today"], ["Ma 10:00-10:50"])
        self.assertEqual(attendance["pending_leave"], 1)

    def test_parse_mateo_days(self):
        payload = [
            {
                "date": "2026-09-29T00:00:00.000Z",
                "meals": [
                    {"name": "Torsk", "type": "Dagens rätt"},
                    {"name": "Vegobollar", "type": "Dagens gröna"},
                ],
            }
        ]
        menu = util.parse_mateo_days(payload)
        self.assertEqual(menu["2026-09-29"][0], {"label": "Dagens rätt", "dish": "Torsk"})


class TestLearnlogAndNews(unittest.TestCase):
    def test_strip_html(self):
        self.assertEqual(util.strip_html("<p>Hej &amp; hej</p><br/>då"), "Hej & hej då")
        self.assertEqual(util.strip_html(None), "")

    def test_parse_mateo_unit(self):
        self.assertEqual(util.parse_mateo_unit("https://meny.mateo.se/kommun/123"), "123")
        self.assertEqual(util.parse_mateo_unit("172"), "172")
        self.assertEqual(util.parse_mateo_unit("https://meny.mateo.se/kommun/123?x=1"), "123")
        self.assertIsNone(util.parse_mateo_unit("ingen-url"))
        self.assertIsNone(util.parse_mateo_unit(""))

    def test_normalize_learnlog(self):
        raw = {
            "entries": [
                {
                    "id": 1,
                    "title": " Vecka 40 ",
                    "text": "<p>Hej 7D!</p>",
                    "subjectsCoursesDisplayString": "Idrott och hälsa",
                    "lastModifiedOn": "den 31 augusti",
                    "attachments": [{"fileName": "plan.docx"}],
                }
            ]
        }
        out = util.normalize_learnlog(raw)
        self.assertEqual(out[0]["title"], "Vecka 40")
        self.assertEqual(out[0]["text"], "Hej 7D!")
        self.assertEqual(out[0]["subject"], "Idrott och hälsa")
        self.assertEqual(out[0]["attachments"], ["plan.docx"])
        self.assertEqual(util.normalize_learnlog({}), [])

    def test_normalize_news_sorts_and_limits(self):
        raw = [
            {"id": 1, "title": "Äldre", "publishedDate": "2026-09-01"},
            {"id": 2, "title": "Nyast", "publishedDate": "2026-09-28"},
        ]
        out = util.normalize_news(raw, limit=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Nyast")


class TestPlans(unittest.TestCase):
    RAW = {
        "uols": [
            {"id": 2, "title": " Bild ", "subjects": [20], "state": "finished"},
            {"id": 1, "title": "Spanska 1C", "subjects": [10, 99], "state": "active"},
            {"id": 3, "title": "Kemi", "subjects": [], "state": "notstarted"},
        ],
        "subjects": [{"id": 10, "name": "Spanska"}, {"id": 20, "name": "Bild"}],
    }

    def test_normalize_plans_maps_subjects(self):
        out = util.normalize_plans(self.RAW)
        self.assertEqual(out[0]["title"], "Spanska 1C")
        self.assertEqual(out[0]["subjects"], ["Spanska"])  # okänt ämnes-id hoppas över

    def test_normalize_plans_keeps_order_but_finished_last(self):
        out = util.normalize_plans(self.RAW)
        self.assertEqual([plan["id"] for plan in out], ["1", "3", "2"])
        self.assertEqual([plan["state"] for plan in out], ["active", "notstarted", "finished"])

    def test_normalize_plans_tolerates_junk(self):
        self.assertEqual(util.normalize_plans({}), [])
        self.assertEqual(
            util.normalize_plans({"uols": [None, {"id": 5}], "subjects": None}),
            [{"id": "5", "title": "", "subjects": [], "state": ""}],
        )

    def test_parse_teachers(self):
        self.assertEqual(util.parse_teachers("Kvarnbrink,   Erika"), ["Erika Kvarnbrink"])
        self.assertEqual(
            util.parse_teachers("Kvarnbrink, Erika,Gomez Fraga, Angeles"),
            ["Erika Kvarnbrink", "Angeles Gomez Fraga"],
        )
        self.assertEqual(util.parse_teachers(None), [])

    def test_normalize_plan_detail(self):
        raw = {
            "sections": [
                {
                    "type": "uol",
                    "overview": [
                        {"label": "Termin", "value": "HT26"},
                        {"label": "Startdatum", "value": "2026-08-18T00:00:00"},
                        {"label": "Slutdatum", "value": "2026-12-18"},
                        {"label": "Årskurs", "value": "9"},
                        {"label": "Lärare", "value": "Kvarnbrink, Erika"},
                        {"label": "Beskrivning", "value": "<p>lång text som inte ska med</p>"},
                    ],
                },
                {"type": "syllabus", "sections": [{"fields": [{"label": "Tidplan:", "value": "V.40"}]}]},
            ]
        }
        self.assertEqual(
            util.normalize_plan_detail(raw),
            {
                "term": "HT26",
                "start": "2026-08-18",
                "end": "2026-12-18",
                "grade": "9",
                "teachers": ["Erika Kvarnbrink"],
            },
        )
        self.assertEqual(
            util.normalize_plan_detail({}),
            {"term": "", "start": "", "end": "", "grade": "", "teachers": []},
        )

    def test_normalize_plan_tasks(self):
        raw = {
            "type": "task",
            "hasMore": False,
            "tasks": [
                {
                    "id": 3,
                    "title": " Loggbok v.37 ",
                    "dueDate": "2026-09-11T00:00:00",
                    "status": "active",
                    "milestoneCount": 0,
                },
                {
                    "id": 1,
                    "title": "Prov: Samhällsekonomi",
                    "dueDate": "2026-10-23",
                    "status": "active",
                    "milestoneCount": 3,
                    "milestonesComplete": 2,
                },
                {"id": 2, "title": "Utan datum", "dueDate": None, "status": "done"},
                None,
            ],
        }
        tasks = util.normalize_plan_tasks(raw)
        self.assertEqual([t["id"] for t in tasks], ["3", "1", "2"])
        self.assertEqual(tasks[0]["title"], "Loggbok v.37")
        self.assertEqual(tasks[1]["milestones"], "2/3")
        self.assertNotIn("milestones", tasks[0])
        self.assertEqual(util.normalize_plan_tasks({}), [])

    def test_dedupe_plans(self):
        base = {
            "title": "Spanska_En la cafetería Unidad 1C",
            "subjects": ["Spanska"],
            "state": "active",
            "start": "2026-09-28",
            "end": "2026-10-11",
            "teachers": ["Diana Morales Guardado"],
        }
        plans = [
            {**base, "id": "8615622"},
            {**base, "id": "8615621"},  # läraren publicerade samma planering två gånger
            {**base, "id": "9", "start": "2026-10-05"},
            {**base, "id": "10", "teachers": ["Någon Annan"]},
        ]
        self.assertEqual([p["id"] for p in util.dedupe_plans(plans)], ["8615622", "9", "10"])
        self.assertEqual(util.dedupe_plans([]), [])


class TestDerivation(unittest.TestCase):
    def setUp(self):
        self.lessons = util.normalize_lessons(
            [
                {"title": "Ma", "start": "2026-09-29T08:00:00", "end": "2026-09-29T08:50:00"},
                {"title": "Idh", "start": "2026-09-29T13:00:00", "end": "2026-09-29T14:00:00"},
                {"title": "Sv", "start": "2026-10-01T09:00:00", "end": "2026-10-01T09:50:00"},
            ]
        )

    def test_next_school_day(self):
        self.assertEqual(util.next_school_day(self.lessons, date(2026, 9, 28)), "2026-09-29")
        self.assertEqual(util.next_school_day(self.lessons, date(2026, 9, 29)), "2026-10-01")
        self.assertIsNone(util.next_school_day(self.lessons, date(2026, 10, 2)))

    def test_school_day_bounds(self):
        lessons = util.lessons_on(self.lessons, "2026-09-29")
        self.assertEqual(util.school_day_bounds(lessons), ("08:00", "14:00"))
        self.assertIsNone(util.school_day_bounds([]))

    def test_pe_lessons(self):
        lessons = util.lessons_on(self.lessons, "2026-09-29")
        self.assertEqual([item["title"] for item in util.pe_lessons(lessons)], ["Idh"])

    def test_tasks_due(self):
        tasks = util.normalize_tasks(
            [
                {"id": 1, "title": "A", "dueDate": "2026-09-30", "status": "Due"},
                {"id": 2, "title": "B", "dueDate": "2026-10-20", "status": "Due"},
                {"id": 3, "title": "C", "dueDate": "2026-09-29", "status": "Done"},
            ]
        )
        due = util.tasks_due(tasks, date(2026, 9, 28), 7)
        self.assertEqual([task["title"] for task in due], ["A"])

    def test_upcoming_event(self):
        calendar = util.normalize_calendar(
            [
                {"id": 1, "title": "Senare", "startDateFull": "2026-10-05"},
                {"id": 2, "title": "Snarast", "startDateFull": "2026-09-30"},
                {"id": 3, "title": "Då", "startDateFull": "2026-09-01"},
            ]
        )
        self.assertEqual(util.upcoming_event(calendar, date(2026, 9, 28))["title"], "Snarast")
        self.assertIsNone(util.upcoming_event([], date(2026, 9, 28)))

    def test_lunch_for(self):
        menu = {"2026-09-29": [{"label": "Dagens rätt", "dish": "Torsk"}]}
        self.assertEqual(util.lunch_for(menu, "2026-09-29")[0]["dish"], "Torsk")
        self.assertEqual(util.lunch_for(menu, "2026-09-30"), [])
        self.assertEqual(util.lunch_for(menu, None), [])


if __name__ == "__main__":
    unittest.main()
