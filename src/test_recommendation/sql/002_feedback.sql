CREATE TABLE IF NOT EXISTS recommendation_feedback (
    feedback_id CHAR(36) PRIMARY KEY,
    run_id CHAR(36) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    suite_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    action ENUM('accept','reject','add','uncertain') NOT NULL,
    reason_category ENUM('relevance','redundancy','cost','failure_detection','other') NOT NULL,
    reason TEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    review_status ENUM('pending','approved','dismissed') NOT NULL DEFAULT 'pending',
    reviewed_by VARCHAR(128) NULL,
    reviewed_at DATETIME(6) NULL,
    FOREIGN KEY (run_id,catalog_key) REFERENCES recommendation_runs(run_id,catalog_key),
    FOREIGN KEY (catalog_key,suite_id) REFERENCES test_suites(catalog_key,suite_id),
    KEY idx_feedback_availability (review_status,reviewed_at),
    CHECK ((review_status='pending' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (review_status IN ('approved','dismissed') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
    CHECK (reviewed_at IS NULL OR reviewed_at >= created_at)
) ENGINE=InnoDB;
