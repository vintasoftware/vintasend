# ai-plans

Feature specs and phased implementation plans live here.

## Naming

```
YYYY-MM-DD-FEATURE_NAME_SPEC.md      <- written by the create-spec skill
YYYY-MM-DD-FEATURE_NAME_PLAN.md      <- written by the plan-feature skill
```

A spec and its plan share the same `YYYY-MM-DD-FEATURE_NAME` prefix. `FEATURE_NAME` is uppercase
with underscores.

While a plan is being executed, [implement-plan](../ai-tools/skills/implement-plan/SKILL.md) keeps a
`TRACKING_{plan-id}.md` file here and deletes it when the plan completes.

## Workflow

1. `create-spec` — turn an idea into a structured spec. Interviews you first.
2. `plan-feature` — turn the spec into a phased plan. Interviews you first.
3. `implement-plan` — execute the plan phase by phase.
4. `amend-plan` — revise a plan after implementation has started.

This directory started empty: the repo had no pre-existing plan or spec documents when the AI
tooling was bootstrapped.
