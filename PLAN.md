# Local Synthetic Test-Recommendation Demo Plan

## Current milestone

The agreed scope is **100 evolution commits and 20 recommendation suites**.
The product corpus is frozen at `corpus-v1-snapshot` (`cc5afe3`) in the separate
`test_recommendation_product` repository. It contains 124 individual test cases;
the 20 primary catalog suites run 110 of them. The remaining 14 cases stay in
the full-product regression suite. The chronology is 70 train / 10 validation /
20 evaluation commits. This preliminary evaluation is smaller than a future
expanded corpus.

`test_catalog.yaml` defines actual unittest class targets, their stable names
and IDs, source ownership, and the revision at which each suite first exists.
Descriptions and ownership describe the snapshot and are not measured coverage.
For historical recommendations, use target-parent evidence; newly introduced
suites require separate evaluation treatment. MySQL, semantic retrieval, and
Claude ranking remain to be implemented.

## Objective

Build a self-contained local demo that recommends a small set of networking test
suites for a changed Git commit. The demo must use only fictional code, paths,
test names, issue IDs, and data. It must not connect to production services or
use product source code.

## Target outcome

For a commit that changes a fictional networking file, the local API returns
known, valid test suites together with the evidence that selected them.

```json
{
  "commit_sha": "<sha>",
  "changed_files": ["src/relay/route_table.py"],
  "recommendations": [
    {
      "area_suite": "RelayMesh::RouteRecovery",
      "confidence": 94,
      "sources": ["coverage", "jira", "semantic"],
      "reason": "The changed route-table code is covered by this suite and is linked to DEMO-1842."
    }
  ]
}
```

## Scope

### Included

- A GitHub repository containing a fictional networking application.
- 100 synthetic commits with believable change and issue history.
- Twenty runnable primary component suites in a local YAML test catalog.
- Local MySQL tables for coverage, Bugzilla, Jira, and commit metadata.
- ChromaDB for local source/test embeddings.
- A recommendation service that combines deterministic evidence and Claude
  ranking.
- Repeatable data generation using a fixed random seed.

### Explicitly excluded from the first version

- Production Gerrit, Jenkins, CIBot, MongoDB, Qdrant, Bugzilla, Jira, and
  product source code.
- Production test execution or coverage instrumentation; local fictional
  tests and per-suite coverage are included for evaluation.
- AST dependency analysis. It can be added after the deterministic and semantic
  demo works.
- Pushing generated commits unless the supplied GitHub repository is explicitly
  dedicated to this demo.

## Fictional repository model

Use only invented names. The example below is a fictional community-mesh
application, not a representation of any product codebase.

```text
mesh-harbor-demo/
├── src/
│   ├── relay/
│   │   ├── peer_registry.py
│   │   ├── route_table.py
│   │   └── heartbeat.py
│   ├── parcels/
│   │   ├── packet_queue.py
│   │   ├── packet_codec.py
│   │   └── delivery_tracker.py
│   ├── signals/
│   │   ├── channel_monitor.py
│   │   ├── link_quality.py
│   │   └── beacon_service.py
│   ├── docks/
│   │   ├── node_enrollment.py
│   │   ├── access_rules.py
│   │   └── connection_limits.py
│   └── observatory/
│       ├── traffic_snapshot.py
│       └── route_metrics.py
├── tests/
├── test_catalog.yaml
└── README.md
```

Each source component owns related test suites. For example,
`src/relay/route_table.py` strongly maps to `RelayMesh::RouteRecovery` and may
weakly map to `RelayMesh::PeerRegistry`.

## Local architecture

```mermaid
flowchart LR
    Git[Local clone of dedicated GitHub demo repo] --> Diff[Git diff reader]
    Diff --> Engine[Recommendation engine]
    MySQL[(Local MySQL)] -->|coverage and issue evidence| Engine
    Chroma[(Local ChromaDB)] -->|semantic source/test matches| Engine
    Catalog[test_catalog.yaml\n20 valid suites] -->|validation| Engine
    Engine --> Claude[Claude API]
    Claude --> Engine
    Engine --> API[Local REST API / CLI]
```

Claude ranks only candidates already produced by the catalog, coverage, issue,
and semantic layers. It must not be allowed to invent test-suite names.

## Required inputs

Provide these when implementation begins:

1. The dedicated `test_recommendation_product` repository (already populated
   and pushed), with pinned baseline/snapshot tags.
2. MySQL host, port, database name, username, and a secure method to supply the
   password.
3. Claude API key through an environment variable, never in source control or
   chat messages.
