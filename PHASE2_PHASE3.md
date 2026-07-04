# Phase 2 & Phase 3 — Implementation Guide

## New capabilities at a glance

| Feature | Where it lives |
|---|---|
| Hierarchical AI Constitution | `bot/ai/constitution_service.py` + `constitution_rules` table |
| Intent detection (keyword + AI fallback) | `bot/ai/intent_service.py` |
| Dynamic model routing (health-aware, DB-backed) | `bot/ai/model_routing.py` + `ai_model_configs/health` tables |
| AI Orchestrator (confidence system, dual review, audit) | `bot/ai/orchestrator.py` |
| Support engine (knowledge + message + announcement + AI) | `bot/services/support_engine.py` |
| Assistant prefix commands (!ask, !translate, !summarize, !explain, !investigate, !draft) | `bot/cogs/assistant_cog.py` + `bot/services/assistant_tools_service.py` |
| Knowledge versioning + correction/approval workflow | `bot/knowledge/learning_service.py` + `bot/repositories/knowledge_repository.py` |
| Three-tier memory (short-term / server / operational) | `bot/services/memory_service.py` + `memory_entries` table |
| Pluggable investigation tools (whitelist, punishment, maintenance, ...) | `bot/investigation/` |
| Investigation service + DB record | `bot/services/investigation_service.py` + `investigations` / `investigation_findings` tables |
| AI moderation intelligence (report analysis, dual review) | `bot/moderation/intelligence_service.py` |
| Spam / raid heuristic detectors (in-memory) | `bot/moderation/heuristics.py` |
| Hard safety guard (kick/ban/permission forbidden; 60-min ceiling) | `bot/moderation/action_guard.py` |
| Moderation intelligence cog (!report, /report, spam/raid listeners) | `bot/cogs/moderation_intel_cog.py` |
| Founder/Owner admin commands (!ai-status, !ai-rules, !ai-memory, etc.) | `bot/cogs/founder_admin_cog.py` |
| /aimodel slash commands (list, status, set, auto) | `bot/cogs/model_routing_cog.py` |
| Staff escalation queue | `staff_escalations` table + `bot/repositories/moderation_intel_repository.py` |
| Reference data tables (whitelist, linked accounts, known issues) | `bot/database/models_moderation_intel.py` |

---

## AI Constitution system

Rules live in `constitution_rules` and are loaded by `ConstitutionService.build_system_prompt()`,
which injects them into every AI call in strict tier order:

```
Tier 1 – PLATFORM_SAFETY  (seeded at startup; govern every guild)
Tier 2 – CORE_BOT         (seeded at startup; govern every guild)
Tier 3 – SERVER           (added by Founder/Owner via !ai-rules add)
Tier 4 – ROLE             (reserved for future per-role overrides)
Tier 5 – TASK             (reserved for per-task prompt overrides)
```

**Critical invariant**: the rules about "never kick/ban/change permissions" and the "60-minute
timeout ceiling" exist in the DB _and_ are enforced as hard Python constants in
`bot/moderation/action_guard.py`. Disabling a DB constitution rule does **not** make those
actions possible, because no code path to perform them exists anywhere in this codebase.

```bash
# View all active rules
!ai-rules list

# Add a server rule (Founder/Owner only)
!ai-rules add No spoilers|Never reveal storyline spoilers in any channel

# Disable a rule
!ai-rules disable 3

# Force-reload the cached constitution prompt
!ai-reload
```

---

## Model routing

Models are rows in `ai_model_configs`. On first startup, the router seeds the table from the
`.env` candidate lists. After that, add/disable models by editing the table or using `/aimodel`.

```bash
/aimodel list              # see all models + health stats
/aimodel status 2          # detailed stats for model id=2
/aimodel set support 3     # pin model id=3 for all "support" tasks in this guild
/aimodel auto support      # remove the manual pin; restore automatic routing
```

Task types: `support`, `moderation_review`, `investigation`, `translation`, `summarization`,
`explanation`, `draft`, `intent_classification`.

**Health-aware failover**: each failed call increments `consecutive_failures`. When it reaches
`MODEL_UNHEALTHY_AFTER_FAILURES` (default 3), the model is skipped and the next eligible one
is tried. After `MODEL_HEALTH_COOLDOWN_SECONDS` (default 300) the model gets one retry.

---

## Dual-review system

For `moderation_review` and `investigation` tasks, the orchestrator calls two different models
and compares their answers before deciding whether to act or escalate to staff.

