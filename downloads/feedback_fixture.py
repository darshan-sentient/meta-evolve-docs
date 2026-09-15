"""Run the existing five-session protocol with authored, model-free responses."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path("examples/research/sealqa_labs").resolve()))

from sealqa_labs.feedback_descent import execute
from sealqa_labs.fixture import evaluator_case
from sealqa_labs.models import SessionRecord


class ScriptedRunner:
    """A protocol fixture, not a solver or a model-performance measurement."""

    def __init__(self):
        answer = evaluator_case("feedback_descent_development").answer
        self.responses = {
            "feedback.candidate_a": {"answer": "wrong"},
            "feedback.candidate_b": {"answer": answer},
            "feedback.comparison": {
                "preferred_candidate": "candidate_b",
                "textual_feedback": "Keep direct evidence and require exact brevity.",
            },
            "feedback.editor": {"prompt": "Use direct evidence; return one exact answer."},
            "feedback.edited_evaluation": {"answer": answer},
        }

    def run(self, request):
        return SessionRecord(label=request.label, value=self.responses[request.label])


def main():
    result = execute(ScriptedRunner())
    print("sessions:", len(result.sessions))  # sessions: 5
    print("preferred:", result.artifacts["preferred"])  # preferred: candidate_b
    print("derived from:", result.artifacts["edit_derived_from"])
    # derived from: feedback.comparison
    print("edited:", result.measurements["edited"].metrics)  # edited: {'exact': 1.0}
    print("prompt:", result.artifacts["edited"])
    # prompt: Use direct evidence; return one exact answer.


if __name__ == "__main__":
    main()
