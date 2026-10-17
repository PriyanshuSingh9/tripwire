# TraceAML

TraceAML is a graph-powered AML detection and investigation workbench built for reproducible experiments on IBM AMLworld HI-Small. The repository is named Tripwire. The product replays synthetic transactions, compares classical and graph-enhanced models, retrieves bounded evidence from FalkorDB, and presents investigations for human review.

The local workspace contains IBM AMLworld HI-Small Kaggle version 8 under `data/raw/hi-small/`. Dataset files are ignored by Git and are not included in this repository. Do not commit raw data, processed tables, credentials, or generated models.

## Repository layout

```text
apps/web/       Next.js analyst workbench
services/api/   FastAPI service, policy, investigation integration
ml/             Data inspection, feature preparation, models, evaluation
graph/          FalkorDB schema, indexes, and query definitions
docs/           Product, architecture, API contracts, decisions, and role charters
compose.yaml    Local FalkorDB service
```

## Requirements

- Node.js 20.9 or newer and pnpm
- Python 3.12 or newer and uv
- Docker Compose

## Local setup

A root `Makefile` wraps the commands below (GNU Make; on Windows install it with `winget install ezwinports.make`). Run `make help` for the targets. Typical flow: `make setup`, `make up`, `make data`, `make graph-load LIMIT=500000`, then `make dev-api` and `make dev-web`. `make check` runs lint, tests, and the web build.

Install the frontend dependencies and start the web app:

```bash
pnpm install
pnpm dev:web
```

Start FalkorDB:

```bash
docker compose up -d falkordb
```

Install and run the API from the repository root:

```bash
uv sync --project services/api
uv run --project services/api fastapi dev services/api/src/api/app.py
```

The API listens on `http://127.0.0.1:8000`; interactive API documentation is at `/docs`. Copy `.env.example` to `.env` to override local service settings. Do not put real credentials in the example file.

## Dataset and data preparation

The pinned input is HI-Small dataset version 8 from Kaggle. The source data is synthetic and licensed separately from this repository's code (CDLA-Sharing-1.0). Keep these files under `data/raw/hi-small/`:

- `HI-Small_Trans.csv`
- `HI-Small_accounts.csv`
- `HI-Small_Patterns.txt`

To reproduce the download with KaggleHub, request each file from the pinned dataset version using the `path` argument, then place the returned file in `data/raw/hi-small/`:

```python
from pathlib import Path
from shutil import copy2

import kagglehub

dataset = "ealtman2019/ibm-transactions-for-anti-money-laundering-aml/versions/8"
destination = Path("data/raw/hi-small")
destination.mkdir(parents=True, exist_ok=True)
for filename in (
    "HI-Small_Trans.csv",
    "HI-Small_accounts.csv",
    "HI-Small_Patterns.txt",
):
    downloaded = Path(kagglehub.dataset_download(dataset, path=filename))
    if downloaded.is_dir():
        downloaded = downloaded / filename
    copy2(downloaded, destination / filename)
```

The local aggregate profile records file hashes, schema, label counts, currency distributions, and account-key cardinalities at `artifacts/runs/hi-small-v8/profile.json`. Raw and generated data paths are ignored by Git.

Regenerate the inspection profile, normalized Parquet table, and frozen chronological split:

```bash
uv run --project ml traceaml-inspect-hi-small
uv run --project ml traceaml-prepare-hi-small
uv run --project ml traceaml-build-features
uv run --project ml traceaml-train-baselines
uv run --project ml traceaml-publish-inference-scores
```

Preparation writes `data/processed/hi-small-v8/transactions.parquet` and `split-manifest.json`. The manifest contains the pinned source hash, canonical columns, split algorithm and boundaries, row counts, and positive counts. It places whole minute-resolution timestamps into one split and keeps `target_is_laundering` offline-only.

## Evaluation principles

The local v8 run has completed logistic regression and LightGBM with tabular and past-only behavioral features. Two clean baseline runs reproduced scores and model artifacts bitwise. On the frozen test split (761,639 transactions; 0.205% positive prevalence), LightGBM average precision is 0.1587; at its validation-selected threshold, precision is 23.79%, recall is 24.79%, and F1 is 0.2428. At a fixed 0.1% alert budget, it detects 248 positives (15.89% recall). Logistic regression average precision is 0.0146 and recall at that alert budget is 0%. These are synthetic-data results. Full metrics and configuration are in the local, Git-ignored `artifacts/runs/hi-small-v8/baselines/` directory and can be regenerated with the commands above.

The graph-feature ablation is complete on the same frozen cohort. It reached average precision 0.1521 versus 0.1587 for tabular LightGBM, and recall at the fixed 0.1% alert budget fell from 15.89% to 14.73%. Test prevalence is 0.205%, but the daily report shows 0.119% across September 9–10 and 59.12% across September 11–18, when only 1,108 test transactions remain. Results describe this synthetic dataset and do not establish real-world AML effectiveness.

The replay API consumes `inference_scores.parquet`, a separate artifact containing only transaction ID, timestamp, and LightGBM risk score. The publishing command explicitly projects those columns and does not read the evaluation target.

The first performance run measured 0.029 ms P50 / 0.041 ms P95 raw LightGBM inference for one prepared row, 152,214 held-out transactions per second in a one-thread batch, and 3.49 s P50 / 3.58 s P95 for the bounded end-to-end investigation. Flow-pattern queries dominate investigation latency. These are serial measurements on the two-day demo graph, not concurrent or full-graph capacity claims. See [the complete benchmark report](docs/benchmarks/iteration-1-performance.md) for methods, query timings, and costs that remain unpriced. Dollar-weighted recall is deferred because no valuation basis has been selected.

## Graph and investigation

FalkorDB is the source of graph topology and investigation evidence. Transfers use first-class transaction nodes:

```text
(:Account)-[:SENT]->(:Transaction)-[:RECEIVED]->(:Account)
```

Approved investigation tools are `trace_funds`, `inspect_counterparties`, and `detect_flow_patterns`. They use bounded parameterized queries and return source transaction IDs, times, values, paths, and query duration. The current investigator runs these tools deterministically and validates the resulting report against retrieved evidence. Optional tool-calling LLM integration is not connected. Models and any future LLM output do not decide policy actions.

Install the graph project and load the local capacity sample with:

```sh
uv sync --project graph
uv run --project graph traceaml-load-graph \
  --parquet data/processed/hi-small-v8/transactions.parquet \
  --graph-name traceaml-capacity --limit 500000
```

The sample reconciles 500,000 transactions and each edge direction. It used about 706 MiB in FalkorDB and took 31.3 seconds on the current host. A simple projection estimates roughly 7 GiB for the full graph; the full graph has not been loaded because that would leave too little memory headroom here. See [the graph README](graph/README.md) for the loader and query bounds.

See [architecture](docs/architecture.md), [API and graph contracts](docs/contracts.md), [decisions](docs/decisions.md), and [team role charters](docs/milestones/README.md).

For the full planning context, implementation summary, benchmark interpretation, and remaining work, see [the project history and Iteration 1 summary](docs/project-history-and-iteration-1-summary.md).

Implementation reference: [Tracer AML Graph Intelligence](docs/research/tracer-aml-reference.md). Its public results use a different account-level split and are not TraceAML benchmark results.
