"""Shared helpers for video-bench."""
from __future__ import annotations
import json, os, platform, shutil, socket, subprocess, sys, time, re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
RESULTS = ROOT / "results"
ASSETS.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)

FFMPEG = shutil.which("ffmpeg") or "/opt/ffmpeg/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/ffmpeg/bin/ffprobe"


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


_nvenc_cache: Optional[bool] = None

def have_nvenc() -> bool:
    """True only if h264_nvenc actually opens an encoder session on this box.

    `nvidia-smi` + listed encoder are not enough — some data-center GPU
    profiles (MIG, vGPU) ship without working NVENC, in which case we
    must skip GPU tests entirely.
    """
    global _nvenc_cache
    if _nvenc_cache is not None:
        return _nvenc_cache
    if not have("nvidia-smi"):
        _nvenc_cache = False
        return False
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=10)
        if "h264_nvenc" not in out.stdout:
            _nvenc_cache = False
            return False
        # Real probe: encode 0.5s of testsrc into /dev/null
        probe = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=0.5",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, text=True, timeout=20)
        _nvenc_cache = probe.returncode == 0
        return _nvenc_cache
    except Exception:
        _nvenc_cache = False
        return False


def have_encoder(name: str) -> bool:
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=10)
        return re.search(rf"\b{re.escape(name)}\b", out.stdout) is not None
    except Exception:
        return False


_nvdec_cache: Optional[bool] = None

def have_nvdec() -> bool:
    """True if `-hwaccel cuda` actually decodes a frame end-to-end.
    NVDEC silicon is present on most NVIDIA GPUs (including H100, which
    has no NVENC), but the ffmpeg build needs CUDA hwaccel and the env
    needs to expose the GPU. We probe by decoding 0.5s of generated h264
    via cuda hwaccel."""
    global _nvdec_cache
    if _nvdec_cache is not None:
        return _nvdec_cache
    if not have("nvidia-smi"):
        _nvdec_cache = False; return False
    try:
        # Need a real h264 stream to decode (lavfi testsrc isn't h264)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            tmp = f.name
        gen = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=0.5",
             "-c:v", "libx264", "-preset", "ultrafast", tmp],
            capture_output=True, text=True, timeout=20)
        if gen.returncode != 0:
            _nvdec_cache = False; return False
        probe = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-hwaccel", "cuda", "-i", tmp,
             "-c:v", "rawvideo", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=20)
        try: os.unlink(tmp)
        except OSError: pass
        _nvdec_cache = probe.returncode == 0
        return _nvdec_cache
    except Exception:
        _nvdec_cache = False; return False


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str, float]:
    """Run a command, return (rc, combined_output, wall_seconds)."""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        dt = time.perf_counter() - t0
        return p.returncode, (p.stdout + p.stderr), dt
    except subprocess.TimeoutExpired as e:
        return 124, f"TIMEOUT after {timeout}s\n{e}", time.perf_counter() - t0


def real_clip(seconds: int = 30) -> Optional[Path]:
    """Download a public-domain reference clip (Big Buck Bunny 1080p) and
    trim to `seconds`. Returns path or None on failure (bench falls back to
    synthetic). Cached after first download.
    """
    import urllib.request
    cache = ASSETS / f"bbb_1080p_{seconds}s.mp4"
    if cache.exists() and cache.stat().st_size > 0:
        return cache
    src = ASSETS / "bbb_1080p_source.mp4"
    if not src.exists():
        # Mirror used by ffmpeg-tests; small (148 MB), CC-BY 3.0
        url = "https://download.blender.org/peach/bigbuckbunny_movies/big_buck_bunny_1080p_h264.mov"
        try:
            print(f"   downloading reference clip ({url}) ...")
            urllib.request.urlretrieve(url, src)
        except Exception as e:
            print(f"   real-clip download failed: {e}")
            return None
    rc, log, _ = run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                      "-ss", "60", "-i", str(src), "-t", str(seconds),
                      "-c", "copy", str(cache)], timeout=120)
    return cache if rc == 0 else None


def gen_clip(name: str, seconds: int, w: int, h: int, fps: int = 30,
             codec: str = "libx264", crf: int = 23) -> Path:
    """Generate a synthetic test clip if missing. Returns its path."""
    path = ASSETS / name
    if path.exists() and path.stat().st_size > 0:
        return path
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"testsrc2=size={w}x{h}:rate={fps}:duration={seconds}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", codec, "-preset", "medium", "-crf", str(crf),
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(path),
    ]
    rc, log, _ = run(cmd, timeout=300)
    if rc != 0:
        raise RuntimeError(f"gen_clip failed: {log[-500:]}")
    return path


def probe(path: Path) -> dict:
    cmd = [FFPROBE, "-v", "error", "-show_streams", "-show_format",
           "-of", "json", str(path)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(p.stdout) if p.stdout else {}


def clip_duration(path: Path) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except Exception:
        return 0.0


def hostname() -> str:
    return socket.gethostname()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H-%M-%S")


def parse_ffmpeg_speed(log: str) -> Optional[float]:
    """Pull the last 'speed=Nx' from ffmpeg stderr."""
    matches = re.findall(r"speed=\s*([\d.]+)x", log)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def parse_ffmpeg_fps(log: str) -> Optional[float]:
    matches = re.findall(r"\bfps=\s*([\d.]+)", log)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None
