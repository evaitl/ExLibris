-- Track EPUB 2 conversion via Calibre ebook-convert.

ALTER TABLE books ADD COLUMN epub_version2 INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_version (version) VALUES (10);
