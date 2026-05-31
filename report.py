#!/usr/bin/env python3
"""Build a side-by-side HTML dashboard from results/*.json.

Sections (in order):
  1. Verdict cards — one per machine, headline answers
  2. Customer scenarios — concrete batch jobs in minutes
  3. Charts — single render time, concurrent throughput curves, quality
  4. Capacity calculator — interactive "how long for N videos × L minutes"
  5. Raw tables — all numbers
"""
from __future__ import annotations
import json, html, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
sys.path.insert(0, str(ROOT))
from lib.capacity import (capacity_summary, batch_minutes, parallel_efficiency,
                          max_realtime_streams, best_concurrent_throughput,
                          single_video_seconds)
from lib.scoring import score_run, WEIGHTS


def load_all() -> list[dict]:
    runs = []
    for p in sorted(RESULTS.glob("*.json")):
        try:
            runs.append(json.loads(p.read_text()))
        except Exception as e:
            print(f"skip {p.name}: {e}", file=sys.stderr)
    return runs


def label(run: dict) -> str:
    base = run.get("hostname", "?")
    if run.get("label"):
        base += f" / {run['label']}"
    if run.get("quick"):
        base += " [quick]"
    return base


def short_label(run: dict) -> str:
    if run.get("label"): return run["label"]
    return run.get("hostname", "?")[:20]


def cell(v) -> str:
    if v is None:
        return '<td class="na">—</td>'
    if isinstance(v, float):
        return f"<td>{v:.2f}</td>"
    return f"<td>{html.escape(str(v))}</td>"


def header_row(labels: list[str]) -> str:
    return "<tr><th>metric</th>" + "".join(
        f"<th>{html.escape(l)}</th>" for l in labels) + "</tr>"


def row(name: str, values: list) -> str:
    return f"<tr><td class='m'>{html.escape(name)}</td>" + "".join(cell(v) for v in values) + "</tr>"


def fmt_min(v):
    if v is None: return "—"
    if v < 1: return f"{v*60:.1f} sec"
    if v < 60: return f"{v:.1f} min"
    return f"{v/60:.1f} hr"


def fmt_sec(v):
    if v is None: return "—"
    if v < 60: return f"{v:.1f} sec"
    return f"{v/60:.1f} min"


TIER_COLORS = {"S":"#a855f7","A":"#0969da","B":"#1a7f37","C":"#9a6700","D":"#cf222e","—":"#666"}


