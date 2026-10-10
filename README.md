# Synthetic Test-Recommendation Demo

This repository contains a proposed local, external-safe demonstration of an
intelligent test-recommendation system. It uses fictional networking code,
synthetic Git history, and generated test evidence.

It must not contain product source code, internal paths, real issue data,
production hosts, credentials, or proprietary test names.

For the full implementation and evaluation design, see [PLAN.md](PLAN.md).

## Goal

Given a local Git commit or diff, recommend a small set of valid fictional test
suites together with the evidence supporting each recommendation.

```text
Local Git change
  → changed-file and diff reader
  → coverage and issue-evidence lookup
  → optional semantic retrieval from ChromaDB
  → valid test-catalog filter
  → Claude-assisted ranking and explanation
  → top test-suite recommendations
```

## Minimum prerequisites

- A dedicated GitHub repository that is safe to populate with fictional demo
  content; generate commits locally before pushing them.
- Git and Python 3.11+.
- Docker Desktop recommended, for a local MySQL instance.
- A local MySQL database or approved non-production MySQL instance.
- ChromaDB stored locally under a project data directory.
- An Anthropic API key supplied only through `ANTHROPIC_API_KEY`; never commit or
  paste the key into source files.

## Python environment

The local environment uses Python 3.14. Activate it from the project directory:

```bash
source .venv/bin/activate
python --version
python -m pip --version
```

To recreate the environment, run `python3.14 -m venv .venv` before activating it.
Run `deactivate` to leave the environment. Install the catalog tooling and its PyYAML dependency with
`python -m pip install -e .`. Networking application tests themselves require
only the standard library.

## Current milestone and catalog

The product corpus contains **100 evolution commits** and **124 test cases**.
This project recommends from the **20 primary component suites** listed in
[test_catalog.yaml](test_catalog.yaml); together they run 110 test cases. The
remaining 14 baseline/integration cases remain part of full-product regression
checks. This catalog is separate from the product repository and pins its frozen
`cc5afe3` snapshot using a full SHA.

Each entry includes a stable ID, suite name, description, unittest class target,
test file, owned source paths, and the suite's introduction SHA. Ownership and
snapshot descriptions are catalog metadata, not coverage or historical
evidence. Before a historical recommendation, the suite and its evidence must
exist at the target parent; introduction SHAs flag unavailable suites.
Runtime estimates are unset until measured.

Validate and run all 20 suites independently from a temporary export of the
pinned product snapshot:

```bash
python -m test_recommendation.catalog --product-repo ../test_recommendation_product --run --report artifacts/catalog_validation.json
```

Omit `--run` for static validation. The optional report records per-suite case
counts, pass/fail results, and elapsed process durations; these timings are
local observations rather than calibrated runtime estimates. The validator
reads Git objects and never changes the product worktree.

Run catalog validation checks:

```bash
python -m unittest discover -s tests -v
```

## Local MySQL storage

The configured local database is **`recommendation_system` on localhost:3306**.
Credentials are loaded from the ignored, owner-readable `.env` file or matching
`MYSQL_*` environment variables. `.env.example` contains placeholders only.

The database has 15 tables, including the migration ledger:

| Tables | Stored data |
| --- | --- |
| `corpora`, `commit_metadata` | Pinned manifests, revisions, ancestry, UTC timestamps, and train/validation/evaluation splits |
| `catalog_versions`, `test_suites` | Content-hashed catalog versions and valid runnable suite targets |
| `coverage_runs`, `coverage_suite_results` | Measured revision, tool/Python versions, run status, per-suite timing and raw JSON |
| `coverage_mapping`, `coverage_artifacts` | Per-file covered/missing lines, percentages, raw coverage files and test output |
| `jira_mapping`, `bugzilla_mapping` | Synthetic issue links, source paths, suites, and the revision where evidence becomes available |
| `recommendation_runs`, `recommendation_results` | Target/evidence revisions, ranked suites, explanations, model/prompt configuration |
| `evaluation_runs`, `evaluation_results` | Oracle type, expected suites, and evaluation metrics |
| `schema_migrations` | Versioned migration checksum and completion status |

