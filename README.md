# video-bench

Portable benchmark suite to estimate **how good a machine feels for video work** —
single-clip render time, parallel throughput, and realistic editing scenarios.

Run it on every machine you care about, drop the JSON results into `results/`,
then `python3 report.py` produces a side-by-side HTML report.

## What it measures

| Category    | Test                              | Why                                    |
|-------------|-----------------------------------|----------------------------------------|
| Probe       | CPU / GPU / RAM / disk / ffmpeg   | Sanity & spec snapshot                 |
| Single      | 1080p x264 transcode              | "Render one video" baseline (CPU)      |
| Single      | 1080p HEVC x265                   | Slower codec stress                    |
| Single      | 1080p NVENC h264                  | GPU encode speedup                     |
| Single      | 4K → 1080p downscale (CPU+GPU)    | Scaling cost                           |
| Single      | Decode-only (no encode)           | Pure decode FPS                        |
| Concurrent  | N parallel x264 (1, 2, 4, 8, 16)  | Saturation / multi-job throughput      |
| Concurrent  | N parallel NVENC (1, 2, 4, 8)     | GPU encode session limit               |
| Scenario    | Cut + concat + re-encode          | "Export an edit" workflow              |
| Scenario    | Thumbnail extraction (1 fps grid) | Preview generation                     |
| Scenario    | Subtitle burn-in                  | Filter-graph cost                      |
| I/O         | Sequential & random disk          | Storage isn't the bottleneck check     |
| Quality     | PSNR / SSIM @ fixed bitrate       | Encoding quality (Netflix VMAF style)  |

All clips are **synthetically generated** by ffmpeg `testsrc2`, so there's no
asset download and every machine runs the exact same input.

## Live comparison dashboard

Auto-published to GitHub Pages on every push to `results/`:
**https://oyzh888.github.io/video-bench/**

To add your machine's numbers, just commit `results/<your-host>-*.json` and push.

## Why this design (vs other open benchmarks)

| Bench | Strength | Why we're different |
|-------|----------|---------------------|
| [c3voc/transcoding-benchmark](https://github.com/voc/transcoding-benchmark) | Standard 1080p clip, x264/x265 sweep | Single-threaded only, no concurrency curve, no scenarios |
| [Netflix VMAF](https://github.com/Netflix/vmaf) | Gold-standard quality metric | Quality-only, doesn't measure throughput |
| [Jellyfin ffmpeg-test](https://github.com/jellyfin/jellyfin-ffmpeg) | Real HW decode + concurrent transcode | Tied to Jellyfin runtime, harder to run standalone |
| **video-bench** | Throughput + concurrency + scenarios + PSNR/SSIM | Stdlib-only, runs anywhere, 2-min total |

We optionally cross-check against the **Big Buck Bunny** reference clip
(used by all the above) via `./run.sh --real-clip` — that's the way to
compare numbers with published benchmarks elsewhere.

## Quick start

```bash
git clone https://github.com/oyzh888/video-bench.git
cd video-bench
./run.sh                       # full suite (~5–15 min depending on machine)
./run.sh --quick               # smoke-test (~1 min)
python3 report.py              # build HTML from results/*.json
open report.html
```

Each run writes `results/<hostname>-<timestamp>.json`. Commit those back so
multiple machines accumulate in the same place, or just `scp` them into one
spot before running `report.py`.

## Requirements

- `ffmpeg` ≥ 4.4 (with libx264 + libx265; NVENC tests auto-skip if absent)
- `python3` ≥ 3.8 (stdlib only — no pip install)
- ~2 GB free disk

NVENC tests auto-skip if `nvidia-smi` or `h264_nvenc` isn't present, so the
suite runs cleanly on Macs / CPU-only boxes too.

## Layout

```
run.sh                # orchestrator
bench/                # individual benchmarks (probe / single / concurrent / scenario / io)
lib/                  # shared helpers (ffmpeg detection, timing, asset gen)
report.py             # JSON → HTML comparator
results/              # one JSON per machine-run
```
