from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

from .weather import HourlySlice

# Thresholds
TEMP_NOGO = 45.0        # °F
TEMP_CAUTION_LOW = 45.0
TEMP_CAUTION_HIGH = 50.0
WIND_NOGO = 50.0        # mph (sustained or gust)
WIND_CAUTION_LOW = 40.0
WIND_CAUTION_HIGH = 50.0
PRECIP_PROB_CAUTION_LOW = 30    # %
PRECIP_PROB_CAUTION_HIGH = 50   # %


@dataclass
class Assessment:
    status: str                         # "GO", "CAUTION", "NO-GO"
    nogo_reasons: list[str] = field(default_factory=list)
    caution_notes: list[str] = field(default_factory=list)
    temp_min: float = 0.0
    temp_max: float = 0.0
    wind_min: float = 0.0
    wind_max: float = 0.0
    gust_max: float = 0.0
    has_precip: bool = False
    precip_window: str = ""             # human-readable window, e.g. "9:00 AM - 1:00 PM"
    precip_prob_max: int = 0
    conditions_summary: str = ""        # dominant description string


def evaluate(
    slices: list[HourlySlice],
    sunrise: datetime,
    sunset: datetime,
    window_start: datetime,
    window_end: datetime,
    overnight_slices: list[HourlySlice] | None = None,
    nws_alerts: list[str] | None = None,
) -> Assessment:
    """Evaluate ride conditions and return an Assessment."""
    nogo: list[str] = []
    caution: list[str] = []

    temps = [s.temp_f for s in slices]
    winds = [s.wind_mph for s in slices]
    gusts = [s.gust_mph for s in slices]
    probs = [s.precip_prob for s in slices]

    temp_min = min(temps)
    temp_max = max(temps)
    wind_min = min(winds)
    wind_max = max(winds)
    gust_max = max(gusts)
    precip_prob_max = max(probs)

    # --- NO-GO checks ---
    if round(temp_min) < TEMP_NOGO:
        nogo.append(f"Temperature below 45°F (low of {temp_min:.0f}°F)")

    precip_slices = [s for s in slices if s.has_precip]
    precip_window_str = ""
    if precip_slices:
        start_str = precip_slices[0].time.strftime("%-I:%M %p")
        if len(precip_slices) == 1:
            precip_window_str = f"starting {start_str}"
        else:
            end_str = precip_slices[-1].time.strftime("%-I:%M %p")
            precip_window_str = f"{start_str} - {end_str}"
        first_precip = precip_slices[0]
        desc = first_precip.description.lower()
        if any(w in desc for w in ("snow", "ice", "hail", "freezing", "sleet", "pellet")):
            nogo.append(f"Winter precipitation in forecast ({precip_window_str})")
        elif "thunder" in desc:
            nogo.append(f"Thunderstorms in forecast ({precip_window_str})")
        else:
            nogo.append(f"Rain in forecast ({precip_window_str})")

    if gust_max > WIND_NOGO:
        nogo.append(f"Wind gusts exceed 50 mph ({gust_max:.0f} mph)")
    elif wind_max > WIND_NOGO:
        nogo.append(f"Sustained winds exceed 50 mph ({wind_max:.0f} mph)")

    # NWS active hazard/warning alerts
    if nws_alerts:
        for alert in nws_alerts:
            nogo.append(f"NWS Alert: {alert}")

    # --- CAUTION checks (only evaluated when not already NO-GO) ---
    if not nogo:
        if TEMP_CAUTION_LOW <= round(temp_min) < TEMP_CAUTION_HIGH:
            caution.append(f"Temperature near threshold: low of {temp_min:.0f}°F")

        if WIND_CAUTION_LOW <= gust_max < WIND_CAUTION_HIGH:
            caution.append(f"Wind gusts {gust_max:.0f} mph (approaching 50 mph threshold)")
        elif WIND_CAUTION_LOW <= wind_max < WIND_CAUTION_HIGH:
            caution.append(f"Sustained winds {wind_max:.0f} mph (approaching 50 mph threshold)")

        if PRECIP_PROB_CAUTION_LOW <= precip_prob_max < PRECIP_PROB_CAUTION_HIGH and not precip_slices:
            caution.append(f"Rain probability {precip_prob_max}% (elevated but no precipitation in forecast)")

        # Overnight rain check
        if overnight_slices and any(s.has_precip for s in overnight_slices):
            last_rain = max(s.time for s in overnight_slices if s.has_precip)
            caution.append(f"Roads may be wet (rain overnight until {last_rain.strftime('%-I:%M %p')})")

        # Partial darkness check — single consolidated note per spec
        if window_start < sunrise or window_end > sunset:
            caution.append(
                f"Reduced visibility: sunrise at {sunrise.strftime('%-I:%M %p')} "
                f"/ sunset at {sunset.strftime('%-I:%M %p')}"
            )

    # --- Determine status ---
    if nogo:
        status = "NO-GO"
    elif caution:
        status = "CAUTION"
    else:
        status = "GO"

    return Assessment(
        status=status,
        nogo_reasons=nogo,
        caution_notes=caution,
        temp_min=temp_min,
        temp_max=temp_max,
        wind_min=wind_min,
        wind_max=wind_max,
        gust_max=gust_max,
        has_precip=bool(precip_slices),
        precip_window=precip_window_str,
        precip_prob_max=precip_prob_max,
        conditions_summary=_dominant_description(slices),
    )


def _dominant_description(slices: list[HourlySlice]) -> str:
    counts: dict[str, int] = {}
    for s in slices:
        counts[s.description] = counts.get(s.description, 0) + 1
    if not counts:
        return "Unknown"
    return max(counts, key=lambda k: counts[k])
