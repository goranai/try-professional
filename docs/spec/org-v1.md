# Organization spec, org/v1

**Status:** draft until the first release of Try Professional. After that, v1 changes are additive only.

This spec defines how an agent organization is described, so any tool can read or write it. It has no dependency on Try Professional. The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

- Machine-readable schemas: [`spec/org-v1.schema.json`](../../spec/org-v1.schema.json) and [`spec/role-v1.schema.json`](../../spec/role-v1.schema.json), JSON Schema draft 2020-12.
- Conformance fixtures: [`spec/conformance/`](../../spec/conformance/).

## Files

An organization is one `org.yaml` file plus one file per role:

```text
org/
├── org.yaml
└── roles/
    └── <department>/
        └── <role>.yaml
```

## Versioning

Every file MUST declare its version in `spec`: `org/v1` for the organization file, `role/v1` for a role file. The spec version is independent of any package version.

| Change | How it is released |
|---|---|
| Adding an optional field | Within v1 |
| Removing or renaming a field, changing its meaning, adding a required field | New major version (`org/v2`) with a migration path |

**Readers** MUST reject a major version they don't know. They MUST reject unknown keys, except keys beginning with `x-`, which they MUST ignore.

**Writers** MUST preserve `x-` keys they read. They MAY drop YAML comments.

Tools MAY store their own data under `x-` keys, ideally namespaced, such as `x-acme-owner`.

## Role references

A role is referenced as `department/role`, such as `engineering/qa`. Within a department's `head`, `roles`, and `qa_gate.reviewer`, a bare role ID refers to that department. Identifiers match `^[a-z][a-z0-9-]*$`.

## `org.yaml`

```yaml
spec: org/v1
company:
  name: Example Co
root_role: executive/ceo
departments:
  - id: executive
    head: ceo
    activation: always
    roles: [ceo, chief-of-staff]
  - id: engineering
    head: tech-lead
    activation: on_demand
    qa_gate: { reviewer: qa }
    roles: [tech-lead, backend, qa]
gates:
  legal:
    reviewer: legal/counsel
    triggers: [public_facing, touches_license, touches_personal_data]
    on_unavailable: block
pipeline:
  max_parallel_tasks: 4
  max_rework: 2
  max_invalid_outputs: 2
  working_language: auto
  budget: { max_tokens_per_run: null, on_exceeded: escalate }
```

