---
name: skill-builder
description: Build or revise a reusable Claude skill from a named proposal and failed task traces. Use when asked to turn a diagnosed failure into a native skill folder.
---

# Build a skill from a failure

1. Read the proposal and inspect the existing skill library. Identify a reusable
   procedure that addresses the failure: finding evidence, choosing a table,
   checking dates and units, comparing rows, or verifying arithmetic.
2. Write the proposed skill under the supplied candidate repository at
   `.claude/skills/<proposal-name>/SKILL.md`. Use the SDK Write/Edit tools.
   Keep other skill folders intact. Revising the named skill is allowed.
3. Start with exactly this frontmatter, substituting the name and a plain,
   single-line description without quotes or YAML punctuation:

   ```markdown
   ---
   name: proposal-name
   description: When to use this skill and what it helps the agent do.
   ---
   ```

4. Write concise, ordered instructions with observable checks and a stopping
   condition. Make the description useful for native skill discovery. Use only
   the executor's available tools: Skill, Read, Glob, and Grep. The executor can
   reason over arithmetic but cannot run shell commands or Python scripts.
5. If needed, place short UTF-8 reference documents beside SKILL.md. Reference
   them from the instructions. Keep the entire library below 100 KB.
6. Read the resulting files back and check the frontmatter and procedure.

Teach methods that transfer to unseen questions. Do not embed benchmark IDs,
questions, answers, filenames for specific questions, or a lookup table. Do not
change grading, datasets, agent settings, permissions, or this builder skill.
The caller supplies only development failures; held-out questions are unavailable.
