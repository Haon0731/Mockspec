"""Run-owned investigation questions and their evidence/stop consistency rules."""

from __future__ import annotations

from copy import deepcopy

from mock_spec.schemas import UnifiedTask
from mock_spec.validation import SourceStore
from stage_protocol import (
    ExplorationStop, InvestigationRequest, QuestionUpdate, materialize_reference,
)


class InvestigationLedger:
    """Own question identity for one run; expose detached snapshots to its stages.

    Goal count and repository capability are fixed at construction. Updates must
    cite observations from completed query receipts; callers cannot mutate the
    ledger by editing a previously delivered snapshot. These are consistency
    checks, not a proof that the model found every material question.
    """

    def __init__(self, goal_count: int, *, repository_available: bool) -> None:
        self._goal_count = goal_count
        self._repository_available = repository_available
        self._questions: dict[str, dict] = {}

    def snapshot(self) -> list[dict]:
        """Return the current insertion-ordered question records, detached from state."""
        return deepcopy(list(self._questions.values()))

    def register(self, requests: list[InvestigationRequest], *, origin: str) -> None:
        """Register new questions or reopen them without losing their association."""
        questions = self._questions
        goal_count = self._goal_count
        names = [request.question.strip().casefold() for request in requests]
        if len(set(names)) != len(names):
            raise ValueError("duplicate investigation question in one continuation")
        for request in requests:
            if not set(request.goal_indexes) <= set(range(goal_count)):
                raise ValueError("investigation references an unknown original goal")
            existing = next((key for key, item in questions.items()
                             if item["question"].strip().casefold() == request.question.strip().casefold()), None)
            key = existing or f"q{len(questions) + 1}"
            previous = questions.get(key)
            if previous is not None and (
                previous["question"] != request.question
                or set(previous["goal_indexes"]) != set(request.goal_indexes)
                or previous["impact"] != request.impact
            ):
                raise ValueError("reopening a question cannot rebind its original goals or impact")
            # Reopening is explicit and retained in the stage history; it cannot
            # inherit a previous nonblocking assessment after new counterevidence.
            questions[key] = {
                **request.model_dump(mode="json"), "question_id": key,
                "state": "open", "rationale": f"Unresolved evidence gap from {origin}.",
                "evidence": [] if previous is None else previous["evidence"], "acquisition": "available",
                "attempted_sequences": [] if previous is None else previous["attempted_sequences"],
                "origin": origin,
            }


    def apply_updates(
        self, updates: list[QuestionUpdate], *, store: SourceStore,
        attempts: list[dict], task: UnifiedTask,
    ) -> None:
        """Apply assessments against the original goals and completed observations."""
        questions = self._questions
        goal_count = self._goal_count
        repository_available = self._repository_available
        seen: set[str] = set()
        for update in updates:
            key = update.question_id or f"q{len(questions) + 1}"
            if key in seen:
                raise ValueError("duplicate question update")
            seen.add(key)
            if update.question_id is not None and key not in questions:
                raise ValueError("question update references an unknown question")
            if update.question_id is None and any(
                item["question"].strip().casefold() == update.question.strip().casefold()
                for item in questions.values()
            ):
                raise ValueError("duplicate question must use its existing host question ID")
            if key in questions and questions[key]["question"] != update.question:
                raise ValueError("cannot replace an outstanding question by renaming it")
            if key in questions and set(questions[key]["goal_indexes"]) != set(update.goal_indexes):
                raise ValueError("question update cannot remove or rebind original goals")
            if not set(update.goal_indexes) <= set(range(goal_count)):
                raise ValueError("question must reference original goals")
            for locator in update.evidence:
                materialize_reference(locator, store, field_path=f"questions.{key}.evidence")
            known_attempts = {item["sequence"] for item in attempts
                              if item["status"] != "registered" and item["result"] is not None}
            if not set(update.attempted_sequences) <= known_attempts:
                raise ValueError("question references an unknown query attempt")
            associated_responses = {item["response_id"] for item in attempts
                                    if item["sequence"] in update.attempted_sequences}
            observations = store.observations()
            for locator in update.evidence:
                if locator.source_id in {source.source_id for source in (
                    task.mut_source, task.masked_test, *task.supporting_sources
                )}:
                    continue
                covered: set[int] = set()
                for item in observations:
                    if (item["source_id"] == locator.source_id
                            and item.get("response_id") in associated_responses
                            and isinstance(item["start_line"], int)
                            and isinstance(item["end_line"], int)):
                        covered.update(range(max(locator.start_line, item["start_line"]),
                                             min(locator.end_line, item["end_line"]) + 1))
                if not set(range(locator.start_line, locator.end_line + 1)) <= covered:
                    raise ValueError("question evidence must be associated with its observed query receipt")
            if update.state in {"resolved", "nonblocking"} and not update.evidence:
                raise ValueError("closing a question requires observed supporting evidence")
            if update.state == "nonblocking" and update.acquisition != "not_needed":
                raise ValueError("nonblocking question must explain why acquisition is not needed")
            if update.state == "open" and update.acquisition == "not_needed":
                raise ValueError("open material question cannot claim acquisition is unnecessary")
            if (update.state == "open" and update.acquisition == "unavailable"
                    and repository_available and not update.attempted_sequences):
                raise ValueError("repository evidence unavailable requires an actual attempted route")
            if (update.state == "open" and update.acquisition == "unavailable"
                    and not update.attempted_sequences and not update.evidence):
                raise ValueError("initial-only access limitation requires evidence of the affected behavior")
            questions[key] = {
                **update.model_dump(mode="json"), "question_id": key,
                "origin": questions.get(key, {}).get("origin", "QUERY"),
            }


    def validate_stop(self, stop: ExplorationStop) -> None:
        """Reject disappearing questions or a stop with accessible material evidence."""
        questions = self._questions
        remaining = {item.question: item for item in stop.remaining_unknowns}
        expected = {item["question"]: item for item in questions.values()
                    if item["state"] != "resolved"}
        if set(remaining) != set(expected) or len(remaining) != len(stop.remaining_unknowns):
            raise ValueError("stop must preserve every unresolved original-goal question")
        for question, state in expected.items():
            unknown = remaining[question]
            if unknown.blocking != (state["state"] == "open") or unknown.impact != state["impact"]:
                raise ValueError("stop unknown impact/status differs from the question ledger")
            if state["state"] == "open" and state["acquisition"] == "available":
                raise ValueError("material evidence remains accessible; continue investigation")
        if stop.reason in {"evidence_sufficient", "unknowns_nonblocking"} and any(
            item["state"] == "open" for item in questions.values()
        ):
            raise ValueError("unresolved original-goal question prevents a sufficient stop")
