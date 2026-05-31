"""Concurrent transcode — 'how many videos can this machine chew through at once'."""
from __future__ import annotations
import json, sys, time, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (FFMPEG, gen_clip, clip_duration, have_nvenc,
                        have_videotoolbox)


def _one_job(src: Path, out: Path, vcodec: str, preset: str, crf: int) -> dict:
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-c:v", vcodec]
    if preset:
        cmd += ["-preset", preset]
    if "nvenc" in vcodec:
        cmd += ["-cq", str(crf), "-b:v", "0"]
    elif "videotoolbox" in vcodec:
        cmd += ["-b:v", "8M"]
    else:
        cmd += ["-crf", str(crf)]
    cmd += ["-c:a", "copy", str(out)]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return {"ok": p.returncode == 0, "wall_s": time.perf_counter() - t0,
            "error": p.stderr[-200:] if p.returncode != 0 else None}


def run_concurrent(src: Path, out_dir: Path, n: int, vcodec: str,
                   preset: str = "medium", crf: int = 23) -> dict:
    out_dir.mkdir(exist_ok=True)
    src_dur = clip_duration(src)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(_one_job, src,
                          out_dir / f"{vcodec}_n{n}_{i}.mp4",
                          vcodec, preset, crf) for i in range(n)]
        per_job = [f.result() for f in as_completed(futs)]
    total = time.perf_counter() - t0
    successes = sum(1 for r in per_job if r["ok"])
    avg_wall = sum(r["wall_s"] for r in per_job) / len(per_job)
    # videos/min = N successful jobs over total wall
    return {
        "n_parallel": n,
        "vcodec": vcodec,
        "wall_s": round(total, 3),
        "avg_per_job_wall_s": round(avg_wall, 3),
        "ok_count": successes,
        "videos_per_minute": round(successes / total * 60, 2) if total else None,
        "aggregate_speed_x_realtime": round(successes * src_dur / total, 2) if total else None,
        "first_error": next((r["error"] for r in per_job if r["error"]), None),
    }


def main(quick: bool = False) -> dict:
    secs = 10 if quick else 20
    src = gen_clip(f"src_1080p_{secs}s.mp4", secs, 1920, 1080, 30)
    out_dir = src.parent / "out_concurrent"
    out_dir.mkdir(exist_ok=True)
    results: dict = {"source_clip_seconds": secs, "cpu": [], "nvenc": [], "videotoolbox": []}

    cpu_levels = [1, 2, 4, 8] if quick else [1, 2, 4, 8, 16]
    for n in cpu_levels:
        results["cpu"].append(
            run_concurrent(src, out_dir, n, "libx264",
                           preset="veryfast", crf=23))

    if have_nvenc():
        gpu_levels = [1, 2, 4] if quick else [1, 2, 4, 8]
        for n in gpu_levels:
            results["nvenc"].append(
                run_concurrent(src, out_dir, n, "h264_nvenc",
                               preset="p4", crf=23))

    if have_videotoolbox():
        vt_levels = [1, 2] if quick else [1, 2, 4, 8]
        for n in vt_levels:
            results["videotoolbox"].append(
                run_concurrent(src, out_dir, n, "h264_videotoolbox",
                               preset=None, crf=23))

    return results


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(json.dumps(main(quick=quick), indent=2))
