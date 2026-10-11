# Golden dataset v1

`golden_review_v1.json` records Codex's explicit source-and-assertion review of
commits 81–100. `golden_dataset_v1.json` freezes G001–G020 version 1, the reviewed
required suites, exact base/target revisions, evidence identities/checksums,
reference results, and review attribution. This is agent-reviewed, not human
approved. Expected labels are evaluation-only data, never retrieval inputs.

The oracle specifies minimum required behavioral suites in the fixed 20-suite
catalog. Extra suites are not automatically wrong; do not calculate ordinary
precision by treating every unlisted suite as irrelevant. The five relay-policy
cases additionally require CLI default-behavior regressions. Full reference runs
include unit and TCP integration tests outside the recommendation catalog;
those supplemental tests are not silently added as selectable catalog suites.

All 20 target reference runs passed (2,271 test executions). This validates
replayability and assertions, not actual defect detection. The changes cover
four feature groups, not every catalog suite. G001, G006, G011, and G016 introduce
their primary suite and have no parent coverage for it. Preserve those cases in
all-case reporting and report new-suite/existing-suite strata separately. Never
silently remove hard cases or replace parent evidence with target measurements.

```bash
python -m test_recommendation.golden --product-repo ../test_recommendation_product
```

On first creation, the command runs full reference tests at the 20 target
revisions, validates historical evidence, and freezes the dataset. Subsequent
runs verify it without replacing reference results. Validation checks actual
Git parents, suite/method existence, catalog/history hashes, successful parent
coverage, artifact checksums, raw JSON consistency, complete file mappings,
AST graph and impact provenance, issue lookup rows and availability, reference
output checksums, and disclosed coverage gaps. It produces
`artifacts/evaluation/golden-v1/validation.json` and stores the immutable dataset
in MySQL `golden_datasets` through migration 6.

Reference logs remain local in ignored artifacts; their paths in v1 refer to
this workspace. Moving the benchmark requires retaining/restoring the logs and
adapting validation's artifact-location handling without changing answer labels.
The dataset JSON in MySQL preserves reference hashes/results, but not full
reference log bytes. Coverage/issue/AST payloads are retained in MySQL.

A new change receives a new case ID after review. An answer correction receives
a new case version. Publish a new dataset version listing the approved case
versions; do not edit these frozen v1 files in place. Compare recommender versions
using the same dataset hash, evidence policy, catalog, and case versions. This
v1 uses rolling-parent evidence, reconstructed with the recorded current tooling;
its results are separate from a fixed-training-cutoff evaluation.

Step 3 preparation is complete for this initial agent-reviewed release. Human
review, broader scenarios, and fault-injection cases can strengthen future
releases. Candidate retrieval, LLM selection, and recommendation scoring/runs
are downstream implementation work; no recommender performance is claimed here.
