-- Controlled Genre labels (comma-separated, up to three). Separate from Calibre tags.
ALTER TABLE books ADD COLUMN genre TEXT;
ALTER TABLE books ADD COLUMN genre_source TEXT;
ALTER TABLE books ADD COLUMN classified_content_hash TEXT;

CREATE INDEX idx_books_genre ON books (genre COLLATE NOCASE);

INSERT INTO schema_version (version) VALUES (12);
