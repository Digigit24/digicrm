"""RFC 5545 recurrence helpers for meetings.

``Meeting.start_at`` / ``end_at`` are always UTC instants; ``Meeting.timezone`` is
the IANA zone the meeting was *authored* in.  An RRULE must be expanded in that
zone (not UTC) so a weekly 09:00 Asia/Kolkata standup stays 09:00 local across
DST boundaries elsewhere in the world.

Nothing here touches the database.
"""
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from dateutil.rrule import rrulestr

# Hard caps so one pathological series cannot melt a range query.
MAX_OCCURRENCES_PER_SERIES = 750
MAX_RANGE_DAYS = 366

_UNTIL_RE = re.compile(r'(UNTIL=)([0-9T:\-]+Z?)', re.IGNORECASE)
_COUNT_RE = re.compile(r'(^|;)COUNT=\d+', re.IGNORECASE)

_AVAILABLE_TIMEZONES = None


def available_timezone_names():
    """Cached ``zoneinfo.available_timezones()`` (the call itself is expensive)."""
    global _AVAILABLE_TIMEZONES
    if _AVAILABLE_TIMEZONES is None:
        _AVAILABLE_TIMEZONES = available_timezones()
    return _AVAILABLE_TIMEZONES


def is_valid_timezone(name):
    if not name:
        return False
    if name in available_timezone_names():
        return True
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    return True


def get_zone(name):
    """Return a ZoneInfo for ``name``, falling back to UTC for unknown zones."""
    try:
        return ZoneInfo(name or 'UTC')
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo('UTC')


