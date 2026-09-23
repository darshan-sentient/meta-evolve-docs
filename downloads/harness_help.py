"""Improve a help assistant's matching instruction through its actual answers."""

# --8<-- [start:setup]
import meta_evolve as meta

HARNESS_SEED = {"planner": "Match exact questions."}
HELP_NOTES = (
    ("reset password", "Open Settings > Password."),
    ("download invoice", "Open Billing > Invoices."),
)


def answer(question, configuration):
    instruction = configuration["planner"]
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


# These expected answers stay fixed while the instruction changes.
HELP_CASES = (
    ("reset password", "Open Settings > Password."),
    ("please reset password", "Open Settings > Password."),
    ("download invoice", "Open Billing > Invoices."),
)
# --8<-- [end:setup]


# --8<-- [start:evaluate]
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
def propose_harness(parent):
    # Change the instruction, preserving the previous configuration.
    return {**parent, "planner": "Match topic words."}
# --8<-- [end:propose]


# --8<-- [start:run]
harness_result = meta.improve(
    seed=HARNESS_SEED,
    proposer=propose_harness,
    evaluator=evaluate_harness,
    trials=1,
)
for label, version in (("Starting", harness_result.trials()[0]),
                       ("Selected", harness_result.best_trial())):
    checks = version.evidence[0].data["checks"]
    print(f"{label}: {sum(check['passed'] for check in checks)}/{len(checks)} checks")
    for check in checks:
        print(check["question"], "->", check["actual"])

selected_harness = harness_result.best().value
print("Selected configuration:", dict(selected_harness))
print("Attempts:", harness_result.usage().trials)
print("Evaluations:", harness_result.usage().evaluations)
print("Reuse:", answer("please download my invoice", selected_harness))
# Output:
# Starting: 2/3 checks
# reset password -> Open Settings > Password.
# please reset password -> I don't have a matching help note.
# download invoice -> Open Billing > Invoices.
# Selected: 3/3 checks
# reset password -> Open Settings > Password.
# please reset password -> Open Settings > Password.
# download invoice -> Open Billing > Invoices.
# Selected configuration: {'planner': 'Match topic words.'}
# Attempts: 1
# Evaluations: 2
# Reuse: Open Billing > Invoices.
# --8<-- [end:run]
