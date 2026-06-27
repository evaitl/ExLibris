-- Add optional path to extracted cover image (relative to project root).
ALTER TABLE books ADD COLUMN cover_path TEXT;

INSERT INTO schema_version (version) VALUES (2);
