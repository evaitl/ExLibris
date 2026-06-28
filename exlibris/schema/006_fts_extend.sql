-- Extend FTS to cover sort_title and tags; rebuild index from books.
DROP TRIGGER IF EXISTS books_fts_insert;
DROP TRIGGER IF EXISTS books_fts_delete;
DROP TRIGGER IF EXISTS books_fts_update;
DROP TABLE IF EXISTS books_fts;

CREATE VIRTUAL TABLE books_fts USING fts5 (
    title,
    sort_title,
    authors,
    publisher,
    description,
    isbn,
    file_name,
    series,
    tags,
    content='books',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER books_fts_insert
AFTER INSERT ON books
BEGIN
    INSERT INTO books_fts (
        rowid, title, sort_title, authors, publisher, description, isbn, file_name, series, tags
    ) VALUES (
        new.id, new.title, new.sort_title, new.authors, new.publisher, new.description,
        new.isbn, new.file_name, new.series, new.tags
    );
END;

CREATE TRIGGER books_fts_delete
AFTER DELETE ON books
BEGIN
    INSERT INTO books_fts (
        books_fts, rowid, title, sort_title, authors, publisher, description, isbn, file_name, series, tags
    ) VALUES (
        'delete', old.id, old.title, old.sort_title, old.authors, old.publisher, old.description,
        old.isbn, old.file_name, old.series, old.tags
    );
END;

CREATE TRIGGER books_fts_update
AFTER UPDATE ON books
BEGIN
    INSERT INTO books_fts (
        books_fts, rowid, title, sort_title, authors, publisher, description, isbn, file_name, series, tags
    ) VALUES (
        'delete', old.id, old.title, old.sort_title, old.authors, old.publisher, old.description,
        old.isbn, old.file_name, old.series, old.tags
    );
    INSERT INTO books_fts (
        rowid, title, sort_title, authors, publisher, description, isbn, file_name, series, tags
    ) VALUES (
        new.id, new.title, new.sort_title, new.authors, new.publisher, new.description,
        new.isbn, new.file_name, new.series, new.tags
    );
END;

INSERT INTO books_fts(books_fts) VALUES('rebuild');

INSERT INTO schema_version (version) VALUES (6);
