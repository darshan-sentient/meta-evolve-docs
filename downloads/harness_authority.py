"""Measure permitted mistakes and refuse a changed harness declaration."""

# --8<-- [start:setup]
from dataclasses import replace

import meta_evolve as meta
from meta_evolve import harness
from meta_evolve.domain import ComponentRef

HELP_NOTES = (
    ("reset password", "Open Settings > Password."),
    ("download invoice", "Open Billing > Invoices."),
)
HELP_CASES = (
    ("reset password", "Open Settings > Password."),
    ("please reset password", "Open Settings > Password."),
    ("download invoice", "Open Billing > Invoices."),
)


def answer(question, configuration):
    instruction = configuration.planner.value
    if instruction not in ("Match exact questions.", "Match topic words."):
        raise ValueError("Unsupported matching instruction")
    question = question.lower()
    for topic, reply in HELP_NOTES:
        if instruction == "Match exact questions.":
            matches = question == topic
        else:
            matches = set(topic.split()) <= set(question.split())
        if matches:
            return reply
    return "I don't have a matching help note."
# --8<-- [end:setup]


# --8<-- [start:evaluate]
HARNESS_SEED = harness.HarnessArtifact(
    planner=harness.TextParam(
        "planner", ComponentRef("planner", "help-matcher", "1"),
        "matching-instruction/v1", "Match exact questions.",
    ),
    context_policy=harness.PolicyParam(
        "context_policy", ComponentRef("context-policy", "fixed-notes", "1"),
        "help-context/v1", {"include": ("help-notes",)},
    ),
)


def evaluate_harness(configuration):
    checks = []
    for question, expected in HELP_CASES:
        actual = answer(question, configuration)
        checks.append({"question": question, "expected": expected,
                       "actual": actual, "passed": actual == expected})
    return meta.EvaluationResult(
        metrics={"score": sum(check["passed"] for check in checks) / len(checks)},
        evidence=(meta.EvidenceDraft(kind="help-checks", data={"checks": checks}),),
    )
# --8<-- [end:evaluate]


# --8<-- [start:propose]
def allowed_improvement(parent):
    return parent.apply(harness.HarnessMutation("planner", "Match topic words."))


def allowed_regression(parent):
    return parent.apply(harness.HarnessMutation("planner", "Match exact questions."))


def redefine_checks(parent):
    return replace(parent, planner=replace(
        parent.planner, name="evaluation_checks", value="accept every answer",
    ))


def propose_harness(parent):
    return next(proposal_steps)(parent)


def show_outcome(label, trial):
    work = f"attempts +{trial.usage.trials}, evaluations +{trial.usage.evaluations}"
    if trial.failure:
        print(f"{label}: {trial.failure.kind}; {work}")
        print("Refusal record: authority_result.trials()[-1]")
        print("Reason:", trial.failure.message, dict(trial.failure.details))
    else:
        checks = trial.evidence[0].data["checks"]
        print(f"{label}: {sum(c['passed'] for c in checks)}/{len(checks)} checks; {work}")
        check = checks[1]
        print(check["question"], "->", check["actual"], check["passed"])
# --8<-- [end:propose]


# --8<-- [start:run]
proposal_steps = iter((allowed_improvement, allowed_regression, redefine_checks))
authority_task = meta.Task(
    artifact=harness.HarnessArtifact, evaluator=evaluate_harness,
    objectives=(meta.Maximize("score"),), budget=meta.Budget(trials=3, evaluations=4),
)
authority_experiment = meta.Experiment(
    task=authority_task, seed=HARNESS_SEED, proposer=propose_harness,
    search=meta.Greedy(max_trials=3),
)
authority_result = meta.run(authority_experiment)
for label, trial in zip(("Starting", "Improvement", "Regression", "Attempt 3"),
                        authority_result.trials()):
    show_outcome(label, trial)
selected_harness = authority_result.best().value
print("Original declaration:", HARNESS_SEED.planner.name)
print("Protected polite check:", HELP_CASES[1])
print("Selected instruction:", selected_harness.planner.value)
work = authority_result.usage()
print("Total:", work.trials, "attempts;", work.evaluations, "evaluations")
# Output:
# Starting: 2/3 checks; attempts +0, evaluations +1
# please reset password -> I don't have a matching help note. False
# Improvement: 3/3 checks; attempts +1, evaluations +1
# please reset password -> Open Settings > Password. True
# Regression: 2/3 checks; attempts +1, evaluations +1
# please reset password -> I don't have a matching help note. False
# Attempt 3: policy_violation; attempts +1, evaluations +0
# Refusal record: authority_result.trials()[-1]
# Reason: harness successor expands mutation authority {'changed_declarations': ('planner.name',)}
# Original declaration: planner
# Protected polite check: ('please reset password', 'Open Settings > Password.')
# Selected instruction: Match topic words.
# Total: 3 attempts; 3 evaluations
# --8<-- [end:run]
