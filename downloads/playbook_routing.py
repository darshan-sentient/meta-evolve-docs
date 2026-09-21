"""Improve named routing rules from mistakes, using a fixed simulated responder."""

# --8<-- [start:setup]
import meta_evolve as meta

# Give each rule a name so we can change it without replacing the others.
PLAYBOOK_SEED = {
    "billing": "Route invoices to billing.",
    "account": "Route passwords to account.",
    "technical": "Route everything else to technical.",
}
# These expected teams stay fixed while the instructions change.
TICKETS = (
    ("Please send my invoice", "billing"),
    ("I need a refund", "billing"),
    ("I forgot my password", "account"),
    ("My login is blocked", "account"),
    ("The dashboard freezes", "technical"),
    ("Export is broken", "technical"),
)


def routing_response(instructions, ticket):
    # A simulation: refunds and login need matching words in the instructions.
    text = ticket.lower()
    if "invoice" in text:
        return "billing"
    if "password" in text:
        return "account"
    if "refund" in instructions and "refund" in text:
        return "billing"
    if "login" in instructions and "login" in text:
        return "account"
    return "technical"
# --8<-- [end:setup]


# --8<-- [start:evaluate]
def evaluate_playbook(playbook):
    instructions = "\n".join(playbook.values())  # The assistant reads all the rules.
    misses = []
    for ticket, expected in TICKETS:
        actual = routing_response(instructions, ticket)
        if actual != expected:
            misses.append({"ticket": ticket, "expected": expected, "actual": actual})
    # Keep the wrong answers as well as the fraction correct.
    return meta.EvaluationResult(
        metrics={"score": (len(TICKETS) - len(misses)) / len(TICKETS)},
        evidence=(meta.EvidenceDraft(kind="routing-misses", data={"misses": misses}),),
    )
# --8<-- [end:evaluate]


# --8<-- [start:propose]
def revise_playbook(playbook, *, context):
    feedback = context.evidence.latest("routing-misses")
    if feedback is None:
        raise ValueError("No routing feedback was selected.")
    misses = feedback.data["misses"]
    if not misses:
        return playbook  # Nothing to fix.
    miss = misses[0]  # Use one recorded mistake to choose the next change.
    team = miss["expected"]
    correction = f"Route requests like {miss['ticket']!r} to {team}."
    # Return a new version with just this team's rule amended.
    return {**playbook, team: playbook[team] + " " + correction}
# --8<-- [end:propose]


# --8<-- [start:run]
playbook_result = meta.improve(
    seed=PLAYBOOK_SEED, proposer=revise_playbook, evaluator=evaluate_playbook,
    trials=2,  # Two revisions after checking the starting rules.
    context=meta.RecentAncestors(),  # Share recent mistakes with the proposer.
)

# Compare each version with the one before it.
previous = PLAYBOOK_SEED
for number, version in enumerate(playbook_result.trials()):
    rules = version.artifact.value
    missed = [case["ticket"] for case in version.evidence[0].data["misses"]]
    print(f"Version {number}: {version.metrics['score']:.0%}; misrouted: {missed}")
    for name, text in rules.items():
        if text != previous[name]:
            print(f"  {name}: {previous[name]}\n    -> {text}")
    previous = rules

# Keep these rules to use with your assistant.
selected_playbook = playbook_result.best().value
print("Selected playbook:")
for name, text in selected_playbook.items():
    print(f"  {name}: {text}")
# Output:
# Version 0: 67%; misrouted: ['I need a refund', 'My login is blocked']
# Version 1: 83%; misrouted: ['My login is blocked']
#   billing: Route invoices to billing.
#     -> Route invoices to billing. Route requests like 'I need a refund' to billing.
# Version 2: 100%; misrouted: []
#   account: Route passwords to account.
#     -> Route passwords to account. Route requests like 'My login is blocked' to account.
# Selected playbook:
#   account: Route passwords to account. Route requests like 'My login is blocked' to account.
#   billing: Route invoices to billing. Route requests like 'I need a refund' to billing.
#   technical: Route everything else to technical.
# --8<-- [end:run]
