"""Local Laya inference; policy retains ownership of thresholds and actions."""
from threading import Lock

from .evaluators import EvaluationError, assessment
from .jev import violation_questions


class LayaEvaluator:
    name = "laya"

    def __init__(self, settings):
        self.model = settings.laya_model
        self.device = settings.laya_device
        self._agent = None
        self._lock = Lock()

    def assess_targets(self, messages, rules, target_start):
        return self.assess(messages, rules, target_start)

    def assess(self, messages, rules, target_start=0):
        questions = violation_questions(messages, rules, target_start)
        if not questions:
            return [[] for _ in messages], {}
        try:
            # Lazy import/loading keeps regex-only and deterministic use lightweight.
            # Serialize access because the model runtime may reuse inference buffers.
            with self._lock:
                if self._agent is None:
                    import laya
                    self._agent = laya.load(self.model, device=self.device)
                answers = self._agent.predict({"messages": messages}, questions)["answers"]
                probabilities = {}
                for name in questions:
                    answer = answers[name]
                    if answer["type"] != "noul":
                        raise ValueError("Invalid answer type")
                    probabilities[name] = answer["noul"]
                return assessment(messages, rules, probabilities, target_start)
        except Exception:
            # Never expose content or fall back to remote assessment.
            raise EvaluationError("laya_unavailable") from None
