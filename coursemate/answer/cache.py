"""SQLite 本地题库缓存。

存在的理由很实际：同一门课的弹题在不同章节、不同次播放中高度重复。
没有缓存，每刷一遍课就要为同样的题重复付一次 AI 调用费用；
有了缓存，第二遍几乎零成本，而且离线也能答。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .base import AnswerResult, Question

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    fingerprint TEXT PRIMARY KEY,
    stem        TEXT NOT NULL,
    qtype       TEXT NOT NULL,
    option_keys TEXT NOT NULL,
    option_texts TEXT NOT NULL,
    answer_text TEXT NOT NULL DEFAULT '',
    confidence  REAL NOT NULL DEFAULT 0,
    reasoning   TEXT NOT NULL DEFAULT '',
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_answers_stem ON answers(stem);
"""


class AnswerCache:
    def __init__(self, path: str | Path | None = None, enabled: bool = True):
        from ..paths import app_dir

        self.enabled = enabled
        self.path = Path(path) if path else app_dir() / "runtime" / "answers.db"
        self._conn: sqlite3.Connection | None = None
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, question: Question) -> AnswerResult | None:
        if not self._conn:
            return None
        row = self._conn.execute(
            "SELECT option_keys, option_texts, answer_text, confidence, reasoning "
            "FROM answers WHERE fingerprint = ?",
            (question.fingerprint,),
        ).fetchone()
        if not row:
            return None
        self._conn.execute(
            "UPDATE answers SET hits = hits + 1 WHERE fingerprint = ?",
            (question.fingerprint,),
        )
        self._conn.commit()
        keys, texts, answer_text, confidence, reasoning = row
        return AnswerResult(
            option_keys=json.loads(keys),
            option_texts=json.loads(texts),
            text=answer_text,
            confidence=float(confidence),
            reasoning=reasoning,
            source="cache",
        )

    def put(self, question: Question, result: AnswerResult) -> None:
        # 空答案不入库：否则一次网络故障会被永久缓存成"这题答不出来"
        if not self._conn or result.empty:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO answers "
            "(fingerprint, stem, qtype, option_keys, option_texts, answer_text, "
            " confidence, reasoning, hits) "
            "VALUES (?,?,?,?,?,?,?,?, COALESCE((SELECT hits FROM answers WHERE fingerprint=?),0))",
            (
                question.fingerprint,
                question.stem[:500],
                question.qtype,
                json.dumps(result.option_keys, ensure_ascii=False),
                json.dumps(result.option_texts, ensure_ascii=False),
                result.text,
                result.confidence,
                result.reasoning[:500],
                question.fingerprint,
            ),
        )
        self._conn.commit()

    def stats(self) -> tuple[int, int]:
        """返回 (题目总数, 累计命中次数)。"""
        if not self._conn:
            return 0, 0
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM answers"
        ).fetchone()
        return int(row[0]), int(row[1])

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
