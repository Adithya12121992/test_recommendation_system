CREATE TABLE IF NOT EXISTS pipeline_stage_artifacts (
    run_id CHAR(36) NOT NULL,
    stage VARCHAR(64) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    corpus_id VARCHAR(64) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    base_revision_sha CHAR(40) NOT NULL,
    target_revision_sha CHAR(40) NOT NULL,
    payload_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY(run_id,stage,content_sha256),
    FOREIGN KEY(catalog_key,corpus_id) REFERENCES catalog_versions(catalog_key,corpus_id)
) ENGINE=InnoDB;
