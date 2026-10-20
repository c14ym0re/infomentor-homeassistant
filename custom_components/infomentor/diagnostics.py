"""Diagnostik för InfoMentor (känsliga fält maskas)."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .util import lessons_on, next_school_day, school_day_bounds, tasks_due

TO_REDACT = {"password", "username", "refresh_token"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Returnerar diagnostik för en config-post."""
    entry_info = {
        "title": entry.title,
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
    }
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        # Posten är inte laddad (t.ex. ett misslyckat setup); bara postdata finns.
        return {"entry": entry_info, "loaded": False}

    coordinator = runtime.coordinator
    data = coordinator.data
    today = date.today()

    pupils = []
    if data is not None:
        for pupil in data.pupils:
            day = next_school_day(pupil.lessons, today)
            pupils.append(
                {
                    "id": pupil.pupil_id,
                    "name": "REDACTED",
                    "lessons": len(pupil.lessons),
                    "calendar": len(pupil.calendar),
                    "tasks": len(pupil.tasks),
                    "tasks_due_7d": len(tasks_due(pupil.tasks, today, 7)),
                    "learnlog": len(pupil.learnlog),
                    "plans": len(pupil.plans),
                    "next_school_day": day,
                    "school_day": "–".join(school_day_bounds(lessons_on(pupil.lessons, day)))
                    if day
                    else None,
                    "absent_today": pupil.attendance.get("absent_today"),
                }
            )

    return {
        "entry": entry_info,
        "loaded": True,
        "last_update_success": coordinator.last_update_success,
        "last_exception": str(coordinator.last_exception)
        if coordinator.last_exception
        else None,
        "pupils": pupils,
        "lunch_days": len(data.lunch) if data else 0,
        "notifications": len(data.notifications) if data else 0,
        "news": len(data.news) if data else 0,
    }
