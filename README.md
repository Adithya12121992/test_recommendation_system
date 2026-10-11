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
to address new code. These reserved scenarios now have agent-reviewed golden cases G001–G020
in `evals/`; case IDs E001–E020 remain local coverage identifiers.

Coverage is reconstructed now from historical source and tests under today's
recorded Python/tool environment. It is not proof of what was actually measured
at the historical commit time; external dependencies and services would also
need reproducible fixtures in a production replay. Rolling links permit evidence
only for their recorded target; the original fixed-cutoff eligibility flags
remain separate.

## Revision-specific AST and call graphs

```bash
python -m test_recommendation.impact_graph --product-repo ../test_recommendation_product
```

Analyzer `python-static-v1` parses production Python files without importing or
executing them. It stores module/class/function definitions, source ranges, AST
fingerprints, resolved call sites, import dependencies, and unresolved calls.
Resolution covers direct/local calls, import aliases and relative imports,
constructors, `self`/`cls` methods, annotated arguments, and simple typed instance
assignments. This is a best-effort static graph: dynamic dispatch, inheritance,
reflection, complex assignments, and scope-sensitive alias/shadowing behavior
are not fully modeled. Unresolved calls include external and builtin calls too.
A resolved edge is a static hypothesis, not proof of runtime execution.

For each reserved evaluation change, compare parent and target ASTs to identify
changed definitions, including additions and removals. Use the actual source-file
diff too, so comments and non-Python source changes retain file impact. Expand
reverse calls and reverse import dependencies using **only the parent graph**,
with a maximum of three hops. Record symbol and file distances independently;
class/module fingerprints can conservatively broaden impact. Newly added symbols
have no parent callers; file dependencies provide a fallback signal, not a
complete guarantee. No expected-suite labels enter this analysis.

Migration 4 adds `ast_graphs` and `ast_impacts`. Graphs are keyed by corpus,
revision, and analyzer version, and impacts record the exact base and target.
JSON payloads have SHA-256 checksums; reruns reject changed payloads under an
existing analyzer version. Local files live under
`artifacts/ast/python-static-v1/`, including an `index.json`. The initial batch
contains 21 graphs and 20 impact reports. Target graphs support diff analysis
and later changes; do not use target call edges as pre-change evidence.

Next, candidate retrieval must join impacted parent files/lines to the matching
case's parent coverage run, retain technical reasons, and explicitly handle
unavailable suites and new code. AST graphs alone do not establish golden answers.

## Synthetic Jira and bug evidence

```bash
python -m test_recommendation.synthetic_issues --product-repo ../test_recommendation_product
```

Generator `historical-issues-v1` links a changed production file to a changed
catalog test module only when that historical test directly imports the source
module and its suite exists at that revision. It uses actual Git diffs and test
source, not intended-suite labels or final-snapshot ownership descriptions.
The initial dataset contains 100 Jira mapping rows and 17 bug mapping rows,
representing all 100 evolution commits. Jira work items cover all matched
changes; fictional regression scenarios cover validation/integration changes.
All rows and summaries explicitly identify the records as synthetic. They are
not imported from real trackers and do not establish fault-detection truth.

Jira links become available when the linked change lands. Bug links become
available one revision later when a later revision exists; creation timestamps
are fictional commit-derived dates, while the availability revision determines
when the complete code/suite link may be retrieved. Evidence for a change can
therefore come from earlier changes, not its own newly generated issue links.
Creation time alone must not override link availability.

The command validates source paths and suite presence at both linked and
availability revisions, validates introduction ancestry, and transactionally
inserts or verifies immutable rows under a database lock. Migration 5 stores
versioned dataset provenance and a SHA-256 checksum. Repeated runs do not add
mapping duplicates; changed generation inputs require an explicit new version
and mapping-identity strategy, rather than silently overwriting existing links.

Artifacts are `artifacts/issues/historical-issues-v1/dataset.json` and
`evaluation_availability.json`. The latter records eligible link counts at the
20 evaluation parents. `available_records` supports `rolling-parent-v1` and
`fixed-training-v1` policies using validated linear corpus ancestry. For rolling
case exports, records must also reference paths and suites present at the parent.
Future recommendation queries must enforce the same availability, catalog, and
path/suite checks when reading MySQL mappings. Issue evidence is not automatically
a golden answer; the independent source/assertion review is recorded in `evals/`,
while candidate retrieval remains pending.

## Frozen golden cases and evidence validation

Step 3 preparation is complete for the initial **agent-reviewed** release:
[golden dataset v1](evals/golden_dataset_v1.json) and
[behavioral review](evals/golden_review_v1.json). G001–G020 are change-to-suite
cases, each pinned to its base/target and parent coverage. Dataset version 1
uses rolling-parent evidence and records reviewer attribution honestly:
`human_approved: false`. No LLM candidate-generation algorithm generated these
expected labels. The minimum-required behavioral labels were established by
source/test assertion review; extra suites are not automatically incorrect.

```bash
python -m test_recommendation.golden --product-repo ../test_recommendation_product
```

All 20 full reference runs passed, totaling 2,271 test executions. The four
new-suite cases disclose unavailable parent coverage. Evidence validation checks
Git ancestry, catalog identity, suite/method availability, coverage/artifact
consistency, graph provenance, issue mapping consistency and availability, and
reference output checksums. Frozen answers/evidence cannot be overwritten by
rerunning the command. MySQL stores the dataset and checksum in `golden_datasets`.

