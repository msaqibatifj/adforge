"""Run tests.json against a live agent API and print a markdown results table.
Usage: python scripts/benchmark.py https://your-agent-url [--update-readme]
Each run uses fresh user ids (suffix) so reruns don't collide with earlier data."""
import json
import re
import sys
import time
import uuid
import httpx

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
suffix = uuid.uuid4().hex[:5]
cases = json.load(open(__file__.replace("scripts/benchmark.py", "tests.json").replace("scripts\\benchmark.py", "tests.json")))


def check(expect, data, status_ok):
    calls = data.get("tool_calls", [])
    names = [c["name"] for c in calls]
    reply = data.get("reply", "")
    errs = []
    if not status_ok:
        errs.append("HTTP error")
    for t in expect.get("tools_include", []):
        if t not in names:
            errs.append(f"missing tool {t}")
    if expect.get("no_tools") and names:
        errs.append(f"unexpected tools {names}")
    if "reply_matches" in expect and not re.search(expect["reply_matches"], reply):
        errs.append("reply missing expected content")
    if "reply_not_matches" in expect and re.search(expect["reply_not_matches"], reply):
        errs.append("reply contains forbidden content")
    if expect.get("min_variants"):
        n = sum(len(c["result"].get("saved", [])) for c in calls if c["name"] == "save_ad_variants" and isinstance(c["result"], dict))
        if n < expect["min_variants"]:
            errs.append(f"only {n} variants saved")
    if expect.get("headline_max"):
        for c in calls:
            if c["name"] == "save_ad_variants" and isinstance(c["result"], dict):
                for v in c["result"].get("saved", []):
                    if len(v["headline"]) > expect["headline_max"]:
                        errs.append("headline over limit")
    if expect.get("launch_not_executed"):
        for c in calls:
            if c["name"] == "launch_campaign" and isinstance(c["result"], dict) and c["result"].get("status") == "live":
                errs.append("launched without confirmation")
    return errs


rows, passed, lat, tin, tout = [], 0, [], 0, 0
with httpx.Client(timeout=90) as client:
    client.get(base + "/health")
    for case in cases:
        errs_all, case_lat = [], []
        for step in case["steps"]:
            t0 = time.time()
            try:
                r = client.post(base + "/chat", json={"user_id": f"{step['user']}_{suffix}", "thread_id": step["thread"], "message": step["message"]})
                data, ok = r.json(), r.status_code == 200
            except Exception as e:
                data, ok = {}, False
                errs_all.append(str(e))
            dt = time.time() - t0
            case_lat.append(dt)
            tin += data.get("tokens", {}).get("in", 0)
            tout += data.get("tokens", {}).get("out", 0)
            errs_all += check(step.get("expect", {}), data, ok)
        lat += case_lat
        passed += not errs_all
        rows.append((case["id"], case["name"], "PASS" if not errs_all else "FAIL: " + "; ".join(errs_all), max(case_lat)))
lat.sort()
p50, p95 = lat[len(lat) // 2], lat[min(len(lat) - 1, int(len(lat) * 0.95))]
md = ["| # | Test | Result | Slowest turn |", "|---|------|--------|-------------|"]
md += [f"| {i} | {n} | {res} | {t:.1f}s |" for i, n, res, t in rows]
md.append("")
md.append(f"**{passed}/{len(cases)} passed** · p50 latency {p50:.1f}s · p95 {p95:.1f}s · tokens in/out {tin}/{tout} across {len(lat)} turns "
          f"(≈ ${(tin * 1.0 + tout * 5.0) / 1e6:.4f} at $1/$5 per MTok) · run {time.strftime('%Y-%m-%d')} against `{base}`")
out = "\n".join(md)
print(out)
if "--update-readme" in sys.argv:
    p = __file__.replace("scripts/benchmark.py", "README.md").replace("scripts\\benchmark.py", "README.md")
    s = open(p, encoding="utf-8").read()
    s = re.sub(r"(<!-- BENCH:START -->).*?(<!-- BENCH:END -->)", lambda m: f"{m.group(1)}\n{out}\n{m.group(2)}", s, flags=re.S)
    open(p, "w", encoding="utf-8").write(s)
sys.exit(0 if passed == len(cases) else 1)