Initial data includes one corpus, **101 revision rows** (baseline plus 100
changes), one catalog version, and **20 suites**. Coverage, issue, recommendation,
and evaluation tables are empty until their producers run. No fabricated
coverage or issue rows were imported.

Initialize, import, and inspect the database:

```bash
python -m test_recommendation.database init
python -m test_recommendation.database seed --product-repo ../test_recommendation_product
python -m test_recommendation.database status
```

Initialization can resume an interrupted migration and checks its checksum.
Repeated seed imports verify immutable values without duplicating data. Changed
catalog contents require a new catalog version. Seed data is transactional.
The authoritative schema is
[src/test_recommendation/sql/001_initial.sql](src/test_recommendation/sql/001_initial.sql).

Coverage is tied to exact revisions through `coverage_runs`; suite references
are validated by foreign keys. Function/branch percentages stay null when not
measured. Raw binary/JSON coverage artifacts can be retained in MySQL as well as
local `artifacts/coverage/` files.

Collect independent line and branch coverage from an exported Git revision:

```bash
python -m test_recommendation.coverage_collection --product-repo ../test_recommendation_product
python -m test_recommendation.coverage_collection --product-repo ../test_recommendation_product --revision snapshot
```

The default measures the last training commit (`55a761e`): 14 available suites,
80 test cases. The final snapshot (`cc5afe3`) measures all 20 suites, 110 cases,
and is marked diagnostic only. Six suites did not yet exist at the training
cutoff; do not backfill their evidence from the final snapshot. An explicit
full corpus SHA is also accepted. Training measurements can support held-out
evaluation only when the measured revision precedes the target commit;
earlier training targets require earlier measurements.

Each run writes `artifacts/coverage/<full-sha>/<run-id>/run.json` and
`coverage_index.json`, plus per-suite `.coverage`, `coverage.json`, and
`test_output.txt`. MySQL retains the same artifacts with SHA-256 checksums,
per-file line/branch measurements, pass status, and elapsed times. The index
includes only files touched by passing suites. Function percentages remain
null; measured timings do not modify the immutable catalog. Runs export source
and tests to a temporary directory, leaving the product worktree untouched.

Both initial runs passed: 34 suite results, 692 file mappings, 102 retained
artifacts. Coverage generation is complete; correlated synthetic issue data
and recommendation retrieval remain to be implemented.
Issue availability uses Git revisions rather than database insertion time.
Recommendation queries must enforce ancestry, catalog availability, successful
coverage runs/suite results, and the train-only evidence policy for evaluation.
Expected labels in `evaluation_results` and `corpora.manifest_json` are
provenance/evaluation data and must never serve as retrieval evidence.

Run live storage checks with the configured database:

```bash
RUN_MYSQL_TESTS=1 python -m unittest discover -s tests -v
```

These checks roll back every fixture write. Without the flag, network-free
checks run and MySQL integration checks are skipped.

## Local configuration

Create an untracked `.env` file from `.env.example`:

```bash
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=recommendation_system
MYSQL_USER=root
MYSQL_PASSWORD=replace_me
ANTHROPIC_API_KEY=replace_me
CHROMA_PATH=./data/chroma
SYNTHETIC_SEED=20261009
```

Do not commit `.env`, API keys, passwords, or database dumps containing secrets.

## Demo data model

The first version uses a fixed YAML catalog of 20 fictional valid test suites,
such as `RelayMesh::RouteRecovery`. A full database-backed test catalog is not
required for the demo.

The implemented MySQL schema includes these core evidence tables:

| Table | Purpose |
| --- | --- |
| `coverage_mapping` | Maps source files to per-suite covered/missing lines and measured percentages, joined to revision-specific runs. |
| `jira_mapping` | Links fictional issue IDs, commits, paths, and valid suites. |
| `bugzilla_mapping` | Links fictional bug IDs, commits, paths, and valid suites. |
| `commit_metadata` | Records generated commit history and changed paths. |

Generated data must be correlated: a changed component, its coverage mappings,
issue links, and expected tests must all describe the same fictional subsystem.

## Recommended build order

1. Create the fixed 20-suite catalog and MySQL schema.
2. Generate fictional networking source and test files.
3. Generate a small local commit history and matching coverage/Jira/Bugzilla data.
4. Implement deterministic recommendations from changed paths and coverage data.
5. Add ChromaDB semantic retrieval and Claude ranking.
6. Evaluate the frozen 100-commit corpus with its 70/10/20 chronological split.
7. Add optional AST-based impact expansion after the baseline works.

