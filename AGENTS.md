# AGENTS.md — How an agent runs and contributes to video-bench

> If you're an AI agent (Claude / Codex / Cursor / etc.) running on a new
> machine and want to add that machine's number to the public leaderboard,
> **this is the only doc you need to read**. The whole flow is self-serve.

**Live dashboard:** https://oyzh888.github.io/video-bench/
**Repo:** https://github.com/oyzh888/video-bench

---

## What this repo does (10-second version)

1. You run `python3 run.py` on a machine. It takes ~2 min and emits one JSON file under `results/`.
2. You commit + push that JSON.
3. A GitHub Actions workflow (`.github/workflows/pages.yml`) automatically rebuilds the dashboard and republishes it to GitHub Pages within ~30s.
4. The new machine appears in the leaderboard, radar chart, calculator, every chart — **with zero front-end code changes**.

The dashboard is fully data-driven. Every section iterates over `results/*.json`. You don't touch HTML, CSS, or JS.

---

## The 5-step contribution flow

```bash
# 1. Clone
git clone https://github.com/oyzh888/video-bench.git
cd video-bench

# 2. Run the bench (full ≈ 2 min, --quick ≈ 40s)
python3 run.py --label "<short-machine-nickname>"

# 3. Verify a JSON appeared
ls results/
# expect: <hostname>-<timestamp>-<label>.json   plus pluto-H100-baseline.json (reference)

# 4. (optional) Build report locally to sanity-check
python3 report.py
open report.html

# 5. Commit and push the JSON
git add results/<your-new-file>.json
git commit -m "bench: add <machine-nickname> result"
git push
```

After step 5, wait ~30s and refresh https://oyzh888.github.io/video-bench/ — your machine is on the leaderboard.

---

## Naming convention for `--label`

Use a short, recognizable nickname:

| Good | Bad |
|---|---|
| `m3-max-mbp` | `Steves Personal MacBook Pro 16inch 2024 M3 Max 64GB` |
| `rtx4090-desktop` | `desktop` |
| `pluto-h200-prod` | `gpu-machine` |
| `aws-c7i-4xlarge` | `cloud` |

Rule of thumb: when 10 machines are on the dashboard, the label has to make it obvious which one is yours.

---

## Pushing — three scenarios

### Scenario A: You have direct push access to `oyzh888/video-bench` (Steve's own agents)

Just `git push` on master. Workflow runs, dashboard updates.

```bash
git remote -v   # check origin URL
# If using a token (e.g. on Steve's pluto pod):
git remote set-url origin "https://oyzh888:${GITHUB_PERSONAL_TOKEN}@github.com/oyzh888/video-bench.git"
git push origin master
git remote set-url origin https://github.com/oyzh888/video-bench.git   # restore clean URL
```

### Scenario B: External machine without push access — open a PR

```bash
gh repo fork oyzh888/video-bench --clone=false
git remote add fork https://github.com/<your-gh-user>/video-bench.git
git checkout -b add-<machine-nickname>
git add results/<your-new-file>.json
git commit -m "bench: add <machine-nickname> result"
git push fork add-<machine-nickname>
gh pr create --repo oyzh888/video-bench \
  --title "bench: add <machine-nickname>" \
  --body "Adds 1 result file. Composite: <X>, Tier: <Y>."
```

PR will be merged manually; once merged, Pages CI redeploys automatically.

### Scenario C: No GitHub access at all (air-gapped / locked-down customer machine)

Just `scp` the JSON to a machine that has access:

```bash
scp results/<your-new-file>.json user@gateway-host:/tmp/
# then on gateway: cd ~/video-bench && cp /tmp/<file>.json results/ && git push
```

The point: **the JSON file is the only contract**. As long as one valid JSON lands in `results/` on master, the dashboard picks it up.

---

## What's in the JSON?

`run.py` writes a `schema_version: 1` blob with these top-level keys (see `lib/scoring.py` for what each is used for):

