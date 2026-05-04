"""Math Trainer — mental arithmetic practice with per-attempt analytics.

Two tables, both lazily created on first request:

* `math_session`   — one row per finished training session (totals + settings).
* `math_attempt`   — one row per individual problem within a session.

The frontend generates problems client-side and POSTs the entire session
(metadata + attempts batch) when it ends.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import psycopg2
import psycopg2.extras
import os
import json

router = APIRouter(prefix="/math", tags=["MathTrainer"])


# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------

def _ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS math_session (
            id              SERIAL PRIMARY KEY,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            duration_s      INTEGER NOT NULL,
            settings        JSONB NOT NULL DEFAULT '{}'::jsonb,
            correct         INTEGER NOT NULL DEFAULT 0,
            wrong           INTEGER NOT NULL DEFAULT 0,
            score_per_min   REAL NOT NULL DEFAULT 0,
            avg_latency_ms  INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS math_attempt (
            id              SERIAL PRIMARY KEY,
            session_id      INTEGER NOT NULL REFERENCES math_session(id) ON DELETE CASCADE,
            problem         TEXT NOT NULL,
            op              CHAR(1) NOT NULL,
            a_value         REAL,
            b_value         REAL,
            user_answer     TEXT,
            correct_answer  TEXT NOT NULL,
            latency_ms      INTEGER NOT NULL,
            is_correct      BOOLEAN NOT NULL,
            ts              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_math_attempt_session
        ON math_attempt(session_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_math_session_started
        ON math_session(started_at DESC)
    """)


def _conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


# ----------------------------------------------------------------------------
# Payloads
# ----------------------------------------------------------------------------

class AttemptPayload(BaseModel):
    problem: str
    op: str
    a_value: Optional[float] = None
    b_value: Optional[float] = None
    user_answer: Optional[str] = None
    correct_answer: str
    latency_ms: int
    is_correct: bool


class SessionPayload(BaseModel):
    duration_s: int
    settings: dict
    attempts: List[AttemptPayload]


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------