4. Whether the demo uses local-only commits or pushes all generated history to
   GitHub.

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

Do not commit `.env`, database dumps containing secrets, or API keys.

## Test catalog

Store the fixed recommendation universe in `test_catalog.yaml`. Twenty suites are
enough for a representative demo; a separate database catalog is unnecessary.

Each entry needs:

```yaml
- id: relay_route_recovery
  area_suite: RelayMesh::RouteRecovery
  pipeline: verification
  test_file: tests/relay/test_route_recovery.py
  owned_paths:
    - src/relay/route_table.py
    - src/relay/heartbeat.py
  estimated_runtime_seconds: 90
```

Use the 20 primary suites across `RelayMesh`, `PacketFlow`, `SignalWatch`,
`DockAccess`, `Observatory`, `Transport`, and `Configuration`. Runtime estimates
are null until measured; names and targets must pass catalog validation.

## MySQL data model

The local database `recommendation_system` is implemented on localhost:3306.
Credentials live in the ignored `.env` file. The authoritative versioned DDL is
`src/test_recommendation/sql/001_initial.sql`; `database.py` applies and checks
migrations and imports the frozen manifest/catalog transactionally.

The normalized model replaces the original four-table sketch:

- `corpora` and `commit_metadata` pin corpus identity, 101 baseline/evolution
  revisions, ancestry, timestamps, and chronological splits.
- `catalog_versions` and `test_suites` preserve content-hashed catalogs and
  enforce valid suite references through foreign keys.
- `coverage_runs` records revision and tool/environment provenance.
  `coverage_suite_results` stores suite status, timing, and raw JSON.
  `coverage_mapping` stores per-file counts, percentages, covered/missing lines.
  `coverage_artifacts` retains binary coverage data, JSON reports, and output.
- `jira_mapping` and `bugzilla_mapping` contain synthetic issue evidence with
  explicit availability revisions, not just insertion timestamps.
- `recommendation_runs` and `recommendation_results` retain target/evidence
  revisions, ranked suites, configuration, and explanations.
- `evaluation_runs` and `evaluation_results` store intended/coverage/fault
  oracles separately from recommendation evidence.
- `schema_migrations` records migration checksums and completion status.

Coverage/issue/recommendation/evaluation producers remain to be implemented;
their tables are initially empty. Do not treat unmeasured function/branch
coverage as zero. Historical queries must enforce ancestry and suite availability.
Final evaluation admits only training evidence. Expected labels in the stored
manifest and evaluation results are not retrieval inputs.

## Synthetic data rules

Generated data must be correlated, not independently random.

1. Assign every source file to one fictional component.
2. Assign every component three to six strongly related test suites.
3. Give each file one to three high-coverage mappings and zero to two lower-
   confidence neighbouring mappings.
4. Make each synthetic commit change one to three files, primarily from one
   component.
5. Put a fictional issue reference in a subset of commit messages, such as
   `DEMO-1842` or `BUG-731`.
6. Map that issue to the same changed paths and their component’s valid suites.
7. Preserve a fixed seed so the complete demo can be recreated exactly.

Example relationship:

```text
Commit: DEMO-1842 Correct relay route expiration
Changed: src/relay/route_table.py
Coverage: RelayMesh::RouteRecovery at 93 percent
Jira: DEMO-1842 -> RelayMesh::RouteRecovery
Bug: BUG-731 -> RelayMesh::PeerRegistry
Expected recommendation: RouteRecovery first, PeerRegistry as a secondary candidate
```

## Commit-history generation for future corpus versions

The current 100-commit corpus is already frozen and pushed. The generation
design below applies to a future corpus version; do not rewrite the v1 history.

Create source and test files first, then produce 100 commits using scripted,
small changes. Spread commits across fictional authors, components, fix/feature/
refactor/documentation categories, and dates.

Recommended commit distribution:

| Type | Approximate share |
| --- | ---: |
| Single-component feature or fix | 60% |
| Multi-file change in one component | 20% |
| Cross-component change | 10% |
| Documentation or non-functional change | 10% |

The generator must record the resulting SHA in MySQL immediately after each
commit, then create the matching coverage and issue mappings. Generate locally
first; push only after validating the history and receiving confirmation.

## ChromaDB index

Use ChromaDB locally for the first implementation.

Index source functions/classes and test descriptions separately. Store metadata
needed to filter results to valid suites:

```text
id, document_type, path, symbol_name, language, text, area_suite, test_id, commit_sha
```

Recommended document types:

- `source`: source-file chunks or functions
- `test`: test code and test description
- `suite`: catalog description plus owned paths
- `commit`: selected commit diff summaries

