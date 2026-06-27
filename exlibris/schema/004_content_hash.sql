-- SHA-1 content hash for duplicate detection (one canonical row per file content).
CREATE UNIQUE INDEX idx_books_content_hash ON books (content_hash)
    WHERE content_hash IS NOT NULL;

INSERT INTO schema_version (version) VALUES (4);
