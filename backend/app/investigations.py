"""Saved investigations ("case files") and follow-up context.

An investigation is a thread of turns about one repository. Every /ask turn is
stored with its focus, evidence and answer, so a follow-up ("and who calls
it?") can be understood in context, and the thread can be reopened later with
the exact evidence each answer was built from.
"""

import re
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from app.db import connection

CONTEXT_TURNS = 3  # earlier turns given to the answer writer
_CITATION = re.compile(r"\[S\d+\]")
# Follow-ups that lean on the previous question for their subject.
_ANAPHORA = re.compile(
    r"\b(it|its|this|that|these|those|they|them|their|there|he|she|the same|above|"
    r"why|how come|what about|and)\b", re.IGNORECASE)


@dataclass
class PriorTurn:
    question: str
    answer: str
    focus: dict | None


def create(repo_id: int, user_id: int, title: str) -> int:
    with connection() as conn:
        return conn.execute(
            "INSERT INTO investigations (repo_id, user_id, title) VALUES (%s, %s, %s) "
            "RETURNING id", (repo_id, user_id, _title(title))).fetchone()["id"]


def _title(question: str) -> str:
    q = " ".join(question.split())
    return q if len(q) <= 90 else q[:87].rstrip() + "…"


def get(investigation_id: int, with_turns: bool = True) -> dict | None:
    with connection() as conn:
        inv = conn.execute(
            """SELECT i.id, i.repo_id, i.user_id, i.title, i.created_at, i.updated_at,
                      count(t.id) AS turns
               FROM investigations i LEFT JOIN investigation_turns t
                 ON t.investigation_id = i.id
               WHERE i.id = %s GROUP BY i.id""", (investigation_id,)).fetchone()
        if inv is None or not with_turns:
            return inv
        inv["turns"] = conn.execute(
            """SELECT id, position, question, focus, mode, steps, evidence, answer, answered_by,
                      status, error, created_at
               FROM investigation_turns WHERE investigation_id = %s ORDER BY position""",
            (investigation_id,)).fetchall()
    return inv


def list_for_repo(repo_id: int, user_id: int) -> list[dict]:
    with connection() as conn:
        return conn.execute(
            """SELECT i.id, i.title, i.created_at, i.updated_at, count(t.id) AS turns,
                      (array_agg(t.question ORDER BY t.position DESC))[1] AS last_question
               FROM investigations i LEFT JOIN investigation_turns t
                 ON t.investigation_id = i.id
               WHERE i.repo_id = %s AND i.user_id = %s
               GROUP BY i.id ORDER BY i.updated_at DESC""", (repo_id, user_id)).fetchall()


def rename(investigation_id: int, title: str) -> bool:
    with connection() as conn:
        cur = conn.execute("UPDATE investigations SET title = %s WHERE id = %s",
                           (_title(title), investigation_id))
        return cur.rowcount > 0


def delete(investigation_id: int) -> bool:
    with connection() as conn:
        return conn.execute("DELETE FROM investigations WHERE id = %s",
                            (investigation_id,)).rowcount > 0


def prior_turns(investigation_id: int, limit: int = CONTEXT_TURNS) -> list[PriorTurn]:
    """The last completed turns, oldest first, answers without their citations
    (those refer to the earlier turns' evidence, not the current one)."""
    with connection() as conn:
        rows = conn.execute(
            """SELECT question, answer, focus FROM investigation_turns
               WHERE investigation_id = %s AND status IN ('done', 'stopped') AND answer <> ''
               ORDER BY position DESC LIMIT %s""", (investigation_id, limit)).fetchall()
    return [PriorTurn(r["question"], _CITATION.sub("", r["answer"]).strip(), r["focus"])
            for r in reversed(rows)]


def retrieval_query(question: str, prior: list[PriorTurn]) -> str:
    """What retrieval searches for. A short or referential follow-up ("why?",
    "who calls it?") borrows the previous question's subject; a self-contained
    question is searched as asked."""
    if not prior:
        return question
    if len(question.split()) <= 12 or _ANAPHORA.search(question):
        return f"{question}\n{prior[-1].question}"
    return question


def save_turn(investigation_id: int, question: str, focus: dict | None, mode: str,
              steps: list[dict], evidence: list[dict], answer: str,
              answered_by: dict | None, status: str, error: str | None) -> int:
    with connection() as conn, conn.transaction():
        # Row lock on the investigation serialises concurrent turns' positions.
        conn.execute("SELECT id FROM investigations WHERE id = %s FOR UPDATE",
                     (investigation_id,))
        position = conn.execute(
            "SELECT coalesce(max(position), 0) + 1 AS p FROM investigation_turns "
            "WHERE investigation_id = %s", (investigation_id,)).fetchone()["p"]
        conn.execute(
            """INSERT INTO investigation_turns (investigation_id, position, question, focus,
                   mode, steps, evidence, answer, answered_by, status, error)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (investigation_id, position, question, Jsonb(focus) if focus else None, mode,
             Jsonb(steps), Jsonb(evidence), answer, Jsonb(answered_by) if answered_by else None,
             status, error))
        conn.execute("UPDATE investigations SET updated_at = now() WHERE id = %s",
                     (investigation_id,))
    return position
