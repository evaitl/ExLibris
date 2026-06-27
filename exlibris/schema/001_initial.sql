-- ExLibris initial schema (version 1)
--
-- One row per ebook file on disk. Bibliographic fields are nullable because
-- extractors vary by format and file quality. File identity fields are required.

PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (
    version     INTEGER NOT NULL PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE books (
    id              INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    -- File identity (required)
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    format          TEXT NOT NULL CHECK (format IN ('epub', 'mobi', 'azw3', 'pdf')),
    file_size       INTEGER NOT NULL CHECK (file_size >= 0),
    file_mtime      REAL NOT NULL,
    content_hash    TEXT,

    -- Bibliographic metadata (optional; populated by scanner extractors)
    title           TEXT,
    sort_title      TEXT,
    authors         TEXT,
    publisher       TEXT,
    published_date  TEXT,
    isbn            TEXT,
    language        TEXT,
    description     TEXT,
    series          TEXT,
    series_index    REAL,
    page_count      INTEGER CHECK (page_count IS NULL OR page_count >= 0),

    -- Scan lifecycle
    first_seen_at   TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL,
    is_missing      INTEGER NOT NULL DEFAULT 0 CHECK (is_missing IN (0, 1)),

    UNIQUE (file_path)
);

CREATE INDEX idx_books_title ON books (title COLLATE NOCASE);
CREATE INDEX idx_books_sort_title ON books (sort_title COLLATE NOCASE);
CREATE INDEX idx_books_authors ON books (authors COLLATE NOCASE);
CREATE INDEX idx_books_format ON books (format);
CREATE INDEX idx_books_isbn ON books (isbn);
CREATE INDEX idx_books_series ON books (series COLLATE NOCASE);
CREATE INDEX idx_books_last_scanned ON books (last_scanned_at);
CREATE INDEX idx_books_is_missing ON books (is_missing);

-- Full-text search over the fields users are most likely to query.
CREATE VIRTUAL TABLE books_fts USING fts5 (
    title,
    authors,
    publisher,
    description,
    isbn,
    file_name,
    series,
    content='books',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER books_fts_insert
AFTER INSERT ON books
BEGIN
    INSERT INTO books_fts (
        rowid, title, authors, publisher, description, isbn, file_name, series
    ) VALUES (
        new.id, new.title, new.authors, new.publisher, new.description,
        new.isbn, new.file_name, new.series
    );
END;

CREATE TRIGGER books_fts_delete
AFTER DELETE ON books
BEGIN
    INSERT INTO books_fts (
        books_fts, rowid, title, authors, publisher, description, isbn, file_name, series
    ) VALUES (
        'delete', old.id, old.title, old.authors, old.publisher, old.description,
        old.isbn, old.file_name, old.series
    );
END;

CREATE TRIGGER books_fts_update
AFTER UPDATE ON books
BEGIN
    INSERT INTO books_fts (
        books_fts, rowid, title, authors, publisher, description, isbn, file_name, series
    ) VALUES (
        'delete', old.id, old.title, old.authors, old.publisher, old.description,
        old.isbn, old.file_name, old.series
    );
    INSERT INTO books_fts (
        rowid, title, authors, publisher, description, isbn, file_name, series
    ) VALUES (
        new.id, new.title, new.authors, new.publisher, new.description,
        new.isbn, new.file_name, new.series
    );
END;

INSERT INTO schema_version (version) VALUES (1);
