CREATE TABLE IF NOT EXISTS golden_datasets (
    dataset_id VARCHAR(64) NOT NULL,
    dataset_version INT NOT NULL,
    corpus_id VARCHAR(64) NOT NULL,
    catalog_key CHAR(64) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    dataset_json JSON NOT NULL,
    PRIMARY KEY(dataset_id,dataset_version),
    FOREIGN KEY(catalog_key,corpus_id) REFERENCES catalog_versions(catalog_key,corpus_id)
) ENGINE=InnoDB;
