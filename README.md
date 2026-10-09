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

## Local configuration

Create an untracked `.env` file from `.env.example`:

```bash
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=mesh_demo
MYSQL_USER=mesh_demo
MYSQL_PASSWORD=replace_me
ANTHROPIC_API_KEY=replace_me
CHROMA_PATH=./data/chroma
SYNTHETIC_SEED=20261009
```

Do not commit `.env`, API keys, passwords, or database dumps containing secrets.

## Demo data model

The first version uses a fixed YAML catalog of 50 fictional valid test suites,
such as `RelayMesh::RouteRecovery`. A full database-backed test catalog is not
required for the demo.

The local MySQL schema contains these core tables:

| Table | Purpose |
| --- | --- |
| `coverage_mapping` | Maps fictional source paths/symbols to test suites and coverage values. |
| `jira_mapping` | Links fictional issue IDs, commits, paths, and valid suites. |
| `bugzilla_mapping` | Links fictional bug IDs, commits, paths, and valid suites. |
| `commit_metadata` | Records generated commit history and changed paths. |

Generated data must be correlated: a changed component, its coverage mappings,
issue links, and expected tests must all describe the same fictional subsystem.

## Recommended build order

1. Create the fixed 50-suite catalog and MySQL schema.
2. Generate fictional networking source and test files.
3. Generate a small local commit history and matching coverage/Jira/Bugzilla data.
4. Implement deterministic recommendations from changed paths and coverage data.
5. Add ChromaDB semantic retrieval and Claude ranking.
6. Scale generation to 10,000 reproducible commits.
7. Add optional AST-based impact expansion after the baseline works.

The first meaningful milestone is a single fictional code change that produces a
valid, evidence-backed test recommendation and matches a known golden answer.

## Proposed evaluation pipeline

The evaluation uses a fixed candidate pool of 50 fictional test suites. Each
suite is run independently on a baseline revision so coverage identifies which
specific suite exercised each source file or symbol.

```text
50-suite candidate pool
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
