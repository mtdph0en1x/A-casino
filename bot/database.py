import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Mount an Azure File Share at /data for persistence across redeploys.
# Without it, data resets when the container restarts (fine for dev/testing).
DB_PATH = os.environ.get("DB_PATH", "bot.db")


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
@contextmanager
def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrency
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------
def init_db() -> None:
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT    NOT NULL,
                balance    REAL    NOT NULL DEFAULT 1.0
            );

            CREATE TABLE IF NOT EXISTS bets (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id             INTEGER NOT NULL,
                proposer_id         INTEGER NOT NULL REFERENCES users(user_id),
                opponent_id         INTEGER NOT NULL REFERENCES users(user_id),
                amount              REAL    NOT NULL,
                description         TEXT    NOT NULL,
                status              TEXT    NOT NULL DEFAULT 'PROPOSED',
                claimer_id          INTEGER REFERENCES users(user_id),
                claim_message_id    INTEGER,
                proposal_message_id INTEGER,
                created_at          TEXT    NOT NULL,
                resolved_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS votes (
                bet_id       INTEGER NOT NULL REFERENCES bets(id),
                voter_id     INTEGER NOT NULL REFERENCES users(user_id),
                voted_for_id INTEGER NOT NULL REFERENCES users(user_id),
                PRIMARY KEY (bet_id, voter_id)
            );
        """)


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------
def get_or_create_user(user_id: int, username: Optional[str], first_name: str):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name),
        )
        # Always keep username/name fresh
        db.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (username, first_name, user_id),
        )
    return get_user(user_id)


def get_user(user_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_user_by_username(username: str):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (username,)
        ).fetchone()


def get_all_users_ranked():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users ORDER BY balance DESC"
        ).fetchall()


def transfer_balance(winner_id: int, loser_id: int, amount: float) -> None:
    with get_db() as db:
        db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, winner_id))
        db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, loser_id))


# ---------------------------------------------------------------------------
# Bet operations
# ---------------------------------------------------------------------------
def create_bet(
    chat_id: int,
    proposer_id: int,
    opponent_id: int,
    amount: float,
    description: str,
) -> int:
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO bets
               (chat_id, proposer_id, opponent_id, amount, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chat_id, proposer_id, opponent_id, amount, description,
             datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_bet(bet_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()


def get_active_bets(chat_id: int):
    with get_db() as db:
        return db.execute(
            """SELECT * FROM bets
               WHERE chat_id=? AND status IN ('PROPOSED','ACTIVE','CLAIMED','DISPUTED')
               ORDER BY id DESC""",
            (chat_id,),
        ).fetchall()


def get_bet_history(chat_id: int, limit: int = 10):
    with get_db() as db:
        return db.execute(
            """SELECT * FROM bets
               WHERE chat_id=? AND status IN ('CONFIRMED','DECLINED')
               ORDER BY resolved_at DESC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()


def update_bet(bet_id: int, **fields) -> None:
    """Generic update: update_bet(3, status='ACTIVE', proposal_message_id=42)"""
    with get_db() as db:
        set_clause = ", ".join(f"{k}=?" for k in fields)
        db.execute(
            f"UPDATE bets SET {set_clause} WHERE id=?",
            list(fields.values()) + [bet_id],
        )


# ---------------------------------------------------------------------------
# Vote operations
# ---------------------------------------------------------------------------
def add_vote(bet_id: int, voter_id: int, voted_for_id: int) -> None:
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO votes (bet_id, voter_id, voted_for_id) VALUES (?,?,?)",
            (bet_id, voter_id, voted_for_id),
        )


def get_votes(bet_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM votes WHERE bet_id=?", (bet_id,)).fetchall()


def count_votes_for(bet_id: int, candidate_id: int) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM votes WHERE bet_id=? AND voted_for_id=?",
            (bet_id, candidate_id),
        ).fetchone()
        return row[0]
