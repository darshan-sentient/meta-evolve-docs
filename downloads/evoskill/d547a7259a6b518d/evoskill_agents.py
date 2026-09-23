"""A small callable over ClaudeSDKClient, with observable traces and receipts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from html import escape
import json
from pathlib import Path
import time
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
    PermissionResultAllow, PermissionResultDeny, ResultMessage, SystemMessage,
    TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
)

import meta_evolve as meta
from meta_evolve.errors import ExecutionAccountingUnknown


def permitted_path(cwd, roots, writable, name, args):
    if name in {"Skill", "StructuredOutput"}:
        return True
    field = "file_path" if name in {"Read", "Write", "Edit"} else "path"
    path = (cwd / args.get(field, ".")).resolve()
    if name in {"Write", "Edit"}:
        return writable is not None and path.is_relative_to(writable)
    if name not in {"Read", "Glob", "Grep"}:
        return False
    pattern = args.get("pattern", "") if name == "Glob" else args.get("glob", "")
    return (any(path.is_relative_to(root) for root in roots)
            and any((path / pattern).resolve().is_relative_to(root) for root in roots))


def file_permissions(cwd, read_roots, write_root=None):
    """Constrain file tools; the example does not expose shell/network tools."""
    cwd = Path(cwd).resolve()
    roots = tuple(Path(root).resolve() for root in read_roots)
    writable = Path(write_root).resolve() if write_root else None

    async def check(event, tool_use_id, context):
        if permitted_path(cwd, roots, writable, event["tool_name"], event["tool_input"]):
            return {}
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "Outside this agent's declared files/tools.",
        }}

    async def approve_write(name, args, context):
        if name in {"Write", "Edit"} and permitted_path(cwd, roots, writable, name, args):
            return PermissionResultAllow(updated_input=args)
        return PermissionResultDeny(message="Only candidate skill writes may be approved.")

    options = {"permission_mode": "dontAsk", "strict_mcp_config": True,
               "hooks": {"PreToolUse": [HookMatcher(hooks=[check])]}}
    if writable:
        # Claude requires a callback for writes to protected .claude paths.
        options.update(permission_mode="default", can_use_tool=approve_write)
    return options


def agent_options(data, *, executor_model="haiku", skill_model="sonnet"):
    """Ordinary SDK options: native skills, separate tool scopes, and call limits."""
    cwd, repository = data.root / "agent", data.root / "skill-repo"
    skills = repository / ".claude/skills"
    return {
        "executor": ClaudeAgentOptions(
            model=executor_model, cwd=cwd, add_dirs=[repository, data.corpus],
            setting_sources=["project"], skills="all",
            tools=["Read", "Glob", "Grep", "Skill"], allowed_tools=["Read", "Glob", "Grep"],
            system_prompt=(
                f"Answer questions using the Treasury documents in {data.corpus}. "
                f"Use relevant skills from {skills} with the Skill tool. "
                "Verify dates, table headers and units. Return final_answer as one numeric "
                "value in the question's requested units, without dates or explanations. "
                "Put your brief source-based explanation in the separate explanation field."
            ),
            max_turns=32, max_budget_usd=0.50,
            **file_permissions(cwd, [cwd, repository, data.corpus]),
        ),
        "proposer": ClaudeAgentOptions(
            model=skill_model, cwd=cwd, tools=[], skills=[], setting_sources=[],
            system_prompt=(
                "Propose one reusable skill from failed task traces. Return its name and "
                "high-level description. Reuse a name to revise an existing skill. "
                "Teach a method; do not memorize benchmark questions, answers or filenames. "
                "Do not use the reserved name skill-builder."
            ),
            permission_mode="dontAsk", strict_mcp_config=True,
            max_turns=5, max_budget_usd=0.50,
        ),
        "generator": ClaudeAgentOptions(
            model=skill_model, cwd=cwd, add_dirs=[repository, data.builder],
            setting_sources=["project"], skills=["skill-builder"],
            tools=["Read", "Glob", "Grep", "Skill", "Write", "Edit"],
            system_prompt=(
                "First invoke skill-builder using the Skill tool. Follow it to create or "
                f"revise only {skills}/<proposal-name>/SKILL.md and its references. "
                "The executor has Read, Glob, Grep and Skill, with no shell."
            ),
            max_turns=16, max_budget_usd=1.00,
            **file_permissions(cwd, [cwd, repository, data.builder], skills),
        ),
    }


@dataclass(frozen=True)
class AgentResult:
    output: object
    record: dict
    usage: meta.Resources
    failure: str | None

    @property
    def trace(self):
        return self.record["trace"]

    @property
    def invoked_skills(self):
        return [block["input"].get("skill") for item in self.trace
                for block in item.get("blocks", []) if block.get("name") == "Skill"]

    @property
    def loaded_skills(self):
        discovered = self.record.get("init", {}).get("skills", [])
        return sorted(item if isinstance(item, str) else item["name"] for item in discovered)

    def evidence(self):
        return meta.EvidenceDraft(kind="evoskill.agent", data=self.record)

    def require(self):
        """Raise on a failed standalone call; search retains typed failures."""
        if self.failure:
            raise RuntimeError(f"{self.failure}; receipt: {self.record['receipt']}")
        return self


def token_count(result):
    """Count cumulative usage once, including cache and over-budget responses."""
    if result.model_usage:
        fields = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        counts = [usage.get(key) for usage in result.model_usage.values() for key in fields]
    else:
        if result.subtype == "error_max_budget_usd":
            raise ValueError("Budget error without cumulative model_usage")
        usage = result.usage or {}
        counts = [usage.get("input_tokens"), usage.get("output_tokens"),
                  usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0)]
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("Missing or invalid cumulative token counts")
    return sum(counts)


def observe(message, record):
    if isinstance(message, SystemMessage) and message.subtype == "init":
        record["init"] = message.data
    elif isinstance(message, (AssistantMessage, UserMessage)):
        blocks = message.content if isinstance(message.content, list) else []
        visible = [asdict(block) for block in blocks
                   if isinstance(block, (TextBlock, ToolUseBlock, ToolResultBlock))]
        if isinstance(message.content, str):
            visible = [{"text": message.content}]
        record["trace"].append({"speaker": type(message).__name__, "blocks": visible})


def finish_response(result, record, output_type):
    try:
        if result is None or result.subtype == "error_during_execution":
            raise ValueError("No trustworthy terminal accounting")
        tokens = token_count(result)
    except ValueError as error:
        raise ExecutionAccountingUnknown(
            f"{error}; stopped without retry. Receipt: {record['receipt']}"
        ) from error
    usage = meta.Resources(tokens=tokens, spend_micros=None, wall_seconds=record["wall_seconds"])
    failure = record["error"]
    if result.is_error or result.subtype != "success":
        failure = f"{result.subtype}: {result.errors or result.result or failure}"
    output = result.result or ""
    if output_type is not None and not failure:
        try:
            output = output_type.model_validate(result.structured_output)
        except ValueError as error:
            failure = f"Invalid structured output: {error}"
    discovered = record.get("init", {}).get("skills", [])
    names = {item if isinstance(item, str) else item.get("name") for item in discovered}
    declared = record["skills"]
    if isinstance(declared, list) and set(declared) - names:
        failure = f"SDK did not discover declared skills: {sorted(set(declared) - names)}"
    if not failure and isinstance(output, str) and not output.strip():
        failure = "Agent returned an empty answer"
    record.update(resources=asdict(usage), failure=failure)
    return AgentResult(output, record, usage, failure)


async def sdk_response(prompt, options, output_type, receipt, name):
    started, result = time.monotonic(), None
    record = {"role": name, "prompt": prompt, "model": options.model,
              "tools": options.tools, "skills": options.skills,
              "system_prompt": options.system_prompt, "output_format": options.output_format,
              "setting_sources": options.setting_sources,
              "max_turns": options.max_turns, "max_budget_usd": options.max_budget_usd,
              "cwd": str(options.cwd), "add_dirs": [str(p) for p in options.add_dirs],
              "trace": [], "error": None, "receipt": str(receipt)}
    try:
        async with ClaudeSDKClient(options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                observe(message, record)
                if isinstance(message, ResultMessage):
                    result = message
    except Exception as error:
        # Preserve terminal usage even when the SDK raises after its result.
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        record.update(result=asdict(result) if result else None,
                      wall_seconds=time.monotonic() - started)
        receipt.write_text(json.dumps(record, indent=2) + "\n")
    reply = finish_response(result, record, output_type)
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    return reply


@dataclass(frozen=True)
class ClaudeAgent:
    """Example-local callable. Every call opens a fresh ClaudeSDKClient session."""
    options: ClaudeAgentOptions
    output_type: object = None
    name: str = "agent"

    def __call__(self, prompt):
        options = self.options
        if self.output_type is not None:
            options = replace(options, output_format={
                "type": "json_schema", "schema": self.output_type.model_json_schema(),
            })
        records = Path(options.cwd).parent / "receipts"
        records.mkdir(parents=True, exist_ok=True)
        receipt = records / f"{self.name}-{uuid4().hex}.json"
        print(f"{self.name}: starting")
        # The SDK's event loop lives in its own thread, including in Jupyter.
        # This exposes the same ordinary callable to the notebook and search.
        with ThreadPoolExecutor(max_workers=1) as worker:
            reply = worker.submit(asyncio.run, sdk_response(
                prompt, options, self.output_type, receipt, self.name,
            )).result()
        print(f"{self.name}: {'failed' if reply.failure else 'complete'}")
        return reply


def failure_feedback(evidence, limit=12000):
    """Forward failed development traces, with explicit clipping and no gold labels."""
    failures = []
    for case in evidence["cases"]:
        if case.get("score") == 0 or case.get("failure"):
            trace = json.dumps(case["trace"], default=dict)
            failures.append({
                "question": case["question"], "answer": case["answer"],
                "score": case.get("score"), "failure": case.get("failure"),
                "trace": trace[-limit:], "trace_truncated": len(trace) > limit,
            })
    return failures


def transcripts(calls):
    """Expandable observable SDK messages; account/session metadata stays in receipts."""
    return "\n".join(
        f"<details><summary>{escape(call.record['role'])} trace {index + 1} "
        f"· skills used: {escape(', '.join(call.invoked_skills) or 'none')}</summary>"
        f"<pre>{escape(json.dumps(call.trace, indent=2))}</pre></details>"
        for index, call in enumerate(calls)
    )
