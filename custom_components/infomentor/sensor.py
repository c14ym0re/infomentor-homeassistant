"""Sensorer: skoldag, uppgifter, nästa händelse och skolmat."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import InfomentorCoordinator, PupilData
from .entity import InfomentorHubEntity, InfomentorPupilEntity
from .util import (
    lessons_on,
    lunch_for,
    next_school_day,
    school_day_bounds,
    tasks_due,
    tasks_overdue,
    upcoming_assignments,
    upcoming_event,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Skapa sensorer utifrån barnen i första hämtningen."""
    coordinator: InfomentorCoordinator = entry.runtime_data.coordinator
    data = coordinator.data
    entities: list[SensorEntity] = []
    if data is not None:
        for pupil in data.pupils:
            entities.append(SchoolDaySensor(coordinator, entry, pupil))
            entities.append(AssignmentsSensor(coordinator, entry, pupil))
            entities.append(NextEventSensor(coordinator, entry, pupil))
            entities.append(LearnlogSensor(coordinator, entry, pupil))
            entities.append(PlansSensor(coordinator, entry, pupil))
        entities.append(NewsSensor(coordinator, entry))
        if data.lunch_unit:
            entities.append(LunchSensor(coordinator, entry))
    async_add_entities(entities)


class _PupilSensor(InfomentorPupilEntity, SensorEntity):
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: InfomentorCoordinator,
        entry: ConfigEntry,
        pupil: PupilData,
        key: str,
    ) -> None:
        InfomentorPupilEntity.__init__(self, coordinator, entry, pupil, key)

    @property
    def _today(self) -> date:
        return dt_util.now().date()


class SchoolDaySensor(_PupilSensor):
    """Skoldagens start–slut för nästa skoldag."""

    _attr_translation_key = "school_day"
    _attr_icon = "mdi:school"

    def __init__(self, coordinator, entry, pupil) -> None:
        super().__init__(coordinator, entry, pupil, "school_day")

    @property
    def native_value(self) -> str | None:
        pupil = self.pupil
        if pupil is None:
            return None
        day = next_school_day(pupil.lessons, self._today)
        bounds = school_day_bounds(lessons_on(pupil.lessons, day)) if day else None
        return f"{bounds[0]}–{bounds[1]}" if bounds else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pupil = self.pupil
        if pupil is None:
            return {}
        day = next_school_day(pupil.lessons, self._today)
        lessons = lessons_on(pupil.lessons, day) if day else []
        return {
            "date": day,
            "first_lesson": lessons[0]["title"] if lessons else None,
            "last_lesson": lessons[-1]["title"] if lessons else None,
            "lesson_count": len(lessons),
        }


class AssignmentsSensor(_PupilSensor):
    """Antal uppgifter som förfaller inom sju dagar."""

    _attr_translation_key = "assignments"
    _attr_icon = "mdi:clipboard-text-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "st"

    def __init__(self, coordinator, entry, pupil) -> None:
        super().__init__(coordinator, entry, pupil, "assignments")

    @property
    def native_value(self) -> int | None:
        pupil = self.pupil
        if pupil is None:
            return None
        return len(tasks_due(pupil.tasks, self._today, 7))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pupil = self.pupil
        if pupil is None:
            return {}
        due = tasks_due(pupil.tasks, self._today, 7)
        return {
            "assignments": [
                {"title": task["title"], "subject": task["subject"], "due": task["due"]}
                for task in due
            ],
            # InfoMentors egen isOverdue sätts inte när dagen passerar, så
            # räkna själva — annars står det alltid 0.
            "overdue": len(tasks_overdue(pupil.tasks, self._today)),
        }


class NextEventSensor(_PupilSensor):
    """Nästa kalenderhändelse."""

    _attr_translation_key = "next_event"
    _attr_icon = "mdi:calendar-star"

    def __init__(self, coordinator, entry, pupil) -> None:
        super().__init__(coordinator, entry, pupil, "next_event")

    @property
    def native_value(self) -> str | None:
        pupil = self.pupil
        if pupil is None:
            return None
        event = upcoming_event(pupil.calendar, self._today)
        return event["title"] if event else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pupil = self.pupil
        if pupil is None:
            return {}
        event = upcoming_event(pupil.calendar, self._today)
        if event is None:
            return {}
        return {"date": event["start"][:10], "subjects": event["subjects"]}


