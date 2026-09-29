"""Rena hjälpfunktioner — inga HA- eller nätberoenden.

Här bor all parsning och härledning (skoldag, idrott, uppgifter, lunch), så att
den kan enhetstestas utan Home Assistant installerat.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

BREAK_RE = re.compile(r"^(lunch|rast|ombyte|m-tid|studietid|frukost)$", re.IGNORECASE)
PE_RE = re.compile(r"\b(idh|idr|idrott|gymnastik|gympa)\b", re.IGNORECASE)
DONE_RE = re.compile(r"done|complete|klar", re.IGNORECASE)


# --------------------------------------------------------------- namn och tid
def display_name(hub_name: str) -> str:
    """'Efternamn, Förnamn' -> 'Förnamn Efternamn'."""
    parts = [p.strip() for p in str(hub_name or "").split(",") if p.strip()]
    if len(parts) > 1:
        return f"{' '.join(parts[1:])} {parts[0]}"
    return parts[0] if parts else ""


def to_date(value: Any) -> date | None:
    """Tolkar datum/datetime/ISO-sträng. Returnerar None vid okänt."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def day_of(value: Any) -> str:
    return str(value or "")[:10]


def time_of(value: Any) -> str:
    return str(value or "")[11:16]


def is_break(title: Any) -> bool:
    return bool(BREAK_RE.match(str(title or "").strip()))


def is_pe(title: Any) -> bool:
    return bool(PE_RE.search(str(title or "").strip()))


def is_done(status: Any) -> bool:
    return bool(DONE_RE.search(str(status or "")))


def parse_names_option(text: Any) -> dict[str, str]:
    """Tolkar options-fältet 'Hub-namn = Smeknamn' (en per rad)."""
    mapping: dict[str, str] = {}
    for line in str(text or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and value.strip():
            mapping[key.strip()] = value.strip()
    return mapping


# --------------------------------------------------------------- HTML-parsning
OAUTH_RE = re.compile(r'name="oauth_token"\s+value="([^"]*)"')
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_NAME_RE = re.compile(r'name="([^"]+)"')
_TYPE_RE = re.compile(r'type="([^"]+)"')


def decode_html(value: str) -> str:
    """Avkodar de HTML-entiteter som förekommer i formulärvärden."""
    return (
        value.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )


def hidden_input(html: str, name: str) -> str:
    """Värdet på ett dolt fält, eller tom sträng."""
    match = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html, re.IGNORECASE)
    return match.group(1) if match else ""


def hidden_inputs(html: str) -> dict[str, str]:
    """Alla dolda fält i formuläret, namn → värde.

    Login-sidan bäddar in hela kommun-/IdP-listan som dolda fält
    (`login_ascx$IdpListRepeater$ctlN$url` m.fl.). En webbläsare skickar med dem —
    därför gör vi också det, annars kan sessionen hamna hos fel kommun.
    """
    fields: dict[str, str] = {}
    for tag in _INPUT_RE.findall(html):
        type_match = _TYPE_RE.search(tag)
        if (type_match.group(1) if type_match else "text").lower() != "hidden":
            continue
        name_match = _NAME_RE.search(tag)
        value_match = re.search(r'value="([^"]*)"', tag, re.IGNORECASE)
        if name_match:
            fields[name_match.group(1)] = decode_html(value_match.group(1)) if value_match else ""
    return fields


def extract_oauth_token(html: str) -> str | None:
    match = OAUTH_RE.search(html)
    return decode_html(match.group(1)) if match else None


def find_login_fields(html: str) -> dict[str, str]:
    """Hittar inloggningsformulärets fältnamn (server-control-ID kan variera)."""
    fields: dict[str, str] = {}
    for tag in _INPUT_RE.findall(html):
        name_match = _NAME_RE.search(tag)
        if not name_match:
            continue
        name = name_match.group(1)
        type_match = _TYPE_RE.search(tag)
        field_type = (type_match.group(1) if type_match else "text").lower()
        if field_type == "password" and "password" not in fields:
            fields["password"] = name
        elif (
            field_type == "text"
            and "username" not in fields
            and re.search(r"notandanafn|user|login|email|anvandare", name, re.IGNORECASE)
        ):
            fields["username"] = name
        elif field_type == "submit" and "submit" not in fields:
            fields["submit"] = name
    # Fallback till de verifierade namn som dementor.net använde.
    fields.setdefault("username", "login_ascx$txtNotandanafn")
    fields.setdefault("password", "login_ascx$txtLykilord")
    fields.setdefault("submit", "login_ascx$btnLogin")
    return fields