- Both agree + confidence ≥ `AUTO_ACTION_CONFIDENCE_THRESHOLD` → automated action applied
- Either disagrees OR confidence too low → creates a `StaffEscalation` row; notifies the
  `ESCALATION_CHANNEL_ID` channel if configured

For moderation analysis, agreement is checked by comparing the `recommended_action` JSON field
(not freeform text similarity), so minor phrasing differences don't cause false disagreements.

---

## Knowledge corrections

```bash
# A Founder/Owner proposes a fix to a knowledge-channel entry
!ai-knowledge list           # list entries awaiting approval
!ai-knowledge approve 12     # approve correction #12 (now used in retrieval)
!ai-knowledge reject 12      # reject it
```

Every time a knowledge-channel message is edited, the old content is preserved in
`knowledge_versions` before the live row is updated, so you always have a full edit history.

---

## Memory system

Three memory scopes, all stored in `memory_entries`:

| Scope | Use | TTL |
|---|---|---|
| `short_term` | Conversational context (channel+user pair) | `SHORT_TERM_MEMORY_TTL_SECONDS` (default 30 min) |
| `server` | Durable server facts (IP, store URL, ...) set via `!ai-memory set` | None (permanent) |
| `operational` | Recurring Q&A resolutions (auto-incremented hit_count) | None (permanent) |

```bash
!ai-memory list              # see server facts + top recurring topics
!ai-memory set server_ip|play.yourserver.com
!ai-memory purge             # remove expired short-term entries
```

---

## Investigation tools

Each tool implements `InvestigationTool` in `bot/investigation/base.py`. Adding a new
diagnostic is three steps:

1. Create a class in `bot/investigation/tools.py` (or a new file) subclassing `InvestigationTool`.
2. Add an instance to `_TOOLS` in `bot/investigation/registry.py`.
3. Add its key to the relevant intent lists in `_INTENT_TOOL_MAP`.

No cog or service code needs to change.

```bash
!investigate @User#1234 why was this user banned
!investigate is the server down
```

---

## Assistant commands

All commands are prefix-based (configurable `COMMAND_PREFIX`, default `!`). They work with
reply context (replying to a message before running `!translate`, `!ask`, etc. will include the
referenced message as context), recent channel history, and short-term conversation memory.

```
!ask what is the server ip
!ask @User what does he mean
!translate                      (reply to a non-English message)
!translate to French            (translate to French instead of English)
!summarize                      (last 20 messages)
!summarize 40                   (last 40 messages)
!explain                        (explain the ongoing argument)
!investigate @User              (run all investigation tools for this user)
!draft apologize for the outage (draft a staff response)
```

---

## Founder / Owner admin commands

All require the Owner or Founder role (case-insensitive, configurable in `OWNER_ROLE_NAMES`).

```
!ai-status           - Overall AI health summary
!ai-rules            - list/add/enable/disable constitution rules
!ai-memory           - list/set/purge memory
!ai-knowledge        - list/approve/reject pending knowledge corrections
!ai-investigations   - list recent investigation records
!ai-reload           - flush the cached constitution prompt
!ai-enable           - re-enable the AI assistant for this server
!ai-disable          - disable the AI assistant (commands refuse gracefully)
!ai-debug            - dump the last 5 AI decision log entries
```

---

## Escalation flow

When any AI decision is flagged for escalation (low confidence, dual-review disagreement, or
`recommended_action == "escalate"`), the following happens automatically:

1. A `StaffEscalation` row is inserted.
2. The `AIDecisionLog` row has `escalated=True`.
3. If `ESCALATION_CHANNEL_ID` is set, the bot posts a staff-alert message in that channel.

Staff can see open escalations via `!ai-debug` (recent decisions) or by querying
`staff_escalations WHERE resolved = false` directly.

---

## Running the test suite

```bash
pip install -r requirements.txt
pytest tests/ -v
```

The tests use an in-memory SQLite database (via `aiosqlite`) and mock Discord objects, so
they work without a real Postgres instance or Discord connection.

---

## Adding a new intent

1. Add a value to `Intent` in `bot/ai/intent_service.py`.
2. Add keyword patterns to `_INTENT_KEYWORDS`.
3. Optionally add investigation tools to `_INTENT_TOOL_MAP` in `bot/investigation/registry.py`.
4. Add test cases to `tests/test_intent_service.py`.
