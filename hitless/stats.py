"""Summaries over attempts.jsonl: where runs die, and how.

Definitions (hitless rules: the first hit ends the run, whatever she does next):
  (AutoHitCounter has one hit count per split, so there is no boss vs.
  on-the-way breakdown)
  reached split k      the run got to split k with no hits before it
                       (hitless_through >= k)
  died at split k      the first hit happened on split k
  death rate at k      died at k / reached k
"""
from __future__ import annotations

from collections import Counter


def usable(attempts: list[dict], profile: str | None = None,
           include_trivial: bool = False) -> list[dict]:
    out = [a for a in attempts
           if (profile is None or a.get("profile") == profile)
           and (include_trivial or not a.get("trivial"))]
    return out


def profiles(attempts: list[dict]) -> Counter:
    return Counter(a.get("profile") for a in attempts if not a.get("trivial"))


def split_names(attempts: list[dict]) -> list[str]:
    """Split names from the furthest-reaching recent attempt; later splits no
    attempt has reached yet are shown as "split N"."""
    if not attempts:
        return []
    total = max(a["splits_total"] for a in attempts)
    cands = [a for a in attempts if a["splits_total"] == total]
    longest = max(cands, key=lambda a: (len(a["splits"]), a.get("ended_ms") or 0))
    names = [x["name"] for x in longest["splits"]]
    return names + [f"split {i + 1}" for i in range(len(names), total)]


def funnel(attempts: list[dict]) -> list[dict]:
    names = split_names(attempts)
    rows = []
    for k, name in enumerate(names):
        reached = [a for a in attempts if a["hitless_through"] >= k
                   and (a["splits_total"] > k)]
        died = [a for a in reached if a.get("first_hit") and a["first_hit"]["split"] == k]
        cleared = [x["splits"][k]["duration_ms"] for x in attempts
                   if x["splits_cleared"] > k and len(x["splits"]) > k
                   and x["splits"][k].get("duration_ms")]
        cleared.sort()
        rows.append({
            "split": k, "name": name, "reached": len(reached), "died": len(died),
            "death_rate": (len(died) / len(reached)) if reached else None,
            "median_clear_ms": cleared[len(cleared) // 2] if cleared else None,
        })
    return rows


def summary(attempts: list[dict]) -> dict:
    completed = [a for a in attempts if a["outcome"] == "completed"]
    hitless = [a for a in completed if a["hitless"]]
    best = max(attempts, key=lambda a: (a["hitless_through"], -a["hits_total"]), default=None)
    killers = Counter(a["first_hit"]["name"] for a in attempts if a.get("first_hit"))
    return {
        "attempts": len(attempts),
        "completed": len(completed),
        "hitless_completions": len(hitless),
        "best_hitless_through": best["hitless_through"] if best else 0,
        "best_attempt": best,
        "nemesis": killers.most_common(1)[0] if killers else None,
        "funnel": funnel(attempts),
    }


def format_summary(profile: str, s: dict) -> str:
    lines = [f"Profile: {profile}",
             f"Attempts: {s['attempts']}   completed: {s['completed']}   "
             f"hitless completions: {s['hitless_completions']}"]
    fun = s["funnel"]
    if s["best_attempt"]:
        b = s["best_attempt"]
        k = s["best_hitless_through"]
        reached = fun[k]["name"] if k < len(fun) else "the end"
        lines.append(f"Best: hitless through {k} split(s), reached {reached} "
                     f"(attempt {b['attempt']}, {(b['started_at'] or b['ended_at'] or '')[:10]})")
    if s["nemesis"]:
        lines.append(f"Nemesis: {s['nemesis'][0]} ({s['nemesis'][1]} runs ended there)")
    lines.append("")
    w = max([len(r["name"]) for r in fun] + [5])
    lines.append(f"  {'split'.ljust(w)}  reached  ended here   rate  median clear")
    for r in fun:
        rate = "    -" if r["death_rate"] is None else f"{r['death_rate'] * 100:4.0f}%"
        mc = r["median_clear_ms"]
        mc = "-" if mc is None else f"{mc // 60000}:{mc // 1000 % 60:02d}"
        lines.append(f"  {r['name'].ljust(w)}  {r['reached']:7d}  {r['died']:10d}  {rate}  {mc:>12}")
    return "\n".join(lines)