# --------------------------------------------------------------- hub-parsning
def extract_pupils(html: str) -> list[dict[str, Any]]:
    """Plockar ut IMHome.pupils ur hub-startsidans HTML."""
    marker = '"pupils":['
    start = html.find(marker)
    if start < 0:
        return []
    open_idx = html.index("[", start)
    depth = 0
    for index in range(open_idx, len(html)):
        char = html[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[open_idx : index + 1])
                except json.JSONDecodeError:
                    return []
    return []


# --------------------------------------------------------------- normalisering
def normalize_lessons(raw: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """gettimetablelist -> lektioner (utan raster/lunch)."""
    out: list[dict[str, Any]] = []
    for item in raw or []:
        title = str(item.get("title") or "").strip()
        if not title or is_break(title):
            continue
        notes = item.get("notes") or {}
        out.append(
            {
                "title": title,
                "start": str(item.get("start") or ""),
                "end": str(item.get("end") or ""),
                "room": str(notes.get("roomInfo") or item.get("details") or ""),
                "teachers": str(notes.get("tutors") or "").strip(),
                "all_day": bool(item.get("allDay")),
                "is_pe": is_pe(title),
            }
        )
    return out


def normalize_calendar(raw: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """calendarv2/getentries -> kalenderposter (utan uppgifts-dubbletter)."""
    out: list[dict[str, Any]] = []
    for item in raw or []:
        if str(item.get("url") or "").startswith("/task/show/"):
            continue
        subjects = item.get("subjects") or []
        out.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or "").strip(),
                "start": str(item.get("startDateFull") or item.get("startDate") or ""),
                "end": str(item.get("endDateFull") or item.get("endDate") or ""),
                "all_day": bool(item.get("isAllDayEvent")),
                "subjects": ", ".join(
                    str(s.get("title"))
                    for s in subjects
                    if isinstance(s, Mapping) and s.get("title")
                ),
            }
        )
    return out


def normalize_tasks(raw: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """task/GetTasks -> uppgifter."""
    out: list[dict[str, Any]] = []
    for item in raw or []:
        out.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or "").strip(),
                "subject": str(item.get("subject") or ""),
                "due": day_of(item.get("dueDate")),
                "status": str(item.get("status") or ""),
                "status_text": str(item.get("statusText") or ""),
                "overdue": bool(item.get("isOverdue")),
                "assigned": str(item.get("assignedOn") or ""),
            }
        )
    return out


def normalize_notifications(
    raw: Iterable[Mapping[str, Any]], child_by_id: Mapping[str, str]
) -> list[dict[str, Any]]:
    """GetNotifications -> notiser, mappade till barn via pupilSourceId."""
    out: list[dict[str, Any]] = []
    for item in raw or []:
        source = str(item.get("pupilSourceId") or "")
        pupil_id = source.split("|")[1] if "|" in source else ""
        out.append(
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("appType") or ""),
                "title": str(item.get("title") or ""),
                "sub_title": str(item.get("subTitle") or ""),
                "date": str(item.get("dateSent") or item.get("orderDate") or ""),
                "url": str(item.get("url") or ""),
                "child_id": pupil_id,
                "child": child_by_id.get(pupil_id, ""),
            }
        )
    return out


