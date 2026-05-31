"""Single-video transcode benchmarks — 'how long to render one clip'."""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (FFMPEG, gen_clip, run, clip_duration,
                        parse_ffmpeg_speed, parse_ffmpeg_fps,
                        have_nvenc, have_nvdec, have_encoder)

# Two source clips: 1080p30 and 4K30, both 30 seconds.
def assets(quick: bool):
    secs = 10 if quick else 30
    src_1080 = gen_clip(f"src_1080p_{secs}s.mp4", secs, 1920, 1080, 30)
    src_4k = gen_clip(f"src_2160p_{secs}s.mp4", secs, 3840, 2160, 30)
    return src_1080, src_4k, secs


def transcode(src: Path, out: Path, vcodec: str, extra: list[str] | None = None,
              preset: str = "medium", crf: int | None = 23,
              filters: list[str] | None = None,
              hwaccel: str | None = None) -> dict:
    cmd = [FFMPEG, "-y", "-hide_banner"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-i", str(src)]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += ["-c:v", vcodec]
    if preset:
        cmd += ["-preset", preset]
    if crf is not None and "nvenc" not in vcodec:
        cmd += ["-crf", str(crf)]
    elif "nvenc" in vcodec:
        cmd += ["-cq", str(crf if crf is not None else 23), "-b:v", "0"]
    if extra:
        cmd += extra
    cmd += ["-c:a", "copy", str(out)]
    rc, log, dt = run(cmd, timeout=900)
    out_size = out.stat().st_size if out.exists() else 0
    src_dur = clip_duration(src)
    return {
        "ok": rc == 0,
        "wall_s": round(dt, 3),
        "src_duration_s": round(src_dur, 3),
        "speed_x_realtime": round(src_dur / dt, 2) if dt else None,
        "ffmpeg_speed": parse_ffmpeg_speed(log),
        "ffmpeg_fps": parse_ffmpeg_fps(log),
        "output_bytes": out_size,
        "cmd": " ".join(cmd),
        "error": None if rc == 0 else log[-400:],
    }


def main(quick: bool = False) -> dict:
    src_1080, src_4k, secs = assets(quick)
    out_dir = src_1080.parent / "out_single"
    out_dir.mkdir(exist_ok=True)
    results: dict = {"source_clip_seconds": secs, "tests": {}}

    def record(name: str, **kw):
        out = out_dir / f"{name}.mp4"
        results["tests"][name] = transcode(out=out, **kw)

    # CPU encodes
    record("x264_1080p_medium",   src=src_1080, vcodec="libx264", preset="medium", crf=23)
    record("x264_1080p_veryfast", src=src_1080, vcodec="libx264", preset="veryfast", crf=23)
    if have_encoder("libx265"):
        record("x265_1080p_medium", src=src_1080, vcodec="libx265", preset="medium", crf=28)
    record("x264_4k_to_1080p", src=src_4k, vcodec="libx264", preset="medium", crf=23,
           filters=["scale=1920:1080"])

    # GPU-decode hybrid (NVDEC + CPU encode). Often a win on encoder-less
    # NVIDIA SKUs (H100/H200 have no NVENC but do have NVDEC), but only if
    # PCIe transfer beats the CPU decode path — which on big CPU boxes,
    # surprisingly, it often doesn't. Worth measuring.
    if have_nvdec():
        record("nvdec_h264_4k_to_1080p", src=src_4k, vcodec="libx264",
               preset="veryfast", crf=23,
               filters=["scale=1920:1080"], hwaccel="cuda")
        record("nvdec_h264_1080p_reencode", src=src_1080, vcodec="libx264",
               preset="veryfast", crf=23, hwaccel="cuda")

    # GPU encodes
    if have_nvenc():
        record("nvenc_h264_1080p", src=src_1080, vcodec="h264_nvenc",
               preset="p4", crf=23)
        if have_encoder("hevc_nvenc"):
            record("nvenc_hevc_1080p", src=src_1080, vcodec="hevc_nvenc",
                   preset="p4", crf=28)
        record("nvenc_4k_to_1080p", src=src_4k, vcodec="h264_nvenc",
               preset="p4", crf=23, filters=["scale=1920:1080"])

    # Decode-only (CPU). Some minimal ffmpeg builds disable
    # `wrapped_avframe`, so we force rawvideo for the null sink.
    cmd = [FFMPEG, "-y", "-hide_banner",
           "-i", str(src_4k), "-c:v", "rawvideo", "-an", "-f", "null", "-"]
    rc, log, dt = run(cmd, timeout=600)
    src_dur = clip_duration(src_4k)
    results["tests"]["decode_only_4k"] = {
        "ok": rc == 0,
        "wall_s": round(dt, 3),
        "src_duration_s": round(src_dur, 3),
        "speed_x_realtime": round(src_dur / dt, 2) if dt else None,
        "ffmpeg_fps": parse_ffmpeg_fps(log),
        "error": None if rc == 0 else log[-400:],
    }

    # NVDEC-only decode (no encode) — pure GPU decode throughput
    if have_nvdec():
        cmd = [FFMPEG, "-y", "-hide_banner", "-hwaccel", "cuda",
               "-i", str(src_4k), "-c:v", "rawvideo", "-an", "-f", "null", "-"]
        rc, log, dt = run(cmd, timeout=600)
        results["tests"]["decode_only_4k_nvdec"] = {
            "ok": rc == 0,
            "wall_s": round(dt, 3),
            "src_duration_s": round(src_dur, 3),
            "speed_x_realtime": round(src_dur / dt, 2) if dt else None,
            "ffmpeg_fps": parse_ffmpeg_fps(log),
            "error": None if rc == 0 else log[-400:],
        }
    return results


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    print(json.dumps(main(quick=quick), indent=2))