| Field | Required | Meaning |
|---|---|---|
| `company.name` | Yes | Display name |
| `root_role` | Yes | The single role that owns the goal |
| `departments[].id`, `.head`, `.roles` | Yes | Department identity, its head, and all its roles |
| `departments[].activation` | No | `always`, or `on_demand` (default). See [Activation](#activation) |
| `departments[].qa_gate.reviewer` | No | The department's default reviewer |
| `gates.<name>` | No | A review gate fired by flags. See [Gates](#gates) |
| `pipeline.max_parallel_tasks` | No | Upper bound on tasks running at once. Default 4 |
| `pipeline.max_rework` | No | Change requests allowed per task before escalating. Default 2 |
| `pipeline.max_invalid_outputs` | No | Retries of an invalid output before escalating. Default 2 |
| `pipeline.working_language` | No | BCP 47 tag, or `auto` to follow the goal. Default `auto` |
| `pipeline.budget.max_tokens_per_run` | No | Token ceiling for a run, or `null` for none |
| `llm_overrides` | No | Per-department or per-role model settings. See [Model overrides](#model-overrides) |

## Model overrides

By default every agent uses the runtime's default model settings. `llm_overrides` gives a department or a single role its own:

```yaml
llm_overrides:
  engineering: { model: fast-model }
  engineering/qa: { api_key_env: QA_LLM_KEY }
  legal: { base_url: https://other.example.com/v1, api_key_env: LEGAL_LLM_KEY, model: careful-model }
```

Each key is a department ID or a role reference. Fields merge one at a time, and a role's value wins over its department's.

| Field | Meaning |
|---|---|
| `model` | Model name |
| `api_key_env` | The **name** of an environment variable holding the key, or `null` for no key |
| `base_url` | Endpoint. MUST be `https`, or `http` on `localhost` or `127.0.0.1` |

These rules protect keys, because org files get shared and forked:

- An org file MUST NOT contain a key. The schema has no field for one, and `api_key_env` only accepts a variable name.
- An override that sets `base_url` MUST also set `api_key_env`. A runtime MUST NOT send a default key to an endpoint named only in an override.

A file from someone else can still name your variable next to their endpoint, so review `llm_overrides` before running an org you didn't write.

A validator SHOULD warn (`ORG102`) when a key doesn't match an installed department or role. This is a valid state after removing a pack.

## Role files

```yaml
spec: role/v1
id: backend
department: engineering
mission: Build and maintain server-side features to spec.
responsibilities: [Implement APIs, Write tests for what it builds]
inputs: [TaskBrief]
outputs: [CodeChange, TaskReport]
allowed_tools: [workspace_read, workspace_write, memory_recall, memory_write, skill_search]
allowed_skill_categories: [backend, testing]
escalation: engineering/tech-lead
reviewer: engineering/qa
model_tier: standard
system_prompt: |
  You are the Backend Engineer...
```

These fields are optional: `model_tier` (default `standard`), and the skill profile below. All others are required. `escalation` is a role reference, or the literal `human` for the root role only.

### Skill profile

These describe which skills fit a role, so a runtime can match skills to it.

```yaml
skill_needs:
  - design and build REST APIs
  - write database migrations
required_tools: [workspace_read, workspace_write]
forbidden_tools: [shell]
max_skills: 8
```

| Field | Default | Meaning |
|---|---|---|
| `title` | the role's ID | What people call this role, such as CTO. It changes no permission or reporting line |
| `skill_needs` | `[]` | Short descriptions of the capabilities this role's skills should provide. Any language |
| `required_tools` | `[]` | Tools this role can't work without. A runtime that can't provide them SHOULD refuse to run the role |
| `forbidden_tools` | `[]` | A skill that needs any of these MUST NOT be matched to this role |
| `max_skills` | `8` | The most skills this role's loadout may hold, from 1 to 50 |

`inputs`, `outputs`, and `allowed_tools` are open vocabularies at spec level, and implementations define what they accept.

## Semantic rules (validation level 2)

JSON Schema is validation level 1. A conforming validator MUST also apply these rules:

| Code | Rule |
|---|---|
| `ORG001` | Department IDs are unique |
| `ORG002` | Role IDs are unique within a department |
| `ORG003` | A department's `head` is listed in its `roles` |
| `ORG004` | Every listed role has a role file |
| `ORG005` | Every role file is listed under the department it declares |
| `ORG006` | `root_role` resolves to the head of its department |
| `ORG007` | Every `escalation` resolves to a role, except `human` on the root role |
| `ORG008` | Following `escalation` from any role reaches the root role, with no cycle |
| `ORG009` | The root role escalates to `human`, and no other role does |
| `ORG010` | Every `reviewer` resolves to a role |
| `ORG011` | A role is not its own `reviewer` |
| `ORG012` | Every `qa_gate.reviewer` resolves to a role |
| `ORG013` | A non-head role escalates within its own department |
| `ORG014` | A head escalates to the root role, or to a role in the root role's department |
| `ORG015` | The root role's department has `activation: always` |
| `ORG016` | A role's `allowed_tools` and `forbidden_tools` don't overlap |
| `ORG017` | A role's `required_tools` are all in its `allowed_tools` |

A validator SHOULD emit a warning, not an error, when a gate's `reviewer` doesn't resolve (`ORG101`). This is a valid state: it means a gate's department was removed, and `on_unavailable` decides what happens.

## Activation

Defining a department does not start it.

- A department with `activation: always` is active on every run.
- A department with `activation: on_demand` is active only if a plan names it or a gate requires it.
- A runtime MUST NOT invoke a role in an inactive department.

## Gates

A gate fires when output carries one of its `triggers`:

| Flag | Meaning |
|---|---|
| `public_facing` | The output will be seen outside the organization |
| `touches_license` | The output changes or depends on licensing terms |
| `touches_personal_data` | The output handles personal data |

A gate fires from flags, whether or not a plan mentions its department. A plan cannot opt out.

If the gate's `reviewer` is unavailable, `on_unavailable` applies. `block` (default) stops the run until a human decides. `waive` continues, and MUST be logged.

## Company policy

`trypro init "<business>"` also applies stricter rules, which aren't part of this spec, because a valid org/v1 company can leave out any department. Check them with `trypro org validate --policy`. A generated company must have an always-active Executive department holding the root role (`GEN001`), a Legal department (`GEN002`), a legal gate with all three triggers that never waives (`GEN003`), a QA gate on every other department (`GEN004`), at most 25 roles (`GEN005`), only known tools, so no shell (`GEN006`), no `llm_overrides` (`GEN007`), and a token ceiling (`GEN008`). A person can change a company by hand afterwards.

## Conformance

Each file under `spec/conformance/` is one test case, with the whole organization in one document:

```yaml
description: What this case tests
org: { ... }             # contents of org.yaml
roles:                   # role files, keyed by department/role
  engineering/backend: { ... }
expect:
  valid: false
  errors: [ORG003]       # error codes that MUST be reported, or SCHEMA for level 1
  warnings: []           # optional, warning codes that MUST be reported
```

A conforming validator reports `valid` as expected and includes every listed code. It MAY report further codes.
