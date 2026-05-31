"""Extrapolate real-world capacity from a bench run's raw measurements.

We don't actually transcode 100 videos — that would take an hour. Instead we
model the machine as:

    one_video_time(L, preset)  =  L  /  speed_x_realtime[preset]
    batch_time(N, L, preset)   ≈  N * one_video_time(L, preset)  /  parallel_efficiency

Where parallel_efficiency comes from the concurrent-throughput sweep:

    parallel_efficiency  =  max(aggregate_speed_x_realtime) / single_speed_x_realtime

i.e. how much extra throughput we got by going parallel vs perfect linear scaling.
For an N-core machine bottlenecked on x264, this is typically 1.0–4.0×.

This is an *estimate*, not a guarantee. The error comes from:
  - GOP boundaries (short clips have higher overhead per file)
  - I/O contention not measured here
  - Thermal throttling on long batches

But it's the right model for "can my customer run their nightly pipeline".
"""
from __future__ import annotations
from typing import Optional


def _get(d: dict, *path, default=None):
    for p in path:
        if not isinstance(d, dict): return default
        d = d.get(p)
        if d is None: return default
    return d


def parallel_efficiency(run: dict) -> Optional[float]:
    """How much we gain by running concurrent jobs vs single. ≥1.0 means
    parallelism helps; ~1.0 means single-thread bound."""
    cpu = _get(run, "concurrent", "cpu", default=[]) or []
    if not cpu: return None
    single = next((e for e in cpu if e.get("n_parallel") == 1), None)
    if not single or not single.get("aggregate_speed_x_realtime"):
        return None
    best = max((e.get("aggregate_speed_x_realtime") or 0) for e in cpu)
    base = single["aggregate_speed_x_realtime"]
    return round(best / base, 2) if base else None


def best_concurrent_throughput(run: dict, kind: str = "cpu") -> Optional[dict]:
    entries = _get(run, "concurrent", kind, default=[]) or []
    if not entries: return None
    return max(entries, key=lambda e: e.get("aggregate_speed_x_realtime") or 0)


def max_realtime_streams(run: dict) -> Optional[int]:
    """Largest N where each parallel job still meets realtime (agg×rt ≥ N).
    Useful for 'how many simultaneous Twitch-style streams can this box hold'."""
    cpu = _get(run, "concurrent", "cpu", default=[]) or []
    best_n = 0
    for e in cpu:
        n = e.get("n_parallel", 0)
        agg = e.get("aggregate_speed_x_realtime") or 0
        if agg >= n:
            best_n = max(best_n, n)
    return best_n if best_n else None


def batch_minutes(run: dict, n_videos: int, video_seconds: int,
                  preset_key: str = "x264_1080p_medium") -> Optional[float]:
    """Estimate wall-clock minutes to process N videos of length L seconds.

    Uses single-clip speed and the measured parallel-efficiency boost.
    """
    speed = _get(run, "single", "tests", preset_key, "speed_x_realtime")
    if not speed: return None
    eff = parallel_efficiency(run) or 1.0
    total_content_s = n_videos * video_seconds
    wall_s = total_content_s / (speed * eff)
    return round(wall_s / 60.0, 2)


def single_video_seconds(run: dict, video_seconds: int,
                         preset_key: str = "x264_1080p_medium") -> Optional[float]:
    speed = _get(run, "single", "tests", preset_key, "speed_x_realtime")
    if not speed: return None
    return round(video_seconds / speed, 2)


def capacity_summary(run: dict) -> dict:
    """The headline 'what can this machine do' numbers, in customer-friendly units."""
    eff = parallel_efficiency(run)
    return {
        "parallel_efficiency_x": eff,
        "max_realtime_1080p_streams": max_realtime_streams(run),
        "best_throughput_videos_per_min": (
            (best_concurrent_throughput(run, "cpu") or {}).get("videos_per_minute")),
        # Headline scenarios
        "scenario_100x_1min_youtube_medium_min": batch_minutes(run, 100, 60, "x264_1080p_medium"),
        "scenario_100x_1min_fast_delivery_min": batch_minutes(run, 100, 60, "x264_1080p_veryfast"),
        "scenario_100x_1min_4k_downscale_min":  batch_minutes(run, 100, 60, "x264_4k_to_1080p"),
        "scenario_single_5min_export_s":  single_video_seconds(run, 5*60,  "x264_1080p_medium"),
        "scenario_single_30min_export_min": (
            (single_video_seconds(run, 30*60, "x264_1080p_medium") or 0) / 60
            if single_video_seconds(run, 30*60, "x264_1080p_medium") else None),
        "scenario_single_60min_export_min": (
            (single_video_seconds(run, 60*60, "x264_1080p_medium") or 0) / 60
            if single_video_seconds(run, 60*60, "x264_1080p_medium") else None),
    }
