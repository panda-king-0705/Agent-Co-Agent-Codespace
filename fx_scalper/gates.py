"""Session, Friday and blackout news entry gates."""
from __future__ import annotations
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from .settings import Settings

def entry_allowed(when: datetime, settings: Settings) -> bool:
    if when.tzinfo is None:
        return False
    utc = when.astimezone(timezone.utc)
    start = time.fromisoformat(settings.session_start_utc)
    end = time.fromisoformat(settings.session_end_utc)
    if not start <= utc.time() < end:
        return False
    if utc.weekday() == 4 and utc.time() >= time.fromisoformat(settings.no_entry_friday_utc):
        return False
    try:
        events = json.loads(Path(settings.blackout_file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        events = []
    except (OSError, ValueError):
        return False
    pause = timedelta(minutes=settings.news_pause_minutes)
    for item in events:
        try:
            event_time = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                return False
            if event_time - pause <= utc <= event_time + pause:
                return False
        except (KeyError, TypeError, ValueError):
            return False
    return True
