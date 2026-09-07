"""Portable calendar reminders; exporting does not install a background service."""

import datetime
import hashlib

from . import inseason


def _utc(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _text(value: str) -> str:
    return value.replace("\r", "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def calendar(state: inseason.WeekState) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ffopt//Lineup reminders//EN"]
    for wave in inseason.lock_waves(state):
        if wave.passed:
            continue
        uid = hashlib.sha256(
            f"{state.league_id}:{state.my_roster_id}:{wave.kickoff_ms}".encode()
        ).hexdigest()
        lines.extend([
            "BEGIN:VEVENT", f"UID:{uid}@ffopt",
            f"DTSTAMP:{_utc(state.fetched_at_ms)}",
            f"DTSTART:{_utc(wave.kickoff_ms)}",
            f"DTEND:{_utc(wave.kickoff_ms + 60000)}",
            "SUMMARY:Fantasy lineup lock",
            "DESCRIPTION:" + _text(
                ", ".join(c.name for c in wave.players)
                + ". Re-run python3 scripts/week.py --refresh; check official inactives "
                "and save the lineup in Sleeper before kickoff."
            ),
        ])
        for minutes in (90, 15):
            # Do not export alarms which have already elapsed.
            if wave.kickoff_ms - minutes * 60000 <= state.fetched_at_ms:
                continue
            lines.extend([
                "BEGIN:VALARM", f"TRIGGER:-PT{minutes}M", "ACTION:DISPLAY",
                "DESCRIPTION:Check fantasy lineup and injury updates", "END:VALARM",
            ])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    # RFC 5545: fold at 75 octets without splitting a UTF-8 code point.
    folded = []
    for line in lines:
        part = ""
        for character in line:
            if len((part + character).encode("utf-8")) > 75:
                folded.append(part)
                part = " "
            part += character
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"
