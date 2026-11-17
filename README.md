# Crawler and embedder results analysis
## Generate figures in Git Bash

The updated figure script has **not been run** for the new PDF-inclusive release. From the project workspace, run:

```bash
cd /c/Users/alpag/Documents/School/Senior/DATA520/data_analysis

uv run --no-project \
  --python ../policy_embedder/.venv/Scripts/python.exe \
  --with-requirements requirements.txt \
  python make_figures.py
```

`uv` may need internet access the first time to install Matplotlib in an isolated run environment. This command uses the existing embedder Python runtime but does not change the embedder's lock file. If that runtime is unavailable, specify another Python 3.10 or newer with `--python`.

The command creates `figures_with_pdfs/` with seven figures as 300-DPI PNG and editable-text SVG files, plus `analysis_summary.json`. Existing files in `figures/` describe the older, mostly HTML release and are not updated by this command. Figure names and suggested captions are in `results.md`. The script only reads the source files and opens SQLite read-only; it does not run the crawler, convert or embed documents, or connect to Qdrant.

To write to a different directory:

```bash
uv run --no-project \
  --python ../policy_embedder/.venv/Scripts/python.exe \
  --with-requirements requirements.txt \
  python make_figures.py --output-dir my_figures
```

## Interpreting updates

The results prose is a fixed snapshot. The figure script reads the current local files and saved Qdrant verification report when you run it; it does not check the live server. If local job states have changed, the release-coverage chart may change even though the selected release is fixed. Update the prose and cite the new observation date before using the figures together.

If the scraper's current manifest differs from the manifest hash in the release record, the script stops rather than mixing runs. Restore or analyze the matching source snapshot explicitly. By default the script targets `ijc_policy_with_pdfs_20261116`; a different release can be selected with `--collection COLLECTION_NAME` when its release record, verification report, matching manifest, and chunk files are available. Use a separate output directory for each release to avoid mixing figures.