def verdict_card(run: dict) -> str:
    cap = capacity_summary(run)
    sc = score_run(run)
    cpu_name = (run.get("probe", {}).get("cpu", {}).get("model") or "?")[:60]
    cores = run.get("probe", {}).get("cpu", {}).get("cores", "?")
    ram = run.get("probe", {}).get("mem", {}).get("total_gb", "?")
    gpus = run.get("probe", {}).get("gpus", [])
    gpu = gpus[0]["name"] if gpus else "no GPU"
    nvenc = run.get("probe", {}).get("ffmpeg", {}).get("nvenc")
    nvdec = run.get("probe", {}).get("ffmpeg", {}).get("nvdec")
    if nvenc and nvdec:
        gpu_str = "✅ NVENC + NVDEC"
    elif nvenc:
        gpu_str = "✅ NVENC only"
    elif nvdec:
        gpu_str = "🟡 NVDEC only (no NVENC)"
    else:
        gpu_str = "❌ no GPU video accel"
    nvenc_str = gpu_str

    tier_color = TIER_COLORS.get(sc["tier"], "#666")
    dims = sc["dimensions"]
    dim_bars = ""
    for k, d in dims.items():
        s = d.get("score") or 0
        raw = d.get("raw")
        raw_str = f"{raw}" if raw is not None else "—"
        note = d.get("note")
        note_html = f'<div class="dim-note">{html.escape(note)}</div>' if note else ""
        dim_bars += f"""
    <div class="dim">
      <div class="dim-label">{html.escape(d['label'])}<span class="dim-raw">{html.escape(str(raw_str))}</span></div>
      <div class="dim-bar"><div class="dim-fill" style="width:{s:.0f}%"></div><span class="dim-score">{s:.0f}</span></div>{note_html}
    </div>"""

    # Optimization headline: only present when bench/optimized.py ran AND found gain
    opt = run.get("optimized", {}) or {}
    opt_speedup = opt.get("headline_speedup_x")
    opt_best = opt.get("best_aggregate_x_realtime")
    opt_html = ""
    if opt_speedup and opt_speedup >= 1.2:
        opt_html = (f'<div class="opt-banner">⚡ <b>{opt_speedup}× speedup</b> '
                    f'available with NUMA/CCD pinning '
                    f'(reaches {opt_best:.1f}× realtime aggregate). '
                    f'See "Optimization potential" section below for command.</div>')

    return f"""
<div class="card">
  <div class="card-top">
    <div>
      <div class="card-h">{html.escape(short_label(run))}</div>
      <div class="card-spec">{html.escape(cpu_name)} · {cores}c · {ram}GB · {html.escape(gpu)} · {nvenc_str}</div>
    </div>
    <div class="tier-block" style="background:{tier_color}">
      <div class="tier-letter">{sc['tier']}</div>
      <div class="tier-score">{(sc['composite'] or 0):.0f}<span class="tier-suffix"> / 100</span></div>
    </div>
  </div>
  <div class="tier-blurb">{html.escape(sc['tier_blurb'])}</div>
  {opt_html}
  <div class="dim-grid">{dim_bars}</div>
  <details class="card-details"><summary>Capacity scenarios</summary>
  <table class="kv">
    <tr><td>100× 1-min YouTube videos (medium)</td><td><b>{fmt_min(cap['scenario_100x_1min_youtube_medium_min'])}</b></td></tr>
    <tr><td>100× 1-min fast delivery (veryfast)</td><td><b>{fmt_min(cap['scenario_100x_1min_fast_delivery_min'])}</b></td></tr>
    <tr><td>100× 1-min 4K → 1080p downscale</td><td><b>{fmt_min(cap['scenario_100x_1min_4k_downscale_min'])}</b></td></tr>
    <tr><td>Single 5-min YouTube clip export</td><td><b>{fmt_sec(cap['scenario_single_5min_export_s'])}</b></td></tr>
    <tr><td>Single 30-min podcast 1080p export</td><td><b>{fmt_min(cap['scenario_single_30min_export_min'])}</b></td></tr>
    <tr><td>Single 1-hr long-form export</td><td><b>{fmt_min(cap['scenario_single_60min_export_min'])}</b></td></tr>
    <tr><td>Max simultaneous 1080p30 realtime streams</td><td><b>{cap['max_realtime_1080p_streams'] or '—'}</b></td></tr>
    <tr><td>Parallel scaling efficiency</td><td><b>{cap['parallel_efficiency_x'] or '—'}×</b></td></tr>
  </table></details>
</div>"""


def leaderboard(runs: list[dict]) -> str:
    """Sortable summary: rank by composite, show all dimensions."""
    scored = [(r, score_run(r)) for r in runs]
    scored.sort(key=lambda x: (x[1]["composite"] or 0), reverse=True)
    dim_keys = list(WEIGHTS.keys())
    head = ("<tr><th>#</th><th>Machine</th><th>Tier</th><th>Composite</th>" +
            "".join(f"<th>{k.replace('_',' ')}<br><span class='w'>w={int(WEIGHTS[k]*100)}%</span></th>"
                    for k in dim_keys) + "</tr>")
    rows = []
    for i, (run, sc) in enumerate(scored, 1):
        cells = []
        for k in dim_keys:
            s = sc["dimensions"][k].get("score")
            cells.append(f'<td class="dimcell">{(s or 0):.0f}</td>' if s is not None else '<td class="na">—</td>')
        tcolor = TIER_COLORS.get(sc["tier"], "#666")
        rows.append(
            f"<tr><td>{i}</td><td class='m'>{html.escape(short_label(run))}</td>"
            f"<td><span class='pill' style='background:{tcolor}'>{sc['tier']}</span></td>"
            f"<td><b>{(sc['composite'] or 0):.1f}</b></td>"
            + "".join(cells) + "</tr>")
    return f"<table class='leaderboard'>{head}{''.join(rows)}</table>"