Use a fixed embedding model and record its name/version in index metadata so
results remain reproducible. Rebuild the index after generating the repository.

## Recommendation algorithm

1. Receive a commit SHA, branch, or local diff.
2. Read changed paths and diff from local Git.
3. Look up coverage mappings for the changed paths.
4. Look up Jira and Bugzilla mappings by commit SHA and changed path.
5. Search Chroma for semantically similar source and test documents.
6. Merge and score candidates. Coverage should carry the highest base score;
   exact issue links add a large boost; semantic matches add a smaller boost.
7. Filter candidates against `test_catalog.yaml`.
8. Send only valid candidate suites, evidence, and diff summary to Claude for
   ranking and concise explanation.
9. Validate Claude’s returned names against the catalog again.
10. Return the top three to five recommendations with source evidence.

## Implementation phases

### Phase 1: Foundation

- Create local project structure, virtual environment, `.env.example`, and
  Docker Compose configuration for MySQL and ChromaDB if desired.
- Add the fictional 20-suite test catalog.
- Create and validate MySQL schema.

### Phase 2: Synthetic repository and history

- Use the existing fictional networking source and tests in the product repo.
- Import the frozen 100-commit manifest and its chronological split.
- Populate `commit_metadata`, `coverage_mapping`, `jira_mapping`, and
  `bugzilla_mapping` in the same generation run.

### Phase 3: Semantic retrieval

Coverage collection is implemented by `test_recommendation.coverage_collection`.
It exports exact Git revisions and measures each available catalog suite
independently. Initial runs cover the training cutoff (14 suites, 80 cases)
and final snapshot (20 suites, 110 cases). Both passed and persist raw artifacts,
line/branch mappings, runtime measurements, and revision provenance in MySQL.
Snapshot evidence is diagnostic only. Six later suites have no training-cutoff
coverage; this is an explicit evidence gap. Function coverage is not yet measured.
Correlated synthetic Jira/Bugzilla evidence and retrieval are still pending.

- Chunk source and test files.
- Generate embeddings and load ChromaDB.
- Verify path, metadata, and test-suite filtering.

### Phase 4: Recommendation service

- Implement a local CLI first, then a small REST API.
- Add deterministic scoring, Chroma retrieval, catalog validation, and Claude
  ranking.
- Load the Claude key from `ANTHROPIC_API_KEY` only.

### Phase 5: Evaluation and demo

- Use commits 81–100 as the held-out evaluation set; commits 1–70 are training
  history and 71–80 are validation history.
- Compare recommended suites with the known mappings used to generate each
  commit.
- Report precision, recall, top-1 accuracy, top-3 accuracy, and response time.
- Demonstrate three scenarios: direct coverage match, issue-linked regression,
  and semantic-only fallback.

## Proposed evaluation pipeline

The evaluation pipeline validates whether the recommendation system selects tests
that are relevant to a change. It uses a fixed CF/EF candidate pool of 20
fictional test suites and is entirely local to the synthetic demo environment.

```mermaid
flowchart LR
    Pool[CF/EF candidate pool\n20 valid test suites] --> Run[Run each suite independently\non a baseline revision]
    Run --> Coverage[Collect per-test coverage]
    Coverage --> Index[Build source file or symbol\nto test-suite coverage index]
    Source[Baseline source code] --> Ast[Optional AST and call graph]
    Patch[Held-out patch] --> Diff[Changed files and symbols]
    Diff --> Impact[Expand affected symbols\nusing AST when enabled]
    Index --> Oracle[Coverage relevance oracle]
    Ast --> Impact
    Impact --> Oracle
    Patch --> ITS[ITS recommendation engine]
    Oracle --> Compare[Compare expected suites\nwith recommendations]
    ITS --> Compare
    Compare --> Report[Metrics report]
```

### Coverage collection

Run each suite separately, rather than collecting one aggregate pipeline coverage
report. Aggregate coverage only identifies files touched by the full pipeline; it
cannot identify the particular suite responsible for a file or function.

The coverage result for every suite must contain at least:

```text
test_id, area_suite, source_path, source_symbol, line_coverage, function_coverage, baseline_sha
```

This produces a deterministic lookup such as:

```text
src/relay/route_table.py::expire_routes
  → RelayMesh::RouteRecovery
  → RelayMesh::PeerRegistry
```

### Optional AST impact layer

AST analysis is optional for the MVP and must be treated as a separate source of
evidence from coverage. It is not generated from coverage data.