def normalize_attendance(raw: Mapping[str, Any]) -> dict[str, Any]:
    """attendance/appData -> närvaro."""

    def sessions(key: str) -> list[str]:
        return [
            f"{s.get('title')} {s.get('formattedTimeString', '')}".strip()
            for s in raw.get(key) or []
            if s.get("isAbsent")
        ]

    return {
        "absent_today": bool(raw.get("absentToday")),
        "absent_tomorrow": bool(raw.get("absentTomorrow")),
        "today": sessions("absenceTodaySessions"),
        "tomorrow": sessions("absenceTomorrowSessions"),
        "pending_leave": len(raw.get("leaveRequests") or []),
    }


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(value: Any) -> str:
    """Gör om enkel HTML till läsbar text."""
    text = _TAG_RE.sub(" ", str(value or ""))
    text = decode_html(text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def normalize_learnlog(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """learnlog/appData -> lärlogg-poster (veckobrev m.m.), nyast först."""
    out: list[dict[str, Any]] = []
    for entry in raw.get("entries") or []:
        if not isinstance(entry, Mapping):
            continue
        attachments = [
            str(a.get("fileName"))
            for a in entry.get("attachments") or []
            if isinstance(a, Mapping) and a.get("fileName")
        ]
        out.append(
            {
                "id": str(entry.get("id") or ""),
                "title": str(entry.get("title") or "").strip(),
                "text": strip_html(entry.get("text")),
                "subject": str(entry.get("subjectsCoursesDisplayString") or ""),
                "group": str(entry.get("groupName") or ""),
                "modified": str(entry.get("lastModifiedOn") or ""),
                "attachments": attachments,
            }
        )
    return out


def normalize_plans(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """uolv2/GetUols -> planeringar (Unit of Learning), icke-avslutade först.

    `subjects` i svaret är en id-lista; vi översätter den till namn så att
    dashboarden kan visa ämnet direkt.
    """
    subjects = {
        str(subject.get("id")): str(subject.get("name") or "")
        for subject in raw.get("subjects") or []
        if isinstance(subject, Mapping)
    }
    out: list[dict[str, Any]] = []
    for item in raw.get("uols") or []:
        if not isinstance(item, Mapping):
            continue
        out.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or "").strip(),
                "subjects": [
                    name for sid in item.get("subjects") or [] if (name := subjects.get(str(sid)))
                ],
                "state": str(item.get("state") or ""),
            }
        )
    # Stabil sortering: aktiva (och notstarted) före avslutade.
    out.sort(key=lambda plan: plan["state"] == "finished")
    return out


def parse_teachers(value: Any) -> list[str]:
    """'Kvarnbrink,   Erika,Gomez Fraga,  Angeles' -> ['Erika Kvarnbrink', …].

    Lärarlistan är parade "Efternamn, Förnamn" med kommatecken emellan.
    """
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    out: list[str] = []
    for index in range(0, len(parts) - 1, 2):
        out.append(f"{parts[index + 1]} {parts[index]}")
    if len(parts) % 2:
        out.append(parts[-1])
    return out


def normalize_plan_detail(raw: Mapping[str, Any]) -> dict[str, Any]:
    """uolv2/GetUol -> period, termins/årskursetiketter och lärare.

    Den pedagogiska planeringens texter och kunskapskraven lämnas medvetet bort:
    de hör hemma i mejlet/historiken, och attributen ska hållas små.
    """
    overview: dict[str, str] = {}
    for section in raw.get("sections") or []:
        if not isinstance(section, Mapping) or section.get("type") != "uol":
            continue
        for row in section.get("overview") or []:
            if isinstance(row, Mapping) and row.get("label"):
                overview[str(row["label"]).strip()] = str(row.get("value") or "")
    return {
        "term": overview.get("Termin", ""),
        "start": day_of(overview.get("Startdatum")),
        "end": day_of(overview.get("Slutdatum")),
        "grade": overview.get("Årskurs", ""),
        "teachers": parse_teachers(overview.get("Lärare", "")),
    }


