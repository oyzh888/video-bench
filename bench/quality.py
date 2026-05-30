"""Quality benchmark — encoding speed vs visual quality (PSNR/SSIM).

Inspired by Netflix VMAF methodology. We avoid requiring libvmaf (not in
all ffmpeg builds) and use PSNR/SSIM filters (always available). Higher
PSNR/SSIM at the same bitrate = better quality at same speed.
"""
from __future__ import annotations
import json, sys, time, subprocess, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import FFMPEG, gen_clip, run


def encode_at_bitrate(src: Path, out: Path, vcodec: str, preset: str,
                      bitrate_k: int) -> tuple[bool, float]:
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-c:v", vcodec, "-preset", preset,
           "-b:v", f"{bitrate_k}k", "-maxrate", f"{bitrate_k}k",
           "-bufsize", f"{2*bitrate_k}k", "-an", str(out)]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, time.perf_counter() - t0


def measure_psnr_ssim(ref: Path, dist: Path) -> dict:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "info",
           "-i", str(dist), "-i", str(ref),
           "-lavfi", "[0][1]psnr;[0][1]ssim",
           "-c:v", "rawvideo", "-an", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    log = p.stdout + p.stderr
    out = {}
    m = re.search(r"PSNR.*average:\s*([\d.]+)", log)
    if m: out["psnr_db"] = float(m.group(1))
    m = re.search(r"SSIM.*All:\s*([\d.]+)", log)
    if m: out["ssim"] = float(m.group(1))
    return out


def main(quick: bool = False) -> dict:
    secs = 10 if quick else 20
    src = gen_clip(f"src_1080p_{secs}s.mp4", secs, 1920, 1080, 30)
    out_dir = src.parent / "out_quality"
    out_dir.mkdir(exist_ok=True)
    bitrate_k = 4000  # 4 Mbps target — typical for 1080p streaming
    presets = [("libx264", "ultrafast"),
               ("libx264", "veryfast"),
               ("libx264", "medium")]
    if quick:
        presets = presets[:2]
    results = {"target_bitrate_kbps": bitrate_k, "tests": {}}
    for codec, preset in presets:
        out = out_dir / f"{codec}_{preset}.mp4"
        ok, dt = encode_at_bitrate(src, out, codec, preset, bitrate_k)
        entry = {"ok": ok, "encode_wall_s": round(dt, 3)}
        if ok:
            entry.update(measure_psnr_ssim(src, out))
            entry["output_bytes"] = out.stat().st_size
        results["tests"][f"{codec}_{preset}"] = entry
    return results


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(json.dumps(main(quick=quick), indent=2))
