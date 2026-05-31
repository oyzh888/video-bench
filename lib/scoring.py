"""6-dimension scoring for video-bench results.

Each dimension scores 0–100 against fixed reference points (calibrated so a
2024-era workstation lands ~70–80, top-end H100/Threadripper ~90+, a 5-yr-old
laptop ~30–50). Fixed refs mean a single-machine run is still meaningful;
multi-machine comparison is just sorting by composite.

Composite weights are picked to reflect "what matters for video work":
  CPU encode speed 25%  (most jobs are still libx264)
  Parallel throughput 25%  (batch is the common production case)
  GPU 15%              (huge speedup when present, 0 when broken)
  Quality 10%          (matters but bounded — once you're at 41 dB you're fine)
  Storage 10%          (rarely the bottleneck — small weight)
  Scenarios 15%        (edit/thumbnail responsiveness — felt experience)

Tiers: S 90+, A 75+, B 60+, C 40+, D <40.
"""
from __future__ import annotations
from typing import Optional


def _get(d, *path, default=None):
    for p in path:
        if not isinstance(d, dict): return default
        d = d.get(p)
        if d is None: return default
    return d


def _scale(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """Linear 0–100 map. value <= lo → 0, value >= hi → 100. None → None."""
    if value is None: return None
    if hi == lo: return None
    pct = (value - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, pct))


def _scale_inverse(value: Optional[float], best: float, worst: float) -> Optional[float]:
    """For metrics where lower is better (e.g. wall time)."""
    if value is None: return None
    if worst == best: return None
    pct = (worst - value) / (worst - best) * 100.0
    return max(0.0, min(100.0, pct))


# ----- Per-dimension scorers ---------------------------------------------

def score_cpu_encode(run: dict) -> dict:
    """x264 medium 1080p ×realtime. 1× → 0, 10× → 100."""
    speed = _get(run, "single", "tests", "x264_1080p_medium", "speed_x_realtime")
    score = _scale(speed, 1.0, 10.0)
    return {"score": score, "raw": speed,
            "raw_unit": "× realtime",
            "label": "CPU encoding (1080p x264 medium)"}


def score_parallel(run: dict) -> dict:
    """Best videos/min observed across the concurrent CPU sweep. 5 → 0, 60 → 100."""
    cpu = _get(run, "concurrent", "cpu", default=[]) or []
    best = max((e.get("videos_per_minute") or 0) for e in cpu) if cpu else None
    score = _scale(best, 5.0, 60.0) if best else None
    return {"score": score, "raw": round(best, 2) if best else None,
            "raw_unit": "videos / min",
            "label": "Parallel throughput (peak)"}


def score_gpu(run: dict) -> dict:
    """NVENC 1080p ×realtime. 0 (or unavailable) → 0, 50× → 100.
    No GPU at all returns 0 (not None) so GPU-less laptops correctly score
    low here — that's a real video-work disadvantage."""
    nvenc_works = _get(run, "probe", "ffmpeg", "nvenc")
    speed = _get(run, "single", "tests", "nvenc_h264_1080p", "speed_x_realtime")
    if not nvenc_works or speed is None:
        return {"score": 0.0, "raw": None, "raw_unit": "× realtime",
                "label": "GPU encode (NVENC h264)",
                "note": "NVENC not working on this machine"}
    score = _scale(speed, 0.0, 50.0)
    return {"score": score, "raw": speed, "raw_unit": "× realtime",
            "label": "GPU encode (NVENC h264)"}


def score_quality(run: dict) -> dict:
    """PSNR at x264 medium @ 4 Mbps. 35 dB → 0, 42 dB → 100.
    Most modern x264 builds land 40–42 dB at this bitrate; floor 35 dB
    catches genuinely broken/old encoders or very noisy sources."""
    psnr = _get(run, "quality", "tests", "libx264_medium", "psnr_db")
    score = _scale(psnr, 35.0, 42.0)
    return {"score": score, "raw": psnr, "raw_unit": "dB PSNR",
            "label": "Encoding quality (PSNR @ 4 Mbps)"}


def score_storage(run: dict) -> dict:
    """Combined disk: read 5 GB/s + write 2 GB/s = 100. 100 MB/s each = ~0."""
    r = _get(run, "probe", "disk", "read_MBps")
    w = _get(run, "probe", "disk", "write_MBps")
    if r is None or w is None:
        return {"score": None, "raw": None, "raw_unit": "MB/s",
                "label": "Storage I/O"}
    # 60% read, 40% write — read matters more for decode-heavy pipelines
    rs = _scale(r, 100.0, 5000.0) or 0
    ws = _scale(w, 100.0, 2000.0) or 0
    score = 0.6 * rs + 0.4 * ws
    return {"score": score, "raw": f"R {r:.0f} / W {w:.0f}",
            "raw_unit": "MB/s", "label": "Storage I/O"}


def score_scenarios(run: dict) -> dict:
    """Edit-export responsiveness. 30s 3-clip export: 0.5s → 100, 5s → 0."""
    edit = _get(run, "scenarios", "edit_export_3clip", "wall_s")
    score = _scale_inverse(edit, 0.5, 5.0) if edit else None
    return {"score": score, "raw": edit, "raw_unit": "sec wall",
            "label": "Editing responsiveness (3-clip export)"}


# ----- Composite ---------------------------------------------------------

WEIGHTS = {
    "cpu_encode": 0.25,
    "parallel":   0.25,
    "gpu":        0.15,
    "quality":    0.10,
    "storage":    0.10,
    "scenarios":  0.15,
}

SCORERS = {
    "cpu_encode": score_cpu_encode,
    "parallel":   score_parallel,
    "gpu":        score_gpu,
    "quality":    score_quality,
    "storage":    score_storage,
    "scenarios":  score_scenarios,
}


def tier(composite: float) -> str:
    if composite is None: return "—"
    if composite >= 90: return "S"
    if composite >= 75: return "A"
    if composite >= 60: return "B"
    if composite >= 40: return "C"
    return "D"


def tier_blurb(t: str) -> str:
    return {
        "S": "Top-tier video workstation — handles any production pipeline comfortably.",
        "A": "Strong machine — good for batch jobs, multi-stream encoding, long-form work.",
        "B": "Solid for single-creator workflows — short clips, 1–2 concurrent jobs.",
        "C": "Usable but slow — fine for occasional clips, painful for batch.",
        "D": "Underpowered for video — expect to wait. Consider for trim/preview only.",
    }.get(t, "")


def score_run(run: dict) -> dict:
    """Score one run across all dimensions + composite."""
    dims = {k: SCORERS[k](run) for k in SCORERS}
    # Composite: weighted average of available dimensions, missing → 0 contribution.
    composite = 0.0
    total_w = 0.0
    for k, d in dims.items():
        s = d.get("score")
        w = WEIGHTS[k]
        if s is None:
            # Treat missing as 0 only for GPU (genuinely zero capability);
            # for others, redistribute by skipping.
            if k == "gpu":
                composite += 0.0 * w; total_w += w
        else:
            composite += s * w; total_w += w
    composite_score = round(composite / total_w * 1.0, 1) if total_w else None
    return {
        "dimensions": dims,
        "composite": composite_score,
        "tier": tier(composite_score),
        "tier_blurb": tier_blurb(tier(composite_score)),
    }
