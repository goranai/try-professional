# Try Professional: Architecture (Phase 1)

**Status:** approved by the owner on 2026-11-06. Open questions are resolved in §14.
**Date:** 2026-11-06
**Upstream versions read:** `mnemosyne-memory` 4.0.0, `mnemosyne-hermes` 0.7.7, Hermes Agent `main` as of 2026-11-06.

Try Professional turns an LLM agent runtime into a structured company: a hierarchy of agents with one accountable owner per task, typed handoffs, review gates, shared multilingual memory and a vetted skill supply chain. It is designed against flat swarms.

**End goal (set by the owner on 2026-11-06).** Someone clones `goranai/try-professional`, runs one command describing a business, and gets a complete company designed for that business, with skills assigned to its departments and roles, ready to run. `trypro init "a YouTube Shorts content company"` should produce Trend Research, Scriptwriting, Production, Distribution, Analytics, Executive, Legal and QA, not the generic nine departments. Then `trypro run "make 5 Shorts about AI tools for this week"` works straight away. In v0.1 the company produces files. Rendering and uploading come later as optional connectors, always gated by human approval. §5.4 has the design, approved by the owner on 2026-11-06.

## Contents

1. [Decisions so far](#1-decisions-so-far)
2. [Changes from the brief](#2-changes-from-the-brief)
3. [Directory tree](#3-directory-tree)
4. [Components](#4-components)
5. [Organization model and spec](#5-organization-model-and-spec)
6. [Pipeline state machine](#6-pipeline-state-machine)
7. [Artifact schemas](#7-artifact-schemas)
8. [Memory](#8-memory)
9. [Skill supply chain](#9-skill-supply-chain)
10. [Adapters](#10-adapters)
11. [Benchmark harness](#11-benchmark-harness)
12. [Phases and exit criteria](#12-phases-and-exit-criteria)
13. [Known limits](#13-known-limits)
14. [Resolved questions](#14-resolved-questions)
15. [Appendix: evidence](#15-appendix-evidence)

---

## 1. Decisions so far

| Topic | Decision | Source |
|---|---|---|
| Names | Display "Try Professional", repo `try-professional`, PyPI `try-professional`, import `try_professional`, CLI `trypro`. Free on PyPI, no conflicting GitHub project found. No trademark search done. | Owner |
| Runtime priority | API, then MCP, then Hermes. The Hermes design is fully specified here (§10.4); its implementation comes last. | Owner |
| Departments | All nine ship as removable packs. Installed does not mean invoked (§5.3). | Owner |
| Embeddings | Local. `paraphrase-multilingual-MiniLM-L12-v2` by default, confirmed after Phase 3. `multilingual-e5-large` is a documented optional upgrade ([docs/embeddings.md](embeddings.md)). | Owner |
| Score floor | Memory recall keeps one. The skill catalog has none. | Owner |
| Paid API calls | Before re-running anything that calls the paid API, tell the owner and wait, even with token ceilings in place. | Owner |
| Tokens per run | A first-class metric, reported in every live test and in Phase 6. The hierarchy must not cost much more than a flat swarm for the same result, or the README says so. | Owner |
| Skill licenses | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC. Configurable. | Owner |
| Extra runtime stub | Generic `adapters/template/` only, with no named stub. | Owner |
| Supply chain | Standalone package in a monorepo. Try Professional depends on it like any other user. | Owner |
| `org.yaml` | Documented, versioned spec with JSON Schema. | Owner |
| Benchmarks | New Phase 6, `trypro bench`, results published even where the hierarchy loses. | Owner |
| Remote embeddings | Refused unless explicitly enabled (§8.4). | Proposed and accepted with the embeddings answer |
| Supply-chain name and position | `skill-customs`: the lockfile, license and pinning layer. Other scanners plug in, and ours is the default. | Owner |
| Skill index | Separate catalog outside Mnemosyne, same embedding model. Memory banks stay on Mnemosyne. | Owner |
| Benchmark spending | Decided after Phase 5. The estimate is shown first, and the approved amount is never exceeded. All three setups use the same mid-priced model. | Owner |
| Python | 3.10 to 3.13 required in CI. 3.14 allowed to fail until Mnemosyne officially supports it. | Owner |
| Repository | `goranai/try-professional`, private until v0.1.0. | Owner |
| LLM provider | Any OpenAI-compatible API, from `TRYPRO_LLM_BASE_URL`, `TRYPRO_LLM_API_KEY` and `TRYPRO_LLM_MODEL` in `.env`. Everyone uses these unless `llm_overrides` in `org.yaml` says otherwise. `OPENAI_API_KEY` is never used. | Owner |
| Model | `deepseek-v4.1-flash-1017`: passed tool calling, a tool loop and JSON output. Phase 6 uses the same model. | Owner's preference, confirmed by test |
| Secrets | `.env` is gitignored, and a scan of the working tree and full history runs before every push. | Owner |
| End goal | One command turns a business description into a company ready to run (§5.4). The YouTube Shorts studio is the flagship example. | Owner |
| Current claims | Any trend or "current" claim cites a source and a date. Without them, a research deliverable labels it unverified and never calls it "this week's trend". | Owner |
| Skill fit | Embeddings shortlist candidates per role, then the cheap model judges fit with a one-line reason. It runs at init and when skills change, is cached, and reports its token cost. | Owner |
| v0.1 scope | Deliverables are files. Real-world actions come later as optional connectors, gated by human approval. The README says this plainly. | Owner |

## 2. Changes from the brief

| Brief | This design | Why |
|---|---|---|
| `core/`, `memory/`, `skills/` at repo root | `packages/<dist>/src/<import_name>/...` | Top-level packages named `core` and `memory` would collide in users' environments. A `src` layout also makes tests run against the installed package. |
| `org/` at repo root | Pack sources live inside the package. `trypro init` writes `org/org.yaml` and `org/roles/<department>/<role>.yaml` into the user's project. | The user's project gets exactly the layout in the brief, and `pip install` can ship the packs. |
| `skills/` subsystem | `packages/skill-customs/` | Owner's change. `skill-guard` was taken on PyPI (§14). |
| Skill index inside a Mnemosyne bank | Separate catalog using the same embedding model | Approved by the owner (§14). Evidence in §8.5. |
| Role agents write to their department bank (company bank writer unspecified) | Only the Executive department writes to the company bank, through an explicit promotion | Closes a gap in the brief. Anything else lets one role change what the whole company reads. |
| Phase 6 is OSS packaging | Phase 6 is the benchmark harness, Phase 7 is OSS packaging | Owner's change. |
| Landing-page example | YouTube Shorts studio example | Owner's change. It's the flagship, and one end-to-end example is easier to keep working than two. |
| The nine departments are the product | They become the `general-company` template, one of five | Owner's change. The packs stay as its source, so nothing is duplicated. |

## 3. Directory tree

```text
try-professional/
├── README.md                     # 60-second quickstart, architecture diagram, badges
├── LICENSE                       # MIT
├── CONTRIBUTING.md               # add a department, role, adapter, or skill
├── CODE_OF_CONDUCT.md
├── SECURITY.md                   # threat model and disclosure
├── GOVERNANCE.md
├── CHANGELOG.md                  # Keep a Changelog + SemVer, one section per package
├── THIRD_PARTY_SKILLS.md         # generated by skill-customs
├── .env.example
├── pyproject.toml                # workspace root; never published
├── spec/
│   ├── org-v1.schema.json        # canonical JSON Schema (draft 2020-12)
│   ├── role-v1.schema.json
│   ├── artifacts-v1/             # generated from the Pydantic models, checked in CI
│   └── conformance/{valid,invalid}/
├── templates/                    # industry templates, a contribution path; copied into the package like spec/
│   ├── README.md                 # how to add an industry
│   ├── general-company/          # one template.yaml each; this one is built from the packs
│   ├── youtube-shorts-studio/
│   ├── saas-startup/
│   ├── ecommerce-store/
│   └── marketing-agency/
├── packages/
│   ├── skill-customs/            # standalone; must not import try_professional
│   │   ├── pyproject.toml
│   │   ├── README.md             # adopt in under 5 minutes
│   │   ├── src/skill_customs/
│   │   │   ├── scan/             # scanner, rule packs, plugin interface
│   │   │   ├── license.py        # allowlist and commercial-use flags
│   │   │   ├── fetch.py          # tarball by SHA, safe extraction
│   │   │   ├── lockfile.py       # skills.lock
│   │   │   ├── discover.py       # GitHub ranking
│   │   │   ├── attribution.py    # THIRD_PARTY_SKILLS.md
│   │   │   └── cli.py
│   │   └── tests/
│   └── try-professional/
│       ├── pyproject.toml
│       ├── src/try_professional/
│       │   ├── org/              # loader, validator (spec levels 1 and 2)
│       │   ├── packs/            # one folder per department
│       │   ├── generator/        # Org Architect, compiler, company policy, provisioning, review screen, estimate
│       │   ├── core/
│       │   │   ├── pipeline/     # pure state machine
│       │   │   ├── orchestrator.py
│       │   │   ├── artifacts/    # Pydantic models
│       │   │   ├── gates.py
│       │   │   ├── ledger.py     # run state in SQLite
│       │   │   └── audit.py      # append-only hash-chained log
│       │   ├── memory/           # MemoryGateway, Mnemosyne backend, fake backend
│       │   ├── skills/           # catalog, retrieval, proposals (uses skill_customs)
│       │   ├── adapters/         # base.py, api_runner/, mcp/, hermes/, template/
│       │   ├── bench/            # runner, setups, tasks, report generator
│       │   └── cli.py
│       └── tests/
├── examples/youtube-shorts-studio/  # flagship: a whole company, one goal, the files it produces
├── benchmarks/results/           # raw results by date, model, version
├── docs/
│   ├── architecture.md           # this file
│   ├── pipeline.md, security.md, i18n.md
│   ├── benchmarks.md             # generated, never edited by hand
│   ├── spec/org-v1.md            # normative spec
│   ├── adr/
│   └── research/                 # evidence behind design claims
└── .github/                      # CI, templates, CODEOWNERS, dependabot, seed-issues.yaml
```

**Tooling:** `uv` workspace for development (plain `pip install -e` also works), `hatchling` as the build backend, Python 3.10+. CI requires 3.10 to 3.13 on Linux, macOS and Windows. 3.14 runs in CI but is allowed to fail until Mnemosyne officially supports it.

**Dependencies:**

| Package | Required | Optional extras |
|---|---|---|
| `skill-customs` | `pyyaml` | none; HTTP uses the standard library |
| `try-professional` | `skill-customs`, `pydantic`, `pyyaml`, `jsonschema`, `httpx`, `mnemosyne-memory[embeddings]` | `[anthropic]`, `[mcp]`, `[bench]` |

There is no telemetry anywhere.

## 4. Components

```mermaid
flowchart TB
  user([User goal, any language]) --> entry

  subgraph runtimes [Runtime adapters]
    direction LR
    api[API runner<br/>push]
    mcp[MCP server<br/>pull]
    hermes[Hermes<br/>queue]
    tmpl[Template]
  end
  entry[CLI or client] --> runtimes

  subgraph core [Core: no LLM calls]
    orch[Orchestrator]
    pipe[Pipeline state machine]
    gates[Gate engine]
    arts[Artifact validator]
    ledger[(Run ledger)]
    audit[(Audit log)]
    orch --> pipe
    orch --> gates
    orch --> arts
    orch --> ledger
    orch --> audit
  end
  runtimes <-->|actions and events| orch

  org[org.yaml + roles<br/>spec org/v1] --> orch

  subgraph mem [Memory]
    gw[MemoryGateway<br/>access rules]
    banks[(Mnemosyne banks<br/>company, dept-*)]
    gw --> banks
  end
  orch --> gw

  subgraph sk [Skills]
    cat[Skill catalog<br/>multilingual retrieval]
    prop[Proposals]
  end
  orch --> cat
  orch --> prop

  sc[[skill-customs<br/>standalone package]]
  cat -->|reads skills.lock| sc
  prop -->|scan| sc
  gh[(GitHub)] -->|tarball by SHA| sc

  embed[Local embedding model]
  banks --> embed
  cat --> embed
```

The important boundary: **core makes no model calls.** It takes events, updates state and emits actions. Adapters perform the actions. This is what lets one pipeline serve all three runtime styles, and it makes the hierarchy rules testable without an API key.

## 5. Organization model and spec

### 5.1 Spec

`docs/spec/org-v1.md` is normative. `spec/org-v1.schema.json` and `spec/role-v1.schema.json` are the machine-readable contract, so other tools can read and write these files without importing this project.

- **Versioning:** every file declares `spec: org/v1` or `spec: role/v1`. The spec version is independent of package versions. Changes within v1 are additive only. A breaking change means `org/v2` and a migration command.
- **Reader rules:** must reject an unknown major version. Must ignore unknown `x-*` keys. Must reject other unknown keys.
- **Writer rules:** must preserve `x-*` keys.
- **Validation levels:** Level 1 is JSON Schema. Level 2 is semantic rules that JSON Schema cannot express: every role belongs to one department, each department has one head, escalation paths form a tree that ends at the CEO, a reviewer is never the owner they review, and gate references resolve.
- **Conformance:** `spec/conformance/` holds valid and invalid fixtures that any implementation can run. `trypro org validate` runs both levels.

### 5.2 Files

```yaml
# org/org.yaml
spec: org/v1
company:
  name: Example Co
root_role: executive/ceo
departments:
  - id: executive
    head: ceo
    activation: always          # always | on_demand
    roles: [ceo, chief-of-staff]
  - id: engineering
    head: tech-lead
    activation: on_demand
    qa_gate: { reviewer: qa }
    roles: [tech-lead, backend, frontend, devops, qa]
  - id: finance
    head: finance-lead
    activation: on_demand
    qa_gate: { reviewer: executive/chief-of-staff }   # one-role department, so the reviewer is outside it
    roles: [finance-lead]
gates:
  legal:
    reviewer: legal/counsel
    triggers: [public_facing, touches_license, touches_personal_data]
    on_unavailable: block       # fail closed if the Legal pack is removed
pipeline:
  max_parallel_tasks: 4
  max_rework: 2
  budget: { max_tokens_per_run: null, on_exceeded: escalate }
```

```yaml
# org/roles/engineering/backend.yaml
spec: role/v1
id: backend
department: engineering
mission: Build and maintain server-side features to spec.
responsibilities: [Implement APIs, Write tests for what it builds]
inputs: [TaskBrief]
outputs: [CodeChange, TaskReport]
allowed_tools: [workspace_read, workspace_write, memory_recall, memory_write, skill_search]
allowed_skill_categories: [backend, testing, databases]
escalation: engineering/tech-lead
reviewer: engineering/qa
model_tier: standard            # executive | standard | fast, mapped in user config
system_prompt: |
  You are the Backend Engineer...
```

### 5.3 Installed is not invoked

Installing a pack makes a department available. It costs nothing until it is activated.

- **Executive** is active on every run.
- **The Plan names the departments it activates**, each with a reason. A plan naming an uninstalled department is rejected, and the CEO must replan.
- **A department with no activation** gets no model calls, no memory opens, and no skill queries. Its Hermes profiles spawn no workers.
- **Gates do not depend on the plan.** They fire from rules (§6.3). If Legal was not activated and a trigger fires, the gate engine activates it and logs why. The CEO cannot avoid a gate by leaving a department out.
- **A missing gate department fails closed.** If the Legal pack was removed and a trigger fires, the run blocks and escalates to the human, unless the user set `on_unavailable` otherwise.
- **Under-activation is recoverable.** A head can escalate to the CEO, who issues a logged plan amendment.
- **Chief of Staff and Finance Lead are mostly deterministic.** Progress and token totals come from the ledger, so their models run only for escalations and explicit requests.

**Required tests** (Phase 2 unless noted):

| Test | Asserts |
|---|---|
| Code-only goal | Marketing and Sales have zero model calls, memory opens and skill queries, shown from the audit log |
| Code-only goal that handles personal data | Legal activates though the plan omitted it |
| Plan omits Legal for public-facing output | Output cannot reach `DONE` without Legal's verdict |
| Legal pack removed and trigger fires | Run blocks and escalates |
| Plan names an uninstalled department | Plan is rejected |
| QA gate | Always runs for an activated department, and owner never equals reviewer |
| Hermes, code-only goal (Phase 5) | No cards assigned to Marketing or Sales profiles |

### 5.4 Org Generator: from a business description to a company

**Status: approved by the owner on 2026-11-06, and being built as Phase 4b.**

```mermaid
flowchart TD
  A["trypro init 'a YouTube Shorts content company'"] --> B[Choose the closest template<br/>local embeddings, no model call]
  B --> C[Org Architect<br/>returns an OrgProposal, not YAML]
  C --> D[Compiler adds Executive, Legal, QA,<br/>tools, escalation and budget]
  D --> E{org/v1 spec and<br/>company policy}
  E -->|invalid, 2 retries at most| C
  E -->|valid| F[Match loadouts for every role]
  F --> G[Discover skills for gaps<br/>through skill-customs, into quarantine]
  G --> H[One review screen]
  H -->|edit| E
  H -->|regenerate, with a reason| C
  H -->|approve, at a terminal| I[Write org/ and skills/decisions.yaml]
  I --> J["trypro run '...'"]
```

#### The Org Architect

**It is a bootstrap step, not a pipeline run.** You asked for the CEO to run it, but the pipeline needs a company before it can run, and at this point there isn't one. So it uses the fixed Executive pack's CEO prompt with one narrow purpose, the same LLM client, its own token ceiling, and its own hash-chained audit log. It sees the description and the template, and no skill text or web content, so nothing it reads can inject instructions into the org.

**The model never writes `org.yaml`.** It returns a narrow `OrgProposal`, and a compiler writes the files. This differs slightly from the brief's wording, and it's the main safety decision here: every generated role's text becomes a system prompt, and `org.yaml` can name endpoints and keys. It also saves tokens, because the model describes changes to a template instead of repeating it.

| The model decides | The compiler decides, and the model can't change |
|---|---|
| Which template departments to keep, rename or remove | Executive department, unmodified from its pack |
| New departments and roles: id, name, mission, responsibilities | Legal department and gate: all three triggers, `on_unavailable: block` |
| `skill_needs` and `allowed_skill_categories` | A `qa_gate` on every department that produces work |
| `outputs`, from the existing artifact types only | `allowed_tools`, from the role's kind: head, worker or reviewer. Never a shell |
| A role's reviewer, from a fixed list | Escalation: worker, head, CEO, person |
| `activation` for non-mandatory departments | `pipeline`, including the token ceiling, and the system prompt wrapper around the role's text |

`llm_overrides` and `x-` fields can't be expressed in an `OrgProposal` at all. A model that could write them could send the default key to an endpoint of its choosing.

**Company policy is separate from the spec.** `org/v1` deliberately allows a company with no Legal department, because you decided departments are removable. So "always Executive, QA gates and Legal" becomes a second check, `trypro org validate --policy`, with its own codes. `trypro init` refuses to activate anything that fails it, including after a person's edits on the review screen. Someone can still remove Legal by hand later, as they can today. Policy also caps a generated company's size, proposed at 25 roles, because every extra role adds to the CEO's prompts.

**Without a key,** `trypro init "..."` stops, names the closest template, and gives the command `trypro init --template <id>`, which needs no key and costs nothing. It doesn't quietly hand back an unadapted template.

#### Templates

Each template is one file, `templates/<id>/template.yaml`: a name, a description, example businesses, and departments written in the same form as an `OrgProposal`. So a template can't express anything a model couldn't, which keeps contributions safe to accept. It's canonical at the repo root and copied into the package like `spec/`, with a drift test, so `pip install` ships it. `general-company` is built from the existing packs, and `--packs` keeps working. Plain `trypro init`, with no description, still creates the default company straight away, because it involves no model and no third-party skills. The owner confirmed this. Templates are a contribution path: every template must pass the policy and a dry run in CI, and new industries get seeded good first issues, which stay unposted until the repo is recreated.

**Flagship, `youtube-shorts-studio`:**

| Department | Roles | Output in v0.1 |
|---|---|---|
| Executive | CEO, chief of staff | Plan, sign-off |
| Trend Research | Head, trend analyst | Topic research, with sources |
| Scriptwriting | Head, scriptwriter | Scripts, storyboards |
| Production | Producer (head), editor, thumbnail designer | Edit notes, thumbnail briefs |
| Distribution | Head, title and SEO, scheduler | Titles, descriptions, upload schedule |
| Analytics | Analyst (head) | Measurement plan, or analysis of files you provide |
| QA | QA lead, reviewer | Reviews of the departments above |
| Legal & Compliance | Counsel | Checks on copyright, claims and platform rules |

QA is a real department here, so a separate role reviews the work, rather than the department head who assigned it. The pipeline is already ready for this: a search of the code found no department names in core, apart from the Legal default in `packs/` and a fallback in the scripted test model.

#### Provisioning skills

After the org validates, for every role: run loadout matching (§9.6), log gaps, then run discovery for the gaps through `skill-customs`. Downloads go into quarantine and aren't run. Nothing reaches a role without the review screen.

**The repo ships zero skills today:** `skills.lock` is empty. So on a fresh clone, every skill has to come from discovery. Three things follow:

- **Categories.** Matching reached 86–91% precision with categories a person assigned, and about 45% on wording alone (§9.6). Most public skills declare no category, and your rule hides those until a person assigns one. So the generator *proposes* a category, from the discovery search that found the skill, and the review screen labels it as proposed. Approving is the person's assignment. **Precision with proposed categories is unmeasured,** and it's an exit criterion for this phase.
- **Some roles will have no good skill.** The screen shows that as a gap. A thumbnail designer may well end up with nothing. The promise is "vetted skills where good ones exist, and a list of what's missing", and the README will say that rather than "the right skills for every role".
- **Discovery searches inside repositories.** The first version used GitHub's repository search, and for the Shorts company 12 searches found 3 repositories and 7 skills, with nothing for the scriptwriter. Most skills sit inside large collections, whose descriptions don't mention them. Code search for `SKILL.md` files found 23 skills in 23 repositories from 20 searches, with candidates for the scriptwriter, editor, and thumbnail designer. That's one company, on 2026-11-06. The scan quarantined 7 of those, and 3 more ship scripts.
- **Discovery uses the network, which needs care.** It sends `skill_needs` phrases to GitHub, never the business description, and prints that it's doing so. It searches one need per role in turn, working departments first, so a limit still reaches every role. `--no-discover` turns it off. Without `GITHUB_TOKEN` it skips discovery with a clear message, because unauthenticated search is heavily rate limited. It reuses queries by category and stops at a time limit, and a slow or failed search leaves gaps rather than failing `init`.
- **Embeddings alone miss too much.** Matching found 43% of what it could on real skills, which isn't enough for this end goal. So embeddings now only shortlist candidates for each role, and the cheap model judges each one's fit, with a one-line reason shown on the review screen. A skill's text reaches that model as untrusted data, and its answer can only choose among the shortlist. The result is cached by role, skill content and model, so it reruns only when one changes, and its tokens are reported.

#### One review screen

```text
Proposed company: Shorts Studio   (adapted from youtube-shorts-studio, match 0.81)
Generated with 14,203 tokens.     Estimated per typical run: 90,000-190,000 tokens (not measured)

Executive
└─ CEO
   ├─ Trend Research
   │  └─ Head ── trend analyst
   │       trend-scan        0.71  needs "find rising topics", category research (proposed)
   ├─ Scriptwriting
   │  └─ Head ── scriptwriter
   │       short-form-hooks  0.66  needs "write a hook", category copywriting (declared)
   ├─ Production
   │  └─ Producer ── editor ── thumbnail designer
   │       GAP: no skill found for "brief a thumbnail"
   ...

Not included in approval:
  QUARANTINED  viral-hacks      scanner: hidden instruction in SKILL.md
  HAS SCRIPTS  video-cutter     needs a shell, and no role has one in v0.1

[a]pprove   [e]dit   [r]egenerate   [q]uit
```

The figures above illustrate the layout and aren't real results. Everything before approval stays in `.trypro/proposals/<id>/`. `org/` and `skills/decisions.yaml` are written only on approval, so **nothing activates before it**.

| Action | What happens |
|---|---|
| Approve | Activates the org, the listed loadouts, and the proposed categories. Needs a terminal and a typed confirmation |
| Edit | Opens the proposal in `$EDITOR`, then revalidates against the spec and policy, and reruns matching |
| Regenerate | Asks for a reason and calls the model again. Each one is a paid call, and the screen shows the running total |
| Quit | Keeps the proposal, and `trypro init --review <id>` reopens it |

Approval can't come from an agent. Without a terminal, `init` stops at the proposal and exits non-zero. Approving is **one step, but it covers less than everything**: quarantined skills are never included, and a skill with scripts needs its own explicit approval. No role has a shell in v0.1, so such a skill can't run even when approved. The screen says so, and approval for them arrives with the shell tool.

#### The token estimate

It's calculated with no model call: this org's real prompt sizes (`scripts/measure_prompts.py`), times the steps a scripted dry run takes, scaled by the ratio of prompts to total tokens in measured runs. It shows as a range and is labelled as an estimate. Today it would rest on **one measured run**, of three roles. Real runs of the flagship recalibrate it, and §13 will state how far off it was. The prompt-size test also has to apply per org, since it now assumes the default 22 roles.

#### Files as deliverables

Roles already write to a run's workspace, but that's in `.trypro/runs/<id>/workspace`, and `trypro run` only prints a summary. **New:** when a run finishes, it exports workspace files and artifact bodies to `deliverables/<run-id>/`, with an `index.md` linking each file to the role that made it and the reviewer that passed it.

No new artifact types are needed. A script is a `PublicContent`, and research is a `ResearchReport`. **The limit is that a schema can check a storyboard's shape, not its quality.** QA reads it against the definition of done.

#### Research needs a source of facts

**No role has a tool that can reach the web today.** Asked for "this week's" AI tools, Trend Research would answer from the model's training data, and could present stale or invented trends as current. That would quietly undermine the flagship. What's proposed:

- An `inputs/` folder any run can read, with the roles' prompts requiring them to say which claims came from it and which didn't.
- An optional read-only `web_search` tool, off by default, with its own key, and its results wrapped as untrusted text. Only research roles get it.
- The flagship example runs from a checked-in `inputs/` file, so it reproduces with no search key.
- **Current claims need a source and a date.** A `ResearchReport` is rejected if a current or trend claim has neither and isn't labelled unverified, or if an unverified one is called "this week's trend". This is checked in code, in English and Indonesian to start with, so other languages rely on the role's instructions and QA until rules are added. A model could also avoid the words, or invent a citation, so each exported research file marks every source as retrieved during the run or not. Search uses Brave's API, tested only against a stand-in, because no search key has been available.

Analytics has the same limit: with no channel data, it writes a measurement plan.

#### Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Skill precision falls without human categories | The "right skills" promise rests on it | Proposed categories are labelled, a person approves, and it's measured before any claim |
| Valid isn't good | A company can pass every schema and still be badly designed | Paid evaluation on businesses with no template, with you judging the results |
| Fabricated research | The flagship's first step has no data source | `inputs/`, the optional `web_search`, and claims labelled by source |
| Token cost of a large goal | The smallest live run, with three roles and one simple task, took 31,360 tokens. Five Shorts across six departments could reach the 200,000 default ceiling and block | Tasks grouped by deliverable type, not by Short; measure and report it; say so in the README if it's expensive |
| Approval fatigue | One-step approval of many skills invites a quick yes, and scanners can be bypassed | Excluded and flagged items are listed apart, and the number of skills being approved is shown |
| Prompt injection through generated text | Generated text becomes system prompts | `OrgProposal`, the compiler, and no outside content reaching the Architect |
| A larger surface for discovery | It downloads from strangers' repos on a first run | Full SHA pinning, license allowlist, scan, and quarantine, none of which change; `--no-discover` |
| "One command" overstates it | Install and a key come first, and the embedding model downloads on first use (about 0.22 GB) | The README gives the real steps, and the time is measured on a clean machine |
| Adapters could expose approval | MCP should be able to generate a company | MCP gets `org_propose` and `org_proposal_get`, and never approve. A person approves at a terminal |

## 6. Pipeline state machine

### 6.1 Run lifecycle

```mermaid
stateDiagram-v2
  [*] --> Intake
  Intake --> Planning: GoalCharter valid
  Planning --> Dispatch: Plan valid (acyclic DAG, installed departments)
  Dispatch --> SkillResolution: TaskBriefs issued
  SkillResolution --> Execution: skills resolved or none found
  Execution --> Review: artifacts submitted
  Review --> Execution: changes requested, under max_rework
  Review --> Dispatch: more packages unblocked
  Review --> Integration: all packages approved
  Integration --> Review: final deliverable needs a gate
  Integration --> Retrospective: FinalDeliverable reported
  Retrospective --> Done: lessons stored, proposals queued
  Done --> [*]

  Intake --> Blocked: needs human
  Planning --> Blocked: needs human
  Execution --> Blocked: escalation, or budget exceeded
  Review --> Blocked: max_rework reached, or gate unavailable
  Blocked --> Planning: plan amended
  Blocked --> Execution: resolved
  Blocked --> Failed: human abandons
  Failed --> [*]
```

### 6.2 Task lifecycle

```mermaid
stateDiagram-v2
  [*] --> Pending
  Pending --> ResolvingSkills: dependencies approved
  ResolvingSkills --> Ready
  Ready --> Running: orchestrator releases
  Running --> Submitted: artifact valid
  Running --> Running: invalid artifact, retry counted
  Running --> Escalated: owner escalates
  Submitted --> InReview
  InReview --> Approved: all required gates pass
  InReview --> Running: changes requested
  InReview --> Escalated: max_rework reached
  Escalated --> Running: resolved
  Escalated --> Failed: abandoned
  Approved --> [*]
  Failed --> [*]
```

### 6.3 Gates

| Gate | Fires when | Reviewer |
|---|---|---|
| Department QA | Every task result | `qa_gate.reviewer`, never the owner |
| Legal | `public_facing`, `touches_license`, or `touches_personal_data` is true | `legal/counsel` |
| CEO sign-off | Final deliverable | `executive/ceo` produces it. A deterministic check then requires a result for every success criterion, and Legal reviews it if any flag is set anywhere in the run, including in the goal |

An agent could understate a trigger flag, so flags are the union of three inputs: what the owner declares, what the Plan declares for the package, and a deterministic check (e.g. the output type is `PublicContent`, a changed file is a license file, a schema field matches a personal-data pattern). A flag can be added but never cleared, and only by a human can a gate be waived. Waivers are logged. Legal output carries a notice that it is not legal advice.

### 6.4 Core invariants

These are enforced by the orchestrator and tested. They do not rely on prompts.

1. A task has exactly one owner, one definition of done and one reviewer.
2. A role can address only its own head or tasks assigned to it. Crossing departments requires a `Handoff` between heads, and it is logged.
3. Parallelism is decided only by the orchestrator: tasks with no dependency path, up to `max_parallel_tasks`.
4. An artifact that fails schema validation is not stored as a result.
5. Nothing reaches `Approved` without the required verdicts.
6. Every transition appends a hash-chained audit record, so later edits to the log are detectable. The ledger stores the latest hash, so removing records from the end is detectable too.
7. A run can't loop forever. Each repeat escalation of a task goes one level higher (head, then CEO, then human), and no new model call starts while a run waits for a human.
8. Only a human decision clears a blocked run. Results still in flight are accepted, but they don't unblock it.

### 6.5 How core stays runtime-agnostic

```python
def advance(state: RunState, event: Event, org: Org) -> tuple[RunState, list[Action]]: ...
```

`advance` is pure. Actions include `InvokeRole`, `RunGate`, `RecallMemory`, `AskHuman` and `Finish`. The ledger saves state after every event, so any run can resume after a crash or be advanced one tool call at a time.

## 7. Artifact schemas

Pydantic v2 models are the source of truth. JSON Schema is generated into `spec/artifacts-v1/`, and CI fails if the two drift.

**Envelope (all artifacts):**

| Field | Notes |
|---|---|
| `artifact_id`, `type`, `schema_version` | ID is a content-addressed hash |
| `run_id`, `task_id` | Task ID is null for run-level artifacts |
| `produced_by` | `department/role` |
| `attested_by` | `runtime` when the adapter fixed the identity, `claimed` when it is self-reported (§10.3) |
| `language` | BCP 47 tag |
| `parents` | Artifact IDs this one was derived from |
| `flags` | `public_facing`, `touches_license`, `touches_personal_data` |
| `payload` | Type-specific, below |

**Payloads:**

| Type | Producer | Key fields |
|---|---|---|
| `GoalCharter` | CEO | `original_goal`, `restated_goal`, `user_language`, `constraints[]`, `success_criteria[]` |
| `Plan` | CEO | `activated_departments[{id, reason}]`, `work_packages[{id, title, owner_department, depends_on[], acceptance[], flags}]` |
| `TaskPlan` | Head | `work_package_id`, `tasks[{id, title, owner_role, definition_of_done[], depends_on[], output_type, flags}]` |
| `TaskBrief` | Orchestrator, from a `TaskPlan` | `task_id`, `work_package_id`, `title`, `owner_role`, `reviewer_role`, `definition_of_done[]`, `depends_on[]`, `output_type`. The reviewer comes from the org, so a head can't choose who reviews its own team |
| `SkillResolution` | Orchestrator | `query`, `candidates[{skill_id, score, trust}]`, `selected[]`, `reason` |
| `TaskReport` | Role | `summary`, `outputs[]`, `done_criteria_met[{criterion, met, evidence}]`, `open_issues[]` |
| `CodeChange` | Engineering | `files[{path, sha256}]`, `tests[{command, result}]` |
| `PublicContent` | Marketing, Sales, Support | `audience`, `channel`, `locale`, `body_ref`, `claims[{claim, source}]` |
| `ResearchReport` | Research | `question`, `findings[{statement, confidence, sources[]}]` |
| `ReviewVerdict` | Reviewer | `gate`, `subject_artifact_id`, `decision` (`approve`, `changes_requested`, `block`), `issues[{severity, description}]` |
| `Handoff` | Head | `from_department`, `to_department`, `request`, `acceptance[]`, `artifact_refs[]`. Creates a work package in the other department, and the requester resumes when it is approved |
| `Escalation` | Any role | `reason`, `options[]`, `recommendation`, `blocking` |
| `EscalationResolution` | The role escalated to | `decision` (`resume`, `abandon`, `escalate_further`, `amend_plan`), `guidance`, `activate_departments[]`, `added_work_packages[]`. Only the root role can amend the plan |
| `FinalDeliverable` | CEO | `summary_in_user_language`, `artifact_refs[]`, `criteria_results[]`, `known_limits[]` |
| `Retrospective` | CEO | `lessons[]`, `procedures[]`, `skill_proposals[]` |

**Language:** schema keys are English. Free-text fields use the goal's language by default (`pipeline.working_language: auto`), and the final report always uses the user's language. CEO detects language as part of `GoalCharter`, so no separate language library is needed.

## 8. Memory

### 8.1 Banks and access

| Store | Read | Write | Backing |
|---|---|---|---|
| `dept-<id>` | Roles in that department | Roles in that department | Mnemosyne bank |
| `company` | All roles | Executive only, through `promote` with a source reference | Mnemosyne bank |
| Skill catalog | All roles, filtered by `allowed_skill_categories` | Orchestrator only | Separate catalog (§8.5) |

Reads and writes go through `MemoryGateway`. The caller's identity comes from the runtime context and is never a model-supplied argument. Every call is audited.

### 8.2 Backend boundary

`MemoryBackend` is a small protocol with a Mnemosyne implementation and a fake for unit tests. Mnemosyne is a dependency, not a fork: `mnemosyne-memory[embeddings]>=4,<5`.

### 8.3 Required Mnemosyne settings

Evidence for each is in §15.

| Setting | Value | Why |
|---|---|---|
| `MNEMOSYNE_EMBEDDING_MODEL` | MiniLM multilingual | Upstream's default is an English model, `bge-small-en-v1.5` |
| `MNEMOSYNE_EMBEDDING_DIM` | `384` | Must match the model |
| `MNEMOSYNE_LEXICAL_GATE_MIN` | `0` | **Critical.** With the default keyword gate, cross-language recall mostly returns nothing (5/40). With `0`, recall@5 was 37/40. |
| Dense score floor, memory only | 0.28 for MiniLM | With the gate off, nonsense queries return a full page of results, and recall results go straight into a task's context. **The floor is a weak filter:** on held-out queries it rejected 7 of 10 nonsense queries, because correct hits (as low as 0.283) and nonsense (as high as 0.336) overlap. It removes the worst noise, and can't detect "no match" reliably. |
| `MNEMOSYNE_DATA_DIR` | `$TRYPRO_HOME/memory` | Keeps data out of `~/.hermes` |
| `MNEMOSYNE_WM_TTL_HOURS`, `MNEMOSYNE_WM_MAX_ITEMS` | Effectively no limit | By default, unconsolidated rows are trimmed after 168 hours. Consolidating them instead could involve an LLM, so expiry is turned off. A test backdates a memory and confirms it survives. |

Mnemosyne's `config.yaml` can outrank the environment, and it falls back to keywords silently if embeddings are unavailable. So at startup `MnemosyneBackend` checks the effective values after import: the model, the expiry, whether embeddings are available, and that text stays local. Any failure stops memory from starting.

### 8.4 Keeping text local

Mnemosyne routes embeddings to a remote API when the model name looks like an API model, when `MNEMOSYNE_EMBEDDING_API_URL` is set to a non-OpenRouter URL, or when `MNEMOSYNE_EMBEDDINGS_VIA_API` is on. `MemoryGateway`'s backend checks these conditions at startup and refuses to start unless `memory.remote_allowed: true` is set in `trypro.yaml`. The only expected network call in the local path is the model download on first use, and `trypro init` does that up front, so the user sees it. A test blocks all sockets and confirms remembering and recalling still work, with `OPENAI_API_KEY` set.

### 8.5 Decision: the skill catalog is not a memory bank

The brief asked for a change like this to be proposed with evidence before switching, and the owner approved it. Memory banks stay on Mnemosyne. Only the skill index changes.

| | Mnemosyne bank (gate off) | Plain cosine, same model |
|---|---|---|
| Recall@5, 40 queries in 5 languages | 37/40 | 39/40 |
| Top-1 | 29/40 | 35/40 |
| English recall@5 | 6/8 | 8/8 |

That set was small, so a larger one followed in Phase 3 (200 queries, 100 of them held out). With a score floor on both, recall@5 was 91/100 for the catalog and 87/100 for the bank, and English 14/20 against 11/20. The structural reasons are still the stronger argument:

- A catalog entry should not be subject to working-memory expiry, consolidation or summarization.
- Recall updates `recall_count` and `last_recalled`, but catalog ranking should be deterministic for reproducible benchmarks.
- A catalog must filter on license, trust and category before ranking, which are structured columns rather than memory text.

**Implementation:** plain SQLite table with vectors stored as blobs, and NumPy cosine (already installed through `fastembed`). No new dependency. The catalog is rebuilt from `skills.lock`, so it holds no state of its own. **The catalog has no score floor.** The floor cost 2 of 100 held-out hits, both in English, in return for filtering out noise that a skill search can tolerate: a weak suggestion carries its score and an agent can ignore it, while a filtered-out skill is lost for good. Without it, nonsense queries also get results, each with a low score.

Phase 4 fills the catalog from `skills.lock`. Evidence: [`docs/research/phase3-retrieval/`](research/phase3-retrieval/).

### 8.6 Model selection

| Model | Dimensions | Download | fastembed support | Status |
|---|---|---|---|---|
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | 0.22 GB | Yes | Default |
| `intfloat/multilingual-e5-large` | 1024 | 2.24 GB | Yes | Documented upgrade; needs `query:`/`passage:` prefixes |
| `BAAI/bge-m3` | not verified | not verified | Not in the lists I checked | Phase 3: measured only if it can run with an optional dependency |

Sizes come from fastembed's model tables. Changing models changes the vector space, so memory and the catalog refuse to open with a different model than they were built with. Moving existing memories to a new model isn't built yet.

**Phase 3 results for the catalog with MiniLM and no floor,** on 100 held-out queries:

| Language | Recall@5 | Review status |
|---|---|---|
| Indonesian | 20/20 | Pending the owner's review |
| Spanish | 19/20 | Not natively reviewed |
| Chinese | 19/20 | Not natively reviewed |
| Arabic | 19/20 | Not natively reviewed |
| English | 16/20 | Not needed |
| **Total** | **93/100** | |

**The 80% target is met in every language, English only just.** Because the held-out set had been seen when the floor was removed, English was confirmed on 20 fresh queries written before measuring: 16/20, exactly at the target. The same author wrote both, so this isn't independent. The languages aren't directly comparable either, because the English queries are the most idiomatic in the set. These are hand-written queries over a 40-skill catalog, so they show the approach works, and they aren't a claim about general accuracy.

`e5-large` scored 98/100 on the same queries, at about 10 times the download and 2.6 times the memory. See [docs/embeddings.md](embeddings.md).

## 9. Skill supply chain

### 9.1 Package

`skill-customs` is the lockfile, license and pinning layer for agent skills. It is not positioned as another scanner: other scanners plug in, and ours is the default. It works with no Try Professional code installed, with a CLI and a Python API. See [its README](../packages/skill-customs/README.md).

```text
skill-customs scan ./my-skill            # findings; exit 0 pass, 1 review, 2 block
skill-customs license ./my-skill         # allowlist verdict and commercial-use flag
skill-customs add owner/repo --path skills/foo --commit <40-hex>
skill-customs resolve owner/repo@main    # prints a SHA; installs nothing
skill-customs verify                     # CI: installed tree matches skills.lock
skill-customs approve <skill-id>         # human only; binds to the content hash
skill-customs list | attribution | discover
```

```python
from skill_customs import Policy, Lockfile, scan, check_license, pin

policy = Policy.default()
report = scan("./my-skill", policy)
verdict = check_license("./my-skill", policy)
entry = pin("owner/repo", path="skills/foo", commit="<40-hex>", dest="./skills", policy=policy)
Lockfile.load("skills.lock").add(entry).save()
```

**Enforced separation:** a CI job installs `skill-customs` alone in a clean environment and runs its tests. A test fails if it imports `try_professional`, and another fails if `try_professional` uses anything outside its public API.

### 9.2 Vetting flow

```mermaid
flowchart TD
  d[Discover: GitHub API, rank per category, top 40] --> f
  m[Manual: skill-customs add] --> f
  p[Proposed by agent after a run] --> s
  f[Fetch tarball by full SHA<br/>no clone, no hooks, safe extraction] --> l
  l{License on allowlist?}
  l -->|No or unknown| r[Rejected: not installed<br/>non-commercial terms flagged in report]
  l -->|Yes| s[Static scan<br/>nothing is executed]
  s --> x{Executable content?}
  x -->|Yes| q[quarantined]
  x -->|No| v{Findings}
  v -->|Block or review| q
  v -->|None| i[indexed: instructions only]
  p -.->|always| q
  q -->|Human reviews and approves| t[verified]
  i -->|Human reviews and approves| t
  t -->|Content hash changes| s
  i -->|Content hash changes| s
```

### 9.3 Details

**Fetching.** Only a full 40-character SHA is accepted. Branches and tags are rejected, and `resolve` exists to turn a ref into a SHA explicitly. Archives are fetched by tarball, so no git hooks can run. Extraction rejects symlinks, path traversal and oversized files.

**License gate.** Reads the SPDX ID from the skill's frontmatter, its own `LICENSE`, and the repo `LICENSE`. If they disagree, the stricter reading applies. An unknown license is rejected. `THIRD_PARTY_SKILLS.md` is generated from the lockfile.

**Scanner.** Looks for embedded instructions to exfiltrate data, override system prompts, fetch remote code, or touch credentials. It also flags hidden Unicode (tag characters, bidirectional controls), encoded blobs, and executable content (`scripts/`, shebangs, script extensions, `curl | sh` patterns in code fences). Rule packs cover at least English, Indonesian, Spanish, Chinese and Arabic, because an English-only scanner would be easy to bypass in a multilingual framework. External scanners can be added through a plugin interface.

**Trust.** `quarantined`, `indexed` and `verified`, as in the brief. An approval records who approved and the content hash, so any change to the content drops the approval.

**Approval is human-only.** `approve` is a CLI command. It is not exposed as an MCP tool or agent tool, and a test asserts this. Agent-proposed skills land in `skills/proposed/` as `quarantined`.

**Tested on real skills.** 24 skills from `anthropics/skills` and `obra/superpowers`: 13 indexed, 8 quarantined, and 3 rejected on license, including the proprietary `docx` and `pdf` skills. An early version of the rules blocked ordinary code like `os.environ["GITHUB_TOKEN"]`, so those lines became benign test cases. Some flags are still false positives: `claude-api`, a skill about prompting, trips override rules by quoting the phrases they look for. That's an acceptable cost, because the result is a person looking at it.

**Runtime treatment.** Skill text is data, so it is delivered inside a labelled untrusted block. Prompts tell the model it cannot override role or tool rules, but **delimiters reduce prompt injection rather than prevent it.** The real barrier is that tool permissions are enforced by the runtime from the role file, whatever the model was persuaded to attempt. A passing scan means no known signal was found. It does not mean a skill is safe, and the docs will say so.

**Discovery.** Ranks by stars, recency, and maintenance signals (recent commits, open-issue ratio, repo age, license present), keeping the top 40 per category from `skill-discovery.yaml`. Stars can be bought, so ranking only nominates candidates, and every candidate still passes the gates. No daemon runs: `.github/workflows/discover-skills.yml` refreshes the list on a schedule you can change, and only produces a list.

### 9.4 `skills.lock`

JSON with sorted keys for clean diffs.

```json
{
  "lock_version": 1,
  "skills": {
    "owner/repo/skills/seo-audit": {
      "source": { "host": "github.com", "repo": "owner/repo", "path": "skills/seo-audit", "commit": "<40-hex>" },
      "content_sha256": "<hash of normalized tree>",
      "license": { "spdx": "MIT", "evidence": "repo:LICENSE", "commercial_use": "allowed" },
      "trust": "indexed",
      "scan": { "scanner_version": "0.1.0", "ruleset": "2026.11", "findings": [] },
      "approval": null
    }
  }
}
```

### 9.5 README and adoption

The README opens with a working flow under 5 minutes: install, scan a folder, add a pinned skill, run `verify` in CI. It has short sections for CrewAI, Hermes and MCP clients. Each of those gets a seeded `good first issue`. Where a framework's skill-loading details aren't yet verified, the issue begins by confirming them.

### 9.6 Loadouts: which skills each role gets

Category filtering is too coarse, so each role gets a ranked list of skills that fit its actual job. Roles declare a skill profile (`skill_needs`, `required_tools`, `forbidden_tools`, `max_skills`), which is part of the [role spec](spec/org-v1.md#skill-profile).

```mermaid
flowchart LR
  L[skills.lock] --> C[Catalog]
  D[decisions.yaml<br/>people only] --> M
  C --> M[Match every skill<br/>against every role]
  F[Usage and QA results] --> M
  M --> P[Waiting for a person]
  P -->|trypro skills admit| A[Active loadout]
  A -->|names and short descriptions| R[Role]
  R -->|skill_get| A
  R -->|used, passed QA| F
  R -->|no skill fits| G[Gap]
  H[Head or CEO] -.->|propose only| P
```

**Score.** Embedding similarity between the skill and the best of the role's `skill_needs` (responsibilities count at 0.9), plus 0.08 if the skill's category is one the role allows or minus 0.10 if not, plus up to 0.05 either way from past QA results once there are at least 3. A skill scoring under 0.44 isn't suggested. Each skill is suggested to at most 4 roles, and only those within 0.10 of its best score.

**Excluded outright:** quarantined skills, uncategorized skills, skills needing a tool the role isn't allowed or is forbidden, and skills a person rejected for that role. A tool name that isn't recognized counts as one the role lacks. A skill that ships scripts needs a shell, and no default role has one, so those skills are excluded everywhere for now.

**Collapsing.** Candidates with a similarity of 0.92 or more collapse into one, keeping an approved copy if there is one, and otherwise the better match. The list is then capped at `max_skills`.

**People decide.** Matching only suggests. A skill enters a loadout when a person runs `trypro skills admit`, which needs a terminal and a typed confirmation. The decision is recorded in `skills/decisions.yaml` for review in git, and tied to the skill's content hash, so any change sends it back to waiting. The CEO and department heads can propose through the `skill_propose` tool, which changes nothing until a person acts. No agent tool can admit, reject, or categorize.

**What a role can reach.** Only its active loadout. Names and 70-character descriptions go in its prompt, labelled as untrusted because third parties wrote them, and the full skill is fetched with `skill_get`. A role with no skills isn't offered the skill tools, so it pays nothing for them. A full 8-skill loadout fits the same prompt budget as a run with none.

**Uncategorized skills.** A skill declaring no category is `uncategorized` until a person runs `trypro skills categorize`. Only the CEO and department heads can see these, always labelled, so they can propose them. Most public skills declare no category, so expect to assign most of them by hand.

**Self-declared categories.** A category in a skill's own frontmatter is the author's claim. It affects ranking by a small amount and can't get a skill in without a person admitting it.

**Feedback.** Each `skill_get` for a task is recorded, along with that task's latest QA verdict, so rework that ends in approval counts as a pass. `trypro skills match` re-ranks using this, and flags skills unused for 20 runs of that role for removal (`--unused-runs` changes this). It never removes anything by itself.

**Gaps.** If a task's best skill scores under 0.40, the role and task are logged. `trypro skills gaps` lists them, and `--discover` searches GitHub for candidates. That sends the task descriptions to GitHub, so it only happens with the flag, and results still go through `skill-customs` and a person.

**Measured** ([results](research/loadouts/)). Thresholds were chosen on separate data, and these were each scored once afterward. Scores are for what matching suggests, before a person decides:

| Held-out set | Precision | Recall |
|---|---|---|
| 14 real skills from a repository not used for tuning, labelled before scoring | 10/11 (91%) | 10/23 (43%) |
| 32 hand-written skills across all 22 roles, English and Indonesian | 30/32 (94%) | 30/35 (86%) |

**Read the real-skill row as the honest one, and still with care.** It's a small set, one person labelled it, and that person also assigned the categories, as a person must in real use. Tools were set aside to measure matching alone, and in a real run, skills needing a shell are excluded. Recall is low by design: a person reviews every suggestion, and a head can propose a skill that was missed.

**Embeddings alone aren't enough (2026-11-06).** A recall of 43% doesn't meet the end goal, so the owner added a re-rank: embeddings shortlist up to 20 skills per role, and the cheap model chooses those that fit, with a one-line reason. It runs at `trypro init` and on `trypro skills match`, and caches by role, skill content, and model, so it only spends when one changes, and it reports its tokens. `--no-rerank` falls back to the numbers above, and so does `trypro init --template`. Skill text reaches the judge as untrusted data, after the scan, and a person still approves the result. This also removes the need for a person's category before matching. See §5.4.

**The re-rank missed its target.** On 30 fresh skills from a third repository, scored once, it found 20 of 39 matches (51%) at 69% precision, against the 60% recall set in advance. Embeddings with a person's categories found 16 of 39 (41%) at 70%. So it adds about 10 points with no human categories, but not yet enough. Details are in [`docs/research/loadouts/`](research/loadouts/).

**Categories do most of the work.** A first version chose a threshold of 0.68 on the hand-written fixture alone, and scored 26/26 there, because one author wrote both the skills and the `skill_needs`. On real skills that threshold suggested **nothing**. Real descriptions say when to use a skill rather than what it does, so they score lower. Wording alone then reached about 45% precision, with code review matching Compliance and Sales. Adding a person's category raised it to 86% on the tuning set. So assigning categories isn't busywork: it's what makes matching useful.

## 10. Adapters

### 10.1 Interface

Runtimes differ in who owns the loop, so the interface states that explicitly.

```python
class RuntimeAdapter(Protocol):
    name: str
    mode: Literal["push", "pull", "queue"]

    def capabilities(self) -> Capabilities: ...  # identity attestation, tool enforcement, parallelism
    def provision(self, org: Org, home: Path) -> ProvisionReport: ...
    def verify(self) -> VerifyReport: ...  # used by each adapter's smoke test
    def deliver(self, action: Action) -> Delivery: ...
    def collect(self) -> Iterable[Event]: ...
```

| Mode | Who runs the model | Example |
|---|---|---|
| `push` | The adapter calls the model and returns the result | API runner |
| `pull` | The client asks for the next action, does it, and submits | MCP |
| `queue` | The adapter enqueues, and a worker picks it up later | Hermes |

`adapters/template/` is a copyable, tested implementation with a README and smoke test.

### 10.2 API runner

Provider-agnostic config with `anthropic` and `openai_compatible` clients. By default every role uses the `TRYPRO_LLM_*` values from the environment or `.env`, and `llm_overrides` in `org.yaml` can give a department or role its own model, key variable, or endpoint (see the [spec](spec/org-v1.md#model-overrides)). A role's override wins over its department's, one field at a time. An override with a new endpoint must also name its key variable, so a shared org file can't quietly send the default key elsewhere. Keys come only from environment variables, and are redacted from provider error messages. Identity is attested and tool permissions are fully enforced. There is no shell tool yet. A Docker-backed one is planned as opt-in.

- **Anthropic** uses the official `anthropic` SDK as an optional extra (`try-professional[anthropic]`), rather than raw HTTP as first proposed. That API changes quickly, and the SDK tracks it.
- **OpenAI-compatible APIs** (OpenAI, Grok, local servers) use `httpx` directly, so they need no extra dependency.
- **Refusal fallbacks:** the default Anthropic config sets `fallbacks: default`, which lets the API retry a declined request on another model. The audit log records the model that answered. Set `fallbacks: null` to turn it off.
- **Artifacts are submitted through a tool.** Current Claude models reject forced tool choice, so the runner uses automatic tool choice with a prompt instruction, and validates every submission itself. This works the same on every provider.

### 10.3 MCP server

Tools: `org_get`, `run_start`, `run_status`, `run_next_action`, `artifact_submit`, `review_submit`, `handoff_request`, `escalate`, `usage_report`, `memory_recall`, `memory_write`, `memory_promote`, `skill_search`, `skill_get`, `audit_tail`. It uses stdio by default, and is started with `trypro mcp`. See [its README](../packages/try-professional/src/try_professional/adapters/mcp/README.md).

The server can't see what a client's model spends, so token ceilings don't apply in this mode, and `usage_report` tokens are marked as self-reported.

**Org Generator (after Phase 4b):** `org_propose` and `org_proposal_get` let a client generate a company and show the proposal. There's no tool to approve it. A person does that at a terminal, as with every other human decision (§5.4).

Two identity modes:

- **Bound:** started with `trypro mcp --role <role>`, so identity comes from the server's launch config and is recorded as `attested_by: runtime`. Hermes uses this.
- **Operator:** one client plays whichever role `run_next_action` names, which suits Claude Code or Cursor. This is recorded as `attested_by: claimed`. The ordering, schemas and gates remain enforced, but separation between roles is procedural, because one model is playing all of them. The docs and audit log state this plainly.

### 10.4 Hermes

Verified against Hermes and Mnemosyne docs and source on 2026-11-06. Items marked ⚠ need a live test in Phase 5. Hermes is not on this machine's PATH, so nothing here has been run against it yet.

**Org chart to profiles.** One profile per role, however many the approved org has, named `tp-<department>-<role>` with `tp-executive-ceo` on top. `trypro hermes install` reads only an approved org. It runs `hermes profile create <name> --description "<mission>"` and generates each profile's files from `org.yaml`, so it remains the only source of truth:

| Profile file or setting | Generated from |
|---|---|
| `SOUL.md` | Role `system_prompt` |
| `config.yaml`, model | Role `model_tier` |
| `memory.provider: mnemosyne` | Fixed |
| `memory.mnemosyne.tools` | Allowlist without any `mnemosyne_shared_*` tool |
| `mcp_servers.trypro` | `TRYPRO_ROLE`, `TRYPRO_HOME`, and `tools.include` from `allowed_tools` |
| `skills.external_dirs` | `verified` skills for the role's categories only |

**Why profiles and not one profile with subagents.** Hermes warns never to point two agents at one profile, and `delegate_task` children are anonymous with flat depth by default. Profiles provide the named identity the design needs.

**Delegation uses Kanban, not `delegate_task`.** The Kanban board is durable and shared across profiles, with one assignee per card, dependency edges and a review state, which match the pipeline closely.

| Pipeline | Kanban |
|---|---|
| Run | One board |
| Work package | Card assigned to the department head |
| Task | Card assigned to the role, with `parents` from the DAG |
| Review gate | `kanban_request_review` / `kanban_request_changes`, with the reviewer profile named |
| Artifact | `artifact_submit` through the `trypro` MCP tools; the card stores the artifact ID |
| Installed, not invoked | No card means no worker, and no gateway per role |

**Hierarchy enforcement.** Hermes describes its Kanban coordination as peer-based: any profile can read and write any task, and workers avoiding fan-out is a convention. So Hermes can't enforce the hierarchy alone:

1. Heads and the CEO create work through `trypro` tools, which validate against the org and then call Hermes.
2. A reconciler compares the board with the ledger and treats any unknown card as a logged violation.
3. Integration accepts only artifacts tied to tasks in the ledger, so an unsanctioned card can't reach the final deliverable.

**Sharing the company bank despite profile isolation.** I checked `docs/hermes-integration.md` as you asked. Its model has two pieces:

- Separate profile homes get separate private stores. This is what isolation means, and one shared plugin install shares code but not memory.
- The supported sharing path is the **shared surface**: profiles set the same absolute `shared_surface_path`, then use `mnemosyne_shared_*` tools or `shared_surface_read`.

The shared surface alone cannot satisfy your access rules, for three reasons:

- **One surface per profile.** A role needs two shared stores, company and department.
- **Any role can write.** `mnemosyne_shared_remember` would let any role write to it, against the rule that roles write only to their department bank.
- **The default path is already global.** The installable provider defaults to `~/.mnemosyne/data/shared/mnemosyne.db`, so without configuration it is a single store for all profiles.

**Design:** use Mnemosyne's isolation for private memory, and route organizational memory through `trypro`.

```mermaid
flowchart LR
  subgraph eng [tp-engineering-backend profile]
    a1[Worker] --> p1[Mnemosyne provider<br/>private store, no shared tools]
    a1 --> m1[trypro MCP<br/>TRYPRO_ROLE=engineering/backend]
  end
  subgraph ceo [tp-executive-ceo profile]
    a2[Worker] --> p2[Mnemosyne provider<br/>private store, no shared tools]
    a2 --> m2[trypro MCP<br/>TRYPRO_ROLE=executive/ceo]
  end
  m1 --> gw[MemoryGateway]
  m2 --> gw
  gw -->|read and write| d[(dept-engineering)]
  gw -->|read only for backend| c[(company)]
  m2 -.->|promote| gw
  gw -->|promote, Executive only| c
```

- **Private layer.** Each profile keeps its own Mnemosyne store through the native provider, which respects Hermes's rule. `memory.mnemosyne.tools` removes the `mnemosyne_shared_*` tools, so the global default surface is unreachable through them. `shared_surface_read` stays `false`. The filter applies to provider-exposed tools; as defense in depth, each profile also gets its own `shared_surface_path`.
- **Organizational layer.** Company and department banks live under `$TRYPRO_HOME/memory`. They are reachable only through `MemoryGateway`, which is opened by the `trypro` MCP server with identity fixed by that profile's launch config. This enforces read-company, read-own-department and write-own-department. Only Executive can promote into `company`.
- **Context injection.** Mnemosyne's automatic `prefetch` sees only the private store, so the orchestrator retrieves department and company context at dispatch and puts it in `TaskBrief.memory_context`. This works identically on every runtime, and the context used is auditable.
- **Rejected alternative.** A separate shared-surface file per department with company access through the gateway only is workable but splits organizational memory across two mechanisms, for no gain.

**Install and verify.** The install script performs these steps. The verification step fails if any of them is wrong.

1. Check for Hermes at or above the version required by the Mnemosyne catalog entry (`>=0.21.4`).
2. Create or update one profile per role in the approved org.
3. In each: `hermes -p <name> plugins install mnemosyne`, then `hermes -p <name> config set memory.provider mnemosyne`.
4. Write the generated config.

```text
trypro hermes verify
  ✓ hermes --profile <name> memory status      provider active in each profile
  ✓ mnemosyne_diagnose                         private database path differs per profile
  ✓ no mnemosyne_shared_* tool in any profile
  ✓ trypro MCP reports the expected role per profile
  ✓ write from backend, read from QA           visible within the department
  ✓ write from backend, read from marketing    not visible
  ✓ backend writes to company                  refused
  ✓ CEO promotes, marketing reads              visible
  ✓ code-only goal                             no Marketing or Sales cards
```

**⚠ To confirm in Phase 5:**

- Concurrent cards for one assignee (the two-agents-one-profile warning suggests limiting to one per role, which `trypro` can enforce by holding cards back).
- Exact board commands, and which profile's gateway hosts the dispatcher.
- Whether Hermes's Skills Hub lock can coexist with `skills.lock` (`external_dirs` should avoid the conflict).
- Whether a `verified` skill loaded through `external_dirs` is treated as instructions. This is why only `verified` skills go there, and `indexed` skills are reached only through `skill_get` as untrusted data.
- Native Windows behavior for the whole flow.
- Which version to support. PyPI stops at `hermes-agent` 0.19.0, the latest GitHub release is v0.23.0, and the Mnemosyne catalog entry needs `>=0.21.4`. Building v0.23.0 from its tag with `uv` failed on this machine on 2026-11-06, so the supported route is probably the official installer. Not resolved yet.

**Fallback.** If live testing breaks this design, I'll report it before changing direction.

## 11. Benchmark harness

### 11.1 Task suite

25 tasks (within your 20-30 range), frozen before the first scored run, with the suite's hash recorded in every result. Mix: coding, content, research, and a proposal with a legal check.

| Check type | Example |
|---|---|
| Automatic | Hidden tests pass; output validates against a schema; required facts appear |
| Rubric | A separate judge model scores a published rubric, blind to which setup produced the output |

Each task has a `tasks/<id>/` folder with fixtures, prompt, check and declared check type. Rubric results are reported separately from automatic ones, because they are less objective.

**The YouTube Shorts studio is one of the 25.** Goal: five Shorts from a fixed `inputs/` file. Automatic checks cover what can be counted, such as five scripts, a spoken length within a Short, and a title, description and schedule entry for each. A rubric covers quality. The company is **frozen from the template**, not generated for each run, so generation's variance doesn't enter the comparison. `single` and `swarm` get the same goal, inputs, and role prompts. Generation's own tokens are measured and reported separately.

### 11.2 Setups

All three use the same mid-priced model, plus the same tools, tasks, and per-task resource cap. Refusal fallbacks are turned off, because they could switch models mid-run. A refusal counts as a failed step.

| Setup | Definition |
|---|---|
| `single` | One agent with all tools and a neutral system prompt |
| `swarm` | The same role prompts as peers, sharing a message board, any agent can message any other, terminating on consensus or at the cap |
| `hierarchy` | Try Professional with the default org, or the frozen Shorts template for that task |

The swarm has to be a fair baseline, or the comparison is worthless. It gets the same role prompts, and its implementation is published with a design note. Contributors can challenge it, and improvements are welcome even if they make the hierarchy look worse.

### 11.3 Metrics

From one shared event format, recorded by the shared LLM client wrapper:

- Success rate.
- **Tokens per run, as a headline number:** total, by role, cached if the provider reports it, and tokens per successful task. Failed runs still count their tokens. Cost comes from provider-reported usage, with the price table and date recorded in the results.
- Wall-clock time.
- Failed or redone steps: invalid outputs, failed tool calls, rework loops and retries. The counting rule is defined per setup before the first run.

Each setup runs each task at least 5 times, giving 375 runs at the default. Reports show the mean, standard deviation and bootstrap confidence intervals, with paired per-task comparisons. Per-task tables are always included.

### 11.4 Skill-retrieval benchmark

Recall@5 for the same labelled skills queried in English, Indonesian, Spanish, Chinese and Arabic, broken down by language. It needs no LLM key and also runs in CI.

- **Compared:** MiniLM and e5-large (bge-m3 if practical), with Mnemosyne hybrid and plain cosine.
- **Query set:** larger than the Phase 1 set and split into development and held-out queries. Thresholds are tuned only on the development split.
- **Query files:** one file per language, each with a review status. The owner, a native Indonesian speaker, reviews the Indonesian file. The others stay labelled as unreviewed until a native speaker reviews them, and the report shows that label.

### 11.5 Reproducibility and honesty rules

- `trypro bench` runs everything. `--estimate` prints the run count and an estimated cost and spends nothing. Paid runs require `--max-cost`, and the harness stops before a call that could exceed it. `--suite retrieval` needs no LLM key.
- **Spending rule:** the budget is decided after Phase 5. The estimate is shown to the owner first, nothing is spent before approval, and the approved amount is never exceeded.
- Results go to `benchmarks/results/<date>-<model>-<version>/`, with raw events, config and suite hash.
- `docs/benchmarks.md` is generated from those raw results and always shows the model, date and version. CI fails if it doesn't match.
- There is no flag to exclude tasks or setups from a report. Failed and aborted runs count as failures. Results are published wherever the hierarchy loses.
- The README has no benchmark numbers, only a link to `docs/benchmarks.md`. CI enforces this with a lint rule.
- **If the hierarchy costs much more than the flat swarm for the same result, the README says so** in words, next to that link. Reducing tokens per run is ongoing work, and each live test reports them.
- **Paid runs wait for approval.** `--estimate` is free. Everything else that calls the paid API is announced to the owner first.
- A `--mock` mode replays recorded responses to test the harness in CI. Its output is marked as mock and can't be published.

**What "reproduces" means:** model outputs aren't deterministic and hosted models change over time. The command reruns the exact procedure, and results should agree within the reported variance, not digit for digit. The docs will say this.

## 12. Phases and exit criteria

Nothing is marked complete without passing tests. Each phase ends with what was built, what was tested and what is missing.

**Status (2026-11-06):** Phases 1 to 4 pass their tests and are pushed. Loadouts (4a) pass their tests and aren't committed. Phases 2 and 3 have run end to end on a live provider, with memory on. Skills (Phases 4 and 4a) have only run with a scripted model, because `skills.lock` is empty, though matching was measured separately on real public skill files. Phase 5 has started: the MCP server and template adapter pass their tests, uncommitted, and Hermes is paused. **Phase 4b is approved and in progress.** Phase numbers 5 to 7 stay the same, so earlier references still hold.

| Phase | Builds | Exit criteria |
|---|---|---|
| 1 | This document | Your approval |
| 2 | Org spec and JSON Schema, org loader, packs, pipeline, artifacts, audit, API runner, `trypro init` | Conformance fixtures pass; invariants and §5.3 tests pass; an end-to-end run against a scripted model client passes, plus one against a real provider if a key is available |
| 3 | Memory gateway, Mnemosyne backend, skill catalog | Cross-lingual tests pass for the 5 languages; access-rule tests pass; expiry and remote-refusal tests pass; model choice confirmed or reopened |
| 4 | `skill-customs` | Passes alone in a clean environment; malicious-skill fixtures are caught; license, pinning and lockfile tests pass; approval unreachable by agents |
| 4a | Loadouts (§9.6) | Precision reported on a labelled fixture and real skills, in English and Indonesian; prompt budget test passes. **Tests pass, not committed** |
| 4b | Org Generator (§5.4): five templates, Org Architect, compiler, company policy, provisioning, review screen, token estimate, `deliverables/`, `inputs/`, Shorts example | See below |
| 5 | MCP (add `org_propose`, `org_proposal_get`), Hermes, and template adapters | Each adapter's smoke test passes; `trypro hermes verify` passes against a real Hermes install, on a generated org with a different size from the default; the ⚠ list in §10.4 is resolved |
| 6 | Benchmark harness and first results, including the Shorts task | `trypro bench` reproduces; `docs/benchmarks.md` is generated from raw results |
| 7 | Docs, CI, repo templates, seeded issues, releases. The README leads with the Shorts demo, from a real recorded run, and states the v0.1 scope | Clean-machine install works on all 3 operating systems; CI is green; publishing to PyPI and creating issues happen only after your explicit go-ahead |

**Phase 4b status (2026-11-06):** built and committed as unproven (`d75f1b4`). Real GitHub discovery ran once. The re-rank was measured on a real model and missed its 60% recall target, so, as agreed, the paid generations and the Shorts run didn't happen. The Architect and the token estimate have still never run on a real model.

**Phase 4b exit criteria:**

*With no paid calls:*

- All five templates pass the spec, the company policy and a full dry run. A test fails if a template and its packaged copy drift.
- `trypro init --template <id>` works with no key.
- Hostile scripted Architect output is refused or compiled away: dropping Legal, removing a QA gate, adding a shell, `llm_overrides`, waiving a gate, raising the budget, and exceeding the size cap.
- `org/` and `skills/decisions.yaml` are unchanged until approval. Approval is unreachable without a terminal, and no MCP tool can do it.
- Quarantined skills and skills with scripts aren't covered by approval. An uncategorized skill reaches a role only after approval.
- Provisioning with GitHub unreachable or rate limited still ends at the review screen, with gaps listed.
- `trypro run --dry-run` on the Shorts company writes `deliverables/` with its index.

*With paid calls, each announced first and run only with your approval:*

- Re-rank on the real-skill sets (`docs/research/loadouts/run_rerank.py`), with categories hidden, tuning first, and the `fresh` set scored once. Report precision, recall and token cost. The owner's target is recall well above 43% without losing much precision. If recall on `fresh` is under 60%, set in advance as "clearly above 43%", stop here and report.
- Generate 8 businesses, 3 times each, on a token cap the harness enforces (`docs/research/org-generator/run_eval.py`). Four have no close template: an online fashion store, a digital marketing agency, and an Indonesian-language education channel, each with its nearest template held out, and a physiotherapy clinic. Only the first generation of each finds skills and pays for the re-rank, as the owner chose. Report first-try validity, retries, and tokens per generation. The owner reviews, per business, the 3 org charts side by side for consistency, and the one loadout.
- One full run of the Shorts company on `deepseek-v4.1-flash-1017`. Report tokens by role, and how far the estimate was from the result.


**CI (Phase 7, added incrementally from Phase 2):** lint and type checks, tests, secret scanning, dependency audit, `skill-customs verify` and scan on every PR, spec and artifact-schema drift checks, the isolation job and the README claims lint.

**Seeded issues** (`.github/seed-issues.yaml`, created only when you approve, after the repo is recreated): new industry templates, new department packs (Customer Success, HR, Data), `skill-customs` integrations with CrewAI, Hermes and MCP clients, new adapters, new language tests and scanner rule packs.

**ADRs to write:** core without LLM calls; hierarchy over swarm; Kanban for Hermes; organizational memory through a gateway; skill catalog separate from memory; monorepo split; embedding default; spec versioning.

## 13. Known limits

- **A real run costs more than the step count suggests.** The smallest live run took 12 model calls and 31,360 tokens on `deepseek-v4.1-flash-1017`, for 7 steps, with memory on and 9,216 input tokens cached. That was down from 15 calls and 43,820 tokens before prompts were cut by 22%, though that's one run of each. Writing and reading files, rejected outputs, and resending context each add calls. New companies get a default ceiling of 200,000 tokens per run, and a test holds prompts to a size budget.
- **Neither search can reliably say "no match".** See §8.3 and §8.5. An agent can still receive a low-relevance skill, labelled with its score, or weak memory above the floor.
- **Access rules are policy, not a security boundary.** A role with a shell tool could open the SQLite files directly on the same machine. The rules prevent accidents and ordinary misbehavior. Containers are required for stronger isolation.
- **The hierarchy may not win.** It will probably cost more tokens than a single agent on simple tasks. The benchmark exists to measure that.
- **Static scanning can be bypassed.** Paraphrased injection will get past rules. That's why trust levels, runtime tool limits and human review exist.
- **In MCP operator mode, one model plays every role.** Reviewer independence is weaker there and is labelled as such.
- **Loadout accuracy rests on small sets, labelled by one person.** See §9.6. On the fairest test, the model re-rank finds about half of the skills a role should get, with about 7 in 10 suggestions right, so a person's review still matters.
- **Agent counsel is not a lawyer.** Legal gate output is a check, not legal advice.
- **A generated company is a first draft.** Passing the spec and policy shows it's well formed, not that it suits the business. That's why a person approves it. (Phase 4b, not built.)
- **The company produces files in v0.1, and nothing else.** It doesn't render video, upload anything, or read your channel's data. (Phase 4b, not built.)
- **Roles have no web access today.** Research comes from the files you provide and the model's training data, unless the optional search tool is approved, built and switched on (§5.4).
- **Upstream movement.** Mnemosyne and Hermes both change quickly. Versions are bounded, and the `MemoryBackend` and `RuntimeAdapter` boundaries limit the impact.
- **Quickstart timing is unproven.** The model download adds time to `trypro init`, and the README's "60 seconds" claim has to be measured on a clean machine, or it gets reworded.

## 14. Resolved questions

All were resolved by the owner on 2026-11-06.

| # | Question | Decision |
|---|---|---|
| Q1 | Name for the supply-chain package, given `skill-guard` is taken on PyPI | `skill-customs` |
| Q2 | Build it, given the existing tools? | Yes, as the lockfile, license and pinning layer. Other scanners plug in, and ours is the default. |
| Q3 | Separate skill catalog? | Yes, outside Mnemosyne with the same embedding model. Memory banks stay on Mnemosyne. |
| Q4 | Benchmark model and budget | Decided after Phase 5, with `trypro bench --estimate` shown first. Nothing is spent before approval, and the approved amount is never exceeded. The same mid-priced model is used for all three setups. |
| Q5 | GitHub owner | `goranai/try-professional`, private until v0.1.0 |
| Q6 | Translated test queries | Written by the maintainer agent, one file per language. The owner reviews the Indonesian file as a native speaker. |

**Tools reviewed for Q2**, from their READMEs and metadata on 2026-11-06, not an audit:

| Project | What it showed | Gap for this design |
|---|---|---|
| `vaibhavtupe/skill-guard` (6 stars, Apache-2.0 badge, Python) | PR gate: validate, scan, conflict detection | SHA pinning and a license allowlist not evident |
| `T-Zevin/SkillGuardrail` (57 stars, Apache-2.0, Go) | Quarantine, scan, policy, install | Not a Python API; guarded install not yet on Windows |
| `nbsp1221/skillpin` (0 stars, MIT, TypeScript) | SHA pinning, lockfile | Linux only |
| `bruc3van/agent-skills-guard` (392 stars) | Desktop scanning app | Not a library |
| Hermes Skills Hub | Quarantine, scan, lock file | Hermes only |

## 15. Appendix: evidence

Scripts and raw output are in [`docs/research/phase1-memory-spike/`](research/phase1-memory-spike/). This was a throwaway feasibility test, **not a benchmark**: 40 hand-written queries, written by me without native review, single run, one model.

**Environment:** Windows 11, Python 3.14.7, `mnemosyne-memory` 4.0.0, `fastembed` 0.9.1, MiniLM multilingual. Mnemosyne's PyPI classifiers stop at 3.13, but it installed and ran here. That's a single small test, not a claim of full support.

**Set:** 40 English-described skills, 8 labelled targets, 32 distractors with some deliberately close. 8 queries in each of 5 languages.

| Config | Top-1 | Recall@5 | Results for 4 nonsense queries |
|---|---|---|---|
| Mnemosyne defaults | 5/40 | 5/40 | 0, 0, 0, 0 |
| `LEXICAL_GATE_MIN=0` | 29/40 | 37/40 | 5, 5, 5, 5 |
| Plain cosine on the same embeddings | 35/40 | 39/40 | Not measured; it has no floor either |

| Recall@5 by language (n=8 each) | en | id | es | zh | ar |
|---|---|---|---|---|---|
| Mnemosyne, gate 0 | 6 | 8 | 8 | 7 | 8 |
| Plain cosine | 8 | 8 | 8 | 7 | 8 |

Other observations:

- Setting `EM_VEC_ADMIT=0.35`, `VEC_TYPE=float32` or vector-only weights on top of gate 0 changed none of these totals. These rows were in working memory, so the episodic admission floor likely doesn't apply, but I didn't investigate why the weights changed nothing.
- Bank isolation held in a separate 8-skill run: a memory in one bank was recalled from that bank and not from another, with distinct database files.
- The cause of the default result is in the code: `_minimum_recall_relevance` in `mnemosyne/core/beam.py` applies a lexical gate. This is sensible for single-language use, and not a Mnemosyne defect. It's a setting this project has to change.
- The test wrote only to temporary directories. `~/.hermes` had no new files and `~/.mnemosyne` wasn't created.

**Sources read:** Mnemosyne `docs/hermes-integration.md`, `integrations/hermes/README.md`, `integrations/hermes/src/mnemosyne_hermes/__init__.py`, `mnemosyne/core/{banks,memory,beam,embeddings}.py`; Hermes `website/docs/user-guide/{profiles,profile-distributions}.md`, `features/{kanban,delegation,mcp,skills,memory-providers}.md`, `AGENTS.md`, `agent/memory_provider.py`, `plugin-catalog/mnemosyne.yaml`; fastembed's model tables.
