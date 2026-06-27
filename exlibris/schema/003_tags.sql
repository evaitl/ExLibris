-- Tags from ebook metadata (Calibre tags / OPF subjects), used as genre filters.
ALTER TABLE books ADD COLUMN tags TEXT;

CREATE INDEX idx_books_tags ON books (tags COLLATE NOCASE);

INSERT INTO schema_version (version) VALUES (3);
