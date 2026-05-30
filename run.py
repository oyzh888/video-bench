#!/usr/bin/env python3
"""video-bench orchestrator. Runs all suites, writes results/<host>-<ts>.json."""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.common import RESULTS, hostname, now_iso
from bench import probe, single, concurrent, scenarios, quality


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="Smoke test (~1 min)")
    ap.add_argument("--skip", nargs="*", default=[],
                    choices=["single", "concurrent", "scenarios", "quality"])
    ap.add_argument("--label", default=None,
                    help="Optional label appended to output filename")
    args = ap.parse_args()

    t0 = time.perf_counter()
    print(f"[{now_iso()}] video-bench on {hostname()} (quick={args.quick})")

    out: dict = {
        "schema_version": 1,
        "hostname": hostname(),
        "started_at": now_iso(),
        "quick": args.quick,
        "label": args.label,
    }

    print("→ probe...")
    out["probe"] = probe.main()

    if "single" not in args.skip:
        print("→ single-video tests...")
        out["single"] = single.main(quick=args.quick)

    if "concurrent" not in args.skip:
        print("→ concurrent tests...")
        out["concurrent"] = concurrent.main(quick=args.quick)

    if "scenarios" not in args.skip:
        print("→ scenarios...")
        out["scenarios"] = scenarios.main(quick=args.quick)

    if "quality" not in args.skip:
        print("→ quality (PSNR/SSIM at fixed bitrate)...")
        out["quality"] = quality.main(quick=args.quick)

    out["total_wall_s"] = round(time.perf_counter() - t0, 1)

    suffix = f"-{args.label}" if args.label else ""
    fname = RESULTS / f"{hostname()}-{out['started_at']}{suffix}.json"
    fname.write_text(json.dumps(out, indent=2))
    print(f"\n✓ done in {out['total_wall_s']}s")
    print(f"  wrote {fname.relative_to(ROOT)}")
    print(f"  next: python3 report.py")


if __name__ == "__main__":
    main()
