CREATE TABLE IF NOT EXISTS evaluation_case_coverage (
    corpus_id VARCHAR(64) NOT NULL,
    target_commit_sha CHAR(40) NOT NULL,
    parent_sha CHAR(40) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    run_id CHAR(36) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    evidence_gaps_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (corpus_id,target_commit_sha,catalog_key,policy_version),
    FOREIGN KEY (corpus_id,target_commit_sha) REFERENCES commit_metadata(corpus_id,commit_sha),
    FOREIGN KEY (corpus_id,parent_sha) REFERENCES commit_metadata(corpus_id,commit_sha),
    FOREIGN KEY (catalog_key,corpus_id) REFERENCES catalog_versions(catalog_key,corpus_id),
    FOREIGN KEY (run_id,catalog_key) REFERENCES coverage_runs(run_id,catalog_key)
) ENGINE=InnoDB;
