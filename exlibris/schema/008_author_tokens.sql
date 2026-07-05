-- Fast author prefix search: token lookup table (avoids slow FTS5 prefix scans).
CREATE TABLE book_author_tokens (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    token TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (book_id, token)
);

CREATE INDEX idx_book_author_tokens_token ON book_author_tokens(token, book_id);

INSERT INTO schema_version (version) VALUES (8);
