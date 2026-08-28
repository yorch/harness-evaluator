"""Static report generator: HTML, JSON, CSV."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Template

from heval.orchestrator.results_store import ResultsStore

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>heval Report — {{ run_name }}</title>
    <style>
        body { font-family: -apple-system, sans-serif; margin: 2rem; background: #f8f9fa; }
        h1 { color: #1a1a1a; }
        h2 { color: #333; border-bottom: 2px solid #ddd; padding-bottom: 0.5rem; }
        table { border-collapse: collapse; width: 100%; margin: 1rem 0; background: white; }
        th, td { border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }
        th { background: #f1f3f5; font-weight: 600; }
        tr:nth-child(even) { background: #f8f9fa; }
        .metric-good { color: #1971c2; font-weight: 600; }
        .metric-bad { color: #c92a2a; font-weight: 600; }
        .summary { display: flex; gap: 2rem; margin: 1rem 0 2rem; }
        .summary-card { background: white; padding: 1rem 1.5rem; border-radius: 8px;
                       box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .summary-card .value { font-size: 1.8rem; font-weight: 700; }
        .summary-card .label { font-size: 0.85rem; color: #666; text-transform: uppercase; }
        .pass { color: #1971c2; }
        .fail { color: #c92a2a; }
        .partial { color: #e67700; }
    </style>
</head>
<body>
    <h1>heval Report — {{ run_name }}</h1>
    <p>Generated: {{ timestamp }}</p>

    <div class="summary">
        <div class="summary-card">
            <div class="value">{{ total_cells }}</div>
            <div class="label">Total Cells</div>
        </div>
        <div class="summary-card">
            <div class="value">{{ passed }}</div>
            <div class="label">Passed</div>
        </div>
        <div class="summary-card">
            <div class="value">{{ failed }}</div>
            <div class="label">Failed</div>
        </div>
        <div class="summary-card">
            <div class="value">${{ total_cost }}</div>
            <div class="label">Total Cost</div>
        </div>
        <div class="summary-card">
            <div class="value">{{ avg_success_pct }}%</div>
            <div class="label">Avg Success</div>
        </div>
    </div>

    <h2>Leaderboard (within-model)</h2>
    {% for model, harnesses in leaderboards.items() %}
    <h3>{{ model }}</h3>
    <table>
        <tr>
            <th>Harness</th>
            <th>Success Rate</th>
            <th>Avg Tokens</th>
            <th>Avg Cost</th>
            <th>Avg Time (s)</th>
            <th>API Calls</th>
        </tr>
        {% for row in harnesses %}
        <tr>
            <td>{{ row.harness }}</td>
            <td class="{{ row.success_class }}">{{ row.success_pct }}%</td>
            <td>{{ row.avg_tokens }}</td>
            <td>${{ row.avg_cost }}</td>
            <td>{{ row.avg_time_s }}</td>
            <td>{{ row.avg_api_calls }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endfor %}

    <h2>Detailed Results</h2>
    <table>
        <tr>
            <th>Cell ID</th>
            <th>Harness</th>
            <th>Model</th>
            <th>Task</th>
            <th>Repeat</th>
            <th>Exit</th>
            <th>Success</th>
            <th>Tokens</th>
            <th>Cost</th>
            <th>Time (s)</th>
            <th>Error Class</th>
        </tr>
        {% for r in results %}
        <tr>
            <td>{{ r.cell_id }}</td>
            <td>{{ r.harness }}</td>
            <td>{{ r.model }}</td>
            <td>{{ r.task_id }}</td>
            <td>{{ r.repeat }}</td>
            <td class="{{ r.exit_class }}">{{ r.exit_class }}</td>
            <td>{{ r.success }}</td>
            <td>{{ r.total_tokens }}</td>
            <td>${{ r.cost }}</td>
            <td>{{ r.time_s }}</td>
            <td>{{ r.error_class or '' }}</td>
        </tr>
        {% endfor %}
    </table>
</body>
</html>
"""


