import os
import psycopg2
import time

# PostgreSQL connection URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Connect to PostgreSQL
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

print("PostgreSQL connected")

# =========================
# CREATE TABLES (AUTO)
# =========================

# Users table
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    age INT,
    gender TEXT,
    city TEXT,
    country TEXT,
    is_premium BOOLEAN DEFAULT FALSE,
    premium_until BIGINT DEFAULT 0,
    joined_at BIGINT
);
""")

# Bans table
cur.execute("""
CREATE TABLE IF NOT EXISTS bans (
    user_id BIGINT PRIMARY KEY,
    banned_at BIGINT,
    reason TEXT
);
""")

# Matches table
cur.execute("""
CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    user1 BIGINT,
    user2 BIGINT,
    matched_at BIGINT
);
""")

# Reports table
cur.execute("""
CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    reporter_id BIGINT,
    reported_id BIGINT,
    reason TEXT,
    reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

print("Tables ensured")

# =========================
# DB FUNCTIONS
# =========================

def add_user(user_id, age=None, gender=None, city=None, country=None):
    cur.execute("""
    INSERT INTO users (user_id, age, gender, city, country, joined_at)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (user_id) DO NOTHING
    """, (user_id, age, gender, city, country, int(time.time())))

def get_user(user_id):
    cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    return cur.fetchone()

def set_premium(user_id, until_ts):
    cur.execute("""
    UPDATE users
    SET is_premium=TRUE, premium_until=%s
    WHERE user_id=%s
    """, (until_ts, user_id))

def ban_user(user_id, reason=""):
    cur.execute("""
    INSERT INTO bans (user_id, banned_at, reason)
    VALUES (%s, %s, %s)
    ON CONFLICT (user_id) DO NOTHING
    """, (user_id, int(time.time()), reason))

def is_banned(user_id):
    cur.execute("SELECT 1 FROM bans WHERE user_id=%s", (user_id,))
    return cur.fetchone() is not None

def add_match(user1, user2):
    cur.execute("""
    INSERT INTO matches (user1, user2, matched_at)
    VALUES (%s, %s, %s)
    """, (user1, user2, int(time.time())))