class LearnlogSensor(_PupilSensor):
    """Lärloggen — veckobrev och annat läraren publicerar.

    State är den senaste postens rubrik; hela listan (rubrik, ämne, datum och
    text) ligger som attribut så den går att visa i en dashboard eller skicka.
    """

    _attr_translation_key = "learnlog"
    _attr_icon = "mdi:email-newsletter"

    def __init__(self, coordinator, entry, pupil) -> None:
        super().__init__(coordinator, entry, pupil, "learnlog")

    @property
    def native_value(self) -> str | None:
        pupil = self.pupil
        if pupil is None or not pupil.learnlog:
            return None
        return pupil.learnlog[0]["title"] or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pupil = self.pupil
        if pupil is None:
            return {}
        return {
            "count": len(pupil.learnlog),
            "entries": [
                {
                    "title": e["title"],
                    "subject": e["subject"],
                    "modified": e["modified"],
                    "text": e["text"],
                    "attachments": e["attachments"],
                }
                for e in pupil.learnlog[:5]
            ],
        }


class PlansSensor(_PupilSensor):
    """Planeringar — vad klassen arbetar med just nu.

    State är antalet aktiva planeringar (Unit of Learning); attributet `plans`
    innehåller de icke-avslutade med titel, ämne, status, period, lärare, termin
    och `assignments` (planeringens kvarvarande prov och inlämningar — de som
    redan passerat utelämnas), så en dashboard kan visa dem utan att ett anrop
    behövs per planering.
    """

    _attr_translation_key = "plans"
    _attr_icon = "mdi:book-open-page-variant-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "st"

    def __init__(self, coordinator, entry, pupil) -> None:
        super().__init__(coordinator, entry, pupil, "plans")

    @property
    def native_value(self) -> int | None:
        pupil = self.pupil
        if pupil is None:
            return None
        return sum(1 for plan in pupil.plans if plan["state"] == "active")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pupil = self.pupil
        if pupil is None:
            return {}
        # Bara kvarvarande uppgifter, så kortets "närmast" inte pekar på något
        # som redan varit (planeringen behåller dem i arkivet).
        open_plans = [
            {**plan, "assignments": upcoming_assignments(plan.get("assignments"), self._today)}
            for plan in pupil.plans
            if plan["state"] != "finished"
        ]
        return {
            "plans": open_plans,
            "active": sum(1 for plan in pupil.plans if plan["state"] == "active"),
            "not_started": sum(1 for plan in pupil.plans if plan["state"] == "notstarted"),
            "finished": sum(1 for plan in pupil.plans if plan["state"] == "finished"),
        }


class NewsSensor(InfomentorHubEntity, SensorEntity):
    """Skolans nyheter — antal, med de senaste som attribut."""

    _attr_should_poll = False
    _attr_translation_key = "news"
    _attr_icon = "mdi:newspaper-variant-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "st"

    def __init__(self, coordinator: InfomentorCoordinator, entry: ConfigEntry) -> None:
        InfomentorHubEntity.__init__(self, coordinator, entry, "news")

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return len(data.news) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "latest": data.news[0]["title"] if data.news else None,
            "news": data.news,
        }


class LunchSensor(InfomentorHubEntity, SensorEntity):
    """Skolmat för nästa skoldag."""

    _attr_should_poll = False
    _attr_translation_key = "lunch"
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator: InfomentorCoordinator, entry: ConfigEntry) -> None:
        InfomentorHubEntity.__init__(self, coordinator, entry, "lunch")

    def _next_day(self) -> str | None:
        data = self.coordinator.data
        if data is None:
            return None
        today = dt_util.now().date()
        days = [
            day
            for pupil in data.pupils
            if (day := next_school_day(pupil.lessons, today))
        ]
        return min(days) if days else None

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        dishes = lunch_for(data.lunch if data else {}, self._next_day())
        return "; ".join(dish["dish"] for dish in dishes) if dishes else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        day = self._next_day()
        dishes = lunch_for(data.lunch if data else {}, day)
        return {"date": day, "dishes": dishes, "unit": data.lunch_unit if data else None}