def optimization_section(runs: list[dict]) -> str:
    """Per-machine 'how to make this box go faster' breakdown.
    Only renders for machines where bench/optimized.py ran AND found a real gain."""
    parts = []
    for run in runs:
        opt = run.get("optimized", {}) or {}
        if not opt.get("feasible"):
            continue
        if not opt.get("headline_speedup_x") or opt.get("headline_speedup_x") < 1.2:
            continue
        topo = opt.get("topology", {}) or {}
        l3 = topo.get("l3_count")
        sockets = topo.get("sockets")
        cps = topo.get("cores_per_socket")
        threads = topo.get("threads_per_core")
        rows = []
        for k, v in opt.get("tests", {}).items():
            rows.append(
                f"<tr><td><code>{html.escape(k)}</code></td>"
                f"<td>{v.get('n_procs')}</td>"
                f"<td>{v.get('threads_per')}</td>"
                f"<td>{'yes' if v.get('pinned') else 'no'}</td>"
                f"<td>{v.get('wall_s')}</td>"
                f"<td><b>{v.get('aggregate_speed_x_realtime')}× rt</b></td></tr>")
        # Generate the actual ready-to-run shell snippet
        n = l3 or 12
        snippet = f"""# Optimal recipe for this machine (auto-generated):
# {sockets}-socket × {cps}-core × {threads}-thread, {l3} L3 caches (CCDs)
# Run {n} ffmpeg processes, each pinned to one CCD (8 cores), threads=8.

for i in $(seq 0 {n-1}); do
  start=$((i*8)); end=$((start+7))
  taskset -c $start-$end ffmpeg -y -threads 8 \\
    -i input_$i.mp4 -c:v libx264 -threads 8 -preset medium -crf 23 -an \\
    output_$i.mp4 &
done
wait"""
        parts.append(f"""
<div class="opt-section">
  <h3 style="margin:0 0 4px">Optimization potential — {html.escape(short_label(run))}</h3>
  <div style="color:#666;font-size:12px;margin-bottom:10px">
    Topology: {sockets}-socket, {cps}c/socket, SMT={threads}, {l3} L3 caches.
    The default ffmpeg behaviour ({{cores}}-thread auto-detect) explodes on
    big-server CPUs — careful pinning recovers <b>{opt['headline_speedup_x']}×</b> throughput.
  </div>
  <table class="opt-table">
    <tr><th>config</th><th>N procs</th><th>threads/proc</th><th>pinned?</th><th>wall (s)</th><th>aggregate</th></tr>
    {''.join(rows)}
  </table>
  <details><summary style="cursor:pointer;color:#0969da;font-size:12px">Ready-to-run shell recipe</summary>
  <pre>{html.escape(snippet)}</pre>
  </details>
</div>""")
    if not parts:
        return ""
    return "<h2>Optimization potential (NUMA / CCD pinning)</h2>" + "".join(parts)


def radar_data(runs: list[dict]) -> dict:
    """Per-machine 6-axis vector, 0–100, for radar chart."""
    out = []
    dims = list(WEIGHTS.keys())
    for r in runs:
        sc = score_run(r)
        out.append({
            "label": short_label(r),
            "values": [(sc["dimensions"][k].get("score") or 0) for k in dims],
        })
    return {"axes": [k.replace("_"," ") for k in dims], "machines": out}


