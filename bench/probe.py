"""System probe: CPU / GPU / RAM / disk / ffmpeg capabilities."""
from __future__ import annotations
import json, os, platform, re, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import FFMPEG, have, have_nvenc, have_nvdec, have_videotoolbox, run


def cpu_info() -> dict:
    info = {"arch": platform.machine(), "cores": os.cpu_count()}
    # Linux /proc/cpuinfo
    try:
        with open("/proc/cpuinfo") as f:
            txt = f.read()
        m = re.search(r"model name\s*:\s*(.+)", txt)
        if m:
            info["model"] = m.group(1).strip()
        info["physical_cores"] = len(set(re.findall(r"core id\s*:\s*(\d+)", txt)))
    except FileNotFoundError:
        # macOS
        try:
            info["model"] = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True).stdout.strip()
        except Exception:
            pass
    return info


def mem_info() -> dict:
    try:
        with open("/proc/meminfo") as f:
            txt = f.read()
        m = re.search(r"MemTotal:\s+(\d+)\s*kB", txt)
        return {"total_gb": round(int(m.group(1)) / 1024 / 1024, 1)} if m else {}
    except FileNotFoundError:
        try:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True).stdout.strip()
            return {"total_gb": round(int(out) / 1024**3, 1)}
        except Exception:
            return {}


def gpu_info() -> list[dict]:
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                                 capture_output=True, text=True, timeout=10).stdout
            gpus = []
            current: dict | None = None
            for raw in out.splitlines():
                line = raw.strip()
                if raw.startswith("    ") and not raw.startswith("      ") and line.endswith(":"):
                    if current:
                        gpus.append(current)
                    current = {"name": line[:-1]}
                elif current and line.startswith("Total Number of Cores:"):
                    current["cores"] = line.split(":", 1)[1].strip()
                elif current and line.startswith("Metal Support:"):
                    current["metal"] = line.split(":", 1)[1].strip()
            if current:
                gpus.append(current)
            return gpus
        except Exception:
            return []

    if not have("nvidia-smi"):
        return []
    out = subprocess.run(
        ["nvidia-smi",
         "--query-gpu=name,driver_version,memory.total,compute_cap",
         "--format=csv,noheader"],
        capture_output=True, text=True).stdout.strip()
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            gpus.append({
                "name": parts[0],
                "driver": parts[1],
                "memory": parts[2],
                "compute_cap": parts[3] if len(parts) > 3 else None,
            })
    return gpus


def ffmpeg_info() -> dict:
    out = subprocess.run([FFMPEG, "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout
    ver = out.splitlines()[0] if out else "unknown"
    enc = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    encoders = []
    for codec in ("libx264", "libx265", "h264_nvenc", "hevc_nvenc",
                  "libvpx-vp9", "libaom-av1", "libsvtav1", "av1_nvenc",
                  "h264_videotoolbox", "hevc_videotoolbox", "h264_qsv"):
        if re.search(rf"\b{re.escape(codec)}\b", enc):
            encoders.append(codec)
    return {"version": ver, "encoders": encoders,
            "nvenc": have_nvenc(), "nvdec": have_nvdec(),
            "videotoolbox": have_videotoolbox()}


def disk_info() -> dict:
    """Quick disk speed: write 256 MB sequentially, read it back.

    Linux will otherwise satisfy the read from page cache because the file was
    just written, which reports memory speed rather than storage speed.
    """
    import time, tempfile
    target = Path(tempfile.gettempdir()) / "vbench_disk.bin"
    size_mb = 256
    chunk = b"\xa5" * (1024 * 1024)
    try:
        t0 = time.perf_counter()
        with open(target, "wb") as f:
            for _ in range(size_mb):
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        dt_w = time.perf_counter() - t0
        t0 = time.perf_counter()
        with open(target, "rb") as f:
            if hasattr(os, "posix_fadvise"):
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            while f.read(1024 * 1024):
                pass
        dt_r = time.perf_counter() - t0
        return {
            "tmpdir": str(target.parent),
            "write_MBps": round(size_mb / dt_w, 1),
            "read_MBps": round(size_mb / dt_r, 1),
            "size_mb_tested": size_mb,
        }
    finally:
        try: target.unlink()
        except FileNotFoundError: pass


def main() -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu": cpu_info(),
        "mem": mem_info(),
        "gpus": gpu_info(),
        "ffmpeg": ffmpeg_info(),
        "disk": disk_info(),
    }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