def to_utc(value):
    """Coerce an aware datetime to UTC (naive values are assumed to be UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(dt_timezone.utc)


def local_date(instant, tz_name):
    """The calendar date ``instant`` falls on in ``tz_name`` -- never the UTC date.

    This is the bug the calendar UI exposes on day one: an IST user's 02:00
    meeting is 20:30 UTC *the previous day*.
    """
    return to_utc(instant).astimezone(get_zone(tz_name)).date()


# ---------------------------------------------------------------------------
# RRULE string handling
# ---------------------------------------------------------------------------
def normalize_rule(rule):
    """Strip an ``RRULE:`` prefix and make ``UNTIL`` explicitly UTC.

    ``dateutil`` refuses to build an rrule whose UNTIL is naive while DTSTART is
    timezone-aware, and we always expand against an aware DTSTART.
    """
    if not rule:
        return None
    cleaned = rule.strip()
    if cleaned.upper().startswith('RRULE:'):
        cleaned = cleaned[6:]
    cleaned = cleaned.replace('\n', '').strip()

    def _fix(match):
        value = match.group(2)
        if value.endswith('Z'):
            return match.group(1) + value
        # A date-only UNTIL (YYYYMMDD) is legal; make it an end-of-day UTC instant.
        if len(value) == 8 and value.isdigit():
            return match.group(1) + value + 'T235959Z'
        return match.group(1) + value + 'Z'

    return _UNTIL_RE.sub(_fix, cleaned)


def build_rrule(rule, dtstart_utc, tz_name):
    """Return a ``dateutil`` rruleset/rrule anchored at ``dtstart`` in ``tz_name``.

    Raises ``ValueError`` for an unparseable rule.
    """
    normalized = normalize_rule(rule)
    if not normalized:
        return None
    zone = get_zone(tz_name)
    dtstart_local = to_utc(dtstart_utc).astimezone(zone)
    return rrulestr(normalized, dtstart=dtstart_local)


def rule_is_valid(rule, dtstart_utc, tz_name):
    try:
        build_rrule(rule, dtstart_utc, tz_name)
    except Exception:
        return False
    return True


def compute_recurrence_end(rule, dtstart_utc, tz_name):
    """Denormalised UTC instant of the last possible occurrence.

    ``None`` means the series is open-ended, which a range query treats as
    "always in range".
    """
    if not rule:
        return None
    normalized = normalize_rule(rule)
    upper = normalized.upper()
    if 'UNTIL=' not in upper and 'COUNT=' not in upper:
        return None
    try:
        rule_obj = build_rrule(normalized, dtstart_utc, tz_name)
    except Exception:
        return None
    last = None
    for index, occurrence in enumerate(rule_obj):
        last = occurrence
        if index >= MAX_OCCURRENCES_PER_SERIES:
            break
    return to_utc(last) if last else None


def set_rule_until(rule, until_utc):
    """Return ``rule`` with COUNT stripped and UNTIL set to ``until_utc``.

    Used by the ``this_and_following`` split: the original master is clipped so
    it stops just before the occurrence the user edited.
    """
    normalized = normalize_rule(rule)
    if not normalized:
        return None
    stamp = to_utc(until_utc).strftime('%Y%m%dT%H%M%SZ')
    parts = [
        part for part in normalized.split(';')
        if part and not part.upper().startswith(('COUNT=', 'UNTIL='))
    ]
    parts.append(f'UNTIL={stamp}')
    return ';'.join(parts)


def remaining_rule(rule, dtstart_utc, tz_name, split_at_utc):
    """The rule for the *tail* of a series that was split at ``split_at_utc``.

    ``UNTIL`` is preserved as-is; ``COUNT`` is reduced by the number of
    occurrences that stay with the original master.
    """
    normalized = normalize_rule(rule)
    if not normalized:
        return None
    upper = normalized.upper()
    if 'COUNT=' not in upper:
        return normalized

    try:
        rule_obj = build_rrule(normalized, dtstart_utc, tz_name)
    except Exception:
        return normalized

    split_at = to_utc(split_at_utc)
    consumed = 0
    total = 0
    for index, occurrence in enumerate(rule_obj):
        total += 1
        if to_utc(occurrence) < split_at:
            consumed += 1
        if index >= MAX_OCCURRENCES_PER_SERIES:
            break

    remaining = max(total - consumed, 1)
    return _COUNT_RE.sub(lambda m: f'{m.group(1)}COUNT={remaining}', normalized, count=1)


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------
def parse_exdates(raw):
    """Normalise ``Meeting.recurrence_exdates`` into a set of UTC instants."""
    result = set()
    for item in raw or []:
        parsed = parse_instant(item)
        if parsed is not None:
            result.add(parsed)
    return result


def parse_instant(value):
    """Parse an ISO-8601 string (or datetime) into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_utc(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        return to_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def expand_occurrences(meeting, range_start, range_end, include_exdates=False,
                       limit=MAX_OCCURRENCES_PER_SERIES):
    """UTC start instants of ``meeting`` that overlap ``[range_start, range_end)``.

    A non-recurring meeting yields at most its own ``start_at``.  A master yields
    every RRULE occurrence whose *interval* (start + duration) intersects the
    window, minus EXDATEs.
    """
    range_start = to_utc(range_start)
    range_end = to_utc(range_end)
    start_at = to_utc(meeting.start_at)
    end_at = to_utc(meeting.end_at)
    duration = (end_at - start_at) if end_at and start_at else timedelta(0)

    if not meeting.recurrence_rule:
        if start_at < range_end and (start_at + duration) > range_start:
            return [start_at]
        # Zero-duration markers still belong to the window they start in.
        if duration == timedelta(0) and range_start <= start_at < range_end:
            return [start_at]
        return []

    try:
        rule_obj = build_rrule(meeting.recurrence_rule, start_at, meeting.timezone)
    except Exception:
        return []
    if rule_obj is None:
        return []

    exdates = set() if include_exdates else parse_exdates(meeting.recurrence_exdates)

    results = []
    # An occurrence starting before the window can still overlap it, so rewind
    # the scan by one duration.
    scan_from = range_start - duration
    for index, occurrence in enumerate(rule_obj):
        if index >= limit:
            break
        occurrence_utc = to_utc(occurrence)
        if occurrence_utc >= range_end:
            break
        if occurrence_utc + duration <= scan_from:
            continue
        if occurrence_utc + duration <= range_start and duration > timedelta(0):
            continue
        if duration == timedelta(0) and occurrence_utc < range_start:
            continue
        if occurrence_utc in exdates:
            continue
        results.append(occurrence_utc)
        if len(results) >= limit:
            break
    return results


def is_valid_occurrence(meeting, occurrence_start):
    """True when ``occurrence_start`` is a real (non-excluded) occurrence.

    Used to reject ``edit_scope=this`` requests carrying a bogus instant.
    """
    target = to_utc(occurrence_start)
    if target is None:
        return False
    if not meeting.recurrence_rule:
        return to_utc(meeting.start_at) == target
    window_start = target - timedelta(seconds=1)
    window_end = target + timedelta(seconds=1)
    occurrences = expand_occurrences(meeting, window_start, window_end, include_exdates=True)
    return target in occurrences


def all_day_bounds(local_date_value, tz_name, days=1):
    """UTC (start, end) for an all-day event.

    Per A.7 rule 3 the end is the *next* local midnight (exclusive DTEND,
    matching RFC 5545 ``DTEND;VALUE=DATE``) -- never ``start == end``.
    """
    zone = get_zone(tz_name)
    start_local = datetime.combine(local_date_value, datetime.min.time(), tzinfo=zone)
    end_local = start_local + timedelta(days=max(days, 1))
    return to_utc(start_local), to_utc(end_local)