See [evaluation notes](evals/README.md) for scope and portability limits. These
20 cases cover four feature groups and establish behavioral relevance, not
fault detection or comprehensive suite relevance. Golden labels are forbidden
as retrieval evidence. Candidate retrieval, new-code fallback, LLM reduction,
and performance scoring remain next.

## Debuggable change extraction and impact expansion

These local stages need neither a GitHub PAT nor a Claude key. They do not read
`.env`, contact GitHub, or invoke an LLM.

```bash
python -m test_recommendation.change_pipeline extract \
  --product-repo ../test_recommendation_product \
  --target corpus-v1-snapshot \
  --output artifacts/pipeline/example/change.json

python -m test_recommendation.change_pipeline expand \
  --product-repo ../test_recommendation_product \
  --input artifacts/pipeline/example/change.json \
  --hops 3 \
  --output artifacts/pipeline/example/impact.json
```

Extraction resolves immutable SHAs, defaults to the first parent, and verifies
ancestry. An explicit `--base <ancestor-sha>` supports a larger comparison.
Merge targets are flagged; this is a first-parent comparison, not merge-base
PR analysis. Root commits need an explicit existing base. NUL-delimited Git
status parsing retains additions, deletions, and rename paths, including spaces.
Production changes carry zero-context old/new diff ranges and patch hashes.
Changed functions/classes carry before/after ranges and AST hashes; source paths
remain recorded even when only formatting or comments changed. Non-production
file names are metadata only, not impact-expansion seeds. Source scope currently
matches this corpus (`src/mesh_harbor/`).

Expansion accepts the saved extraction artifact, verifies its checksum, rebuilds
its pinned parent graph, and checks the graph checksum/version. It traverses
reverse calls and reverse imports separately, up to three hops. Each affected
symbol/file retains its distance and a deterministic shortest trace with call
line numbers or import relationships. Other equally short paths are not listed.
Unresolved calls in affected code and newly added symbols are explicit gaps.
Parent callers are never fabricated for a new target symbol. Both stages share
a run ID and stage/analyzer versions. Their JSON envelopes have payload hashes.

Default extraction output is `artifacts/pipeline/<run-id>/change.json`; expansion
writes an adjacent `impact.json`. Use separate `--output` paths when comparing
hop settings. These new stage traces are local artifacts; existing frozen AST
graphs remain in MySQL. The stages do not modify the golden release, inspect
expected labels, or perform coverage lookup, candidate selection, or execution.
Current static analyzer limitations still apply.

Extraction stage `change-impact-v2` also saves a readable `change.patch` covering
all changed files and numbered `diffs/*.patch` for individual files, alongside
`change.json`. Its `diff_artifact` references give relative paths and SHA-256
checksums. These unified diffs include three context lines and show removed (`-`)
and added (`+`) code. The zero-context ranges remain separate for analysis.
Expansion verifies the saved patches before replay; legacy v1 artifacts remain
supported. Binary files receive Git's binary-change notice rather than a textual
code diff. Test/docs patches are available for inspection but do not seed impact
expansion. Local Git calculates these patches from pinned revisions; downloading
from GitHub is unnecessary when both revisions are already present locally.

## Remote GitHub acquisition without a clone

```bash
python -m test_recommendation.github_pipeline run \
  --repository Adithya12121992/test_recommendation_product \
  --target cc5afe39cab1b0694dd24a8a0d428c317a03ad27 \
  --output artifacts/pipeline/github_example/change.json
```

This mode reads `GITHUB_TOKEN` from the ignored `.env` or environment and calls
GitHub REST directly. It resolves a single-parent commit, paginates changed-file
metadata, downloads the full diff into a temporary directory, parses hunk ranges,
and parses per-file hunks from that downloaded diff (GitHub metadata snippets
can have different context). It retains
hashes, ranges, statuses, and AST-derived summaries, then deletes the downloaded
patch, including on exceptions. No full or per-file raw patch is retained.

A diff alone cannot describe unchanged callers. To build the parent call graph,
this mode also downloads pinned parent and target production Python blobs via
the Git trees/blobs API, verifying blob hashes. Source is processed in memory
and never saved as a checkout. There is no Git clone and no local Git dependency.
Only derived `change.json`, `base_graph.json`, and `impact.json` remain. Source
scope remains `src/mesh_harbor/`; the graph has the existing static-analysis limits.

For separate debugging stages, use `github_pipeline extract --repository ...
--target ... --output .../change.json`, followed by `github_pipeline expand
--input .../change.json`. Expansion replays the retained derived parent graph,
checks its identity/hash, and needs no network/token. The remote extraction JSON
intentionally has no local patch references, since raw downloads were deleted.
Use the remote expansion command, not local patch-validation mode, for these
artifacts. Local patch-retention mode remains explicitly separate.

Fine-grained tokens need access to the repository and Contents: read. Classic
tokens need private-repository access when the repository is private. Unsupported
merge/root commits, truncated trees, oversized API responses, missing files in the downloaded diff, and incomplete file pagination fail explicitly. Errors do not print
credentials. Live target-repository verification succeeds with the updated token.
The remote cleanup and analysis flow is also covered by simulated API tests.
