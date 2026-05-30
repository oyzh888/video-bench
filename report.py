#!/usr/bin/env python3
"""Build a side-by-side HTML report from results/*.json."""
from __future__ import annotations
import json, html, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


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


def render(runs: list[dict]) -> str:
    if not runs:
        return "<html><body><h1>No results yet.</h1><p>Run <code>./run.sh</code> first.</p></body></html>"

    labels = [label(r) for r in runs]

    # ---------- Probe ----------
    def gpu_name(r):
        gpus = r.get("probe", {}).get("gpus", [])
        return gpus[0]["name"] if gpus else "—"

    probe_rows = [
        row("Platform",      [r.get("probe", {}).get("platform", "?") for r in runs]),
        row("CPU model",     [r.get("probe", {}).get("cpu", {}).get("model", "?") for r in runs]),
        row("Logical cores", [r.get("probe", {}).get("cpu", {}).get("cores") for r in runs]),
        row("RAM (GB)",      [r.get("probe", {}).get("mem", {}).get("total_gb") for r in runs]),
        row("GPU",           [gpu_name(r) for r in runs]),
        row("ffmpeg",        [r.get("probe", {}).get("ffmpeg", {}).get("version", "?").split(" version ")[-1].split(" Copyright")[0] for r in runs]),
        row("Encoders",      [", ".join(r.get("probe", {}).get("ffmpeg", {}).get("encoders", [])) for r in runs]),
        row("Disk write MB/s", [r.get("probe", {}).get("disk", {}).get("write_MBps") for r in runs]),
        row("Disk read  MB/s", [r.get("probe", {}).get("disk", {}).get("read_MBps") for r in runs]),
    ]

    # ---------- Single ----------
    single_keys = sorted({k for r in runs for k in r.get("single", {}).get("tests", {}).keys()})
    single_rows = []
    for k in single_keys:
        single_rows.append(row(f"{k} — wall (s)",
            [r.get("single", {}).get("tests", {}).get(k, {}).get("wall_s") for r in runs]))
        single_rows.append(row(f"{k} — × realtime",
            [r.get("single", {}).get("tests", {}).get(k, {}).get("speed_x_realtime") for r in runs]))
        single_rows.append(row(f"{k} — fps",
            [r.get("single", {}).get("tests", {}).get(k, {}).get("ffmpeg_fps") for r in runs]))

    # ---------- Concurrent ----------
    def conc_lookup(r, kind, n):
        for entry in r.get("concurrent", {}).get(kind, []):
            if entry.get("n_parallel") == n:
                return entry
        return {}

    cpu_levels = sorted({e["n_parallel"] for r in runs
                         for e in r.get("concurrent", {}).get("cpu", [])})
    nvenc_levels = sorted({e["n_parallel"] for r in runs
                           for e in r.get("concurrent", {}).get("nvenc", [])})
    conc_rows = []
    for n in cpu_levels:
        conc_rows.append(row(f"CPU x264 ×{n} — videos/min",
            [conc_lookup(r, "cpu", n).get("videos_per_minute") for r in runs]))
        conc_rows.append(row(f"CPU x264 ×{n} — agg ×realtime",
            [conc_lookup(r, "cpu", n).get("aggregate_speed_x_realtime") for r in runs]))
    for n in nvenc_levels:
        conc_rows.append(row(f"NVENC ×{n} — videos/min",
            [conc_lookup(r, "nvenc", n).get("videos_per_minute") for r in runs]))
        conc_rows.append(row(f"NVENC ×{n} — agg ×realtime",
            [conc_lookup(r, "nvenc", n).get("aggregate_speed_x_realtime") for r in runs]))

    # ---------- Scenarios ----------
    scen_rows = []
    for k in ("edit_export_3clip", "thumbnail_grid_1fps", "subtitle_burn"):
        scen_rows.append(row(f"{k} — wall (s)",
            [r.get("scenarios", {}).get(k, {}).get("wall_s") for r in runs]))
    scen_rows.append(row("subtitle_burn — × realtime",
        [r.get("scenarios", {}).get("subtitle_burn", {}).get("speed_x_realtime") for r in runs]))
    scen_rows.append(row("thumbnail — thumbs/sec",
        [r.get("scenarios", {}).get("thumbnail_grid_1fps", {}).get("thumbs_per_sec") for r in runs]))

    # ---------- Verdict ----------
    def verdict(r):
        tests = r.get("single", {}).get("tests", {})
        cpu_speed = tests.get("x264_1080p_medium", {}).get("speed_x_realtime") or 0
        nvenc_speed = tests.get("nvenc_h264_1080p", {}).get("speed_x_realtime") or 0
        cpu_lvl = r.get("concurrent", {}).get("cpu", [])
        max_cpu_thru = max((e.get("videos_per_minute") or 0 for e in cpu_lvl), default=0)
        return {
            "1080p x264 speed": f"{cpu_speed:.1f}× realtime" if cpu_speed else "—",
            "1080p NVENC speed": f"{nvenc_speed:.1f}× realtime" if nvenc_speed else "—",
            "Best parallel CPU throughput": f"{max_cpu_thru:.1f} videos/min" if max_cpu_thru else "—",
        }
    verdict_rows = []
    keys = ["1080p x264 speed", "1080p NVENC speed", "Best parallel CPU throughput"]
    verdicts = [verdict(r) for r in runs]
    for k in keys:
        verdict_rows.append(row(k, [v[k] for v in verdicts]))

    sections = [
        ("Verdict (TL;DR)", verdict_rows),
        ("Hardware / probe", probe_rows),
        ("Single-clip render times", single_rows),
        ("Concurrent throughput", conc_rows),
        ("Realistic scenarios", scen_rows),
    ]

    parts = ['<!doctype html><meta charset="utf-8"><title>video-bench report</title>',
             '<style>',
             'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px;max-width:1400px;color:#111}',
             'h1{margin:0 0 4px}h2{margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}',
             'table{border-collapse:collapse;margin:8px 0;font-size:13px;width:100%}',
             'th,td{border:1px solid #ddd;padding:6px 10px;text-align:right}',
             'th{background:#f5f5f7;text-align:left}',
             'td.m{font-weight:600;text-align:left;background:#fafafa;font-family:ui-monospace,monospace;font-size:12px}',
             'td.na{color:#aaa}',
             '.note{color:#666;font-size:13px}',
             '</style>',
             f'<h1>video-bench report</h1>',
             f'<div class="note">{len(runs)} run(s) compared. Lower wall-time and higher ×realtime / videos-per-minute = better.</div>']

    for title, rows in sections:
        parts.append(f'<h2>{html.escape(title)}</h2>')
        parts.append('<table>')
        parts.append(header_row(labels))
        parts.extend(rows)
        parts.append('</table>')

    parts.append('<h2>Raw runs</h2><ul>')
    for r in runs:
        parts.append(
            f'<li>{html.escape(label(r))} — started {html.escape(r.get("started_at",""))}, '
            f'total {r.get("total_wall_s","?")}s</li>')
    parts.append('</ul>')
    return "\n".join(parts)


def main():
    runs = load_all()
    out = ROOT / "report.html"
    out.write_text(render(runs))
    print(f"wrote {out} ({len(runs)} runs)")


if __name__ == "__main__":
    main()
