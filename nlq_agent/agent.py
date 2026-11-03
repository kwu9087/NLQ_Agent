"""The question-to-answer flow. No prompt text, SQL rules or display code here.

    question
      -> SqlGenerator.plan()        query / clarify / cannot_answer
      -> Database.run()             checked, read-only, limited
         on an SQL error: SqlGenerator.repair() and run again (once)
      -> AnswerWriter.write()       plain-English answer from the rows
      -> AgentResult                everything the interface needs to display

Every outcome, including failures, comes back as an AgentResult. ask() never raises.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional

import anthropic

from . import config
from .database import Database, QueryTimeoutError, UnsafeQueryError
from .llm import AnswerWriter, LLMError, ModelRefusal, QueryPlan, SqlGenerator

log = logging.getLogger(__name__)

Status = Literal["answered", "no_data", "clarify", "cannot_answer", "error"]


@dataclass
class AgentResult:
    question: str
    status: Status
    answer: str = ""                     # answered / no_data
    explanation: str = ""                # how the result was calculated
    assumptions: list[str] = field(default_factory=list)
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False
    clarifying_question: str = ""        # clarify
    reason: str = ""                     # cannot_answer
    error: str = ""                      # error: safe to show the user
    error_detail: str = ""               # error: technical, shown folded away
    attempts: int = 0                    # SQL attempts made


class Agent:
    def __init__(self, database: Database, generator: SqlGenerator, writer: AnswerWriter):
        self.db = database
        self.generator = generator
        self.writer = writer

    @classmethod
    def create(cls) -> "Agent":
        """Build the agent with real dependencies (API key read from the environment)."""
        client = anthropic.Anthropic()
        return cls(Database(), SqlGenerator(client), AnswerWriter(client))

    def ask(self, question: str, today: Optional[date] = None) -> AgentResult:
        question = question.strip()
        if not question:
            return AgentResult(question, "clarify",
                               clarifying_question="What would you like to know about ticket sales?")
        try:
            return self._ask(question, today or config.today())
        except LLMError as e:
            if isinstance(e, ModelRefusal):
                return AgentResult(question, "cannot_answer", reason=e.user_message)
            log.warning("Model call failed: %s", e.detail)
            return AgentResult(question, "error", error=e.user_message, error_detail=e.detail)
        except Exception as e:  # last resort: never crash the interface
            log.exception("Unexpected error")
            return AgentResult(question, "error", error="Something went wrong. Please try again.",
                               error_detail=f"{type(e).__name__}: {e}")

    def _ask(self, question: str, today: date) -> AgentResult:
        plan = self.generator.plan(question, today)
        if plan.action != "query":
            return self._non_query_result(question, plan)

        result = AgentResult(question, "error")
        for attempt in range(1, config.MAX_SQL_ATTEMPTS + 1):
            self._copy_plan(plan, result)
            result.attempts = attempt
            try:
                rows = self.db.run(plan.sql)
                break
            except UnsafeQueryError as e:
                # The model wrote something other than one SELECT. Don't retry.
                result.error = "That request would need more than reading the data, so it wasn't run."
                result.error_detail = str(e)
                return result
            except QueryTimeoutError as e:
                result.error = "The question took too long to answer. Try narrowing it down, e.g. to one year or one category."
                result.error_detail = str(e)
                return result
            except sqlite3.Error as e:
                log.info("SQL attempt %d failed: %s", attempt, e)
                result.error_detail = f"{type(e).__name__}: {e}"
                if attempt == config.MAX_SQL_ATTEMPTS:
                    result.error = "I couldn't build a working query for that question. Try rephrasing it."
                    return result
                plan = self.generator.repair(plan, str(e))
                if plan.action != "query":       # the repair decided it can't be done
                    return self._non_query_result(question, plan)
        result.columns, result.rows, result.truncated = rows.columns, rows.rows, rows.truncated
        result.error_detail = ""
        result.status = "no_data" if rows.is_empty else "answered"
        try:
            result.answer = self.writer.write(question, plan, rows.columns, rows.rows,
                                              rows.truncated)
        except LLMError as e:
            # The data is still worth showing even if the summary failed.
            result.answer = e.user_message
            result.error_detail = e.detail
        return result

    @staticmethod
    def _copy_plan(plan: QueryPlan, result: AgentResult) -> None:
        result.sql, result.explanation, result.assumptions = (
            plan.sql, plan.explanation, plan.assumptions)

    @staticmethod
    def _non_query_result(question: str, plan: QueryPlan) -> AgentResult:
        if plan.action == "clarify":
            return AgentResult(question, "clarify", clarifying_question=plan.question)
        return AgentResult(question, "cannot_answer", reason=plan.reason)
