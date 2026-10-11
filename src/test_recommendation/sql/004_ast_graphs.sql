CREATE TABLE IF NOT EXISTS ast_graphs (
    corpus_id VARCHAR(64) NOT NULL,
    revision_sha CHAR(40) NOT NULL,
    analyzer_version VARCHAR(64) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    graph_json JSON NOT NULL,
    PRIMARY KEY(corpus_id,revision_sha,analyzer_version),
    FOREIGN KEY(corpus_id,revision_sha) REFERENCES commit_metadata(corpus_id,commit_sha)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ast_impacts (
    corpus_id VARCHAR(64) NOT NULL,
    target_commit_sha CHAR(40) NOT NULL,
    base_revision_sha CHAR(40) NOT NULL,
    analyzer_version VARCHAR(64) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    impact_json JSON NOT NULL,
    PRIMARY KEY(corpus_id,target_commit_sha,analyzer_version),
    FOREIGN KEY(corpus_id,target_commit_sha,analyzer_version) REFERENCES ast_graphs(corpus_id,revision_sha,analyzer_version),
    FOREIGN KEY(corpus_id,base_revision_sha,analyzer_version) REFERENCES ast_graphs(corpus_id,revision_sha,analyzer_version)
) ENGINE=InnoDB;
