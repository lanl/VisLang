#!/usr/bin/env python3
"""Run VisLang benchmark tasks and print a summary table.

Usage:
    python benchmark.py                                           # parallel, default model
    python benchmark.py anthropic/claude-haiku-4-5-20251001       # parallel, specific model
    python benchmark.py anthropic/claude-opus-4-6 --sequential    # one at a time
"""

import asyncio
import sys
import time

from tools.pipeline import DEFAULT_MODEL, VislangResult, _run_pipeline

# ---------------------------------------------------------------------------
# Original 5 tasks: general multi-field visualization challenges
# ---------------------------------------------------------------------------
TASKS_ORIGINAL = [
    {
        "prompt": "Show how life expectancy relates to fertility rate across countries, and whether income level explains the pattern",
        "data": "gapminder-health-income.csv",
        "tag": "original",
    },
    {
        "prompt": "Show how unemployment rates across industries changed during recessions vs expansions",
        "data": "unemployment-across-industries.json",
        "tag": "original",
    },
    {
        "prompt": "Show the yield of different barley varieties across sites — which varieties perform best and does it depend on location?",
        "data": "barley.json",
        "tag": "original",
    },
    {
        "prompt": "How has the mix of energy sources in Iowa changed over time, and which sources are growing fastest?",
        "data": "iowa-electricity.csv",
        "tag": "original",
    },
    {
        "prompt": "Compare the seasonal patterns of temperature and precipitation in Seattle — are wet months also cold months?",
        "data": "seattle-weather.csv",
        "tag": "original",
    },
]

# ---------------------------------------------------------------------------
# 5 new tasks targeting known LLM weaknesses from DracoGPT paper
# (arxiv.org/abs/2408.06845)
#
# The paper found LLMs:
#   - Fail on summary/aggregate tasks (near-zero correlation with human data)
#   - Avoid size encoding even when perceptually effective for summaries
#   - Rarely generate faceted designs despite ranking them positively
#   - Over-fixate on positional channels for "important" variables
# ---------------------------------------------------------------------------
TASKS_DRACOGPT = [
    # 1. Summary task + size encoding avoidance
    # Best design: species on x, flipper_length on y, body_mass as size.
    # LLMs tend to penalize size for the important variable (body_mass)
    # and will likely put body_mass on y instead.
    {
        "prompt": "Compare the typical body mass of each penguin species and whether flipper length varies with it",
        "data": "penguins.json",
        "tag": "dracogpt: summary + size",
    },
    # 2. Faceting preference
    # Best design: facet by sex, age on x, people on y.
    # LLMs tend to use color for categorical splits rather than faceting,
    # even when faceting gives a clearer comparison.
    {
        "prompt": "How does the age distribution of the US population differ between males and females?",
        "data": "population.json",
        "tag": "dracogpt: faceting",
    },
    # 3. Summary/aggregate comparison
    # Requires aggregation across job categories and comparison of
    # gender-disaggregated shares — an aggregate judgment task where
    # LLMs showed worst alignment with human perceptual data.
    {
        "prompt": "Which job categories show the largest gender gap in employment share?",
        "data": "jobs.json",
        "tag": "dracogpt: aggregate comparison",
    },
    # 4. Three variables where size encoding is natural
    # year (temporal), deaths (quantitative, important), entity (categorical).
    # Size for deaths on a time × type plot would be effective, but LLMs
    # over-fixate on putting the "important" variable on a positional channel.
    {
        "prompt": "Show how the scale and frequency of natural disasters has changed over time across different disaster types",
        "data": "disasters.csv",
        "tag": "dracogpt: size for important var",
    },
    # 5. Multi-variable summary with size + faceting opportunity
    # cluster (categorical), fertility (q1), pop (q2), life_expect (q3).
    # Population as bubble size is the classic Gapminder design.
    # Tests whether size encoding and faceting by region emerge.
    {
        "prompt": "Compare how fertility rates and life expectancy relate across world regions, and whether population size plays a role",
        "data": "gapminder.json",
        "tag": "dracogpt: size + faceting",
    },
]

