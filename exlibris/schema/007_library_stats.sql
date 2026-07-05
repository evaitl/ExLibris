-- Cached library totals and filter options for fast browse/search pages.
CREATE TABLE library_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    library_total INTEGER NOT NULL,
    languages TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);

INSERT INTO schema_version (version) VALUES (7);
