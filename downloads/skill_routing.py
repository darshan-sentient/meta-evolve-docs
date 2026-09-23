"""Improve a skill from recorded mistakes, using a fixed simulated responder."""

# --8<-- [start:setup]
import meta_evolve as meta

# Start with a short instruction document; the name does not create a file.
SKILL_SEED = {
    "routing.md": "Route invoices to billing, passwords to account, otherwise technical.",
}
# --8<-- [start:tickets]
# Keep these expected teams fixed while we revise the instructions.
TICKETS = (
    ("Please send my invoice", "billing"),
    ("I need a refund", "billing"),
    ("I forgot my password", "account"),
    ("My login is blocked", "account"),
    ("The dashboard freezes", "technical"),
    ("Export is broken", "technical"),
)
# --8<-- [end:tickets]


# --8<-- [start:responder]
# This small simulation stands in for the agent that reads the instructions.
def routing_response(instructions, ticket):
    text = ticket.lower()
    # The simulated agent already handles these two topics.
    if "invoice" in text:
        return "billing"
    if "password" in text:
        return "account"
    # These topics need a matching word in the instruction document.
    if "refund" in instructions and "refund" in text:
        return "billing"
    if "login" in instructions and "login" in text:
        return "account"
    return "technical"  # Send anything else to the technical team.
# --8<-- [end:responder]
# --8<-- [end:setup]


# --8<-- [start:evaluate]
def evaluate_skill(skills):
    misses = []
    # Try the current document on every ticket.
    for ticket, expected in TICKETS:
        actual = routing_response(skills["routing.md"], ticket)
        if actual != expected:
            # Save the mistake so the next revision has something to fix.
            misses.append({"ticket": ticket, "expected": expected, "actual": actual})
    # Report both the fraction correct and the mistakes behind that score.
    return meta.EvaluationResult(
        metrics={"score": (len(TICKETS) - len(misses)) / len(TICKETS)},
        evidence=(meta.EvidenceDraft(kind="routing-misses", data={"misses": misses}),),
    )
# --8<-- [end:evaluate]


# --8<-- [start:propose]
def revise_skill(skills, *, context):
    # Read the latest mistakes shared with this revision step.
    feedback = context.evidence.latest("routing-misses")
    if feedback is None:
        raise ValueError("No routing feedback was selected.")
    misses = feedback.data["misses"]
    if not misses:
        return skills  # No mistakes to fix.
    # Turn one missed ticket into an extra instruction.
    miss = misses[0]
    rule = f"Route requests like {miss['ticket']!r} to {miss['expected']}."
    # Build a new version, leaving the previous document intact.
    return {**skills, "routing.md": skills["routing.md"] + "\n" + rule}
# --8<-- [end:propose]


# --8<-- [start:run]
# Test the starting skill, then repeat: revise, test, and keep the best.
skill_result = meta.improve(
    seed=SKILL_SEED,
    proposer=revise_skill,
    evaluator=evaluate_skill,
    trials=2,  # Allow two revisions after checking the starting document.
    context=meta.RecentAncestors(),  # Share recent results with revise_skill.
)

# Follow each version and see which tickets it still gets wrong.
for number, version in enumerate(skill_result.trials()):
    missed = [case["ticket"] for case in version.evidence[0].data["misses"]]
    print(f"Version {number}: {version.metrics['score']:.0%}; misrouted: {missed}")
# Read the selected document and use it for another ticket.
selected_skill = skill_result.best().value
print("Selected routing.md:")
print(selected_skill["routing.md"])
print("Refund please:", routing_response(selected_skill["routing.md"], "Refund please"))
# Output:
# Version 0: 67%; misrouted: ['I need a refund', 'My login is blocked']
# Version 1: 83%; misrouted: ['My login is blocked']
# Version 2: 100%; misrouted: []
# Selected routing.md:
# Route invoices to billing, passwords to account, otherwise technical.
# Route requests like 'I need a refund' to billing.
# Route requests like 'My login is blocked' to account.
# Refund please: billing
# --8<-- [end:run]