def calculator_data(runs: list[dict]) -> list[dict]:
    """Per-machine inputs for the JS calculator."""
    out = []
    for r in runs:
        tests = r.get("single", {}).get("tests", {})
        out.append({
            "label": short_label(r),
            "hostname": r.get("hostname"),
            "speed": {
                "x264_medium":   tests.get("x264_1080p_medium", {}).get("speed_x_realtime"),
                "x264_veryfast": tests.get("x264_1080p_veryfast", {}).get("speed_x_realtime"),
                "x264_4k_down":  tests.get("x264_4k_to_1080p", {}).get("speed_x_realtime"),
                "x265_medium":   tests.get("x265_1080p_medium", {}).get("speed_x_realtime"),
                "nvenc_h264":    tests.get("nvenc_h264_1080p", {}).get("speed_x_realtime"),
            },
            "parallel_efficiency": parallel_efficiency(r) or 1.0,
        })
    return out


def chart_data(runs: list[dict]) -> dict:
    """JSON blobs for Chart.js."""
    labels = [short_label(r) for r in runs]

    # Single render time (lower=better) — bar grouped by test
    single_keys = ["x264_1080p_medium", "x264_1080p_veryfast",
                   "x264_4k_to_1080p", "x265_1080p_medium",
                   "nvenc_h264_1080p", "decode_only_4k"]
    single_speed = {}
    for k in single_keys:
        single_speed[k] = [
            (r.get("single", {}).get("tests", {}).get(k, {}) or {}).get("speed_x_realtime")
            for r in runs]

    # Concurrent throughput curve (CPU): N → videos_per_minute
    conc_curves = []
    for r in runs:
        cpu = r.get("concurrent", {}).get("cpu", []) or []
        conc_curves.append({
            "label": short_label(r),
            "points": [(e["n_parallel"], e.get("videos_per_minute"))
                       for e in sorted(cpu, key=lambda x: x["n_parallel"])],
        })

    # Quality: PSNR vs encode time (one point per preset, per machine)
    quality_pts = []
    for r in runs:
        for k, v in (r.get("quality", {}).get("tests", {}) or {}).items():
            if v.get("psnr_db") and v.get("encode_wall_s"):
                quality_pts.append({
                    "machine": short_label(r),
                    "preset": k,
                    "x": v["encode_wall_s"],
                    "y": v["psnr_db"],
                })

    return {
        "labels": labels,
        "single_speed": single_speed,
        "concurrent": conc_curves,
        "quality": quality_pts,
    }


