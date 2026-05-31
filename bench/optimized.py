"""Optimized-encoding benchmark — show how much a NUMA-aware/CCD-pinned
strategy can squeeze out of a big-server CPU.

Skipped automatically on machines that don't benefit:
  - macOS (no `taskset`)
  - <32 logical cores (the gain is from pinning around CCDs, which only
    matters on big chiplet-based AMDs and dual-socket boxes)
  - Single-CCD machines (laptop CPUs)

The speedup typically lands at +50–100% aggregate throughput on EPYC
boxes (12 CCDs × 8 cores), where ffmpeg's default thread auto-detection
explodes when it sees 100+ logical CPUs.
"""
from __future__ import annotations
import json, sys, shutil, subprocess, time, os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import FFMPEG, gen_clip, clip_duration


def detect_topology() -> dict:
    """Return {sockets, cores_per_socket, threads_per_core, l3_count, total_logical}.
    Uses lscpu — falls back to None fields if not Linux."""
    info = {"linux": sys.platform.startswith("linux")}
    if not info["linux"]:
        return info
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True).stdout
        def grab(key, cast=int):
            for line in out.splitlines():
                if line.startswith(key + ":"):
                    return cast(line.split(":", 1)[1].strip().split()[0])
            return None
        info["sockets"] = grab("Socket(s)")
        info["cores_per_socket"] = grab("Core(s) per socket")
        info["threads_per_core"] = grab("Thread(s) per core")
        info["total_logical"] = os.cpu_count()
        # L3 count from `lscpu` 'L3 cache' instances
        for line in out.splitlines():
            if "L3 cache" in line and "instances" in line:
                # e.g. "L3 cache: 384 MiB (12 instances)"
                import re
                m = re.search(r"\((\d+) instances\)", line)
                if m: info["l3_count"] = int(m.group(1))
        return info
    except Exception as e:
        info["error"] = str(e)
        return info


def can_optimize() -> tuple[bool, str]:
    """Return (yes, reason). 'yes' means optimized mode will likely beat default."""
    if not shutil.which("taskset"):
        return False, "taskset not available (probably non-Linux)"
    cores = os.cpu_count() or 0
    if cores < 32:
        return False, f"only {cores} logical CPUs — pinning gain is small"
    topo = detect_topology()
    l3 = topo.get("l3_count") or 1
    if l3 < 2:
        return False, f"single L3 cache (no CCD layout to exploit)"
    return True, f"{cores} cores across {l3} L3 caches — CCD pinning should help"


def encode_one(cpus: str | None, threads: int, src: Path, out: Path,
               preset: str = "medium") -> float:
    cmd = []
    if cpus:
        cmd += ["taskset", "-c", cpus]
    cmd += [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-threads", str(threads), "-i", str(src),
            "-c:v", "libx264", "-threads", str(threads),
            "-preset", preset, "-crf", "23", "-an", str(out)]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return time.perf_counter() - t0


def run_parallel(n_procs: int, threads_per: int, src: Path, out_dir: Path,
                 ccd_size: int = 8, pin: bool = True,
                 preset: str = "medium") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    src_dur = clip_duration(src)
    cmds_args = []
    for i in range(n_procs):
        cpus = None
        if pin:
            start = i * ccd_size
            end = start + ccd_size - 1
            cpus = f"{start}-{end}"
        cmds_args.append((cpus, threads_per,
                          out_dir / f"{n_procs}_{i}.mp4"))
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_procs) as ex:
        list(ex.map(lambda a: encode_one(a[0], a[1], src, a[2], preset), cmds_args))
    wall = time.perf_counter() - t0
    return {
        "n_procs": n_procs,
        "threads_per": threads_per,
        "pinned": pin,
        "wall_s": round(wall, 3),
        "aggregate_speed_x_realtime": round(n_procs * src_dur / wall, 2) if wall else None,
        "videos_per_minute": round(n_procs / wall * 60, 2) if wall else None,
        "per_video_wall_s": round(wall, 3),
    }


def main(quick: bool = False) -> dict:
    feasible, reason = can_optimize()
    result = {"feasible": feasible, "reason": reason,
              "topology": detect_topology(), "tests": {}}
    if not feasible:
        return result

    secs = 10 if quick else 30
    src = gen_clip(f"src_1080p_{secs}s.mp4", secs, 1920, 1080, 30)
    out_dir = src.parent / "out_optimized"
    cores = os.cpu_count() or 0
    l3 = detect_topology().get("l3_count") or 1

    # Reference: 1× ffmpeg with sane explicit threads (16) — what the
    # 'concurrent' bench measures as N=1. Used as the speedup baseline.
    result["tests"]["baseline_1proc_16threads"] = run_parallel(
        1, 16, src, out_dir, pin=False, preset="medium")

    # Optimized: pin N procs each to one CCD (8 cores)
    for n in (4, l3, l3 * 2 if not quick else None):
        if n is None or n > l3 * 2: continue
        if n > 24: continue  # diminishing returns past 24
        result["tests"][f"pinned_{n}procs_8threads"] = run_parallel(
            n, 8, src, out_dir, ccd_size=8, pin=True, preset="medium")

    # Unpinned reference at the same N as the best pinned, to show the pinning gain
    best_n = l3  # one proc per CCD
    result["tests"][f"unpinned_{best_n}procs_8threads"] = run_parallel(
        best_n, 8, src, out_dir, pin=False, preset="medium")

    # Headline: best pinned vs baseline_1proc, expressed as speedup
    base = result["tests"]["baseline_1proc_16threads"]["aggregate_speed_x_realtime"]
    pinned_keys = [k for k in result["tests"] if k.startswith("pinned_")]
    if pinned_keys and base:
        best_pinned = max(result["tests"][k]["aggregate_speed_x_realtime"]
                          for k in pinned_keys)
        result["headline_speedup_x"] = round(best_pinned / base, 2)
        result["best_aggregate_x_realtime"] = best_pinned

    return result


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(json.dumps(main(quick=quick), indent=2))
