"""Prompt builders for sprint-gated coordinator shortcut commands."""

from __future__ import annotations

from dataclasses import dataclass


SPRINT_SHORTCUT_COMMANDS = frozenset(
    {"plan_sprint", "run_sprint", "continue_sprint", "auto_agent"}
)


@dataclass(frozen=True)
class SprintShortcut:
    """Resolved sprint shortcut command payload."""

    command: str
    prompt: str
    usage: str | None = None


def _clean_args(args: str | None) -> str:
    return (args or "").strip()


def build_sprint_shortcut_prompt(command: str, args: str | None = None) -> SprintShortcut:
    """Return the coordinator prompt for a sprint shortcut command.

    These commands are intentionally implemented as prompt rewrites instead of
    direct filesystem/process operations. The coordinator remains responsible
    for loading the sprint-plan-executor skill, discovering the active plan,
    choosing an appropriate worker/profile, verifying results, updating the
    durable plan, and stopping before approval-required actions.
    """

    canonical = command.lower().strip().lstrip("/").replace("-", "_")
    raw_args = _clean_args(args)

    if canonical == "plan_sprint":
        goal_text = raw_args or (
            "No explicit goal was provided in the slash command. Infer the plan goal from "
            "the current conversation/channel context and the user's most recent concrete "
            "request. If there is no concrete request available, do not return a terse usage "
            "error; ask one compact follow-up question with 2-3 example goal formats."
        )
        prompt = f"""Sprint mode: create a sprint-gated plan for this request, then stop before implementation.

User goal/context:
{goal_text}

Requirements:
- Load and follow `sprint-plan-executor`, `plan-continuity-tracker`, and `writing-plans`.
- Infer the project/workdir/repo/branch from the current Discord/channel context or ask only if genuinely ambiguous.
- Save a durable plan under the project-local `.hermes/plans/<project-slug>/...md` path.
- Include Execution Policy, Acceptance Criteria, Phase/Sprint Map, Sprint Backlog, Verification Log, and Next Action.
- Divide work into sprint-sized checkpoints with reasonable turn budgets.
- Do not edit product code yet unless the user explicitly requested immediate execution.
- Final reply must be compact: Plan path, first sprint, verification expectations, and next command (`/run_sprint auto`).
""".strip()
        return SprintShortcut(command=canonical, prompt=prompt)

    if canonical == "run_sprint":
        mode = raw_args or "auto"
        prompt = f"""Sprint mode: run the next sprint from the latest active unfinished plan.

Mode/args: {mode}

Requirements:
- Load and follow `sprint-plan-executor`.
- Do not require the user to provide a plan path or profile name.
- Discover the active plan automatically, preferring this Discord channel/thread/project, then the most recently updated unfinished plan under `.hermes/plans/**`.
- If multiple active plans are plausible, stop and ask with 2-3 compact choices.
- Select the most suitable Hermes worker/profile automatically when useful; "profile" means a real Hermes profile launch such as `hermes -p backend-eng ...`, not generic `delegate_task`.
- For coding/profile work, prefer `/Users/tik/.hermes/scripts/profile_worker.py` so profile-specific plugins/MCP such as Serena are actually loaded.
- For CheckinFlow, default to `staging` and split API/Web work through the wrapper (`--scope api`, `--scope web`, or `--scope both`) using isolated temp worktrees.
- Use generic `delegate_task` only for tiny reasoning/review subtasks where profile-specific config and Serena are irrelevant.
- Execute exactly one sprint/checkpoint, not the whole plan.
- Worker/profile instructions must be bounded to the selected sprint only and must stop before deploy, database migration, destructive commands, push main, gateway restart, secrets, or scope drift.
- Coordinator must verify worker output before claiming success.
- Update the plan file before final reply.
- Final reply must be compact: Sprint, Plan path, Done, Verify, Next.
""".strip()
        return SprintShortcut(command=canonical, prompt=prompt)

    if canonical == "continue_sprint":
        mode = raw_args or "auto"
        prompt = f"""Sprint mode: continue the latest active sprint plan from its Next Action.

Mode/args: {mode}

Requirements:
- Load and follow `sprint-plan-executor`.
- Do not require the user to provide a plan path or profile name.
- Prefer the plan most recently reported/updated in this Discord channel/thread; otherwise discover the latest unfinished plan under the current project `.hermes/plans/**`.
- Select the most suitable Hermes worker/profile automatically when useful; "profile" means a real Hermes profile launch such as `hermes -p frontend-eng ...`, not generic `delegate_task`.
- For coding/profile work, prefer `/Users/tik/.hermes/scripts/profile_worker.py` so profile-specific plugins/MCP such as Serena are actually loaded.
- For CheckinFlow, default to `staging` and split API/Web work through the wrapper (`--scope api`, `--scope web`, or `--scope both`) using isolated temp worktrees.
- Use generic `delegate_task` only for tiny reasoning/review subtasks where profile-specific config and Serena are irrelevant.
- Execute exactly the next sprint/checkpoint only.
- Stop before approval-required operations: deploy, database migration, destructive commands, push main, gateway restart, credential/secret handling, or unclear scope.
- Update the durable plan before final reply.
- Final reply must be compact: Sprint, Plan path, Done, Verify, Next.
""".strip()
        return SprintShortcut(command=canonical, prompt=prompt)

    if canonical == "auto_agent":
        if not raw_args:
            return SprintShortcut(
                command=canonical,
                prompt="",
                usage="Usage: /auto_agent <task>",
            )
        prompt = f"""Auto agent profile mode: handle this bounded task with coordinator-selected worker/profile routing.

Task:
{raw_args}

Requirements:
- The coordinator chooses the best execution path; the user should not need to name a profile or plan path.
- If this is more than a tiny one-shot, create or locate a sprint-gated plan first using `sprint-plan-executor`.
- If a worker/profile is useful, select it automatically based on task type (UI/frontend, backend/API, QA, review/debug, ops) and bound it to a single sprint/task.
- "Profile" means a real Hermes profile launch such as `hermes -p frontend-eng ...` or `hermes -p backend-eng ...`, not generic `delegate_task`.
- For coding/profile work, prefer `/Users/tik/.hermes/scripts/profile_worker.py` so profile-specific plugins/MCP such as Serena are actually loaded.
- For CheckinFlow, default to `staging` and split API/Web work through the wrapper (`--scope api`, `--scope web`, or `--scope both`) using isolated temp worktrees.
- Use generic `delegate_task` only for tiny reasoning/review subtasks where profile-specific config and Serena are irrelevant.
- If the task is tiny, the coordinator may execute it directly.
- Stop before deploy, database migration, destructive commands, push main, gateway restart, credential/secret handling, or scope drift.
- Verify results before reporting success.
- If a plan was used/created, update it before final reply.
- Final reply must be compact: Route used, Done, Verify, Next.
""".strip()
        return SprintShortcut(command=canonical, prompt=prompt)

    return SprintShortcut(command=canonical, prompt="", usage=f"Unknown sprint shortcut: /{command}")
