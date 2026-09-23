"""Pinned public OfficeQA sample, external grading, and native skill folders."""

import csv
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.request import urlopen

import meta_evolve as meta

DEVELOPMENT = ("UID0003", "UID0012", "UID0045")
HELD_OUT = ("UID0001", "UID0002", "UID0006")
SKILLS = ".claude/skills"
CLARIFICATIONS = {
    "UID0003": "Use Table 2, Expenditures by Major Classifications, in the February 1954 Treasury Bulletin.",
}


@dataclass(frozen=True)
class OfficeQA:
    root: Path
    corpus: Path
    builder: Path
    rows: dict
    score_answer: object
    identity: dict

    def cases(self, split):
        ids = {"development": DEVELOPMENT, "held_out": HELD_OUT}[split]
        return tuple(self.rows[uid] for uid in ids)


def verified_download(item, destination):
    destination = Path(destination)
    if destination.exists():
        content = destination.read_bytes()
    else:
        with urlopen(item["url"], timeout=60) as response:
            content = response.read()
    if (len(content) != item["bytes"] or
            hashlib.sha256(content).hexdigest() != item["sha256"]):
        raise ValueError(f"Checksum mismatch: {item['name']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return content


def prepare(root, manifest=None, builder_skill=None):
    """Download ~4.6 MB once; verify cached bytes on every subsequent setup."""
    source = Path(__file__).resolve().parent
    if manifest is None:
        manifest = json.loads((source / "resources.json").read_text())
    if builder_skill is None:
        builder_skill = (source / "skill-builder/SKILL.md").read_text()
    root = Path(root).resolve()
    corpus, private, builder = root / "corpus", root / "private", root / "builder"
    for item in manifest["files"]:
        directory = corpus if item["name"].endswith(".txt") else private
        verified_download(item, directory / item["name"])
    with (private / "officeqa_sample.csv").open(newline="") as stream:
        rows = {row["uid"]: row for row in csv.DictReader(stream)}
    if not set(DEVELOPMENT + HELD_OUT) <= rows.keys():
        raise ValueError("The pinned sample does not contain the declared split")
    for uid, clarification in CLARIFICATIONS.items():
        rows[uid]["original_question"] = rows[uid]["question"]
        rows[uid]["question"] += " " + clarification
    namespace = {"__name__": "evoskill_reference_grader"}
    grader = private / "reward.py"
    exec(compile(grader.read_text(), str(grader), "exec"), namespace)
    target = builder / SKILLS / "skill-builder" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(builder_skill)
    (root / "agent" / ".git").mkdir(parents=True, exist_ok=True)
    identity = {**manifest, "development": DEVELOPMENT, "held_out": HELD_OUT,
                "tolerance": 0.0, "question_clarifications": CLARIFICATIONS,
                "builder_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    return OfficeQA(root, corpus, builder, rows, namespace["score_answer"], identity)


def skill_names(tree):
    return sorted(Path(path).parent.name for path in tree if path.endswith("/SKILL.md"))


def validate_skills(tree):
    """A deliberately small native-skill format: name, description, Markdown body.

    Extra frontmatter (tool permissions, hooks, alternate models) is outside the
    candidate's authority. Plain UTF-8 references may accompany each SKILL.md.
    """
    tree = meta.SourceTree(tree)
    if sum(len(text.encode()) for text in tree.values()) > 100_000:
        raise ValueError("Skill library exceeds 100 KB")
    names = skill_names(tree)
    for path, text in tree.items():
        parts = Path(path).parts
        if (len(parts) < 4 or parts[:2] != (".claude", "skills") or
                not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", parts[2]) or
                parts[2] == "skill-builder" or parts[2] not in names):
            raise ValueError(f"Not a candidate skill resource: {path}")
        if parts[-1] == "SKILL.md":
            match = re.fullmatch(
                r"---\nname: ([a-z][a-z0-9-]{0,62})\n"
                r"description: ([^\n]+)\n---\n([\s\S]+)", text,
            )
            if not match or match[1] != parts[2] or len(parts) != 4:
                raise ValueError(f"Expected name/description frontmatter: {path}")
    return tree


def snapshot(repository):
    folder = Path(repository) / SKILLS
    folder.mkdir(parents=True, exist_ok=True)
    # Capture only skill content: never settings, transcripts, gold labels or logs.
    files = meta.load_source_tree(folder)
    return validate_skills(meta.SourceTree({f"{SKILLS}/{p}": text for p, text in files.items()}))


def restore(tree, repository):
    """Restore one immutable candidate before evaluation or revision."""
    repository = Path(repository)
    tree = validate_skills(tree)
    folder = repository / SKILLS
    if folder.is_symlink():
        raise ValueError("Skill directory cannot be a symlink")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for path, text in tree.items():
        target = repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return repository


def generated_skills(parent, proposal, generated, repository):
    if "skill-builder" not in generated.invoked_skills:
        raise ValueError("Generator did not invoke the initialized skill-builder")
    candidate = snapshot(repository)
    changed = {path for path in set(parent) | set(candidate)
               if parent.get(path) != candidate.get(path)}
    prefix = f"{SKILLS}/{proposal.name}/"
    if not changed or any(not path.startswith(prefix) for path in changed):
        raise ValueError("Generator must change only the proposed skill folder")
    if prefix + "SKILL.md" not in candidate:
        raise ValueError("Generator did not write the proposed SKILL.md")
    return candidate


def skill_revision(parent, proposal, generated, repository, derived_from):
    """Validate files and account for both Sonnet calls before proposing a candidate."""
    from meta_evolve.domain import InvalidOutput, ProposerFailure

    calls = [proposal] + ([generated] if generated is not None else [])
    fields = {"usage": sum((call.usage for call in calls), meta.Resources()),
              "evidence": tuple(call.evidence() for call in calls),
              "derived_from": derived_from}
    failed = next((call for call in calls if call.failure), None)
    if failed:
        return meta.ProposalResult(failure=ProposerFailure(message=failed.failure), **fields)
    try:
        candidate = generated_skills(parent, proposal.output, generated, repository)
    except (OSError, ValueError) as error:
        return meta.ProposalResult(failure=InvalidOutput(message=str(error)), **fields)
    return meta.ProposalResult(candidate=dict(candidate),
                               hypothesis=proposal.output.description, **fields)


def graded_case(case, call, score_answer):
    row = {"uid": case["uid"], "question": case["question"],
           "answer": None if call.failure else call.output.final_answer, "trace": call.trace,
           "loaded_skills": call.loaded_skills, "invoked_skills": call.invoked_skills}
    if call.failure:
        row["failure"] = call.failure
        return row
    try:
        predicted = f"<FINAL_ANSWER>{call.output.final_answer}</FINAL_ANSWER>"
        row["score"] = float(score_answer(case["answer"], predicted, tolerance=0.0))
        if row["score"] not in (0.0, 1.0):
            raise ValueError("OfficeQA must return a binary score")
    except Exception as error:
        row.pop("score", None)
        row["failure"] = f"OfficeQA grader: {error}"
    return row


def answer_table(cases, calls, score_answer, before=None):
    """Show the private answer key to the reader, never to an agent."""
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    rows = ["| Task | Expected | " + ("Before | " if before else "") + "Answer | Grade | Skills used |",
            "|:--|--:|" + ("--:|" if before else "") + "--:|:--|:--|"]
    for index, (case, call) in enumerate(zip(cases, calls, strict=True)):
        grade = graded_case(case, call, score_answer)
        previous = [before[index].output.final_answer] if before else []
        values = [case["uid"], case["answer"], *previous, grade["answer"],
                  grade.get("failure") or ("Pass" if grade["score"] else "Miss"),
                  ", ".join(call.invoked_skills) or "none"]
        rows.append("| " + " | ".join(cell(value) for value in values) + " |")
    return "\n".join(rows)


def skill_markdown(tree):
    return "\n\n".join(f"**`{path}`**\n\n```yaml\n{text.split('---', 2)[1].strip()}\n```\n"
                       + text.split("---", 2)[2].strip()
                       for path, text in tree.items() if path.endswith("/SKILL.md"))


def evaluation(cases, calls, score_answer, split):
    """Only a completed answer gets a score; an incomplete suite cannot rank."""
    from meta_evolve.domain import EvaluatorFailure

    usage = sum((call.usage for call in calls), meta.Resources())
    rows = [graded_case(case, call, score_answer)
            for case, call in zip(cases, calls, strict=True)]
    evidence = (meta.EvidenceDraft(kind=f"evoskill.{split}", data={"cases": rows}),
                *(call.evidence() for call in calls))
    failures = [row["failure"] for row in rows if row.get("failure")]
    if failures:
        return meta.EvaluationResult(failure=EvaluatorFailure(message="; ".join(failures)),
                                     usage=usage, evidence=evidence)
    return meta.EvaluationResult(
        metrics={"solved": sum(row["score"] for row in rows) / len(rows)},
        usage=usage, evidence=evidence,
    )


def population_table(run):
    """A compact Markdown view of the recorded population, with readable labels."""
    attempts = run.trials()
    labels = {attempt.artifact.id: "Seed" if index == 0 else f"Revision {index}"
              for index, attempt in enumerate(attempts) if attempt.artifact is not None}
    rows = ["| Candidate | Parent | Solved | Frontier after grading |",
            "|:--|:--|--:|:--|"]
    updates = {step.candidate.id: step for step in run.inspect().decisions if step.kind == "archive"}
    for index, attempt in enumerate(attempts):
        label = "Seed" if index == 0 else f"Revision {index}"
        parent = ", ".join(labels[item] for item in attempt.parent_ids) or "—"
        score = f"{attempt.metrics['solved']:.0%}" if "solved" in attempt.metrics else "Failed"
        update = updates.get(attempt.artifact.id) if attempt.artifact is not None else None
        frontier = (", ".join(labels[item] for item in update.member_ids) or "—") if update else "Unchanged"
        rows.append(f"| {label} | {parent} | {score} | {frontier} |")
    return "\n".join(rows)


def save_report(run, data, baseline, selected):
    receipts = [json.loads(path.read_text()) for path in (data.root / "receipts").glob("*.json")]
    usage = sum((meta.Resources(**record["resources"]) for record in receipts), meta.Resources())
    costs = [record["result"].get("total_cost_usd") for record in receipts]
    report = {
        "controls": data.identity,
        "development": [dict(trial.metrics) for trial in run.trials()],
        "held_out_baseline": dict(baseline.metrics), "held_out_selected": dict(selected.metrics),
        "held_out_failures": [str(result.failure) if result.failure else None
                              for result in (baseline, selected)],
        "resources": asdict(usage), "sdk_calls": len(receipts),
        "estimated_cost_usd": sum(costs) if all(cost is not None for cost in costs) else None,
        "selected_digest": run.best().digest,
        "selected_population_seed": run.best().id == run.trials()[0].artifact.id,
    }
    (data.root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    for label, result in (("baseline", baseline), ("selected", selected)):
        (data.root / f"held-out-{label}.json").write_text(
            json.dumps(dict(result.evidence[0].data), default=dict, indent=2) + "\n")
    return report