def _as_int(value: Any) -> int:
    """Tål strängar och skräp — delmålsräknarna kan komma som text."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_plan_tasks(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """uolv2/GetAllTasks -> uppgifterna som hör till planeringen.

    Trimmade (id, titel, förfallodag, status) och sorterade på förfallodatum —
    en dashboard behöver sällan mer, och attributen ska hållas små. Delmålen
    följer med som "2/3" när planeringen använder dem.
    """
    out: list[dict[str, Any]] = []
    for task in raw.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        item: dict[str, Any] = {
            "id": str(task.get("id") or ""),
            "title": str(task.get("title") or "").strip(),
            "due": day_of(task.get("dueDate")),
            "status": str(task.get("status") or ""),
        }
        total = _as_int(task.get("milestoneCount"))
        if total > 0:
            item["milestones"] = f"{_as_int(task.get('milestonesComplete'))}/{total}"
        out.append(item)
    out.sort(key=lambda task: task["due"] or "9999")
    return out


def parse_mateo_unit(value: Any) -> str | None:
    """Tar emot en Mateo-URL eller ett id och returnerar enhets-id:t.

    T.ex. 'https://meny.mateo.se/kommun/123' -> '172', '172' -> '172'.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    match = re.search(r"/(\d+)(?:[/?#]|$)", text)
    return match.group(1) if match else None


def normalize_news(raw: Iterable[Mapping[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """News/GetNewsList -> senaste nyheterna (nyast först)."""
    items = []
    for item in raw or []:
        items.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or "").strip(),
                "published": day_of(item.get("publishedDate")),
                "by": str(item.get("publishedBy") or ""),
            }
        )
    return sorted(items, key=lambda x: x["published"], reverse=True)[:limit]


def parse_mateo_days(payload: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Mateo api/v1/days -> { 'YYYY-MM-DD': [{label, dish}] }."""
    menu: dict[str, list[dict[str, str]]] = {}
    for day in payload or []:
        key = day_of(day.get("date"))
        if not key:
            continue
        menu[key] = [
            {"label": str(meal.get("type") or "Lunch"), "dish": str(meal.get("name") or "")}
            for meal in day.get("meals") or []
            if meal.get("name")
        ]
    return menu


# --------------------------------------------------------------- härledning
def next_school_day(
    lessons: Sequence[Mapping[str, Any]], from_day: date, include_today: bool = False
) -> str | None:
    """Första dagen (från from_day) som har lektioner."""
    days = sorted({day_of(item.get("start")) for item in lessons if day_of(item.get("start"))})
    for day in days:
        parsed = to_date(day)
        if parsed is None:
            continue
        if parsed > from_day or (include_today and parsed == from_day):
            return day
    return None


def lessons_on(lessons: Sequence[Mapping[str, Any]], day: str) -> list[dict[str, Any]]:
    return sorted(
        (dict(item) for item in lessons if day_of(item.get("start")) == day),
        key=lambda item: item.get("start", ""),
    )


def school_day_bounds(lessons: Sequence[Mapping[str, Any]]) -> tuple[str, str] | None:
    """(start, slut) för en dags lektioner, som 'HH:MM'."""
    if not lessons:
        return None
    first = min(item.get("start", "") for item in lessons)
    last = max((item.get("end") or item.get("start") or "") for item in lessons)
    return time_of(first), time_of(last)


def pe_lessons(lessons: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(item) for item in lessons if is_pe(item.get("title"))]


def tasks_due(
    tasks: Iterable[Mapping[str, Any]], today: date, days: int = 7
) -> list[dict[str, Any]]:
    """Oavklarade uppgifter som förfaller inom `days` dagar (eller är försenade)."""
    limit = today + timedelta(days=days)
    out = []
    for task in tasks or []:
        if is_done(task.get("status")):
            continue
        due = to_date(task.get("due"))
        if due is None or due > limit:
            continue
        out.append(dict(task))
    return sorted(out, key=lambda item: item.get("due", ""))


def upcoming_event(calendar: Iterable[Mapping[str, Any]], today: date) -> dict[str, Any] | None:
    future = [
        dict(item)
        for item in calendar or []
        if (to_date(item.get("start")) or date.min) >= today
    ]
    return min(future, key=lambda item: item.get("start", "")) if future else None


def lunch_for(
    menu: Mapping[str, Sequence[Mapping[str, str]]], day: str | None
) -> list[dict[str, str]]:
    if not day:
        return []
    return [dict(item) for item in menu.get(day, [])]
