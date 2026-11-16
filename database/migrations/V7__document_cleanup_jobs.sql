-- V5/V6 belong to email verification and password reset; cleanup uses V7.
-- No foreign key: cleanup must survive deletion of document/lesson metadata.
CREATE TABLE document_cleanup_jobs (
    storage_key VARCHAR(500) NOT NULL PRIMARY KEY,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;
