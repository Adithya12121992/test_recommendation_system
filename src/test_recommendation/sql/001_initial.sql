-- Corpus identities and immutable revision metadata.
CREATE TABLE IF NOT EXISTS corpora (
    corpus_id VARCHAR(64) PRIMARY KEY,
    baseline_sha CHAR(40) NOT NULL,
    snapshot_sha CHAR(40) NOT NULL,
    history_sha256 CHAR(64) NOT NULL,
    manifest_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS commit_metadata (
    corpus_id VARCHAR(64) NOT NULL,
    commit_sha CHAR(40) NOT NULL,
    parent_sha CHAR(40) NULL,
    tree_sha CHAR(40) NOT NULL,
    sequence_number INT NULL,
    split_name ENUM('baseline','train','validation','evaluation') NOT NULL,
    commit_message TEXT NOT NULL,
    change_category VARCHAR(32) NOT NULL,
    changed_files JSON NOT NULL,
    source_components JSON NOT NULL,
    authored_at DATETIME(6) NOT NULL,
    committed_at DATETIME(6) NOT NULL,
    PRIMARY KEY (corpus_id, commit_sha),
    UNIQUE KEY uq_commit_sequence (corpus_id, sequence_number),
    KEY idx_commit_split (corpus_id, split_name),
    FOREIGN KEY (corpus_id) REFERENCES corpora(corpus_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS catalog_versions (
    catalog_key CHAR(64) PRIMARY KEY,
    corpus_id VARCHAR(64) NOT NULL,
    catalog_version INT NOT NULL,
    product_snapshot_sha CHAR(40) NOT NULL,
    catalog_json JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_catalog_version (corpus_id, catalog_version),
    UNIQUE KEY uq_catalog_corpus (catalog_key, corpus_id),
    FOREIGN KEY (corpus_id, product_snapshot_sha) REFERENCES commit_metadata(corpus_id, commit_sha)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS test_suites (
    catalog_key CHAR(64) NOT NULL,
    corpus_id VARCHAR(64) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    area_suite VARCHAR(255) NOT NULL,
    unittest_target VARCHAR(255) NOT NULL,
    test_file VARCHAR(512) NOT NULL,
    description TEXT NOT NULL,
    owned_paths JSON NOT NULL,
    available_since_sha CHAR(40) NOT NULL,
    estimated_runtime_seconds DOUBLE NULL,
    PRIMARY KEY (catalog_key, suite_id),
    UNIQUE KEY uq_suite_name (catalog_key, area_suite),
    FOREIGN KEY (catalog_key, corpus_id) REFERENCES catalog_versions(catalog_key, corpus_id),
    FOREIGN KEY (corpus_id, available_since_sha) REFERENCES commit_metadata(corpus_id, commit_sha)
) ENGINE=InnoDB;

-- Coverage runs retain the measured revision, tool versions, and raw evidence.
CREATE TABLE IF NOT EXISTS coverage_runs (
    run_id CHAR(36) PRIMARY KEY,
    catalog_key CHAR(64) NOT NULL,
    corpus_id VARCHAR(64) NOT NULL,
    revision_sha CHAR(40) NOT NULL,
    coverage_tool_version VARCHAR(64) NOT NULL,
    python_version VARCHAR(64) NOT NULL,
    measurement_kind ENUM('line','branch') NOT NULL,
    status ENUM('running','passed','failed') NOT NULL,
    started_at DATETIME(6) NOT NULL,
    completed_at DATETIME(6) NULL,
    environment_json JSON NOT NULL,
    UNIQUE KEY uq_coverage_catalog (run_id, catalog_key),
    KEY idx_coverage_revision (corpus_id, revision_sha),
    FOREIGN KEY (catalog_key, corpus_id) REFERENCES catalog_versions(catalog_key, corpus_id),
    FOREIGN KEY (corpus_id, revision_sha) REFERENCES commit_metadata(corpus_id, commit_sha)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS coverage_suite_results (
    run_id CHAR(36) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    passed BOOLEAN NOT NULL,
    test_case_count INT NOT NULL,
    elapsed_seconds DOUBLE NOT NULL,
    raw_report_json JSON NOT NULL,
    PRIMARY KEY (run_id, suite_id),
    FOREIGN KEY (run_id, catalog_key) REFERENCES coverage_runs(run_id, catalog_key),
    FOREIGN KEY (catalog_key, suite_id) REFERENCES test_suites(catalog_key, suite_id),
    CHECK (test_case_count >= 0 AND elapsed_seconds >= 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS coverage_mapping (
    run_id CHAR(36) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    source_path VARCHAR(512) NOT NULL,
    executable_line_count INT NOT NULL,
    covered_line_count INT NOT NULL,
    line_coverage DECIMAL(5,2) NOT NULL,
    function_coverage DECIMAL(5,2) NULL,
    branch_coverage DECIMAL(5,2) NULL,
    executed_lines JSON NOT NULL,
    missing_lines JSON NOT NULL,
    PRIMARY KEY (run_id, suite_id, source_path),
    KEY idx_coverage_path (source_path),
    FOREIGN KEY (run_id, suite_id) REFERENCES coverage_suite_results(run_id, suite_id),
    CHECK (covered_line_count >= 0 AND executable_line_count >= covered_line_count),
    CHECK (line_coverage BETWEEN 0 AND 100),
    CHECK (function_coverage IS NULL OR function_coverage BETWEEN 0 AND 100),
    CHECK (branch_coverage IS NULL OR branch_coverage BETWEEN 0 AND 100)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS coverage_artifacts (
    run_id CHAR(36) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    artifact_kind ENUM('coverage_data','json_report','test_output') NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    media_type VARCHAR(128) NOT NULL,
    content LONGBLOB NOT NULL,
    PRIMARY KEY (run_id, suite_id, artifact_kind),
    FOREIGN KEY (run_id, suite_id) REFERENCES coverage_suite_results(run_id, suite_id)
) ENGINE=InnoDB;

-- Issue availability is a revision, not the later database insertion timestamp.
CREATE TABLE IF NOT EXISTS jira_mapping (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    corpus_id VARCHAR(64) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    jira_key VARCHAR(32) NOT NULL,
    linked_commit_sha CHAR(40) NOT NULL,
    available_at_commit_sha CHAR(40) NOT NULL,
    source_path VARCHAR(512) NOT NULL,
    issue_summary TEXT NOT NULL,
    issue_created_at DATETIME(6) NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,
    inserted_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_jira_key (jira_key),
    KEY idx_jira_path (source_path),
    KEY idx_jira_commit (corpus_id, linked_commit_sha),
    FOREIGN KEY (catalog_key, corpus_id) REFERENCES catalog_versions(catalog_key, corpus_id),
    FOREIGN KEY (catalog_key, suite_id) REFERENCES test_suites(catalog_key, suite_id),
    FOREIGN KEY (corpus_id, linked_commit_sha) REFERENCES commit_metadata(corpus_id, commit_sha),
    FOREIGN KEY (corpus_id, available_at_commit_sha) REFERENCES commit_metadata(corpus_id, commit_sha),
    CHECK (is_synthetic = TRUE)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS bugzilla_mapping (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    corpus_id VARCHAR(64) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    bug_id BIGINT NOT NULL,
    linked_commit_sha CHAR(40) NOT NULL,
    available_at_commit_sha CHAR(40) NOT NULL,
    source_path VARCHAR(512) NOT NULL,
    bug_summary TEXT NOT NULL,
    severity ENUM('low','medium','high','critical') NOT NULL,
    issue_created_at DATETIME(6) NOT NULL,
    is_synthetic BOOLEAN NOT NULL DEFAULT TRUE,
    inserted_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    KEY idx_bug_id (bug_id),
    KEY idx_bug_path (source_path),
    KEY idx_bug_commit (corpus_id, linked_commit_sha),
    FOREIGN KEY (catalog_key, corpus_id) REFERENCES catalog_versions(catalog_key, corpus_id),
    FOREIGN KEY (catalog_key, suite_id) REFERENCES test_suites(catalog_key, suite_id),
    FOREIGN KEY (corpus_id, linked_commit_sha) REFERENCES commit_metadata(corpus_id, commit_sha),
    FOREIGN KEY (corpus_id, available_at_commit_sha) REFERENCES commit_metadata(corpus_id, commit_sha),
    CHECK (is_synthetic = TRUE AND bug_id > 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS recommendation_runs (
    run_id CHAR(36) PRIMARY KEY,
    catalog_key CHAR(64) NOT NULL,
    corpus_id VARCHAR(64) NOT NULL,
    target_commit_sha CHAR(40) NOT NULL,
    evidence_revision_sha CHAR(40) NOT NULL,
    status ENUM('running','passed','failed') NOT NULL,
    model_name VARCHAR(128) NULL,
    prompt_version VARCHAR(64) NULL,
    configuration_json JSON NOT NULL,
    latency_seconds DOUBLE NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_recommendation_catalog (run_id, catalog_key),
    FOREIGN KEY (catalog_key, corpus_id) REFERENCES catalog_versions(catalog_key, corpus_id),
    FOREIGN KEY (corpus_id, target_commit_sha) REFERENCES commit_metadata(corpus_id, commit_sha),
    FOREIGN KEY (corpus_id, evidence_revision_sha) REFERENCES commit_metadata(corpus_id, commit_sha)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS recommendation_results (
    run_id CHAR(36) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    rank_position INT NOT NULL,
    confidence DECIMAL(5,2) NULL,
    evidence_json JSON NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, suite_id),
    UNIQUE KEY uq_recommendation_rank (run_id, rank_position),
    FOREIGN KEY (run_id, catalog_key) REFERENCES recommendation_runs(run_id, catalog_key),
    FOREIGN KEY (catalog_key, suite_id) REFERENCES test_suites(catalog_key, suite_id),
    CHECK (rank_position > 0),
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 100)
) ENGINE=InnoDB;

-- Expected labels live only in evaluation results, never retrieval evidence.
CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id CHAR(36) PRIMARY KEY,
    corpus_id VARCHAR(64) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    split_name ENUM('validation','evaluation') NOT NULL,
    oracle_kind ENUM('intended','coverage','fault_detection') NOT NULL,
    configuration_json JSON NOT NULL,
    metrics_json JSON NULL,
    status ENUM('running','passed','failed') NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    FOREIGN KEY (catalog_key, corpus_id) REFERENCES catalog_versions(catalog_key, corpus_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS evaluation_results (
    evaluation_run_id CHAR(36) NOT NULL,
    corpus_id VARCHAR(64) NOT NULL,
    target_commit_sha CHAR(40) NOT NULL,
    recommendation_run_id CHAR(36) NOT NULL,
    expected_suites JSON NOT NULL,
    metrics_json JSON NOT NULL,
    PRIMARY KEY (evaluation_run_id, target_commit_sha),
    FOREIGN KEY (evaluation_run_id) REFERENCES evaluation_runs(run_id),
    FOREIGN KEY (corpus_id, target_commit_sha) REFERENCES commit_metadata(corpus_id, commit_sha),
    FOREIGN KEY (recommendation_run_id) REFERENCES recommendation_runs(run_id)
) ENGINE=InnoDB;
