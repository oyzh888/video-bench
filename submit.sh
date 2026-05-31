#!/usr/bin/env bash
# One-shot bench-and-submit. Usage:
#   ./submit.sh              # auto-detect label from hostname
#   ./submit.sh my-machine   # explicit label
#
# Tries direct push to master first; falls back to printing PR instructions.
set -euo pipefail
cd "$(dirname "$0")"

LABEL="${1:-$(hostname | cut -c1-20)}"

echo "=== video-bench: submitting result for label '$LABEL' ==="
echo

# 1. Make sure we're up to date so we don't conflict with other machines.
git fetch origin
git pull --ff-only origin "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo master)" || {
  echo "(skipping pull — no upstream or non-fast-forward; continuing)"
}

# 2. Run the bench.
python3 run.py --label "$LABEL"

# 3. Sanity check: report.py must succeed (CI runs the same).
python3 report.py >/dev/null

# 4. Stage and commit only the new JSON.
NEW_JSON=$(ls -t results/*.json | head -1)
echo
echo "→ committing $NEW_JSON"
git add "$NEW_JSON"
git -c user.name="${GIT_AUTHOR_NAME:-video-bench-agent}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-noreply@users.noreply.github.com}" \
    commit -m "bench: add $LABEL result"

# 5. Try to push.
if git push origin "$(git rev-parse --abbrev-ref HEAD)"; then
  echo
  echo "✓ pushed."
  echo "  Dashboard updates in ~30s: https://oyzh888.github.io/video-bench/"
else
  cat <<EOF

✗ Direct push failed (no write access?). Two options:

  A) If you have a token, set the remote with credentials:
     git remote set-url origin "https://<user>:<TOKEN>@github.com/oyzh888/video-bench.git"
     git push

  B) Open a PR from your fork:
     gh repo fork oyzh888/video-bench --clone=false
     git remote add fork https://github.com/<your-user>/video-bench.git
     git push fork HEAD:add-$LABEL
     gh pr create --repo oyzh888/video-bench --title "bench: add $LABEL"

Your local commit is preserved — nothing was lost.
EOF
  exit 1
fi
