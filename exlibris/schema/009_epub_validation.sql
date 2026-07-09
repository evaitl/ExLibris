-- Track EPUB validation results so cleanup can skip re-checking known-good files.

ALTER TABLE books ADD COLUMN epub_validated INTEGER NOT NULL DEFAULT 0;
ALTER TABLE books ADD COLUMN epub_deep_validated INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_version (version) VALUES (9);
