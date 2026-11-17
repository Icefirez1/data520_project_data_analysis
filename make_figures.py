"""Create publication-ready figures from the scraper and embedder snapshots.

This script reads source data only. It does not contact Qdrant or modify pipeline
state. Run from Git Bash as described in README.md. No figures are produced on import.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator

ROOT = Path(__file__).resolve().parents[1]
RUN = "4428b26f61bb41b0a928ea851a4974de"
SCRAPE = ROOT / "policy_web_scraper" / "data" / "runs" / RUN
EMBED = ROOT / "policy_embedder" / "data"
COLORS = {"ink": "#23405b", "blue": "#3979a1", "teal": "#55a68f", "gold": "#d89b44", "red": "#bc625d", "gray": "#9aa8b0", "light": "#e3e9ed"}


def json_lines(path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_data(collection):
    manifest = list(json_lines(SCRAPE / "manifest.jsonl"))
    candidates = list(json_lines(SCRAPE / "candidates.jsonl"))
    attempts = list(json_lines(SCRAPE / "attempts.jsonl"))
    report = read_json(SCRAPE / "report.json")
    with (SCRAPE / "inventory.csv").open(encoding="utf-8", newline="") as source:
        inventory = list(csv.DictReader(source))
    key = lambda item: (item.get("url"), item.get("sha256"), item.get("artifact"))
    if Counter(map(key, manifest)) != Counter(map(key, inventory)):
        raise ValueError("Manifest and inventory differ; refresh the scraper exports before analysis.")

    release = read_json(EMBED / "releases" / f"{collection}.json")
    manifest_hash = hashlib.sha256((SCRAPE / "manifest.jsonl").read_bytes()).hexdigest()
    if manifest_hash != release.get("manifest_sha256"):
        raise ValueError("The scraper manifest differs from the one used for the release; use its frozen snapshot for analysis.")
    verification = read_json(EMBED / "reports" / f"verify-{collection}.json")
    if verification.get("collection") != collection or verification.get("verified") is not True:
        raise ValueError("The saved verification report does not confirm this collection.")
    if verification.get("actual_points") != release.get("expected_points"):
        raise ValueError("The saved release and verification point counts differ.")
    fingerprint = release["fingerprint"]
    hashes = release["artifacts"]
    if len(set(hashes)) != len(hashes):
        raise ValueError("Duplicate artifact IDs in the release.")

    # Opening SQLite read-only prevents this analysis from altering pipeline state.
    database_uri = (EMBED / "state.sqlite3").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as db:
        jobs = {(sha, stage): status for sha, stage, status in db.execute(
            "SELECT sha256, stage, status FROM jobs WHERE fingerprint=?", (fingerprint,))}
    artifact_records = {}
    for row in manifest:
        artifact_records.setdefault(row["sha256"], row)
    missing = set(hashes) - artifact_records.keys()
    if missing:
        raise ValueError(f"Release references {len(missing)} artifacts outside the manifest.")

    # The physical release's selection is authoritative for which chunks were
    # reported uploaded; current SQLite stage states may have changed later.
    chunks, per_doc = [], []
    chunks_by_type = Counter()
    for sha in hashes:
        path = EMBED / "chunks" / fingerprint / f"{sha}.json"
        doc_chunks = read_json(path)
        per_doc.append(len(doc_chunks))
        chunks.extend(doc_chunks)
        chunks_by_type[Path(artifact_records[sha]["artifact"]).suffix.lower()] += len(doc_chunks)
    if len(chunks) != release["expected_points"]:
        raise ValueError("Local chunk count differs from the selected release point count.")
    if len({chunk["id"] for chunk in chunks}) != len(chunks):
        raise ValueError("The selected local chunks contain duplicate point IDs.")

    return {"manifest": manifest, "inventory": inventory, "candidates": candidates, "attempts": attempts,
            "report": report, "release": release, "verification": verification, "jobs": jobs,
            "artifacts": artifact_records, "selected": set(hashes), "chunks": chunks, "per_doc": per_doc,
            "chunks_by_type": chunks_by_type,
            "verification_mtime": datetime.fromtimestamp((EMBED / "reports" / f"verify-{collection}.json").stat().st_mtime, tz=timezone.utc).isoformat()}


def summarize(data):
    records, artifacts, jobs, selected = data["manifest"], data["artifacts"], data["jobs"], data["selected"]
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": RUN, "run_status": data["report"].get("status"),
        "manifest_sha256": data["release"]["manifest_sha256"],
        "release_collection": data["release"]["collection"], "release_id": data["release"]["release_id"],
        "pipeline_fingerprint": data["release"]["fingerprint"], "verification_file_modified_utc": data["verification_mtime"],
        "url_records": len(records), "unique_artifacts": len(artifacts), "candidates": len(data["candidates"]),
        "http_attempts": len(data["attempts"]), "selected_artifacts": len(selected), "selected_chunks": len(data["chunks"]),
        "record_types": dict(Counter(Path(r["artifact"]).suffix.lower() for r in records)),
        "artifact_types": dict(Counter(Path(r["artifact"]).suffix.lower() for r in artifacts.values())),
        "selected_types": dict(Counter(Path(artifacts[sha]["artifact"]).suffix.lower() for sha in selected)),
        "selected_source_records": sum(r["sha256"] in selected for r in records),
        "chunks_by_type": dict(data["chunks_by_type"]),
        "candidate_states": dict(Counter(r.get("state") or "unknown" for r in data["candidates"])),
        "http_statuses": {str(k): v for k, v in Counter(r.get("status") for r in data["attempts"]).items()},
        "metadata_populated": {field: sum(bool(r.get(field)) for r in records) for field in ("title", "language", "category", "date", "board", "watershed")},
        "conversion_status": dict(Counter(status for (sha, stage), status in jobs.items() if stage == "convert")),
        "embedding_status": dict(Counter(status for (sha, stage), status in jobs.items() if stage == "embed")),
        "token_median": statistics.median(c["token_count"] for c in data["chunks"]),
        "token_mean": statistics.mean(c["token_count"] for c in data["chunks"]),
        "token_p95_lower_index": sorted(c["token_count"] for c in data["chunks"])[int(.95 * (len(data["chunks"]) - 1))],
        "token_max": max(c["token_count"] for c in data["chunks"]),
        "chunks_per_artifact_median": statistics.median(data["per_doc"]),
        "page_citation_chunks": sum(c.get("page_start") is not None for c in data["chunks"]),
        "heading_path_chunks": sum(bool(c.get("heading_path")) for c in data["chunks"]),
        "saved_qdrant_verification": data["verification"],
    }
    return result


def setup_plotting():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 13,
                         "axes.labelsize": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": "white", "savefig.facecolor": "white", "svg.fonttype": "none"})


def save(fig, output, name):
    fig.tight_layout()
    for suffix in ("png", "svg"):
        fig.savefig(output / f"{name}.{suffix}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def bar_labels(ax):
    for container in ax.containers:
        ax.bar_label(container, fmt=lambda value: f"{int(value):,}", padding=3, fontsize=9)


def make_figures(data, summary, output):
    output.mkdir(parents=True, exist_ok=True)
    setup_plotting()
    comma = FuncFormatter(lambda x, _: f"{int(x):,}")

    states = Counter(row.get("state") or "unknown" for row in data["candidates"])
    order = sorted(states, key=states.get)
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.barh(order, [states[s] for s in order], color=[COLORS["teal"] if s == "collected" else COLORS["blue"] for s in order])
    bar_labels(ax); ax.xaxis.set_major_formatter(comma); ax.set_xlim(0, max(states.values()) * 1.2)
    ax.set(xlabel="URL candidates", title="Crawler candidate outcomes")
    fig.text(.12, .01, "Each candidate has one final recorded state; unsupported includes external links.", fontsize=8, color="#45545f")
    save(fig, output, "01_crawler_states")

    statuses = Counter(str(row.get("status") or "No response") for row in data["attempts"])
    order = sorted(statuses, key=lambda x: (x == "No response", int(x) if x.isdigit() else 999))
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.bar(order, [statuses[k] for k in order], color=[COLORS["teal"] if k.startswith("2") else COLORS["gold"] if k.startswith("3") else COLORS["red"] if k.startswith(("4", "5")) else COLORS["gray"] for k in order])
    ax.set_yscale("symlog", linthresh=10); bar_labels(ax); ax.set_ylim(top=max(statuses.values()) * 5)
    ax.set(xlabel="HTTP status (or no response)", ylabel="HTTP attempts (symmetric log scale)", title="Recorded request outcomes")
    fig.text(.12, .01, "Attempts include repeated requests and redirects; they are not unique URLs.", fontsize=8, color="#45545f")
    save(fig, output, "02_http_attempts")

    types = [".html", ".pdf"]
    fig, ax = plt.subplots(figsize=(7.8, 4.6)); x = range(len(types)); width = .34
    ax.bar([i - width/2 for i in x], [summary["record_types"].get(k, 0) for k in types], width, label="Collected URL records", color=COLORS["blue"])
    ax.bar([i + width/2 for i in x], [summary["artifact_types"].get(k, 0) for k in types], width, label="Unique file hashes", color=COLORS["teal"])
    bar_labels(ax); ax.set_xticks(list(x), ["HTML", "PDF"]); ax.yaxis.set_major_formatter(comma)
    ax.set_ylim(0, max(summary["record_types"].values()) * 1.28); ax.set(ylabel="Count", title="Collected URLs and unique files by type"); ax.legend(frameon=False)
    save(fig, output, "03_downloaded_types")

    categories = ["In partial release", "Metadata only", "Excluded", "Converted, not in release", "Conversion failed", "Other / unprocessed"]
    colors = [COLORS["teal"], COLORS["light"], COLORS["gold"], COLORS["blue"], COLORS["red"], COLORS["gray"]]
    coverage = {t: Counter() for t in types}
    for sha, rec in data["artifacts"].items():
        ext = Path(rec["artifact"]).suffix.lower()
        if ext not in coverage: continue
        convert = data["jobs"].get((sha, "convert")); excluded = data["jobs"].get((sha, "exclude")) == "excluded"
        if sha in data["selected"]: category = categories[0]
        elif excluded: category = categories[2]
        elif convert == "metadata_only": category = categories[1]
        elif convert == "ready": category = categories[3]
        elif convert == "failed": category = categories[4]
        else: category = categories[5]
        coverage[ext][category] += 1
    fig, ax = plt.subplots(figsize=(9.8, 4.6)); left = [0, 0]
    for category, color in zip(categories, colors):
        vals = [coverage[t][category] for t in types]
        ax.barh([0, 1], vals, left=left, label=category, color=color, edgecolor="white")
        for y, val, start in zip([0, 1], vals, left):
            if val >= 100: ax.text(start + val / 2, y, f"{val:,}", va="center", ha="center", fontsize=9, color="white" if color in (COLORS["teal"], COLORS["blue"], COLORS["red"]) else COLORS["ink"])
        left = [a+b for a,b in zip(left, vals)]
    ax.set_yticks([0, 1], ["HTML", "PDF"]); ax.invert_yaxis(); ax.xaxis.set_major_formatter(comma)
    ax.set(xlabel="Unique artifacts", title="Coverage of the verified partial release")
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -.22), ncol=3, frameon=False, fontsize=8)
    fig.text(.12, .01, "Release selection is fixed; other categories use the current local job state.", fontsize=8, color="#45545f")
    save(fig, output, "04_release_coverage")

    tokens = [c["token_count"] for c in data["chunks"]]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.hist(tokens, bins=range(0, 501, 25), color=COLORS["blue"], edgecolor="white")
    ax.axvline(statistics.median(tokens), color=COLORS["gold"], linestyle="--", label=f"Median: {statistics.median(tokens):.0f}")
    ax.axvline(480, color=COLORS["red"], linestyle=":", label="Configured maximum: 480")
    ax.set(xlabel="Tokens per complete embedding input", ylabel="Chunks", title="Embedding-input lengths in the partial release")
    ax.yaxis.set_major_formatter(comma); ax.legend(frameon=False)
    save(fig, output, "05_chunk_token_lengths")

    fields = ["title", "language", "category", "date", "board", "watershed"]
    values = [summary["metadata_populated"][f] for f in fields]
    fig, ax = plt.subplots(figsize=(8, 4.6)); ax.barh(fields, [100*v/len(data["manifest"]) for v in values], color=COLORS["blue"])
    for y, value in enumerate(values): ax.text(100*value/len(data["manifest"]) + 1, y, f"{value:,} / {len(data['manifest']):,}", va="center", fontsize=9)
    ax.invert_yaxis(); ax.set_xlim(0, 122); ax.set(xlabel="Percentage of collected URL records", title="Source metadata field completeness")
    fig.text(.12, .01, "Presence is not a validation of metadata accuracy.", fontsize=8, color="#45545f")
    save(fig, output, "06_metadata_completeness")

    daily = Counter(row["retrieved_at"][:10] for row in data["manifest"] if row.get("retrieved_at"))
    dates = sorted(daily)
    fig, ax = plt.subplots(figsize=(8, 4.6)); ax.bar(dates, [daily[d] for d in dates], color=COLORS["teal"])
    bar_labels(ax); ax.yaxis.set_major_formatter(comma); ax.set_ylim(0, max(daily.values()) * 1.25)
    ax.set(xlabel="Retrieval date (UTC)", ylabel="Collected URL records", title="Collected records by retrieval date")
    ax.tick_params(axis="x", rotation=20)
    fig.text(.12, .01, "Dates are UTC and represent collected records, not all request attempts.", fontsize=8, color="#45545f")
    save(fig, output, "07_retrieval_by_day_utc")
    return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="ijc_policy_with_pdfs_20261116")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "figures_with_pdfs")
    args = parser.parse_args()
    data = load_data(args.collection)
    summary = summarize(data)
    coverage = make_figures(data, summary, args.output_dir)
    summary["release_coverage_by_type"] = {key: dict(value) for key, value in coverage.items()}
    (args.output_dir / "analysis_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote 7 PNGs, 7 SVGs, and a summary to {args.output_dir}")
    print(f"Run {RUN}; {summary['url_records']:,} URL records; {summary['unique_artifacts']:,} artifacts; {summary['selected_chunks']:,} release chunks.")


if __name__ == "__main__":
    main()
