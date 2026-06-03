CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_date TEXT,
    venue TEXT,
    capacity INTEGER NOT NULL CHECK (capacity >= 0),
    accessible_capacity INTEGER NOT NULL DEFAULT 0 CHECK (accessible_capacity >= 0),
    is_open INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL DEFAULT 1,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    organization TEXT,
    accessible_required INTEGER NOT NULL DEFAULT 0,
    token TEXT NOT NULL UNIQUE,
    manual_code TEXT UNIQUE,
    created_at TEXT NOT NULL,
    checked_in_at TEXT,
    FOREIGN KEY (event_id) REFERENCES events(id)
);

CREATE INDEX IF NOT EXISTS idx_participants_token ON participants(token);
CREATE INDEX IF NOT EXISTS idx_participants_email ON participants(email);