TASKS = TASKS_ORIGINAL + TASKS_DRACOGPT


async def run_task(i: int, task: dict, model: str) -> tuple[int, VislangResult, float]:
    """Run a single benchmark task. Returns (index, result, elapsed)."""
    start = time.time()
    result = await _run_pipeline(
        prompt=task["prompt"],
        data_path=task["data"],
        model=model,
        log=True,
        notebook=False,
    )
    elapsed = time.time() - start
    return i, result, elapsed


async def run_all(model: str, parallel: bool):
    if parallel:
        tasks = [run_task(i, t, model) for i, t in enumerate(TASKS, 1)]
        completed = await asyncio.gather(*tasks)
    else:
        completed = []
        for i, t in enumerate(TASKS, 1):
            print(f"\nRunning task {i}/{len(TASKS)}: {t['prompt'][:60]}...")
            completed.append(await run_task(i, t, model))
    return sorted(completed, key=lambda x: x[0])


def print_results(completed: list[tuple[int, VislangResult, float]]):
    rows = []
    for i, result, elapsed in completed:
        task = TASKS[i - 1]
        verdict = result.comparison_verdict or "?"
        winner = verdict.split("—")[0].split(":")[0].strip()
        draco_mark = result.recommendations.get("mark_type", "?")

        rows.append({
            "task": i,
            "data": task["data"],
            "tag": task.get("tag", ""),
            "verdict": verdict,
            "winner": winner,
            "draco_mark": draco_mark,
            "draco_ok": result.draco_chart is not None,
            "baseline_ok": result.baseline_chart is not None,
            "elapsed": elapsed,
            "log_dir": result.log_dir,
        })

        print(f"\n{'='*60}")
        print(f"TASK {i}: {task['prompt'][:60]}...")
        print(f"  [{task.get('tag', '')}]")
        print(f"  Data: {task['data']}")
        print(f"  Draco mark: {draco_mark}")
        print(f"  Verdict: {verdict}")
        print(f"  Time: {elapsed:.1f}s")
        print(f"  Logs: {result.log_dir}")

    print(f"\n\n{'='*60}")
    print("BENCHMARK SUMMARY")
    print(f"{'='*60}")
    print(f"{'#':<4} {'Tag':<30} {'Data':<35} {'Winner':<12} {'Time':>6}")
    print(f"{'-'*4} {'-'*30} {'-'*35} {'-'*12} {'-'*6}")
    for r in rows:
        print(f"{r['task']:<4} {r['tag']:<30} {r['data']:<35} {r['winner']:<12} {r['elapsed']:>5.0f}s")

    total_time = sum(r["elapsed"] for r in rows)
    draco_wins = sum(1 for r in rows if "draco" in r["winner"].lower())
    baseline_wins = sum(1 for r in rows if "baseline" in r["winner"].lower())
    ties = len(rows) - draco_wins - baseline_wins
    print(f"\nDraco: {draco_wins}  Baseline: {baseline_wins}  Tie: {ties}")
    print(f"Total time: {total_time:.0f}s")


def main():
    # Parse model from positional arg (first non-flag argument)
    args = sys.argv[1:]
    model = DEFAULT_MODEL
    sequential = False

    positional = [a for a in args if not a.startswith("--")]
    if positional:
        model = positional[0]

    if "--sequential" in args:
        sequential = True

    parallel = not sequential

    print(f"Model: {model}")
    print(f"Mode: {'parallel' if parallel else 'sequential'}")
    print(f"Tasks: {len(TASKS)}")

    start = time.time()
    completed = asyncio.run(run_all(model, parallel))
    wall_time = time.time() - start

    print_results(completed)
    print(f"Wall time: {wall_time:.0f}s")


if __name__ == "__main__":
    main()