@router.post("/session")
def create_session(payload: SessionPayload):
    """Persist a finished session and all its attempts in one transaction."""
    if payload.duration_s <= 0:
        raise HTTPException(400, "duration_s must be positive")

    correct = sum(1 for a in payload.attempts if a.is_correct)
    wrong = len(payload.attempts) - correct
    score_per_min = (correct / payload.duration_s) * 60.0 if payload.duration_s else 0.0
    avg_latency = (
        int(sum(a.latency_ms for a in payload.attempts) / len(payload.attempts))
        if payload.attempts else None
    )

    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        _ensure_tables(cur)

        cur.execute(
            """
            INSERT INTO math_session
                (duration_s, settings, correct, wrong, score_per_min, avg_latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, started_at
            """,
            (payload.duration_s, json.dumps(payload.settings), correct, wrong,
             score_per_min, avg_latency),
        )
        session_id, started_at = cur.fetchone()

        if payload.attempts:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO math_attempt
                    (session_id, problem, op, a_value, b_value,
                     user_answer, correct_answer, latency_ms, is_correct)
                VALUES %s
                """,
                [
                    (session_id, a.problem, a.op, a.a_value, a.b_value,
                     a.user_answer, a.correct_answer, a.latency_ms, a.is_correct)
                    for a in payload.attempts
                ],
            )

        conn.commit()
        return {
            "id": session_id,
            "started_at": started_at.isoformat(),
            "correct": correct,
            "wrong": wrong,
            "score_per_min": round(score_per_min, 2),
            "avg_latency_ms": avg_latency,
        }
    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to save session: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/sessions")
def list_sessions(limit: int = 50):
    """Return the most recent sessions (lightweight, no attempts)."""
    limit = max(1, min(500, limit))
    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        _ensure_tables(cur)
        cur.execute(
            """
            SELECT id, started_at, duration_s, settings, correct, wrong,
                   score_per_min, avg_latency_ms
            FROM math_session
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "started_at": r[1].isoformat(),
                "duration_s": r[2],
                "settings": r[3],
                "correct": r[4],
                "wrong": r[5],
                "score_per_min": float(r[6]),
                "avg_latency_ms": r[7],
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(500, f"Failed to list sessions: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/stats")
def aggregate_stats():
    """Aggregates: highest score, totals, accuracy + latency per operation."""
    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        _ensure_tables(cur)

        cur.execute("""
            SELECT
                COUNT(*) AS sessions,
                COALESCE(MAX(score_per_min), 0) AS highest,
                COALESCE(SUM(correct), 0) AS total_correct,
                COALESCE(SUM(wrong),   0) AS total_wrong
            FROM math_session
        """)
        sessions, highest, total_correct, total_wrong = cur.fetchone()

        cur.execute("""
            SELECT
                op,
                COUNT(*)                                             AS n,
                SUM(CASE WHEN is_correct THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*),0)                             AS accuracy,
                AVG(latency_ms)::int                                 AS avg_latency
            FROM math_attempt
            GROUP BY op
            ORDER BY op
        """)
        by_op = [
            {"op": r[0], "n": r[1],
             "accuracy": float(r[2]) if r[2] is not None else None,
             "avg_latency_ms": r[3]}
            for r in cur.fetchall()
        ]

        # Last 30 sessions for trend chart
        cur.execute("""
            SELECT started_at, score_per_min
            FROM math_session
            ORDER BY started_at DESC
            LIMIT 30
        """)
        trend = [
            {"started_at": r[0].isoformat(), "score_per_min": float(r[1])}
            for r in cur.fetchall()
        ]
        trend.reverse()

        return {
            "sessions": sessions,
            "highest_per_min": float(highest),
            "total_correct": total_correct,
            "total_wrong": total_wrong,
            "by_op": by_op,
            "trend": trend,
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to load stats: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/weakness")
def weakness(min_attempts: int = 2, top_k: int = 60):
    """Return the (op, a, b) tuples where the user struggles most.

    Used by the frontend's "practice weakness" mode: it fetches this list
    once at the start of a session and biases problem generation toward
    these pairs (weighted by error rate × log(latency)).
    """
    min_attempts = max(1, min(50, min_attempts))
    top_k = max(1, min(500, top_k))
    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        _ensure_tables(cur)
        cur.execute(
            """
            SELECT
                op,
                a_value,
                b_value,
                COUNT(*)                                                AS n,
                SUM(CASE WHEN is_correct THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0)                               AS accuracy,
                AVG(latency_ms)::int                                    AS avg_latency,
                MAX(ts)                                                 AS last_seen,
                MAX(CASE WHEN is_correct = FALSE THEN ts END)           AS last_wrong_at,
                COUNT(*) FILTER (
                    WHERE ts > NOW() - INTERVAL '7 days'
                )                                                       AS recent_n,
                SUM(CASE WHEN is_correct = FALSE
                    AND ts > NOW() - INTERVAL '7 days'
                    THEN 1 ELSE 0 END)                                  AS recent_wrong
            FROM math_attempt
            WHERE a_value IS NOT NULL AND b_value IS NOT NULL
            GROUP BY op, a_value, b_value
            HAVING COUNT(*) >= %s
            ORDER BY
                (1 - SUM(CASE WHEN is_correct THEN 1 ELSE 0 END)::float
                     / NULLIF(COUNT(*), 0)) DESC,
                AVG(latency_ms) DESC
            LIMIT %s
            """,
            (min_attempts, top_k),
        )
        pairs = [
            {
                "op": r[0],
                "a": float(r[1]),
                "b": float(r[2]),
                "n": r[3],
                "accuracy": float(r[4]) if r[4] is not None else 0.0,
                "avg_latency_ms": r[5],
                "last_seen": r[6].isoformat() if r[6] else None,
                "last_wrong_at": r[7].isoformat() if r[7] else None,
                "recent_n": r[8] or 0,
                "recent_wrong": r[9] or 0,
            }
            for r in cur.fetchall()
        ]
        return {"weak_pairs": pairs}
    except Exception as e:
        raise HTTPException(500, f"Failed to compute weakness: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/heatmap")
def heatmap(op: str = "*", lo: int = 1, hi: int = 12):
    """Per-cell accuracy/attempts grid for integer (a, b) pairs of a given op.

    Defaults to the classic 1–12 multiplication table. The frontend renders
    each cell tinted by accuracy and saturation by attempt count; empty
    cells stay neutral.
    """
    if op not in {"+", "-", "*", "/"}:
        raise HTTPException(400, "op must be one of + - * /")
    lo = max(0, min(999, lo))
    hi = max(lo, min(999, hi))

    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        _ensure_tables(cur)
        cur.execute(
            """
            SELECT
                a_value::int                                         AS a,
                b_value::int                                         AS b,
                COUNT(*)                                             AS n,
                SUM(CASE WHEN is_correct THEN 1 ELSE 0 END)          AS correct,
                AVG(latency_ms)::int                                 AS avg_latency
            FROM math_attempt
            WHERE op = %s
              AND a_value IS NOT NULL AND b_value IS NOT NULL
              AND a_value = ROUND(a_value)
              AND b_value = ROUND(b_value)
              AND a_value BETWEEN %s AND %s
              AND b_value BETWEEN %s AND %s
            GROUP BY a, b
            """,
            (op, lo, hi, lo, hi),
        )
        cells = [
            {
                "a": r[0],
                "b": r[1],
                "n": r[2],
                "correct": r[3],
                "accuracy": (r[3] / r[2]) if r[2] else 0.0,
                "avg_latency_ms": r[4],
            }
            for r in cur.fetchall()
        ]
        return {"op": op, "lo": lo, "hi": hi, "cells": cells}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to compute heatmap: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/latencies")
def latencies(limit: int = 2000):
    """Return latency samples (split by correctness) for histogram rendering."""
    limit = max(50, min(20000, limit))
    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        _ensure_tables(cur)
        cur.execute(
            """
            SELECT latency_ms, is_correct
            FROM math_attempt
            ORDER BY ts DESC
            LIMIT %s
            """,
            (limit,),
        )
        correct, wrong = [], []
        for r in cur.fetchall():
            (correct if r[1] else wrong).append(int(r[0]))
        return {"correct": correct, "wrong": wrong}
    except Exception as e:
        raise HTTPException(500, f"Failed to load latencies: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.get("/mistakes")
def mistakes(limit: int = 20):
    """Return the most recent wrong attempts for review."""
    limit = max(1, min(200, limit))
    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        _ensure_tables(cur)
        cur.execute(
            """
            SELECT id, problem, op, user_answer, correct_answer, latency_ms, ts
            FROM math_attempt
            WHERE is_correct = FALSE
            ORDER BY ts DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "problem": r[1],
                "op": r[2],
                "user_answer": r[3],
                "correct_answer": r[4],
                "latency_ms": r[5],
                "ts": r[6].isoformat(),
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(500, f"Failed to load mistakes: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()


@router.delete("/session/{session_id}")
def delete_session(session_id: int):
    conn = None
    cur = None
    try:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM math_session WHERE id = %s", (session_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Session not found")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to delete session: {e}")
    finally:
        if cur: cur.close()
        if conn: conn.close()
