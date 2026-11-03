"""Model calls: all prompt building and Claude API access lives here.

Two steps, each with its own system prompt:
  * SqlGenerator turns a question into a QueryPlan (action, SQL, explanation,
    assumptions, or a clarifying question / reason). It can repair its SQL after an
    error, continuing the same conversation.
  * AnswerWriter turns the query result into a short plain-English answer.

Every failure is raised as LLMError with a message that is safe to show the user.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import anthropic

from . import config

Action = Literal["query", "clarify", "cannot_answer"]

# Structured output schema for the SQL step. Every field is required; unused ones are
# empty, which keeps the schema simple for the model and for our parsing.
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["query", "clarify", "cannot_answer"]},
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action", "sql", "explanation", "assumptions", "question", "reason"],
    "additionalProperties": False,
}

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """A model call failed. `user_message` is safe to show; `detail` is technical."""

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


class ModelRefusal(LLMError):
    """The model declined the request (stop_reason == "refusal")."""


@dataclass
class QueryPlan:
    action: Action
    sql: str = ""
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    question: str = ""          # clarifying question
    reason: str = ""            # why it can't be answered
    # The conversation so far, so a repair request can continue it.
    messages: list[dict[str, Any]] = field(default_factory=list, repr=False)


def _escape(text: str) -> str:
    """Stop user text from closing our XML-style tags."""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _load_prompt(path, **values) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("<!--"):                     # drop the maintainer comment
        text = text.split("-->", 1)[1].lstrip()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


class _ClaudeStep:
    """Shared request logic: caching, effort, refusal fallback, error mapping."""

    def __init__(self, client: anthropic.Anthropic, system_prompt: str, effort: str,
                 max_tokens: int, model: str = config.MODEL):
        self._client = client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        # One cached block: the prompt never changes between requests.
        self._system = [{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}]

    def _call(self, messages, output_format=None):
        output_config: dict[str, Any] = {"effort": self._effort}
        if output_format:
            output_config["format"] = output_format
        kwargs: dict[str, Any] = dict(model=self._model, max_tokens=self._max_tokens,
                                      system=self._system, messages=messages,
                                      output_config=output_config)
        try:
            if config.USE_FALLBACKS:
                response = self._client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                response = self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMError("The AI service rejected the API key. Check ANTHROPIC_API_KEY.",
                           str(e)) from e
        except anthropic.RateLimitError as e:
            raise LLMError("The AI service is busy right now. Please try again in a minute.",
                           str(e)) from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Couldn't reach the AI service. Check your internet connection.",
                           str(e)) from e
        except anthropic.APIStatusError as e:
            raise LLMError("The AI service returned an error. Please try again.",
                           f"{e.status_code}: {e.message}") from e

        if response.stop_reason == "refusal":
            raise ModelRefusal("I can't help with that request.",
                               str(getattr(response, "stop_details", "")))
        if response.stop_reason == "max_tokens":
            raise LLMError("The AI's response was cut off. Try a simpler question.",
                           "stop_reason=max_tokens")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return response, text


class SqlGenerator(_ClaudeStep):
    def __init__(self, client: anthropic.Anthropic):
        schema = config.VIEWS_SQL_PATH.read_text(encoding="utf-8").strip()
        super().__init__(client, _load_prompt(config.SQL_PROMPT_PATH, schema=schema),
                         effort=config.SQL_EFFORT, max_tokens=config.SQL_MAX_TOKENS)

    def plan(self, question: str, today: date) -> QueryPlan:
        user = (f"<today>{today.isoformat()}</today>\n"
                f"<question>{_escape(question)}</question>")
        return self._request([{"role": "user", "content": user}])

    def repair(self, previous: QueryPlan, error: str) -> QueryPlan:
        """Ask for corrected SQL, continuing the conversation that produced it."""
        note = (f"Running that SQL failed with this error:\n<error>{_escape(error)}</error>\n"
                "Return the full JSON object again with corrected SQL. Use only the views "
                "and columns in the schema.")
        return self._request(previous.messages + [{"role": "user", "content": note}])

    def _request(self, messages) -> QueryPlan:
        response, text = self._call(
            messages, output_format={"type": "json_schema", "schema": PLAN_SCHEMA})
        try:
            data = json.loads(text)
            plan = QueryPlan(**{k: data[k] for k in PLAN_SCHEMA["required"]})
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise LLMError("The AI returned a response I couldn't read. Please try again.",
                           f"{e}: {text[:500]}") from e
        if plan.action == "query" and not plan.sql.strip():
            raise LLMError("The AI didn't produce a query. Please rephrase the question.",
                           "action=query with empty sql")
        # Pass the assistant content back unchanged (thinking blocks included).
        plan.messages = messages + [{"role": "assistant", "content": response.content}]
        return plan


class AnswerWriter(_ClaudeStep):
    def __init__(self, client: anthropic.Anthropic):
        super().__init__(client, _load_prompt(config.ANSWER_PROMPT_PATH),
                         effort=config.ANSWER_EFFORT, max_tokens=config.ANSWER_MAX_TOKENS)

    def write(self, question: str, plan: QueryPlan, columns: list[str], rows: list[tuple],
              truncated: bool) -> str:
        shown = rows[:config.ANSWER_ROWS]
        result = {
            "columns": columns,
            "rows": [list(r) for r in shown],
            "row_count": len(rows),
            "cut_off": truncated or len(rows) > len(shown),
        }
        user = (f"<question>{_escape(question)}</question>\n"
                f"<assumptions>{_escape(json.dumps(plan.assumptions))}</assumptions>\n"
                f"<result>{_escape(json.dumps(result, default=str))}</result>")
        _, text = self._call([{"role": "user", "content": user}])
        if not text:
            raise LLMError("The AI returned an empty answer. The data is shown below.",
                           "empty answer text")
        return text
