"""Real-world scenarios — closer to what 'doing video work' actually feels like."""
from __future__ import annotations
import json, sys, time, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import FFMPEG, gen_clip, run, clip_duration, parse_ffmpeg_speed


def cut_concat_encode(src: Path, out_dir: Path) -> dict:
    """Simulate an editor exporting a 3-clip cut."""
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    # 3 segments of 5s each, picked from different offsets
    for i, ss in enumerate([0, 8, 16]):
        seg = out_dir / f"seg_{i}.mp4"
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-ss", str(ss), "-i", str(src), "-t", "5",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k", str(seg)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            return {"ok": False, "error": p.stderr[-300:]}
        parts.append(seg)
    listfile = out_dir / "list.txt"
    listfile.write_text("\n".join(f"file '{p}'" for p in parts))

    final = out_dir / "edit_export.mp4"
    t0 = time.perf_counter()
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(listfile),
           "-c:v", "libx264", "-preset", "medium", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", str(final)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.perf_counter() - t0
    return {
        "ok": p.returncode == 0,
        "wall_s": round(dt, 3),
        "output_bytes": final.stat().st_size if final.exists() else 0,
        "error": p.stderr[-300:] if p.returncode != 0 else None,
    }


def thumbnail_grid(src: Path, out_dir: Path) -> dict:
    """Extract 1 thumbnail per second, scaled."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "thumb_%03d.jpg"
    t0 = time.perf_counter()
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src),
           "-vf", "fps=1,scale=320:-1", "-q:v", "3", str(pattern)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.perf_counter() - t0
    n = len(list(out_dir.glob("thumb_*.jpg")))
    return {"ok": p.returncode == 0, "wall_s": round(dt, 3),
            "thumbnails": n,
            "thumbs_per_sec": round(n / dt, 1) if dt else None,
            "error": p.stderr[-300:] if p.returncode != 0 else None}


def subtitle_burn(src: Path, out_dir: Path) -> dict:
    """Filter-graph stress (drawbox + scale + fade), proxy for sub burn-in.

    Uses drawbox instead of drawtext because not every ffmpeg build ships
    libfreetype (e.g. NVIDIA's data-center ffmpeg). Cost profile is similar:
    per-frame pixel filter on top of full re-encode.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "subbed.mp4"
    vf = ("drawbox=x=0:y=ih-100:w=iw:h=80:color=black@0.5:t=fill,"
          "drawbox=x=40:y=ih-90:w=iw-80:h=60:color=white@0.9:t=2,"
          "fade=in:0:30,fade=out:st=20:d=2")
    t0 = time.perf_counter()
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-c:a", "copy", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.perf_counter() - t0
    src_dur = clip_duration(src)
    return {"ok": p.returncode == 0, "wall_s": round(dt, 3),
            "speed_x_realtime": round(src_dur / dt, 2) if dt else None,
            "error": p.stderr[-300:] if p.returncode != 0 else None}


def main(quick: bool = False) -> dict:
    secs = 20 if quick else 30
    src = gen_clip(f"src_1080p_{secs}s.mp4", secs, 1920, 1080, 30)
    out_dir = src.parent / "out_scenario"
    return {
        "edit_export_3clip": cut_concat_encode(src, out_dir / "edit"),
        "thumbnail_grid_1fps": thumbnail_grid(src, out_dir / "thumbs"),
        "subtitle_burn": subtitle_burn(src, out_dir / "subs"),
    }


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(json.dumps(main(quick=quick), indent=2))
