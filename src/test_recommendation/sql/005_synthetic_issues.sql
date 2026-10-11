CREATE TABLE IF NOT EXISTS synthetic_issue_datasets (
    corpus_id VARCHAR(64) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    generator_version VARCHAR(64) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    dataset_json JSON NOT NULL,
    PRIMARY KEY(corpus_id,catalog_key,generator_version),
    FOREIGN KEY(catalog_key,corpus_id) REFERENCES catalog_versions(catalog_key,corpus_id)
) ENGINE=InnoDB;
