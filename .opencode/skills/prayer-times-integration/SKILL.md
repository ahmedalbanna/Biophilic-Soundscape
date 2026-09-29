---
name: prayer-times-integration
description: Fetching and caching prayer times from the Aladhan API, and building mute/lock windows around them. Use when wiring live prayer schedules into an app, handling adhan pre-emptive muting, or when an API date format or fallback chain is wrong.
---

# Prayer Times Integration

Live prayer schedules drive hard mute windows, so both the fetch and the window
math have to be right. Patterns from a working Aladhan integration.

## Fetch by city and method, not hardcoded

```python
url = "https://api.aladhan.com/v1/timingsByCity"
params = {
    "city": config.city,        # "Sanaa"
    "country": config.country,  # "Yemen"
    "method": config.prayer_method,  # 3 = MWL (رابطة العالم الإسلامي)
    "date": f"{day.day:02d}-{day.month:02d}-{day.year:04d}",
}
```

Read location and method from config. Hardcoding the city means the app cannot
be pointed anywhere else without a code change.

## The date format is DD-MM-YYYY

This is the single most common bug, and it fails confusingly: the request
succeeds, so you assume it worked.

```python
# wrong - strftime with a % tuple raises ValueError at runtime
day.strftime("%%d-%%m-%%Y") % (day.day, day.month, day.year)

# right - f-string, zero padded
f"{day.day:02d}-{day.month:02d}-{day.year:04d}"
```

Always assert on a parsed field after the first request. A wrong date returns
valid-looking timings for the wrong day, so no error surfaces.

## Parse defensively

Aladhan returns values with a suffix:

```python
"04:42 (EET)"
```

```python
def _parse_hhmm(value: str) -> time:
    token = value.strip().split()[0]   # drop "(EET)"
    h, m = token.split(":")[:2]
    return time(int(h), int(m))
```

## Cache per day, and never block the UI

```python
if cached and cached["date"] == date.today().isoformat():
    return cached_times, "cache"
try:
    return fetch(...), "api"
except Exception:
    return stale_cache or default_times, "cache"     # or "default"
```

Three levels, worst to best: offline defaults → any cached day → API. Return
the source alongside the times so the UI can show which one it got.

Fetch on a worker thread and hand the result back through a queue — see the
`windows-tk-audio` skill. A blocking HTTPS call on the UI thread freezes the
window for the full timeout.

Refresh when the date changes, not on a fixed timer. Comparing
`date.today()` catches the case where the machine is left running overnight.

## Window math

```python
mute_start = adhan - timedelta(minutes=pre_adhan_mute_min)   # -3
lock_end   = adhan + timedelta(minutes=prayer_duration_min + post_prayer_lock_min)  # +20+15
```

- Mute **before** the adhan, per the specification. Muting at the adhan is
  already too late.
- The post-prayer lock is the long tail: 20 min prayer + 15 min lock.
- Half-open comparison `mute_start <= now < lock_end` — otherwise the last
  instant of the window is ambiguous.

Check **today and yesterday**. Isha can extend past midnight, so yesterday's
Isha window is what is active at 00:30. Checking only today produces a silent
bug that appears once a day.

```python
for day in (now.date(), now.date() - timedelta(days=1)):
    windows = cached_windows(day) if day == now.date() else build_windows(day)
```

## Expose a separate moment-level query

The mute window and the adhan instant are different questions. A chime belongs
at the adhan; silence belongs across the whole window.

```python
def athan_moment(self, now, window_sec=60):
    for w in windows:
        if 0 <= (now - w.adhan).total_seconds() < window_sec:
            return w.name
    return None
```

Dedupe on `(date, prayer_name)` so a chime cannot retrigger across ticks.

## Keep the fallback times honest

Default times are for tests and offline startup only. Label them clearly
(`default_times()`, "approximate, not for real use") so nobody mistakes them
for authoritative data.

Do not reach into another module's private state for the fallback. Expose
`PrayerEngine.default_times()` as a public method and call that — reaching
into `_times` couples the provider to the engine's internals.

## Friday

On Friday, Dhuhr is replaced by Jumu'ah. Keep the computation identical and
adjust only the display name, so the mute window logic never branches on
weekday.
