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
    """x264 medium 1080p ×realtime. 1× → 0, 10× → 100.

    Includes raw fps for context — that's the per-process throughput you
    actually feel as a single-creator workflow. High-clock laptop CPUs
    (Arrow Lake-HX, Apple M-series) often hit 280+ fps, while 100+ core
    server EPYCs land at 150-230 fps even though they have 10× more cores.
    """
    speed = _get(run, "single", "tests", "x264_1080p_medium", "speed_x_realtime")
    fps = _get(run, "single", "tests", "x264_1080p_medium", "ffmpeg_fps")
    score = _scale(speed, 1.0, 10.0)
    raw_str = f"{speed:.2f}× rt" if speed else "—"
    if fps:
        raw_str += f" ({fps:.0f} fps)"
    return {"score": score, "raw": raw_str,
            "raw_unit": "× realtime",
            "label": "CPU encoding (1080p x264 medium)"}


def score_parallel(run: dict) -> dict:
    """Best videos/min observed across the concurrent CPU sweep. 5 → 0, 60 → 100.

    Also detects the 'big-server flat-curve' anti-pattern: when N=1 → N=8 gives
    less than +30% throughput, the machine is being capped by ffmpeg's
    internal threading (typically ~16 threads/process) and/or NUMA effects,
    not core count. This shows up on EPYC servers with 100+ cores running
    single ffmpeg instances and is genuinely surprising.
    """
    cpu = _get(run, "concurrent", "cpu", default=[]) or []
    if not cpu:
        return {"score": None, "raw": None, "raw_unit": "videos / min",
                "label": "Parallel throughput (peak)"}
    best = max((e.get("videos_per_minute") or 0) for e in cpu)
    score = _scale(best, 5.0, 60.0)

    # Detect flat-curve anti-pattern
    n1 = next((e for e in cpu if e.get("n_parallel") == 1), None)
    n8 = next((e for e in cpu if e.get("n_parallel") == 8), None)
    note = None
    if n1 and n8:
        speedup = (n8.get("aggregate_speed_x_realtime") or 0) / max(
            n1.get("aggregate_speed_x_realtime") or 1, 0.01)
        cores = _get(run, "probe", "cpu", "cores") or 0
        if speedup < 1.3 and cores >= 32:
            note = (f"Flat throughput curve ({speedup:.2f}× from N=1→N=8) on a "
                    f"{cores}-core box — ffmpeg threading or NUMA is the cap, "
                    f"not cores. Single-stream speed matters more than core count here.")

    return {"score": score, "raw": round(best, 2),
            "raw_unit": "videos / min",
            "label": "Parallel throughput (peak)",
            "note": note}


def score_gpu(run: dict) -> dict:
    """GPU acceleration score — credits whichever GPU path actually works
    AND provides speedup vs CPU on this box.

    Three cases (in priority order):
      1. NVENC works → score by NVENC ×realtime (1080p), 50× = 100.
      2. NVDEC works AND beats CPU decode → partial credit by speedup ratio,
         capped at 50 (NVDEC alone is half a video-acceleration story).
      3. No working GPU acceleration → 0.

    Why this matters: H100/H200 ship without NVENC silicon (compute-only
    SKUs) but still have NVDEC. On big-CPU boxes, NVDEC often loses to
    libx264 software decode due to PCIe overhead — we want the score to
    reflect real-world useful acceleration, not just hardware presence.
    """
    nvenc_works = _get(run, "probe", "ffmpeg", "nvenc")
    nvdec_works = _get(run, "probe", "ffmpeg", "nvdec")
    nvenc_speed = _get(run, "single", "tests", "nvenc_h264_1080p", "speed_x_realtime")

    if nvenc_works and nvenc_speed:
        return {"score": _scale(nvenc_speed, 0.0, 50.0),
                "raw": nvenc_speed, "raw_unit": "× realtime (NVENC)",
                "label": "GPU encode (NVENC h264)",
                "mode": "nvenc"}

    # NVDEC fallback: compare 4K decode-only NVDEC vs CPU
    nvdec_4k = _get(run, "single", "tests", "decode_only_4k_nvdec", "speed_x_realtime")
    cpu_4k   = _get(run, "single", "tests", "decode_only_4k", "speed_x_realtime")
    if nvdec_works and nvdec_4k and cpu_4k:
        speedup = nvdec_4k / cpu_4k  # >1 means NVDEC helps
        # Speedup of 1.0× → 0 score; 4× → 50 score (max for decode-only).
        # NVDEC alone is capped at 50 — it's only half of the video pipeline.
        s = _scale(speedup, 1.0, 4.0)
        score = (s or 0) * 0.5
        note = ("NVDEC works but is slower than CPU on this box"
                if speedup < 1.0 else f"NVDEC gives {speedup:.1f}× speedup over CPU decode")
        return {"score": score, "raw": f"{nvdec_4k:.0f}× rt (vs CPU {cpu_4k:.0f}×)",
                "raw_unit": "× realtime (NVDEC)",
                "label": "GPU acceleration (NVDEC decode)",
                "mode": "nvdec",
                "note": note}

    if nvdec_works:
        return {"score": 0.0, "raw": None, "raw_unit": "—",
                "label": "GPU acceleration",
                "note": "NVDEC available but no benchmark data"}

    return {"score": 0.0, "raw": None, "raw_unit": "—",
            "label": "GPU acceleration",
            "note": "No working GPU encode/decode"}


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
