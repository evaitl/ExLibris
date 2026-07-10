-- Partial indexes for EPUB maintenance queries at scale.

CREATE INDEX IF NOT EXISTS idx_books_epub_needs_v2
  ON books(id)
  WHERE is_missing = 0 AND format = 'epub' AND epub_version2 = 0;

CREATE INDEX IF NOT EXISTS idx_books_epub_needs_validation
  ON books(id)
  WHERE is_missing = 0 AND epub_validated = 0;

CREATE INDEX IF NOT EXISTS idx_books_epub_needs_deep_validation
  ON books(id)
  WHERE is_missing = 0 AND epub_deep_validated = 0;

INSERT INTO schema_version (version) VALUES (11);