- Coverage answers: “Which suite exercised this file or symbol?”
- AST answers: “Which functions, imports, or direct calls are affected by this
  change?”

For a Python-only demo, use the standard-library `ast` module to extract
functions, classes, imports, and direct calls. Limit graph traversal to one to
three hops. Use the resulting impacted symbols to expand the coverage lookup.

### Relevance oracle

For every held-out patch:

1. Read its changed files and, when enabled, changed symbols.
2. Use the coverage index and optional AST impact graph to calculate the expected
   relevant suites.
3. Run ITS using only evidence available before the patch’s timestamp.
4. Compare the ITS top-k results with the expected relevant suites.

Example:

```text
Changed symbol: src/relay/route_table.py::expire_routes
Expected suites: RelayMesh::RouteRecovery, RelayMesh::PeerRegistry
ITS top two: RelayMesh::RouteRecovery, Observatory::RouteMetrics

Top-2 recall: 50 percent
Precision at 2: 50 percent
Missed suite: RelayMesh::PeerRegistry
Unexpected suite: Observatory::RouteMetrics
```

This is a **coverage/AST relevance oracle**. It demonstrates structural relevance,
but does not prove that a suite detects a real defect.

### Fault-detection oracle

Create a smaller set of intentionally defective synthetic commits. Each fault is
introduced in a known source component and has one or more tests designed to
fail when that defect exists.

For each fault-injected commit:

1. Run the full 20-suite candidate pool.
2. Record the suites that actually fail as the fault-detection ground truth.
3. Run ITS before executing the full suite set.
4. Measure whether ITS selected the failing suite in its top-k recommendations.

This is the strongest validation metric because it answers: “Would ITS have
selected a test that exposes the injected regression?”

### Evaluation scorecard

Report metrics separately for relevance and fault detection:

| Metric | Definition | Purpose |
| --- | --- | --- |
| Top-1 accuracy | Top recommendation is an expected suite. | Ranking quality. |
| Top-3 recall | At least one expected suite appears in top three. | Practical selection safety. |
| Precision at k | Relevant recommended suites divided by k. | Avoiding excess tests. |
| Critical miss rate | Fault-detecting suite omitted from top-k. | Highest-priority safety metric. |
| Invalid-suite rate | Recommendation not in `test_catalog.yaml`. | Must be zero. |
| Test-count reduction | Recommended test count versus 20-suite pool. | Efficiency gain. |
| Estimated runtime reduction | Runtime avoided versus the full pool. | CI capacity value. |
| Latency | Time to produce a recommendation. | Operational usability. |

### Evaluation integrity

- Hold out the final 20 commits for this 100-commit milestone and keep them
  out of index-building and tuning decisions. Keep five-commit groups intact.
- Split data by time; do not randomly mix related commits or patch chains.
- Restrict every recommendation to `test_catalog.yaml`.
- Use only mappings, issue links, coverage, and embeddings available before the
  held-out patch.
- Record data snapshot, catalog version, embedding model, Claude model, prompt
  version, configuration, and random seed with every run.
- Describe results externally as a **controlled synthetic validation harness**,
  not production validation.

## Acceptance criteria

- The repository contains 100 reproducible fictional commits.
- No real product paths, names, credentials, or production URLs appear in the
  generated repository or data.
- Exactly 20 catalog suites are valid recommendation targets.
- Every generated commit has consistent metadata and at least one expected suite.
- The API never returns a suite not in `test_catalog.yaml`.
- At least one recommendation is returned for a known changed networking file.
- The response shows the supporting evidence: coverage, Jira, Bugzilla, and/or
  semantic retrieval.
- Re-running with the same seed recreates the same mappings and expected results.

## Decisions remaining

The dedicated product repository, Python application, 100 evolution commits,
and baseline/snapshot tags are already in place on GitHub. The 20-suite catalog
is implemented in the recommendation-system repository.

The local MySQL instance is configured at localhost:3306 with database
`recommendation_system`.

1. Which embedding and Claude models should be pinned when those layers begin?
2. Should a web UI follow the initial CLI and REST API?

### Human-in-the-loop evidence

Record per-suite accept/reject/add/uncertain feedback against recommendation runs.
Keep reasons, reviewer identity, review state, and availability timestamps.
Approved, deduplicated, context-relevant feedback contributes at most ±5 points
to the 0–100 technical ranking; enforce mandatory/strong-evidence protections
independently. Feedback is not automatically golden ground truth. Prevent
post-decision feedback from entering historical replay evidence.

Implemented: additive migration 2 and bounded scoring helper. Pending: capture,
review, temporal/context retrieval, and recommendation integration.
