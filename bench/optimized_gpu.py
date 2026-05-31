"""GPU-assisted optimization benchmark — NVDEC + libx264 hybrid pipelines.

For SKUs without NVENC (H100, H200, etc.) the only GPU video acceleration
available is NVDEC (decode) and CUDA filters (scale_cuda, scale_npp).
This bench measures whether routing decode through NVDEC actually helps
when paired with libx264 CPU encoding, vs the optimized pure-CPU path.

The honest answer on a 192-core EPYC + H100 box is: NVDEC helps for 4K
batch transcodes (+10–20%) but hurts for 1080p ones (PCIe transfer
overhead > decode savings on a CPU this beefy).

Skipped on machines without NVDEC.
"""
from __future__ import annotations
import json, sys, shutil, subprocess, time, os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import FFMPEG, gen_clip, clip_duration, have_nvdec
from bench.optimized import detect_topology


def feasible() -> tuple[bool, str]:
    if not have_nvdec():
        return False, "NVDEC not working"
    if not shutil.which("taskset"):
        return False, "taskset not available"
    cores = os.cpu_count() or 0
    if cores < 16:
        return False, f"only {cores} cores — GPU pipelines won't help"
    return True, "NVDEC + multi-core CPU available"


def encode_one(cpus, threads, src, out, hwaccel=None, vf=None,
               preset="medium"):
    cmd = []
    if cpus:
        cmd += ["taskset", "-c", cpus]
    cmd += [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-threads", str(threads), "-i", str(src)]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-threads", str(threads),
            "-preset", preset, "-crf", "23", "-an", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode


def run_parallel_gpu(n_procs, src, out_dir, *,
                     threads_per=8, ccd_size=8, pin=True,
                     hwaccel="cuda", vf=None, preset="medium",
                     name=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    src_dur = clip_duration(src)
    args = []
    for i in range(n_procs):
        cpus = None
        if pin:
            start = i * ccd_size
            cpus = f"{start}-{start + ccd_size - 1}"
        args.append((cpus, threads_per, src,
                     out_dir / f"{name}_{i}.mp4", hwaccel, vf, preset))
    t0 = time.perf_counter()
    rcs = []
    with ThreadPoolExecutor(max_workers=n_procs) as ex:
        rcs = list(ex.map(lambda a: encode_one(*a), args))
    wall = time.perf_counter() - t0
    return {
        "n_procs": n_procs,
        "threads_per": threads_per,
        "pinned": pin,
        "hwaccel": hwaccel,
        "filter": vf,
        "wall_s": round(wall, 3),
        "aggregate_speed_x_realtime": round(n_procs * src_dur / wall, 2) if wall else None,
        "videos_per_minute": round(n_procs / wall * 60, 2) if wall else None,
        "ok_count": sum(1 for r in rcs if r == 0),
    }


def main(quick: bool = False) -> dict:
    feas, reason = feasible()
    out = {"feasible": feas, "reason": reason,
           "topology": detect_topology(), "tests": {}}
    if not feas:
        return out

    secs = 10 if quick else 30
    src_1080 = gen_clip(f"src_1080p_{secs}s.mp4", secs, 1920, 1080, 30)
    src_4k = gen_clip(f"src_2160p_{secs}s.mp4", secs, 3840, 2160, 30)
    out_dir = src_1080.parent / "out_optimized_gpu"

    # 1080p batch: NVDEC + libx264, pinned. Use 12 procs (one per CCD on big box).
    cores = os.cpu_count() or 0
    l3 = detect_topology().get("l3_count") or 1
    n_batch = min(l3, 12)
    quick_n = max(2, n_batch // 3) if quick else n_batch

    out["tests"]["nvdec_1080p_x12_pinned"] = run_parallel_gpu(
        quick_n, src_1080, out_dir, hwaccel="cuda",
        name="g1080", preset="medium")

    # 4K-to-1080p batch (NVDEC's strong suit): NVDEC decode, CPU scale + encode
    out["tests"]["nvdec_4k_to_1080p_x12_pinned"] = run_parallel_gpu(
        quick_n, src_4k, out_dir, hwaccel="cuda",
        vf="scale=1920:1080", name="g4k", preset="medium")

    # Single NVDEC reference (1 proc, threads=16, no pin)
    out["tests"]["nvdec_1080p_single"] = run_parallel_gpu(
        1, src_1080, out_dir, threads_per=16, ccd_size=16, pin=False,
        hwaccel="cuda", name="g1080_single", preset="medium")

    out["tests"]["nvdec_4k_to_1080p_single"] = run_parallel_gpu(
        1, src_4k, out_dir, threads_per=16, ccd_size=16, pin=False,
        hwaccel="cuda", vf="scale=1920:1080",
        name="g4k_single", preset="medium")

    # Compute speedup vs the CPU-only optimized numbers (if present in same JSON)
    return out


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(json.dumps(main(quick=quick), indent=2))
