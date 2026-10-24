root=$(cd "$(dirname "$0")/.." && pwd)
P="$root/scripts/preflight.sh"
bash "$P" | grep -q "Preflight OK" || { echo "FAIL: expected OK on this dev machine (uv+git present)"; exit 1; }
out=$(PATH="" /bin/bash "$P" 2>&1 || true)   # empty PATH: builtins still run, uv/git absent
echo "$out" | grep -q "Preflight FAILED" || { echo "FAIL: expected FAILED on empty PATH"; exit 1; }
echo "$out" | grep -q "MISSING uv"       || { echo "FAIL: expected uv reported missing"; exit 1; }
echo "PASS test_preflight.sh"
