# CIBot Database Analysis Tool

This tool connects to the CIBot MySQL database, executes an optimized query to get pipeline/area/suite/owner data for the nsx-main branch, enriches it with CK Direct and Manager information via API calls, and exports everything to Excel.

## Features

- **Database Query**: Optimized SQL query to get last execution time for each suite in nsx-main branch
- **Owner Resolution**: Smart logic to use suite owners when available, fallback to area owners
- **API Integration**: Parallel API calls to get CK Direct and Manager information
- **Excel Export**: Formatted Excel output with styling and summary statistics
- **Error Handling**: Comprehensive error handling and retry logic
- **Rate Limiting**: API rate limiting to avoid overwhelming the CIBot API

## Files

- `cibot_analysis.py` - Main application entry point
- `config.py` - Configuration settings
- `db_connector.py` - Database connection and query execution
- `api_client.py` - API client for CK Direct and Manager data
- `data_processor.py` - Data processing and enrichment logic
- `excel_exporter.py` - Excel export with formatting
- `cibot_query.sql` - Raw SQL query for reference
- `requirements.txt` - Python dependencies

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Ensure you have access to:
   - CIBot MySQL database: `cibot-db-prd.lvn.broadcom.net`
   - CIBot API: `https://cibot-api.lvn.broadcom.net`

## Usage

Run the analysis tool:

```bash
python cibot_analysis.py
```

The tool will:
1. Connect to the CIBot database
2. Execute the optimized query for nsx-main branch data
3. Process owner information (suite owners with area owner fallback)
4. Make parallel API calls to get CK Direct and Manager information
5. Export results to Excel with formatting and summary statistics

## Output Files

- **Excel file**: `cibot_analysis_YYYYMMDD_HHMMSS.xlsx` - Main formatted report
- **CSV backup**: `cibot_analysis_YYYYMMDD_HHMMSS.csv` - Simple CSV backup
- **Summary JSON**: `cibot_analysis_summary_YYYYMMDD_HHMMSS.json` - Statistics
- **Log file**: `cibot_analysis.log` - Detailed execution log

## Excel Output Columns

1. **Pipeline_Name** - CIBot pipeline name
2. **Area_Name** - Test area name
3. **Suite_Name** - Test suite name
4. **Owner** - Resolved owner email (suite owner or area owner fallback)
5. **Last_Executed** - Last execution timestamp for the suite
6. **CK_Direct** - CK Direct information from API
7. **Manager_Name** - Manager name from API
8. **Manager_Email** - Manager email from API
9. **Data_Source** - Source of owner info (suite_owner/area_owner/fallback_logic)
10. **API_Status** - Status of API calls (SUCCESS/FAILED/PARTIAL_SUCCESS)

## Configuration

Edit `config.py` to modify:
- Database connection settings
- API endpoints and rate limits
- Excel formatting options
- Logging configuration

## Database Query Details

The tool uses an optimized SQL query that:
- Joins testruns, branch, pipeline, areas, suite, and association tables
- Uses window functions to get the latest execution per suite
- Handles owner resolution with COALESCE logic
- Filters for nsx-main branch only
- Returns the most recent test run for each unique suite

## API Integration

- **Rate Limited**: 8 requests per second to avoid overwhelming APIs
- **Retry Logic**: 3 retry attempts with exponential backoff
- **Parallel Processing**: Uses ThreadPoolExecutor for concurrent API calls
- **Error Handling**: Graceful handling of API failures with status tracking

## Error Handling

- Database connection failures with retry logic
- API call failures with retry and fallback
- Missing owner data with area owner fallback
- Excel export errors with CSV backup
- Comprehensive logging for debugging

## Performance

- Parallel API processing for faster execution
- Connection pooling for database efficiency
- Batch processing of unique emails to minimize API calls
- Optimized SQL query with proper indexes

## Troubleshooting

1. **Database Connection Issues**:
   - Verify VPN connection to Broadcom network
   - Check database credentials and host accessibility

2. **API Call Failures**:
   - Check network connectivity to cibot-api.lvn.broadcom.net
   - Verify API endpoints are still valid
   - Review rate limiting settings if getting timeouts

3. **Excel Export Issues**:
   - Ensure write permissions in current directory
   - Check available disk space
   - CSV backup will be created even if Excel fails

4. **Performance Issues**:
   - Reduce max_workers in parallel processing
   - Increase API rate limiting delay
   - Process data in smaller batches

## Monitoring

The tool provides:
- Real-time progress logging
- Summary statistics upon completion
- API success rate tracking
- Data source breakdown
- Performance metrics

## Security

- No hardcoded credentials (uses config.py)
- Secure API URL encoding
- Input validation for email addresses
- Error logging without sensitive data exposure

---

## Local Synthetic Test-Recommendation Demo

This repository also contains a proposed, external-safe demo of an intelligent
test-recommendation system. It is a separate workstream from the CIBot database
analysis tool above.

The demo uses a fictional networking application, synthetic data, and a local
evaluation harness. It must not contain product source code, real paths, real
issue data, production hosts, or credentials.

For the complete architecture, schema, generation, and evaluation design, see
[PLAN.md](PLAN.md).

### Goal

Given a local Git commit or diff, recommend a small set of valid fictional test
suites, along with the evidence used to select them.

```text
Local Git change
  → changed-file and diff reader
  → coverage, Jira, and Bugzilla evidence lookup
  → optional semantic retrieval from ChromaDB
  → valid test-catalog filter
  → Claude-assisted ranking and explanation
  → top test-suite recommendations
```

### Minimum prerequisites

- A dedicated GitHub repository that is safe to populate with fictional demo
  content; generate commits locally before pushing them.
- Git and Python 3.11+.
- Docker Desktop recommended, for a local MySQL instance.
- A local MySQL database or approved non-production MySQL instance.
- ChromaDB stored locally under a project data directory.
- An Anthropic API key supplied only through `ANTHROPIC_API_KEY`; never commit or
  paste the key into source files.

### Local configuration

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

### Demo data model

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

### Recommended build order

1. Create the fixed 50-suite catalog and MySQL schema.
2. Generate fictional networking source and test files.
3. Generate a small local commit history and matching coverage/Jira/Bugzilla data.
4. Implement deterministic recommendations from changed paths and coverage data.
5. Add ChromaDB semantic retrieval and Claude ranking.
6. Scale generation to 10,000 reproducible commits.
7. Add optional AST-based impact expansion after the baseline works.

The first meaningful milestone is a single fictional code change that produces a
valid, evidence-backed test recommendation and matches a known golden answer.

### Proposed evaluation pipeline

The evaluation uses a fixed candidate pool of 50 fictional test suites. Each
suite is run independently on a baseline revision so coverage identifies which
specific suite exercised each source file or symbol.

```text
50-suite candidate pool
  → per-suite coverage collection
  → source file/symbol to suite index
  → held-out synthetic patch
  → optional AST impact expansion
  → ITS recommendations
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