The first meaningful milestone is a single fictional code change that produces a
valid, evidence-backed test recommendation and matches a known golden answer.

## Proposed evaluation pipeline

The evaluation uses a fixed candidate pool of 20 fictional test suites. Each
suite is run independently on a baseline revision so coverage identifies which
specific suite exercised each source file or symbol.

```text
20-suite candidate pool
  → per-suite coverage collection
  → source file/symbol to suite index
  → held-out synthetic patch
  → optional AST impact expansion
  → test recommendations
  → comparison with expected suites
  → metrics report
```

AST and coverage are complementary:

- Coverage answers: “Which test suite exercised this file or symbol?”
- AST answers: “Which functions, imports, calls, or dependencies are affected?”

Use AST as an optional score-expansion signal, not as a replacement for
per-test coverage. Limit graph traversal to one to three hops.

Evaluate on held-out synthetic commits with data available only before the patch.
Track Top-1 accuracy, Top-3 recall, precision at k, critical-miss rate,
invalid-suite rate, test-count reduction, estimated runtime reduction, and
recommendation latency.

For a stronger fault-detection evaluation, create a smaller set of deliberately
defective synthetic commits. Run the full candidate pool, record which tests
actually fail, and measure whether the recommendation engine included a failing
test in its top-k results.

Externally, describe this as a **controlled synthetic validation harness**, not
production validation.

## Human feedback

Migration `002_feedback.sql` adds `recommendation_feedback`, linked to a
recommendation run and a catalog suite. The run identifies the target commit.
Actions are accept, reject, add a missing suite, or uncertain; reasons distinguish
relevance, redundancy, cost, and failure detection. Feedback starts pending and
requires reviewer identity and review time for approval or dismissal.

`test_recommendation.feedback.adjust_score` provides a bounded ranking adjustment:
base technical score (0–100) plus at most ±5 points, clipped to 0–100. A neutral
prior dampens single-user influence; conflicting votes cancel. This is a five-point
cap, not a trained probability or a guaranteed five-percent contribution.

Future retrieval must use only approved feedback available before the decision,
from relevant, ancestor-compatible changes and compatible suite identities.
Deduplicate repeated votes per reviewer/context; pending, dismissed, uncertain,
and cost-only feedback must not become negative relevance evidence. Historical
evaluations freeze the review-time cutoff. Golden answer changes require separate
review. Mandatory suites and strongly supported technical candidates remain
protected by the selector, regardless of feedback adjustments.

Storage and the scoring helper are implemented. Capture UI/CLI, review workflows,
context matching, retrieval, and integration with the future recommender remain
pending; feedback currently changes no recommendations.

## Rolling coverage for evaluation changes

```bash
python -m test_recommendation.evaluation_coverage --product-repo ../test_recommendation_product
```

This measures all available catalog suites at the exact parent of each of the
20 reserved evaluation commits. Policy `rolling-parent-v1` allows evidence from
previously landed changes, including earlier evaluation changes; it is separate
from the fixed-training policy. Each case has a target-specific eligibility link
in `evaluation_case_coverage`, added by migration 3. No final-snapshot coverage
is substituted for parent coverage, and expected-suite labels are not consulted.

Results are indexed in
`artifacts/coverage/evaluation_cases/rolling-parent-v1/index.json` and refer to
revision/run-specific artifacts. Reruns validate and reuse pinned successful
runs, checking exact parent, catalog, suite counts, and all artifact checksums.
Unavailable suites are explicit evidence gaps; suites introduced by the target
change have no parent coverage. AST analysis and fallback selection will need
to address new code. These are reserved evaluation scenarios, not yet vetted
golden cases. Case IDs E001–E020 are local coverage identifiers.

Coverage is reconstructed now from historical source and tests under today's
recorded Python/tool environment. It is not proof of what was actually measured
at the historical commit time; external dependencies and services would also
need reproducible fixtures in a production replay. Rolling links permit evidence
only for their recorded target; the original fixed-cutoff eligibility flags
remain separate.