class ReportGenerator:
    """Generates static reports from evaluation results."""

    def __init__(self, results_store: ResultsStore) -> None:
        self.store = results_store

    def generate(
        self,
        run_name: str,
        output_dir: str | Path,
    ) -> dict[str, str]:
        """Generate HTML, JSON, and CSV reports.

        Returns dict of {format: file_path}.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = self.store.get_all_results(run_name)
        leaderboards = self._build_leaderboards(results)

        # Generate JSON
        json_path = output_dir / f"{run_name}_report.json"
        with open(json_path, "w") as f:
            json.dump(
                {
                    "run_name": run_name,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "total_cells": len(results),
                    "leaderboards": leaderboards,
                    "results": results,
                },
                f,
                indent=2,
            )

        # Generate CSV
        csv_path = output_dir / f"{run_name}_report.csv"
        with open(csv_path, "w", newline="") as f:
            if results:
                writer = csv.DictWriter(f, fieldnames=results[0].keys())
                writer.writeheader()
                writer.writerows(results)

        # Generate HTML
        html_path = output_dir / f"{run_name}_report.html"
        html = self._generate_html(run_name, results, leaderboards)
        with open(html_path, "w") as f:
            f.write(html)

        return {
            "json": str(json_path),
            "csv": str(csv_path),
            "html": str(html_path),
        }

    def _build_leaderboards(
        self, results: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Build within-model leaderboards."""
        # Group by model, then by harness
        model_harness: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for r in results:
            model = r["model"]
            harness = r["harness"]
            if model not in model_harness:
                model_harness[model] = {}
            if harness not in model_harness[model]:
                model_harness[model][harness] = []
            model_harness[model][harness].append(r)

        leaderboards: dict[str, list[dict[str, Any]]] = {}
        for model, harnesses in model_harness.items():
            rows: list[dict[str, Any]] = []
            for harness, runs in harnesses.items():
                n = len(runs)
                avg_success = sum(r["success"] for r in runs) / n if n else 0
                avg_tokens = sum(
                    r["input_tokens"]
                    + r["output_tokens"]
                    + r["cache_read_tokens"]
                    + r["cache_write_tokens"]
                    + r["reasoning_tokens"]
                    for r in runs
                ) / n if n else 0
                avg_cost = sum(r["total_cost"] for r in runs) / n if n else 0
                avg_time = sum(r["latency_ms"] for r in runs) / n if n else 0
                avg_api_calls = sum(r["num_api_calls"] for r in runs) / n if n else 0

                success_pct = f"{avg_success * 100:.1f}"
                if avg_success >= 0.8:
                    success_class = "pass"
                elif avg_success >= 0.5:
                    success_class = "partial"
                else:
                    success_class = "fail"

                rows.append(
                    {
                        "harness": harness,
                        "success_pct": success_pct,
                        "success_class": success_class,
                        "avg_tokens": f"{avg_tokens:.0f}",
                        "avg_cost": f"{avg_cost:.6f}",
                        "avg_time_s": f"{avg_time / 1000:.1f}",
                        "avg_api_calls": f"{avg_api_calls:.1f}",
                    }
                )
            # Sort by success rate descending
            rows.sort(key=lambda x: float(x["success_pct"]), reverse=True)
            leaderboards[model] = rows

        return leaderboards

    def _generate_html(
        self,
        run_name: str,
        results: list[dict[str, Any]],
        leaderboards: dict[str, list[dict[str, Any]]],
    ) -> str:
        """Generate HTML report."""
        total = len(results)
        passed = sum(1 for r in results if r["exit_class"] == "pass")
        failed = total - passed
        total_cost = sum(r["total_cost"] for r in results)
        avg_success = sum(r["success"] for r in results) / total if total else 0

        # Format results for template
        formatted_results = []
        for r in results:
            formatted_results.append(
                {
                    "cell_id": r["cell_id"],
                    "harness": r["harness"],
                    "model": r["model"],
                    "task_id": r["task_id"],
                    "repeat": r["repeat"],
                    "exit_class": r["exit_class"],
                    "success": f"{r['success']:.2f}",
                    "total_tokens": r["input_tokens"]
                    + r["output_tokens"]
                    + r["cache_read_tokens"]
                    + r["cache_write_tokens"]
                    + r["reasoning_tokens"],
                    "cost": f"{r['total_cost']:.6f}",
                    "time_s": f"{r['latency_ms'] / 1000:.1f}",
                    "error_class": r.get("error_class"),
                }
            )

        template = Template(HTML_TEMPLATE)
        html: str = template.render(
            run_name=run_name,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
            total_cells=total,
            passed=passed,
            failed=failed,
            total_cost=f"{total_cost:.4f}",
            avg_success_pct=f"{avg_success * 100:.1f}",
            leaderboards=leaderboards,
            results=formatted_results,
        )
        return html