def render(runs: list[dict]) -> str:
    if not runs:
        return ("<html><body><h1>No results yet.</h1>"
                "<p>Run <code>./run.sh</code> first, then <code>python3 report.py</code>.</p>"
                "</body></html>")

    cards = "\n".join(verdict_card(r) for r in runs)
    cd = chart_data(runs)
    calc = calculator_data(runs)
    rd = radar_data(runs)
    lb = leaderboard(runs)

    # Raw tables (kept short — main info is in cards/charts)
    labels = [label(r) for r in runs]
    probe_rows = [
        row("CPU model",     [r.get("probe", {}).get("cpu", {}).get("model", "?") for r in runs]),
        row("Logical cores", [r.get("probe", {}).get("cpu", {}).get("cores") for r in runs]),
        row("RAM (GB)",      [r.get("probe", {}).get("mem", {}).get("total_gb") for r in runs]),
        row("GPU",           [(r.get("probe", {}).get("gpus") or [{"name":"—"}])[0]["name"] for r in runs]),
        row("NVENC working", [r.get("probe", {}).get("ffmpeg", {}).get("nvenc") for r in runs]),
        row("Disk read MB/s",  [r.get("probe", {}).get("disk", {}).get("read_MBps") for r in runs]),
        row("Disk write MB/s", [r.get("probe", {}).get("disk", {}).get("write_MBps") for r in runs]),
    ]

    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>video-bench dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       margin:0;background:#f8f9fb;color:#111;font-size:14px}}
  .wrap{{max-width:1400px;margin:0 auto;padding:24px}}
  h1{{margin:0 0 4px;font-size:28px}}
  h2{{margin:32px 0 12px;font-size:20px;border-bottom:2px solid #ddd;padding-bottom:6px}}
  .sub{{color:#666;font-size:13px;margin-bottom:16px}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:16px}}
  .card{{background:#fff;border:1px solid #e1e4e8;border-radius:10px;padding:18px;
         box-shadow:0 1px 3px rgba(0,0,0,0.04)}}
  .card-h{{font-size:18px;font-weight:700;margin-bottom:4px}}
  .card-spec{{color:#666;font-size:12px;margin-bottom:10px;line-height:1.4}}
  .big{{font-size:36px;font-weight:800;color:#0969da;margin:8px 0 14px}}
  .big .unit{{font-size:13px;color:#666;font-weight:400}}
  table.kv{{width:100%;border-collapse:collapse;font-size:13px}}
  table.kv td{{padding:5px 0;border-bottom:1px solid #f0f1f4}}
  table.kv td:last-child{{text-align:right;white-space:nowrap}}
  table.kv tr:last-child td{{border-bottom:none}}
  .card-top{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
  .tier-block{{color:#fff;border-radius:10px;padding:10px 14px;text-align:center;min-width:90px}}
  .tier-letter{{font-size:36px;font-weight:900;line-height:1}}
  .tier-score{{font-size:16px;font-weight:700;margin-top:2px}}
  .tier-suffix{{font-size:11px;font-weight:400;opacity:0.85}}
  .tier-blurb{{font-size:12px;color:#444;font-style:italic;margin:10px 0;padding:6px 10px;background:#f3f4f6;border-radius:5px}}
  .dim-grid{{display:flex;flex-direction:column;gap:6px;margin:12px 0 6px}}
  .dim-label{{font-size:11px;color:#444;display:flex;justify-content:space-between;margin-bottom:2px}}
  .dim-raw{{color:#888;font-family:ui-monospace,monospace;font-size:10.5px}}
  .dim-bar{{position:relative;height:14px;background:#eef0f3;border-radius:7px;overflow:hidden}}
  .dim-fill{{position:absolute;top:0;left:0;height:100%;background:linear-gradient(90deg,#22c55e,#0969da);border-radius:7px;transition:width .4s}}
  .dim-score{{position:absolute;right:8px;top:0;font-size:11px;font-weight:700;color:#222;line-height:14px}}
  .dim-note{{font-size:11px;color:#9a6700;margin-top:3px;font-style:italic}}
  .opt-banner{{margin:8px 0 4px;padding:8px 12px;background:linear-gradient(90deg,#fef3c7,#fde68a);border:1px solid #f59e0b;border-radius:6px;font-size:12px;color:#7c2d12}}
  .opt-section{{background:#fff;border:1px solid #e1e4e8;border-radius:10px;padding:18px;margin-top:8px;font-size:13px}}
  .opt-section pre{{background:#0d1117;color:#e6edf3;padding:12px;border-radius:6px;overflow-x:auto;font-size:12px;line-height:1.5}}
  .opt-table{{width:100%;border-collapse:collapse;margin:8px 0}}
  .opt-table th,.opt-table td{{padding:5px 10px;border-bottom:1px solid #eee;text-align:right;font-size:12px}}
  .opt-table th{{background:#f5f5f7;text-align:left}}
  .opt-table td:first-child,.opt-table th:first-child{{text-align:left}}
  details.card-details{{margin-top:8px;font-size:13px}}
  details.card-details summary{{cursor:pointer;color:#0969da;font-size:12px;outline:none}}
  table.leaderboard{{width:100%;border-collapse:collapse;background:#fff;font-size:13px}}
  table.leaderboard th,table.leaderboard td{{border:1px solid #e1e4e8;padding:6px 10px;text-align:center}}
  table.leaderboard th{{background:#f5f5f7;font-size:11px}}
  table.leaderboard th .w{{font-weight:400;color:#888;font-size:10px}}
  table.leaderboard td.m{{text-align:left;font-weight:600}}
  table.leaderboard td.dimcell{{font-family:ui-monospace,monospace}}
  .pill{{display:inline-block;padding:3px 12px;border-radius:12px;color:#fff;font-weight:700;font-size:13px;min-width:18px}}
  .charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(450px,1fr));gap:20px}}
  .chartbox{{background:#fff;border:1px solid #e1e4e8;border-radius:10px;padding:16px}}
  .chartbox h3{{margin:0 0 4px;font-size:15px}}
  .chartbox .desc{{color:#666;font-size:12px;margin-bottom:10px}}
  table.raw{{border-collapse:collapse;width:100%;font-size:12px;background:#fff}}
  table.raw th,table.raw td{{border:1px solid #e1e4e8;padding:6px 10px;text-align:right}}
  table.raw th{{background:#f5f5f7;text-align:left}}
  table.raw td.m{{font-weight:600;text-align:left;background:#fafafa;
                  font-family:ui-monospace,monospace;font-size:11px}}
  .calc{{background:#fff;border:1px solid #e1e4e8;border-radius:10px;padding:18px}}
  .calc label{{display:inline-block;margin-right:14px}}
  .calc input,.calc select{{padding:5px 8px;border:1px solid #ccc;border-radius:5px;font-size:13px}}
  .calc-results table{{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px}}
  .calc-results td{{padding:6px 10px;border-bottom:1px solid #f0f1f4}}
  .calc-results td:last-child{{text-align:right;font-weight:700;color:#0969da}}
  .footer{{color:#666;font-size:12px;margin-top:32px;text-align:center}}
  code{{background:#f3f4f6;padding:1px 5px;border-radius:3px;font-size:12px}}
</style>
</head><body><div class="wrap">

<h1>video-bench dashboard</h1>
<div class="sub">{len(runs)} machine(s) compared · all scenarios extrapolated from measured per-clip speed × parallel-throughput curve · lower wall-time / higher ×realtime / higher videos-per-min = better</div>

<h2>Leaderboard</h2>
<div class="sub">Composite = weighted average of 6 dimensions, each scored 0–100 against fixed reference points.
Tiers: <span class="pill" style="background:#a855f7">S</span> 90+ ·
<span class="pill" style="background:#0969da">A</span> 75+ ·
<span class="pill" style="background:#1a7f37">B</span> 60+ ·
<span class="pill" style="background:#9a6700">C</span> 40+ ·
<span class="pill" style="background:#cf222e">D</span> &lt;40</div>
{lb}

<h2>Score breakdown — each machine</h2>
<div class="cards">{cards}</div>

{optimization_section(runs)}

<h2>Score profile (radar)</h2>
<div class="chartbox" style="max-width:600px;margin:0 auto"><canvas id="ch_radar" height="380"></canvas></div>

<h2>Performance charts</h2>
<div class="charts">
  <div class="chartbox">
    <h3>Single-clip encoding speed (×realtime, higher=better)</h3>
    <div class="desc">How fast each preset processes one 30s 1080p clip. NVENC bars only appear if GPU encode actually works on the box.</div>
    <canvas id="ch_single" height="260"></canvas>
  </div>
  <div class="chartbox">
    <h3>Concurrent throughput (videos/min vs N parallel)</h3>
    <div class="desc">Where does the throughput curve plateau? Peak = best batch concurrency setting. After plateau, more parallelism just slows everyone down.</div>
    <canvas id="ch_concurrent" height="260"></canvas>
  </div>
  <div class="chartbox">
    <h3>Quality vs encoding time (PSNR @ 4 Mbps)</h3>
    <div class="desc">Up-and-left is best (high quality, low time). Each marker is one preset on one machine.</div>
    <canvas id="ch_quality" height="260"></canvas>
  </div>
  <div class="chartbox">
    <h3>Customer batch scenario (100× 1-min videos, minutes)</h3>
    <div class="desc">Estimated wall-clock for the headline batch jobs. Lower = better.</div>
    <canvas id="ch_scenarios" height="260"></canvas>
  </div>
</div>

<h2>Capacity calculator</h2>
<div class="calc">
<div>
  <label>Videos: <input type="number" id="cN" value="100" min="1" max="100000" style="width:80px"></label>
  <label>Length each: <input type="number" id="cL" value="60" min="1" max="36000" style="width:80px"> sec</label>
  <label>Preset:
    <select id="cPreset">
      <option value="x264_medium">x264 medium (good quality)</option>
      <option value="x264_veryfast">x264 veryfast (fast delivery)</option>
      <option value="x264_4k_down">4K → 1080p downscale</option>
      <option value="x265_medium">x265 medium (smaller files)</option>
      <option value="nvenc_h264">NVENC h264 (GPU)</option>
    </select>
  </label>
</div>
<div class="calc-results" id="calcResults"></div>
</div>

<h2>Hardware probe</h2>
<table class="raw">
{header_row(labels)}
{''.join(probe_rows)}
</table>

<div class="footer">
  Estimates based on this machine's measured single-clip ×realtime × parallel-efficiency boost.
  Real long-running batches may differ ±10–20% due to GOP overhead, I/O contention, thermal throttling.
  · Source: <a href="https://github.com/oyzh888/video-bench">github.com/oyzh888/video-bench</a>
</div>

<script>
const RUNS = {json.dumps(calc)};
const CD = {json.dumps(cd)};
const RD = {json.dumps(rd)};
const COLORS = ['#0969da','#cf222e','#1a7f37','#9a6700','#8250df','#bf3989','#0a3069'];

// === Single-clip speed bar chart ===
{{
  const tests = ['x264_1080p_medium','x264_1080p_veryfast','x264_4k_to_1080p',
                 'x265_1080p_medium','nvenc_h264_1080p','decode_only_4k'];
  const haveAny = (k) => CD.single_speed[k] && CD.single_speed[k].some(v => v != null);
  const used = tests.filter(haveAny);
  new Chart(document.getElementById('ch_single'), {{
    type:'bar',
    data:{{
      labels: used,
      datasets: CD.labels.map((lab,i) => ({{
        label: lab,
        data: used.map(k => CD.single_speed[k][i] || 0),
        backgroundColor: COLORS[i % COLORS.length],
      }})),
    }},
    options: {{
      indexAxis:'y',
      scales:{{x:{{title:{{display:true,text:'× realtime'}}}}}},
      plugins:{{legend:{{position:'top'}}}}
    }}
  }});
}}

// === Concurrent throughput line chart ===
{{
  new Chart(document.getElementById('ch_concurrent'), {{
    type:'line',
    data:{{
      datasets: CD.concurrent.map((c,i) => ({{
        label: c.label,
        data: c.points.map(p => ({{x:p[0], y:p[1]}})),
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length],
        tension:0.2,
      }}))
    }},
    options: {{
      scales:{{
        x:{{type:'linear',title:{{display:true,text:'parallel jobs (N)'}}}},
        y:{{title:{{display:true,text:'videos / minute'}}}}
      }}
    }}
  }});
}}

// === Quality scatter ===
{{
  const byMachine = {{}};
  CD.quality.forEach(p => {{
    if (!byMachine[p.machine]) byMachine[p.machine] = [];
    byMachine[p.machine].push({{x:p.x, y:p.y, preset:p.preset}});
  }});
  new Chart(document.getElementById('ch_quality'), {{
    type:'scatter',
    data:{{
      datasets: Object.entries(byMachine).map(([m,pts],i) => ({{
        label: m,
        data: pts,
        backgroundColor: COLORS[i % COLORS.length],
        pointRadius: 7,
      }}))
    }},
    options: {{
      scales:{{
        x:{{title:{{display:true,text:'encode wall (s, lower=better)'}}}},
        y:{{title:{{display:true,text:'PSNR dB (higher=better)'}}}}
      }},
      plugins:{{tooltip:{{callbacks:{{
        label: ctx => `${{ctx.raw.preset}}: ${{ctx.raw.x.toFixed(2)}}s, ${{ctx.raw.y.toFixed(2)}} dB`
      }}}}}}
    }}
  }});
}}

// === Customer scenario bars ===
{{
  const scenarios = ['100× 1min medium','100× 1min veryfast','100× 1min 4K→1080p',
                     '5min export','30min export','60min export'];
  const presetMap = {{
    '100× 1min medium': ['x264_medium', 100, 60],
    '100× 1min veryfast': ['x264_veryfast', 100, 60],
    '100× 1min 4K→1080p': ['x264_4k_down', 100, 60],
    '5min export': ['x264_medium', 1, 5*60],
    '30min export': ['x264_medium', 1, 30*60],
    '60min export': ['x264_medium', 1, 60*60],
  }};
  function batch(run, preset, n, l) {{
    const speed = run.speed[preset];
    if (!speed) return null;
    const eff = (n > 1) ? (run.parallel_efficiency || 1) : 1;
    return n * l / (speed * eff) / 60; // minutes
  }}
  new Chart(document.getElementById('ch_scenarios'), {{
    type:'bar',
    data:{{
      labels: scenarios,
      datasets: RUNS.map((r,i) => ({{
        label: r.label,
        data: scenarios.map(s => batch(r, ...presetMap[s])),
        backgroundColor: COLORS[i % COLORS.length],
      }}))
    }},
    options: {{
      scales:{{y:{{title:{{display:true,text:'estimated wall time (min)'}}}}}},
      plugins:{{tooltip:{{callbacks:{{
        label: ctx => `${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(2)}} min`
      }}}}}}
    }}
  }});
}}

// === Radar chart ===
{{
  new Chart(document.getElementById('ch_radar'), {{
    type:'radar',
    data:{{
      labels: RD.axes,
      datasets: RD.machines.map((m,i) => ({{
        label: m.label, data: m.values,
        borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length] + '33',
        pointBackgroundColor: COLORS[i % COLORS.length],
      }}))
    }},
    options:{{
      scales:{{r:{{min:0,max:100,ticks:{{stepSize:25}}}}}},
      plugins:{{legend:{{position:'top'}}}}
    }}
  }});
}}

// === Calculator ===
function recalc() {{
  const N = +document.getElementById('cN').value;
  const L = +document.getElementById('cL').value;
  const preset = document.getElementById('cPreset').value;
  let html = '<table>';
  html += '<tr><th style="text-align:left">Machine</th><th style="text-align:right">Wall time</th><th style="text-align:right">Per-video</th></tr>';
  RUNS.forEach(r => {{
    const speed = r.speed[preset];
    if (!speed) {{
      html += `<tr><td>${{r.label}}</td><td style="text-align:right;color:#aaa">preset not measured</td><td>—</td></tr>`;
      return;
    }}
    const eff = N > 1 ? (r.parallel_efficiency || 1) : 1;
    const totalSec = N * L / (speed * eff);
    const perVid = L / speed;
    const fmt = (s) => s < 60 ? s.toFixed(1)+' sec' : (s < 3600 ? (s/60).toFixed(1)+' min' : (s/3600).toFixed(2)+' hr');
    html += `<tr><td>${{r.label}}</td><td>${{fmt(totalSec)}}</td><td>${{fmt(perVid)}}</td></tr>`;
  }});
  html += '</table>';
  document.getElementById('calcResults').innerHTML = html;
}}
['cN','cL','cPreset'].forEach(id => document.getElementById(id).addEventListener('input', recalc));
recalc();
</script>
</div></body></html>"""


def main():
    runs = load_all()
    out = ROOT / "report.html"
    out.write_text(render(runs))
    print(f"wrote {out} ({len(runs)} runs)")


if __name__ == "__main__":
    main()