```json
{
  "schema_version": 1,
  "hostname": "...",
  "label": "...",
  "started_at": "2026-...",
  "quick": false,
  "probe":      { "cpu": {...}, "mem": {...}, "gpus": [...], "ffmpeg": {...}, "disk": {...} },
  "single":     { "tests": { "x264_1080p_medium": {...}, ... } },
  "concurrent": { "cpu": [...], "nvenc": [...] },
  "scenarios":  { "edit_export_3clip": {...}, ... },
  "quality":    { "tests": { "libx264_medium": {...}, ... } },
  "total_wall_s": 118.6
}
```

**Don't edit the JSON by hand.** If a field is missing, the scoring/dashboard handles it gracefully (shows "—" in the cells).

---

## How the dashboard updates itself

`.github/workflows/pages.yml`:

```yaml
on:
  push:
    branches: [master, main]
    paths:
      - "results/**"           # any new JSON triggers
      - "report.py"            # report logic changes too
      - ".github/workflows/pages.yml"
```

The workflow:
1. Checks out the repo.
2. `python3 report.py` — re-renders `report.html` from **all** `results/*.json` it finds.
3. Copies `report.html` → `_site/index.html`, plus `results/` for raw JSON access.
4. Deploys to GitHub Pages.

So the answer to "is it automatic vs. do I need to change frontend code" is: **fully automatic, no frontend changes ever**. Only edit `report.py` if you want to add a new chart or change the scoring presentation; even then, it's pure Python that emits HTML.

---

## What an agent should check before pushing

Quick correctness gate (the workflow runs report.py too, so anything that breaks `report.py` blocks the deploy):

```bash
python3 report.py        # must succeed and write report.html
ls -la report.html       # nonzero size
python3 -c "import json; json.load(open('results/<your-file>.json'))"   # valid JSON
```

If `python3 report.py` errors on your JSON, the schema evolved and you need to either re-run on a fresh clone of master, or open a PR with the schema change.

---

## What NOT to do

- **Don't commit `assets/`** — that directory is generated test clips (~50–200 MB).
- **Don't commit `report.html`** — CI generates it. Local `python3 report.py` is for sanity-check only.
- **Don't push results from a `--quick` run as the canonical machine result.** `--quick` is for smoke-testing the suite. Do a full `python3 run.py --label ...` for the real submission.
- **Don't push `results/<host>-<ts>-quick.json` files** — clean those up first.
- **Don't rename or reformat existing results.** Other people's submissions are theirs.
- **Don't skip dimensions.** If the machine has no GPU, that's fine — the scoring already handles it (GPU dim → 0). Don't fake numbers.

---

## When something goes weird

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'lib'` | Running from a sibling dir | `cd` into repo root first |
| ffmpeg "Encoder not found" mid-run | This ffmpeg build is missing libx265 / drawtext | The bench skips them gracefully; if it crashes, file an issue |
| NVENC test "fails" but GPU is present | Possibly a compute-only SKU (H100/H200 — no NVENC) or `NVIDIA_VISIBLE_DEVICES=void` | Check `lib/common.py:have_nvenc()` — it does a real probe encode. The score correctly drops to 0 for unavailable GPUs |
| NVDEC scores 0 but the GPU is there | NVDEC works but is slower than CPU on this box (192-core machines beat NVDEC on PCIe-overhead) | This is the right answer — see the dim-note in the dashboard card |
| Pages site is stale after push | Wait ~45s and hard-refresh. Check `gh run list -R oyzh888/video-bench --limit 3` for failed CI |
| `report.py` crashes on someone else's JSON | Schema drift | Open a PR adding a `.get(..., default=...)` guard in `report.py` for the missing key |

---

## TL;DR for an agent in a hurry

```bash
git clone https://github.com/oyzh888/video-bench && cd video-bench
python3 run.py --label "$(hostname | cut -c1-20)"
git add results/*.json && git commit -m "bench: add $(hostname)" && git push
# Wait 30s, open https://oyzh888.github.io/video-bench/
```

Done. Don't touch anything else.
