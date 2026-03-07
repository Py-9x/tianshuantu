SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    emergency_contact TEXT,
    age INTEGER,
    height REAL,
    weight REAL,
    bmi REAL,
    bmi_status TEXT,
    profile_complete INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS adventures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    destination TEXT NOT NULL,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    status TEXT CHECK(status IN ('planning', 'ongoing', 'archived')) DEFAULT 'planning',
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS vitals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adventure_id INTEGER NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hr REAL,
    spo2 REAL,
    temp REAL,
    lat REAL,
    lon REAL,
    risk_score REAL,
    FOREIGN KEY (adventure_id) REFERENCES adventures(id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adventure_id INTEGER NOT NULL,
    type TEXT CHECK(type IN ('pre', 'mid', 'post')),
    content TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (adventure_id) REFERENCES adventures(id)
);
"""
