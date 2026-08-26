# Engine spike log

Phase 1 of `docs/roadmap-fitting-mcp.md`. Timebox: one week per candidate,
A first. This log is the record the decision gets made from.

## 2026-08-16 — Candidate A (pyfa's embedded eos): extraction succeeded

**Result: pyfa's eos runs fully headless.** One session took it from "unknown
extraction depth" to the complete 10-fit reference battery computing every
v1 stat-panel figure — EHP by layer with resists, burst/sustained/drone DPS,
volley, cap stability from the event simulation, align/speed/sig, targeting,
fitting headroom. Runner and battery live in `fitting/spike/`.

### The entanglement map (the spike's central question, answered)

The wx dependency is shallower than the roadmap feared:

- **eos has zero top-level GUI imports.** The only `from gui` imports in the
  whole package are three *lazy* imports inside functions in
  `eos/effectHandlerHelpers.py` (fit-command helpers); none trigger on the
  headless calculation path.
- **One indirect wx reach**: `eos.db` → `eos/db/migration.py` →
  root `config.py` → `import wx` — and root config uses wx only for
  `wx.Colour` UI constants. A 3-line stub class satisfies it
  (`fitting/spike/wxstub/`). Root config also needs `cryptography`
  (ESI token storage) — a real pip dependency, installed not stubbed.
- **saveddata goes in-memory** via pyfa's own CI hook: eos/config.py checks
  `sys._called_from_test`.
- **Two API sharp edges** (documented in the runner, cost ~20 min total):
  `import eos.db` must precede any `eos.saveddata` import (circular
  otherwise), and `module.owner`/`drone.owner` are ORM backrefs that must be
  set manually when no saveddata session exists.
- **`eve.db` builds headless** from the JSON static data bundled in pyfa's
  repo (`python3 db_update.py`, ~1 min, 100 MB) — no GUI, no network beyond
  the clone.
- **Minimal dependency set**: sqlalchemy 1.4.50, logbook, python-dateutil,
  pyyaml, roman, cryptography, requests. No wxPython, no matplotlib, no
  numpy.

**What did not extract cleanly: EFT import/export.** `service/port/eft.py`
imports `service.fit`, `service.market` and `gui.fitCommands.helpers` —
the service layer imports wx at top level. Options for v1: stub deeper, or
write a thin EFT parser that builds `eos.saveddata` objects directly (the
battery runner already shows the construction pattern; a parser over it is
small). Leaning: own parser, revisit when the mutated-module EFT dialect
lands.

### The data-skew rule, vindicated immediately

pyfa's bundled static data is **client build 3424810, dumped 2026-07-07**.
CCP's current SDE (and our layer-1 database) is **build 3466501, released
2026-08-13**. The skew the roadmap's data-sync rule exists to name is not
hypothetical — it is present on day one of the spike. Every reference JSON
carries `engine_client_build` so no number can be quoted without its data
generation. Refreshing pyfa's staticdata (their Phobos dump pipeline) or
feeding eos from our SQLite is the open v2 investigation, now with evidence.

### The reference battery

10 fits, all-V, uniform damage profile, chosen for effect-matrix coverage
(`fitting/spike/battery.py` documents what each exercises; panels in
`fitting/spike/reference/`):

| fit | dps | sustained | volley | ehp | cap | align | m/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rifter-ac-brawler | 172.3 | 164.1 | 213.9 | 2,955 | 22.5s | 4.69 | 3,213 |
| punisher-pulse-armor | 91.6 | 91.6 | 206.6 | 6,682 | 87.7% | 5.10 | 1,071 |
| merlin-blaster-shield | 227.0 | 220.9 | 512.0 | 6,538 | 540s | 5.04 | 2,930 |
| caracal-rlml-shield | 298.0 | 178.7 | 780.9 | 16,943 | 80s | 6.27 | 2,078 |
| vexor-drone-armor | 431.6 | 428.9 | 1,744.9 | 31,102 | 69.9% | 10.25 | 575 |
| drake-ham-passive | 535.1 | 520.6 | 2,317.8 | 47,178 | 1,430s | 11.25 | 444 |
| hurricane-arty-alpha | 554.1 | 525.9 | 4,066.9 | 37,541 | 200s | 9.40 | 1,557 |
| abaddon-pulse-armor | 1,149.4 | 1,149.4 | 5,300.5 | 188,246 | 198.3s | 22.72 | 289 |
| raven-cruise-active | 838.2 | 793.2 | 5,473.9 | 52,360 | 120s | 16.77 | 381 |
| zealot-pulse-t2 | 442.4 | 442.4 | 1,360.2 | 44,572 | 77.7% | 10.93 | 593 |

Spot checks that pass: the RLML burst/sustained split (298 → 179) shows clip
+ 35 s reload modeling; the Drake panel shows the hull resist bonus
compounding with the hardener on shield only, DC II on hull, and 196.6 hp/s
peak passive regen; MWD sig bloom and mass math visible on the Rifter
(3,213 m/s, 210 m sig). Full battery computes in ~4 s.

### Verdict: candidate A wins — spike closed 2026-08-16

The human spot-check happened the same day: three battery fits (Rifter,
Punisher, Raven) imported into a desktop pyfa GUI with an All-5 character
and compared panel-by-panel against the reference JSONs. **Every figure
matched at display precision** — EHP per layer and resists, DPS and volley,
cap capacity plus stable-% / time-to-empty from the simulation (Raven
"lasts 2m0s" = the JSON's 120.0 s), speed/align/sig, scan res, lock range,
sensor strength, max targets, and CPU/PG including pyfa's two-decimal
rounding. Headless eos and the GUI are the same engine producing the same
numbers; the criterion ("reproduces pyfa's stat panels within rounding,
driven headless") is satisfied with confirmation, not by construction alone.

**The engine decision is made: wrap pyfa's eos.** The dogma-engine timebox
is not needed; B remains the named fallback if A develops a blocker, and
the battery JSONs are ready to grade it if that day comes.

Two small notes from the spot-check screenshots:
- The GUI's "Recharge rates" panel shows active rep rates (raw and
  effective HP/s) that our stat panel does not yet capture — add
  `fit.effectiveTank` rep rates to the `get_stats` schema at MCP v1.
- The battery's Punisher uses 3 guns on a 4-hardpoint hull (visible as 3/4
  in the GUI). Harmless for coverage; leave as-is since the references are
  now pinned, fill the 4th slot only if the battery is ever regenerated.

### Follow-ups carried out of the spike

- Battery additions: implants + booster fit, alpha-clone skill set
  (`cloneGrades` from layer 1), overheated states, a mutated-module fit,
  non-uniform damage profiles.
- ~~Thin EFT parser over eos.saveddata construction~~ **Done 2026-08-16**:
  `fitting/engine/eft.py` — parse/build/render; self-test proves the parsed
  battery produces panels identical to the pinned references and survives a
  render round-trip (10/10).
- License note: eos is GPL — fine while we run it as a local tool; if the
  MCP server ships bundled with pyfa code, the server is GPL too. Flag at
  MCP v1 packaging.
- ~~pyfa staticdata refresh cadence vs CCP builds~~ **Resolved 2026-08-16**,
  and better than the v2 investigation hoped: `fitting/adapter/` generates
  pyfa's staticdata inputs from CCP's current JSONL export, so pyfa's own
  unmodified `db_update.py` builds `eve.db` at the skill's SDE build.
  Verified: battery at build 3466501 vs pinned 3424810 references — 440
  leaves, zero diffs. The engine and the skill now share one data source,
  and the panel diff on future builds is the balance-change report.

### 2026-08-17 — MCP v1 server landed, token budget validated

`fitting/mcp/server.py`: 11 tools over headless eos — lifecycle, EFT
import/export, `edit_fit` ops, `set_skills` (all-5 and alpha, via eos's own
AlphaClone data from `cloneGrades`), `get_stats` with damage-profile
weights plus rep rates (closing the spot-check note), `compare_fits`
(diff-only), `validate_fit` (named constraints: cpu/pg/calibration, slots,
hardpoints, drone bandwidth/bay), `engine_info` with the explicit
unmodeled list. `test_server.py` drives the whole surface over real stdio,
asserting panel numbers against the pinned battery — passes on both the
bundled data build and the adapter-generated current build.

**Measured budget** (printed by every test run): ~880 tokens standing for
all schemas, ~260 per stats panel, ~290 per edit+stats iteration — an
order of magnitude inside the roadmap's envelope, sized for a Sonnet-class
consumer going answer to answer.

One engineering finding worth keeping: MCP tools execute on worker
threads, and sqlite `:memory:` saveddata is per-connection — the server
uses a temp-file saveddata DB plus `eos.db.saveddata_meta.create_all()`
(pyfa.py's own startup call). Symptom if regressed: `no such table:
overrides` on first import.

### 2026-08-16 — post-spike: EFT parser and data-sync adapter landed

Both first work items for MCP v1 are in:
- `fitting/engine/eft.py` — EFT parse (text-only, no eos) / build (eos
  objects, category-classified like pyfa's importer, comma-in-name safe) /
  render. Mutated modules fail loudly by design pending the dialect
  decision. `fitting/engine/selftest.py` is the proof harness.
- `fitting/adapter/make_staticdata.py` — see `fitting/adapter/README.md`
  for the format notes (two real CCP-vs-pyfa divergences found and
  handled: dynamicItemAttributes list-vs-dict, localized effect
  descriptions).

## 2026-08-17 — fitting-knowledge skill v1 + first graded eval run

Phase 3 of the roadmap. `.claude/skills/eve-fitting/` (named to match the
MCP server it pairs with, parallel to `eve-sde`): a ~1.6k-token router —
well inside the ~4k budget — plus three references (`reading-stats`,
`tradeoffs`, `traps`, ~1.5–1.9k each). The router carries the answer
discipline (authority order SDE > engine > wiki > memory, layer naming,
the engine/SDE/CCP build-skew check, unmodeled-means-named); the
references carry the teaching. NPC damage-profile weights come from
pyfa's own presets, so even the "game knowledge" table is engine-layer
sourced.

**Writing the traps file caught a formulas-doc error.** Verifying §1's
beacon claims against pyfa's actual handlers: category 2 decides
*eligibility*, the attribute's stackable flag decides each case — the
black-hole velocity multiplier and the resist maluses are penalized
(`stackingPenalties=True`), but a Pulsar's shield HP multiplier hits
`shieldCapacity` (stackable) and applies in full. The doc's example had
overshot; corrected 2026-08-17.

**Eval set 1** (`fitting/evals/`): 10 questions, roadmap classes 1+2,
keys engine-pinned per data build (`keys-3424810.json`, regenerable by
`make_keys.py`). Key generation itself caught an engine bug —
`set_skills('alpha')` mutated pyfa's shared All-5 character, silently
turning every fit in the session alpha (fixed; smoke test now asserts
restoration and fresh-import isolation) — and a docs error (the battery
Caracal *gains* EHP vs Guristas, 16,943 → 18,511; the doc claimed a
loss). Also pinned: the battery Hurricane (1,681/1,425) and Vexor
(985/875) are PG-over — coverage fits, never legality-checked; the evals
now use that as a discipline probe.

**Graded run** (`results-2026-08-17.md`): control 27.5/40 (69%) vs
with-skill 39/40 (98%), fresh Sonnet sessions, identical engine access.
Both control outright misses (guessed NPC profile weights; "env effects
are stacking-exempt" plus inverted Wolf-Rayet effects) are exactly the
docs-owned content the skill carries. Every miss root-caused: one docs
gap fixed and pinned mid-run (validate_fit doesn't check skill
prerequisites — traps §T12), one harness key fixed (the battery Vexor has
a free mid; a shield tank needn't drop the web), one model-owned nit
logged. Notable negative result: the control's 69% shows the MCP surface
itself (problems lists, unit-suffixed keys) carries real discipline —
every control run that imported a fit caught the PG-illegal battery fits.

## 2026-08-17 — phase 4: graphs + the external-effects pipeline (first slice)

Same day, phase 4 of the roadmap. The MCP grows from 11 to 14 tools
(~1,281 tokens standing, still an order of magnitude inside the envelope);
eval keys verified unchanged after the refactor.

**`graph()`** — `dps_vs_range`, `dps_vs_target_speed`, `cap_vs_time`; ≤30
points + summary stats + named assumptions, ~110–190 tokens a payload.
Wrap-don't-reimplement held: the application factors are pyfa's own
`fitDamageStats/calc/application.py` — reached by registering synthetic
`graphs.*` package entries so the wx GUI `__init__`s never run, with
`GraphSettings` shimmed to pyfa's pinned defaults — and the cap series is
eos's event-sim trace (`fit.getCapSimData`, times in seconds). One
teaching note fell out: applied DPS at perfect hit reads ~1.015× the
panel figure (the wrecking-shot expectation; the GUI graph shows the
same) — pinned in `reading-stats.md`.

**`set_env`** — projects a system beacon onto the fit
(`projectedModules`, one per fit, groups: Effect Beacon,
MassiveEnvironments, Abyssal Hazards, Destructible Effect Beacon).
Verified: C5 Wolf-Rayet takes the battery Rifter ×2.69 DPS — the ×2.72
beacon modifier stack-penalized in the multiply group, exactly the traps
§T1 story, now engine-computable instead of doc-only. `set_env` affects
only the fit it is set on; the skill's T2 now says so.

**`set_booster`** — command bursts, pyfa's recursive model without the
saveddata ORM: each booster fit's own `calculateModifiedAttributes(subject,
CalcType.COMMAND)` runs before the subject's calc. Measured: Drake burst
+15.0% shield, Vulture +17.25% (hull scaling), both-projected = Vulture
alone (strongest-wins). Engineering finding worth keeping: **eos consumes
`commandBonuses` as it applies them** (`__runCommandBoosts` deletes each
entry), so the booster pass must rerun before *every* calculation —
`panel.stat_panel` gained an injectable `recalc` for this; the smoke test
asserts the second read still carries the burst.

**T3D modes** — `edit_fit` op `mode` sets `fit.mode` (group Ship
Modifiers); Confessor Defense vs Sharpshooter sig 43.3 vs 65 verified.

**Deferred from v1.5, with reasons:** projected fits (remote reps/ewar —
same CalcType pattern but needs per-module projection wiring and a
target-fit surface), fighters (ability-level model plus battery
additions), mutated modules (blocked on the EFT dialect decision).
`engine_info().unmodeled` names all three, and now also names
implants/boosters — true since v1, previously unstated.

Docs updated in step: router + traps §T1/T2 no longer call bursts/env
unmodeled, reading-stats gained a graphs section, MCP README re-measured.
Eval generation 2 candidates (results-2026-08-17.md) now include
engine-truth keys for T4/T5's mechanics, which this slice made computable.

## 2026-08-17 — v1.5 closed (minus mutated), all-0 preset, v2 scoped

`set_projected` finishes the external-effects pipeline: other fits'
modules/drones apply onto the subject (webs ×0.500 exact, a Curse neut
takes a stable Punisher to dead-in-12-seconds, Scythe remote reps land in
`reps_hps`). The ordering finding is the mirror of the burst one and both
now live in `_recalc`: **bursts before the subject's local calc, projected
fits after it** — the local calc's `clear()` wipes anything projected
earlier, which cost an hour of "projection silently does nothing" before
pyfa's own LOCAL path revealed the order. Fighters (squadron-sized,
standard attack, `dps_fighters` panel key, tube/bay validation), implants
and drugs (category-routed through `edit_fit` add; +3% hardwiring and
Quafe Zero verified to the percent) closed the rest. `set_skills` gained
`all-0` — per product decision, the *default answer* for a pilot of
unknown skills is now the all-0 floor bracketed with the all-V ceiling.

Mutated modules are the one v1.5 item deferred: the dialect decision is
made (pyfa's `[N]`-reference EFT format), the remaining work is parser +
eos construction, spec'd in the roadmap's new "What v2 needs" list along
with siege states, spool-up, structures, custom sheets, projection-range
realism, fighter toggles, heat, and ISK cost.

15 tools, ~1,422 tokens standing; smoke test covers every new mechanism;
eval keys verified unchanged throughout.

### 2026-08-17 — graded run 2: layered 100% vs fit-sim-only 81%

Full write-up in `fitting/evals/results2-2026-08-17.md`. The headline:
sim-only jumped 69% → 81% between generations *because v1.5 moved the
mechanics into the engine* — the skill's edge now concentrates in
discipline, the unmodeled, and the engine/game-knowledge boundary
(spool: 1.5 vs 4; T2 prerequisites: 3 vs 4). Two engine fixes fell out
of grading and are pinned in the smoke test: `get_stats` names zero-spool
floors on spool weapons, and charge ops reject wrong-size charges (the
sim arm's Vedmak number had been computed on an L charge in an M gun,
silently). One docs pin (§T1: bonuses and penalties are separate chains)
and one harness fix (G4's phantom armor repairer). Token accounting and
the no-effort-control caveat are in the results file.

### 2026-08-17 — run 3: multi-turn session costs

Twelve persistent sessions × three turns (fitting-layered, fitting-bare,
SDE-only, and layered-with-unrelated-questions arms), measuring marginal
cost per follow-up. Full data in `fitting/evals/results3-2026-08-17.md`.
Headlines: turn one is the whole cliff (38–82k transcript tokens) and
follow-ups run ~6k (SDE) to ~8–18k (fitting) — even *unrelated* questions
in a warm session cost a fifth of a cold start; latency is dominated by
per-invocation engine boots the production MCP registration doesn't pay;
and the two cheapest turns in the run were the two *wrong* ones (sim-only
answering environment and neut questions from memory). One engine bug
found and fixed mid-run: rack overflow had never actually been validated
(eos compares slot enums by identity; EFT-built modules carry ints) —
caught by a layered subject reading layer 1, of all things, after it
flagged an illegal test fit of mine the broken check had passed. Router
gained the conversation-economy rules the outlier turns paid for.

### 2026-08-17 — mutated (abyssal) modules land; v1.5 is complete

Pyfa's EFT mutation dialect adopted exactly (`service/port/eft.py` +
`muta.py` are the de-facto spec): fitted lines carry the *base* item name
plus an ` [N]` reference; a trailing section maps each N to base item,
mutaplasmid item, and comma-separated `attr value` pairs — **absolute**
rolled values. `fitting/engine/eft.py` now parses the section (strict:
malformed pairs and unknown attrs raise, naming the block), builds via
eos's own path (`getDynamicItem(mutaplasmid.ID)` →
`Module/Drone(dyn.resultingItem, baseItem, dyn)` → set
`mutators[attrID].value`, where the Mutator validator clamps to the
mutaplasmid band), and renders the section back (attrs sorted by name,
`floatUnerr`, refs renumbered from 1 — byte-compatible with pyfa's
export).

Verified: a max-roll Decayed Gyrostabilizer moves a Rifter's panel DPS,
export→reimport is stat-identical, an out-of-band roll (2.0 on a
0.995–1.008 mutaplasmid) clamps to the max-roll number, and the drone
path round-trips too (Exigent-mutated Hobgoblin). Two traps worth the
log: a bare abyssal type name ('Abyssal Gyrostabilizer') used to die
inside eos with "Passed item is not a Module" — it now raises a named
EftError explaining the mutation block *is* the data; and unrolled
attributes still carry the mutated item's own baseline values, so the
render emits the full mutator set, exactly like pyfa. One test-authoring
lesson: Hobgoblin II's base damageMultiplier is already 1.92 on this
build — the first "mutated" drone test rolled the base value and proved
nothing until the roll moved.

`engine_info().unmodeled` drops 'mutated modules'; skill router updated;
traps gains §T15 (roll-is-the-data, killboard pastes without the section,
clamping); eval keys 1 and 2 verified unchanged; smoke test grows the
module + drone round-trip, clamp, and bare-abyssal-rejection assertions.

### 2026-08-17 — build refresh operationalized; engine moves to 3470007

`fitting/adapter/refresh.sh` turns the adapter's three manual steps into
one idempotent command: read CCP's manifest (or `--build N`), download
that build's JSONL zip into a gitignored cache, generate pyfa's
staticdata, swap it into the checkout, rebuild with pyfa's own
`db_update.py`, and verify `client_build` — a no-op when the db is
already current. CCP shipped build 3470007 the same day (the manifest
moved past even layer 1's 3466501), so the working engine refreshed to
it as the first real run.

The re-pin that follows a refresh, executed in full: battery rerun at
3470007 vs the pinned 3424810 references — **440 leaves, zero
differences** (two CCP builds without a balance change these fits
touch); reference panels re-stamped at the working build (meta-only
diff); eval keys regenerated — numerically identical, so
`keys-3470007.json` / `keys2-3470007.json` replace the 3424810 files
with only the embedded build string moved; selftest 10/10 and the full
smoke suite green on the new db. Skill docs now quote 3470007 and traps
notes the claims held across the refresh.

The remaining skew window is operational, not architectural: layer 1's
release workflow polls CCP every 3 h and self-publishes; the engine
refreshes when `refresh.sh` is run. Between the two, `engine_info()` vs
`meta.sdeBuildNumber` names the gap — which is the designed behavior,
not a bug. CI running `refresh.sh` + battery-diff per CCP build (the
auto-generated balance report) stays on the backlog.

### 2026-08-17 — CI: one poll, both layers

The layer-1 release workflow grows two jobs instead of a sibling file, so
there is exactly one CCP poll and one "build changed" decision. On a new
build (schedule/dispatch, default branch only — GitHub's rule for
schedules): `fitting-engine` restores a cached pyfa checkout + venv,
runs `refresh.sh --build <N>` (the same build the release job just
published), reruns the reference battery with the diff posted to the job
summary — an empty diff is "no balance change touches the fits", a
non-empty one is the auto-generated re-pin worklist — then runs selftest
plus the full MCP smoke suite, whose pinned assertions are the
enforcement: a real balance change turns the job red until references,
keys and docs are re-pinned. On any push touching `fitting/` (any
branch): `fitting-tests` runs the same suite against the bundled data
build, deterministic. The engine job uploads its battery panels as a
workflow artifact and adds nothing to the release, keeping every layer
independently installable. Concurrency groups split so push-test runs
never queue behind release builds; the marker-commit push can't
retrigger the workflow (path-filtered, and GITHUB_TOKEN pushes don't
fire workflows anyway).

### 2026-08-17 — module_attrs + sweep: per-module truth and cheap enumeration

Two tools close the gap between "interpret a fit" and "author one". The
finding that motivated them: eos models overload bonuses fully headless
(Fed Navy web 14 → 18.2 km at +30%, Warp Disruptor II 24 → 28.8 km at
+20%, both verified) and `edit_fit` already accepted `state:
'overheated'` — but nothing exposed per-module *modified* attributes, so
"what's my heated web range" had no computed source; and any tradeoff
scan cost one conversation round-trip per variant.

`module_attrs(fit, item, attrs)` returns named dogma attributes off the
live calculated module (or drone) — skills, hull bonuses, heat and
mutations applied — ~30 tokens. `sweep(fit, item, candidates, metrics)`
swaps each candidate in server-side, reports dotted panel metrics plus
cpu/pg margins and problem count per row, and restores the fit
(smoke-tested: post-sweep panel identical); ~30 tokens a row, 20-candidate
cap. A ten-variant tradeoff question ("meta plate to free room for a
better rep?") drops from ~10 round-trips / ~3–4k tokens to one call at
~350. The division of labor is in the skill now: knowledge prunes the
candidate list, the engine adjudicates it; mutaplasmid roll feasibility
stays a layer-1 SQL enumeration with only the winner engine-verified.

18 tools, ~1,885 tokens standing (was ~1,550 at 16 — the two schemas pay
for themselves the first time a sweep replaces a hand loop).
`engine_info().unmodeled` now names heat burnout timers explicitly while
stating overload bonuses ARE modeled, since that split invited folklore.

### 2026-08-17 — run 4: complex composition questions, 6/6, no balloon

The question behind the run: does asking the stack to enumerate (roll
feasibility, candidate tradeoffs) balloon token and time cost? Answer in
`fitting/evals/results4-2026-08-17.md`: no — six hard questions
averaged 42.5k tokens (35.8–56.4k, inside run 3's ordinary turn-one
band) and 60–192 s. The roll-ceiling question — "which faction web +
mutaplasmid matches a heated faction point's range" — was answered
correctly as **impossible** (24.4 km ceiling vs 36 km, engine-verified
as a built mutated fit overheated), with the enumeration done in SQL
and the engine reserved for verifying the winner; the legality landmine
(a pasted fit quietly over powergrid) and the napkin-math trap
((base+flat)×skill vs base×skill+flat) were both caught. Remaining
C-axis residue: two answers restated table numbers in prose.

Key preparation earned its keep again: driving the live long-lived
server crashed it on a sqlite cross-thread error — eos SQLAlchemy
objects are thread-bound, the MCP SDK dispatches to arbitrary worker
threads, and the smoke test's client masks it by single-threading —
fixed by pinning tools to one re-entrant engine thread (naive submit
self-deadlocked: tools call tools). And the router's mutaplasmid recipe
pointed at engine-db table names that don't exist in layer 1
(`dynamicItemAttributes` JSON is the layer-1 home); fixed before
subjects launched.

### 2026-08-17 — EFT rack layout preserved (heat-conscious ordering)

Within an EFT section, line order is slot order — the game client fills
slots in sequence on import — and `[Empty ... slot]` placeholders hold
gaps, which is how players space overloaded modules apart (heat damage
spreads to *adjacent* slots, attenuated per hull). The parser used to
skip placeholders, so a heat-planned layout round-tripped scrambled.

Now: placeholders parse into positioned empty modules
(`Module.buildEmpty`), build uses `appendIgnoreEmpty` — eos's plain
`append()` fills the first empty position in the rack, which was
silently swallowing authored gaps (found when the first test's Low and
High placeholders vanished but Med survived: only gaps with no module
after them lived) — and render re-emits `[Empty X slot]` in position.
`edit_fit` add keeps the fill-the-gap behavior deliberately, matching
what fitting a module in-game does. Stats are order-independent (heat
over time unmodeled), so the smoke test pins: placeholders identical
through export, dps identical with and without them, add fills the gap.

### 2026-08-17 — keep_slot removal, layout-safe sweep, heat-aware authoring

Follow-ups to layout preservation. `edit_fit` remove gains
`keep_slot: true`: the module's position becomes an `[Empty ...]` gap
(eos's `HandledModuleList.free`) instead of the rack closing up —
in-game semantics, so remove-then-add round-trips a swap in place. One
eos landmine: `free()`'s dummy carries no owner, and calc paths read
`module.owner.factorReload` even on empties — the freed gap crashed the
next stat panel until the server gave the dummy an owner (imported
placeholders already got one, which is why import-built layouts never
hit it).

`sweep` now replaces candidates *in position* (`replace(idx, mod)`)
instead of remove+append — append semantics were quietly re-filling
authored gaps during trials, so a sweep on a layout fit would return
correct rows and a scrambled fit. Smoke test pins export-identical
before and after a sweep on a gapped fit.

The authoring half: tradeoffs.md now tells the model to lay racks out
for heat when building fits from scratch — infer the overload set from
the fit's job (brawler: prop/tackle/reps; kiter: prop/point; gun racks
heat as a block), space those with gaps where slots are free, and order
full racks so the heated module sits next to what the pilot would
sacrifice first. Engine stats are order-blind; the layout rides the EFT.

### 2026-08-17 — summaries show racks; v2 scope settled (owner's cut)

Fit summaries (`import_fit`/`edit_fit`/`create_fit`/`clone_fit`) now
carry `slots` ({rack: [used, total]}, subsystems included when the hull
has them) and `hardpoints` (turret/launcher) — the shape questions that
used to cost a `validate_fit` round trip are now free with every
mutation. Writing the assertion produced a tidy own-medicine moment:
"Rifter has 4 highs" is folklore — the data says 4 low / 3 high, and
the test now pins the data. The answer-economy rule against restating
table cells in prose got the explicit wording run 4 showed it needed.

v2 scope is now the owner's five: siege/bastion/triage (with a standing
requirement that bastion's odd stacking behavior be derived from dogma
effect data + engine verification, source cited — no wiki folklore),
spool-across-time, projection & application realism (falloff-aware
projection plus target sig/speed context for turrets *and* missiles),
Upwell structures (service interactions + fuel; POS setup math verified
already covered by layer 1 — `controlTowerResources` for towers, and
gotchas-industry now documents the Upwell per-service fuel dogma next to
it), and full fighter support. Dropped: custom skill sheets,
heat-over-time, ISK/ESI.

### 2026-08-17 — v2 item 1: siege/bastion/triage land; bastion stacking sourced

The headline finding: the three states were never unmodeled — they were
*unverified*. Bastion, siege and triage modules are ordinary active
modules to eos; their effects fire headless with `state: active` and the
panel simply becomes the in-state ship (Phoenix torps 126 → 1,890 dps
sieged at 3 launchers ≈ 15×, speed 0; Minokawa Capital RSB 1,437 hp /
20 s → 7,906 hp / 5 s in triage).

The owner-flagged bastion question — "where do you find the weird
stacking rule" — resolved from primary sources, not the wiki: pyfa's
`moduleBonusBastionModule` handler multiplies each resonance with
`stackingPenalties=True, penaltyGroup='preMul'` (hull layer:
`penalize=False`), ordinary hardeners boost resonance in the default
`postPercent` group, and eos's calculator penalizes **per group**
(`__penalizedMultipliers[attr][group]`). Separate groups = separate
chains, so bastion never dilutes hardeners and vice versa.
Engine-verified on a Golem: hardener ×0.675, bastion ×0.700, both
0.4725 — the product exactly, where same-chain math gives 0.4990; a
second hardener meanwhile penalizes normally (×0.7175). Now traps §T16,
source named.

Productized: battery grows bastion-Golem / siege-Phoenix /
minokawa-triage reference fits (13 fits, 572 pinned leaves, old 10
byte-identical); `validate_fit` gains the in-game hull restrictions
(`fit.canFit`: canFitShipType/Group + fitsToShipType + Standup split,
plus the capital-size rule) so a bastion Rifter finally fails loudly;
`get_stats` appends a note naming any active siege-class state and what
it costs; smoke suite pins the resist product, the restriction, sieged
dps/immobility and triage rep numbers. `engine_info` unmodeled now
carries 'industrial core state' (out of scope) instead of 'siege
states'.

### 2026-08-17 — v2 item 2: spool across time; DC joins the bastion chain

Weapon spool is modeled: `get_stats` takes `spool: 0..1` (default 1.0 —
full spool, pyfa's own `globalDefaultSpoolupPercentage` convention,
replacing the old zero-spool floors), `offense.spool` carries the level
+ zero-spool floor + time-to-full, the panel note names the level, and
`graph(fit, 'dps_vs_time')` returns the ramp via eos's own
`SpoolOptions(TIME, t)` — all pyfa math (`calculateSpoolup`), no new
formulas. Both eval key sets verified unchanged across the default
switch. T11 rewritten: quote the band ("full X after Y s, floor Z"),
never one number.

Owner follow-up on bastion answered from the handlers and pinned into
T16: the `preMul` chain's other common resident is the **Damage
Control** — `damageControl` multiplies shield/armor resonance with
`penaltyGroup='preMul'` exactly like bastion, so DC and bastion DO
penalize each other (Golem: DC ×0.875 + bastion ×0.700 → 0.6240
penalized, not the 0.6125 product) while both stay independent of
hardeners. And bastion has no passive resist component: its effect list
is online/hiPower/moduleBonusBastionModule — resists exist only while
the state runs; the passive preMul resident is the DC. Also verified
the new hull-restriction check covers the whole class the owner asked
about: covert ops cloaks (Buzzard yes / Rifter no), bomb launchers,
burst jammers (hull-restricted in the data), clone vats — one
`fit.canFit` check, covert cloak pinned in the smoke suite.

### 2026-08-17 — v2 item 3: projection ranges + applied damage in one call

`set_projected` now takes `{fit_id, range_km}` entries: the range flows
into eos's own projected calc (`ProjectedFit.projectionRange` →
`forcedProjRange` → each effect handler's `calculateRangeFactor`), so a
web at half its optimal webs at full strength, at 8× optimal it does
nothing, and everything between follows the module's real
optimal/falloffEffectiveness — smoke-tested at all three points. Bare
ids still mean zero range (calculateRangeFactor(None) = 1), so existing
behavior and its "worst case" framing are unchanged.
`graph(projector, 'ewar_vs_range', item=…)` returns the effectiveness
band (pyfa's calculateRangeFactor over the module's modified attrs, heat
included if overheated).

`applied_dps(fit, distance_km, target={sig_m, speed_ms})` is the
application half: pyfa's full `getApplicationPerKey` model — turret
tracking/sig, missile explosion radius+velocity, drone mobility — as a
single call returning raw vs applied totals and a per-source-class
split. Smoke-tested both directions: an AC Rifter collapses to ~14%
application against a 35 m / 700 m/s target and recovers to ~100%
against 400 m / 100 m/s; an RLML Caracal shows the same shape through
the missile formula. One honest wrinkle pinned in the test and docs:
perfect turret application reads ~101.5% of paper dps — the
wrecking-shot expectation, pyfa's own model, not a bug. Damage maps in
graphs now use full spool, matching the panel default. 19 tools,
~2,180 tokens standing; both key sets unchanged.

### 2026-08-17 — v2 item 4: Upwell structures land

The Citadel calc branch works headless: build_fit (and so create_fit)
constructs `Citadel` for category-Structure hulls, and an Astrahus with
standup modules computes everything — 1,023 dps of standup cruise
missiles, 30.15M EHP across layers, service fuel — pinned as battery
fit 14 (616 leaves). Service slots joined every rack surface (EFT
`[Empty Service slot]`, summaries, overflow validation), `fit.canFit`'s
Standup/ship split gives two-way legality (a Gyrostabilizer on an
Astrahus and a Standup service on a Rifter both fail loudly), and the
panel gains two structure sections: `services` (per-service fuel
blocks/hr + onlining cost) and `defense.incoming_dps_cap` — the
per-layer `*DamageLimit` attributes (Astrahus 5,000/layer), because
EHP ÷ cap is the floor on time-to-kill and quoting structure EHP
without it misleads. T17 written; reinforcement windows and low-power
state named unmodeled.

Also this session: the wrecking-shot number pinned precisely — pyfa's
`_calcTurretMult` (citing EVE Uni) has wrecking shots *replace* the top
1% of hit rolls rather than add: 0.99 × 0.995 + 0.01 × 3 = 1.01505, the
observed 101.5%, not the folk 102–103%.

### 2026-08-17 — versus: the duel question becomes one call

Owner question: is "how does this fit do vs ship X" one tool, both
directions? It wasn't — applied_dps covered outgoing application but
not the victim's resists, and the mirror direction took composed calls.
`versus(fit_a, fit_b, distance_km)` closes it: for each direction it
computes the attacker's applied damage *mix* (application vs the
victim's current post-ewar sig/speed), sets the victim's damage pattern
to that actual mix and reads EHP against it (resists finally in the
loop), subtracts sustained reps, applies structure incoming-damage caps,
and reports time-to-kill or `tanked`. Assumptions ship in the response:
victim at max transversal, reps as one pool (defender-favoring), ewar
only if projected. Smoke test pins both directions on a Rifter/Punisher
duel and that webbing the victim raises the attacker's applied dps.
20 tools, ~2,340 tokens standing.

### 2026-08-17 — v2 item 5: full fighters; v2 scope complete

The `ability` edit op toggles any fighter squadron ability (substring
match; a miss lists the squadron's real ability names), `module_attrs`
now surfaces fighters with per-ability active flags, and fighter tube
validation splits by class — light/support/heavy, ship-side and standup
— on top of the total count, which yields cross-legality for free (a
Standup Einherji on a Thanatos and an Einherji II on an Astrahus both
fail with the exact tube class named). One default worth knowing,
pinned in T9 and the smoke suite: eos's Fighter constructor activates
every implemented damage ability it iterates before reaching the
standard attack, so light fighters default to missiles ON — 521 dps for
a Thanatos Einherji II squad includes the limited-shot missile volley.
The panel quotes what's active; toggling missiles off drops it, MWD on
raises squadron speed in module_attrs. With this, the owner's five-item
v2 scope is complete: siege states, spool, projection/application,
structures, fighters — all engine-verified, all pinned.

### 2026-08-17 — post-v2 review: nine findings, two of them serious

A high-effort review of everything since eval run 4 (12 commits) found
nine real issues, all fixed and pinned the same session:

1. **Fighters were invisible to applied_dps and versus** — the damage
   map never included them while pyfa's application map keys fighters as
   `(fighter, effectID)` per ability. A carrier duel computed from ~0
   attacker dps. Fixed via `getDpsPerEffect` with matching tuple keys;
   a Thanatos now shows its fighters bucket (794 dps applied at 10 km).
2. **Fighters were dropped by export/clone** — render_eft never emitted
   them, so clone_fit produced fighterless copies (and versus's own
   advice is "clone_fit it first"). Fixed + quantity round-trip
   ('Einherji II x3' imports as 3, exports as 3; a bare fighter line no
   longer crashes the builder — and a partial squadron no longer
   silently quotes full-squadron dps).
3. **versus leaked the opponent's damage pattern** onto both fits,
   skewing later sweep/module_attrs reads; now saved and restored.
4. Offline disintegrators no longer trigger spool notes or graph a
   flat-zero ramp (spool detection now requires ACTIVE state).
5. versus names its full-spool assumption.  6. edit_fit's bad-op error
   lists all six ops.  7. The spool-ramp scan lives in ONE place
   (`panel.spool_ramp`, graphs import it).  8. The rack table is one
   module-level constant.  9. (verified non-issue: ewar_vs_range's
   attr choice matches the effect handlers.)

Battery 616 leaves and both key sets verified unchanged across all nine
fixes. Lesson recorded: the fighter gaps shipped inside the very item
called "full fighter support" — review-after-milestone stays in the
process.

## 2026-08-18 — eval gen 5: 20 live multi-turn sessions, full v2 surface

Ran the v2 acceptance eval: 20 subjects × 3 turns (10 full-stack, 10
layer-2-only), 30 brand-new questions, keys drive-script-pinned at build
3470007 before launch (`fitting/evals/questions5.md`, raw:
`keys5-3470007.json`, results: `results5-2026-08-18.md`).

**53/60 PASS (9 PASS+), 7 PARTIAL, 0 FAIL.** Subjects beat the pinned keys
twice — Standup Market Hub cannot fit an Astrahus (the key's derivation
had bypassed hull legality with a raw edit-add; validate knows better),
and the T1-frigate mids answer is a three-way tie at 5 (Griffin/Heron/
Vigil). Both corrections verified and folded back. One key typo fixed
(Astrahus armor 9.0M → 30 min).

Mid-run incident became the best test of the run: an account session
limit killed six subjects mid-turn AND silently restarted the shared MCP
server, wiping the fit registry. The two ch2 subjects lost the same
resident Vedmak: the one whose stale id failed loudly re-imported and
hit the key exactly; the one whose stale id had been recycled to another
subject's Thanatos got silently-aliased fighter numbers and built a
confident (wrong) engine-bug narrative on top of an honest refusal to
quote them. Loud staleness recovers; silent aliasing misleads.

Three product fixes implemented from findings: (1) fit-scoped responses
echo the ship name; (2) fit ids salted per server boot so stale handles
never silently resolve; (3) `incoming_dps_cap` reports a layer as 'none'
when its "cap" equals full layer HP. Root cause of the cap-flatten turned
out to be the skill itself: traps.md T17 asserted "5,000 dps on every
layer" — the doc taught the error and three of four subjects repeated it
over the panel's own 14.4M. T17 rewritten (non-uniform caps, read them
per layer). A wrong pinned fact grades worse than no fact — in the trap
catalogue most of all. Also recorded: both arms
mis-directed the Drake uniform-vs-Guristas comparison (traps.md
candidate), and the l2only arm can still answer enumeration questions
from the engine's own staticdata db (legit layer-2 capability; eval-arm
design note).

## 2026-08-18 — eval gen 6: Sonnet re-run, doc-fix regression test

Owner call: run 5's Fable-xhigh subjects ate the session budget for no
product signal; all subagents run Sonnet from now on (CLAUDE.md). Run 6
re-ran the identical 20x3 protocol on `claude-sonnet-5` with byte-identical
prompts (`fitting/evals/results6-2026-08-18.md`).

**51/60 PASS (11 PASS+), 6 PARTIAL, 3 FAIL** vs Fable's 53/7/0 — same PASS
rate, different tail. Every run-5 systematic miss came back clean off the
doc fixes alone (the old server process still prints the raw cap shape, so
the T17 rewrite carried it unaided). Sonnet matched or beat Fable wherever
the answer came from a tool call — including catches Fable never made
(1-of-4 hardpoints; the Rifter's own 22.5s cap-out) — and failed in exactly
one shape: confident game-knowledge folklore where one tool call would
have arbitrated (storm read from sign-inverted raw SDE attrs instead of
set_env; "faction webs can't be mutated"; invented fighter tube/squadron
facts; a recall hull-list that forgot the Heron; the engine's fictional
both-active-AB panel quoted as real).

Each miss became a doc line the same day: traps T18 (prop-mod exclusivity
+ engine-fiction warning), T15 (applicability is data — try the import),
T9 (fighter tubes/squadrons are read, not recalled), T1 (beacons via
set_env diff on a fitted hull only), SKILL.md (l2 enumeration goes to the
engine's own db). One code fix: eft.py now imports a quantity-less drone
line as one drone instead of dying on eos's opaque module error (a live
subject hit it). Suite green at 3470007.

Cost: Sonnet spent ~40% more tool calls for the same answers but a
fraction of the tokens — the run never approached the session limit.

## 2026-08-18 — Sonnet-medium spot-check: tripwires must live in the router

Product target corrected to Sonnet at MEDIUM effort. Re-asked the five
gen-6 folklore misses at exactly that config via the workflow orchestrator
(model+effort pinned per agent): 1 PASS, 2 PARTIAL, 2 FAIL. The storm
question — whose guard lives at router level — was fixed; the two-ABs and
mutaplasmid questions — whose guards lived only in traps.md — failed the
same way again, because a medium-effort subject doesn't open the
reference file before asserting a mechanic. Hoisted all five repeat
offenders into one "never assert a mechanic from memory when one call
can check it" bullet in SKILL.md's always-read list. Router = interrupt,
references = detail: that's the design rule the whole measurement chain
(Fable-xhigh -> Sonnet-xhigh -> Sonnet-medium) converged on.

## 2026-08-18 — eval gen 7: held-out folklore at the product config

Ten questions on mechanics classes no doc names, keys derived live
against engine+SDE and owner-reviewed pre-launch (review corrected the
Q8 key: MWD penalties differ by variant — the sweep answers it). Ten
sonnet-medium subjects: **4 PASS / 4 PARTIAL / 2 FAIL**
(results7-2026-08-18.md). The discriminator is mechanical: both FAILs
made zero engine calls; every engine-touching subject passed its core.
Seen-class fixes held; the habit didn't generalize — subjects stop at
SQL (or memory) when the question needs the engine. Router bullet now
carries a mechanical self-audit: zero engine/SDE calls on a mechanics
answer = unverified by definition.

Key-derivation protocol paid off three times before launch: caught two
engine validation gaps (maxGroupFitted — two WCS validated clean;
rigSize — a Large rig fit a battlecruiser) and one key example error
(that very Large rig). Both gaps fixed + smoke-tested same day: the
validator now answers two of the ten folklore traps by itself. Zero
subject key corrections this generation — deriving keys with tools and
reviewing them with a human beats the subjects to the errors.

## 2026-08-18 — eval gen 8: the audit rule moves the needle

Second held-out folklore round (questions8/results8): **6 PASS (3 PASS+),
2 PARTIAL, 2 FAIL** vs gen 7's 4/4/2 at identical protocol. Nine of ten
subjects used tools (was three of ten); the strongest answers now run
their own experiments (a cross-ship SeBo projection to measure the 86.9%
step; a rebuilt dual-tank fit matching the key to the decimal). Residual
classes sharpened to two: one holdout zero-call folklore answer (ASB
confused with an ancillary armor rep), and SQL-without-engine — six
resource-arithmetic queries concluding an MJD fits a cruiser when one
import_fit names the rejection; the same resource-math-isn't-legality
error this project's own gen-5 key derivation made. Router gains the
"can X fit Y = import + validate" line. Per-round trend on the folklore
fringe: 20% confident-wrong holding, but shifting from pure memory
toward one-layer-short — a narrower, more fixable shape.

## 2026-08-18 — cost profile: the token bill is context re-reads, not output

Built `fitting/evals/cost-profile.html` (published artifact) charting the
three usability factors across gens 5–8, 140 graded answers. Numbers
recomputed consistently from transcripts: billed = fresh input + cache
reads + cache writes + output, per-request max to dedupe streamed usage
rows.

Findings that change how we optimise:
- **~0.25% of the bill is output.** Gen 8: 1.3k output against 511k
  billed per question. Prompt/answer brevity is a UX lever, not a cost
  lever.
- **Cost ≈ tool rounds × context size.** Across five runs the two track
  almost linearly (6.4 rounds/292k → 13.0 rounds/694k). One tool round
  saved ≈ 50k tokens. This is what makes the skill's batch-your-calls
  and resident-fit guidance load-bearing.
- **Cold start dominates a session.** A 1-turn session costs MORE per
  question (511–577k) than a 3-turn one (292–479k): skill read, tool
  schemas and fit setup are per-session, not per-answer. Follow-ups are
  the cheap questions — gen 6 turn 1 495k → turn 3 229k.
- Sonnet bills ~1.6x Fable's tokens for the same work (more tool rounds)
  at a fraction of the per-token rate; volume and price move opposite
  ways, so neither number alone is the cost story.
- Latency confirmed third: 53–73s median per question, worst case ~2 min
  on a cold first turn.

## 2026-08-18 — cost work: fold the mutate→stats pair, teach rounds-not-calls

Measured the call corpus (2,062 tool-bearing requests across every eval
generation) before changing anything:
- `edit_fit -> get_stats` is the single most common consecutive pair (75),
  with `import_fit -> get_stats` (17) behind it — a whole round spent
  re-reading ~45k of context to fetch numbers the mutation already knew.
- **90% of requests carried exactly one tool call.** Only 10% batched two
  or more. That is the headroom: billing is per request, not per call.

Changes: `import_fit`, `create_fit`, `clone_fit` and `edit_fit` now return
the full stat panel inline (`stats=True` default, `stats=False` for the id
alone; multi-fit imports stay lean unless asked). Cost: +268 tokens on an
import response. Saves: one round, ~45k. SKILL.md's "Driving the engine"
section lost its stale `edit_fit -> get_stats` iteration-loop advice —
which taught the exact anti-pattern — and gained a rounds-are-the-cost
block: put independent calls in one reply, never follow a mutation with
get_stats.

Also corrected the file-size budgeting table (traps 4.2k, router 3.6k) —
it had drifted as docs grew.

Context accounting, for the record: of a subject's 37.8k base, ~2.75k is
the eval rig's 23 bundled skills and ~1.3k its github/google tool names —
about 4k that a fresh product session would not pay. The remaining ~34k is
the client's own floor (system prompt + built-in tool defs), which this
project does not control. Project footprint on top: ~1.1k of MCP schemas,
+6.2k when the skill loads.

## 2026-08-18 — cost rerun: the guidance backfired, and the money is in SQL

Re-ran gen 8's ten questions with the new batching guidance live (the MCP
server still had the pre-fold tool shapes — restarting it was blocked by
the permission classifier, so this measures guidance only). 9/10 completed;
q9's subject tried to `kill` the shared server process on its own
initiative and was stopped by the safety classifier — worth noting that a
subagent reached for infrastructure surgery unprompted.

**Result: cost went UP.** Billed/question 511k → 620k mean (+21%), median
507k → 561k (+11%); requests 10.9 → 12.8.

Why, measured by tool kind:
- SQL/Bash rounds 69 → 61 (−12%)
- engine rounds 22 → 41 (**+86%**)

The batching block barely moved batching (4 → 9 batched requests out of
~110). What moved was the *accuracy* guidance accumulated since gen 8 —
"import + validate for legality", "never assert from memory", "set_env
diff on a fitted hull" — each of which mandates a call. Verification and
cost are the same dial, now with a number on it.

**The real finding: two-thirds of the bill is layer-1 SQL, one query per
round.** 67% of gen-8 tool calls were Bash/SQL (~7 per question, ~345k of
the ~511k). The worst subject spent 18 of its 27 rounds on separate
sqlite invocations — 18 context re-reads to run 18 small queries that one
invocation could have answered. The eve-fitting batching guidance could
never have caught this: the exploration happens in layer 1, whose skill
says nothing about batching.

Corollary: the mutate→stats fold is real but small — only 4 such pairs in
gen 8, ~20k/question. It stands, but it is not the lever.

## 2026-08-19 — gen-9: cost by layer need (baseline, pre eve-sde-server)

15 subjects x 3 turns, split by which layer the opening question needs
(`fitting/evals/questions9.md`, results in `results9-2026-08-19.md`). Run on
the current stack — the new `eve-sde` MCP server is in `.mcp.json` but a
session reads that at startup, so this is the **baseline**, not a test of it.

Three findings worth carrying forward:

1. **Layer 1 costs what layer 2 costs** — 418k vs 403k mean billed per opening
   question. A "just look it up" layer should not price like engine work. It
   does because layer-1 answers are produced by exploratory hand-written SQL
   (one subject: 21 SQL calls / 14 rounds to compare two hulls' cargo). This is
   the number the `eve-sde` server has to beat, and the cleanest justification
   for it yet measured.
2. **Cross-layer questions are superlinear** — 924k against an additive
   expectation of ~821k, driven by rounds (16.4 vs ~9). Lookup and engine
   iterate against each other rather than running in sequence.
3. **Warm sessions tax unrelated questions.** The same question asked cold as
   a T1 and warm as a T3 cost 219k vs 402-584k (1.8-2.7x). Rounds barely
   change; context per round grows 48k -> 86k. Corollary: **shrinking what
   tools return beats shrinking how often they are called** — returned bytes
   are paid once per call and then on every subsequent round for the rest of
   the session.

Design questions answered this session (accuracy risk of encoding the layer-1
docs into `sde/mcp/server.py`; exposure to a live-refreshing SDE):

- Encoding shifts risk rather than raising it uniformly. A doc nobody reads
  misleads 1-in-29 subjects; wrong code misleads 29-in-29 and wears authority.
  Mitigations shipped: raw + interpreted side by side, a loud note on any
  unitID with no rule, a `NOT_CORRECTED` list of five classes the server
  explicitly does not fix, and `units_without_a_rule` computed from the live DB
  (8 unit types / 490 attributes uncovered). Unmeasured until a run with the
  server live.
- Live-refresh exposure ranks: a unitID *changing meaning* is the only silent
  failure (no guardrail); a *new* unitID degrades loudly to raw-plus-warning;
  schema changes break with SQL errors. Fix for the silent case: have the
  server diff each rule against the live `dogmaUnits` row and flag drift.
- **Unlogged hazard found: layer 1 and layer 2 can drift apart.** eos carries
  its own snapshot (engine build 3470007); the SQLite SDE refreshes separately
  (items build 3466501). Nothing compares them, so a cross-layer answer can mix
  two game versions. Arguably a bigger live-version risk than the unit table.
  Cheap fix: a build-skew check. Not implemented.

## 2026-08-19 — gen-10: the layer-1 server measured, and a server that never ran

Paired rerun of gen-9's layer-1 arm with `eve-sde` live
(`fitting/evals/results10-2026-08-19.md`).

**The server had never worked.** It imported the `mcp` SDK, which lives only in
layer 2's virtualenv, while `.mcp.json` launched it with a bare `python3`. Every
start was `CONNECTION_CLOSED`. `test_server.py` hid it by launching via
`sys.executable`: run under the virtualenv the import resolved, so every
assertion passed against a server that could not start in deployment. Two
lessons, both general: a smoke test must launch the thing the way production
launches it (it now reads the command out of `.mcp.json`), and a layer that is
meant to ship alone must not import anything (`_stdio.py` is stdlib-only).

**The cost result is a non-result, and that is the finding.** Paired across
comparable turns: **-2%**. The opening-question slice looks good at -33% (9.6
-> 7.0 rounds), but the control arm — the T3 align question, pure layer 2,
where the SDE server is unreachable — moved **+30%**. A -21% treatment beside a
+30% control at n=5 is not separable from variance. Gen-9's "418k is the number
to beat" was the wrong frame: five samples cannot size this.

What did move unambiguously is the **mechanism**: shell round-trips for raw SQL
went 49 -> 4 calls, replaced by 27 server calls; four of five subjects used no
shell at all. Skill loads and ToolSearch held flat, so it is not discovery
overhead shuffling. The variance has a shape worth keeping: the server
compresses the worst cases (recharge 14 rounds -> 4, -70%) and costs a round on
the cheapest ones (blueprint 5 -> 8, +54%). Cost lives in the tail, so that is
the right direction — but it is a tail claim, not a mean one.

**Batching is unused — 22 of 24 `query` calls sent exactly one statement**
(mean 1.1). The tool was designed around "the round is the unit of cost, so
send many statements"; that premise is simply not being exercised, and the
-33% comes from deleting the shell round-trip instead. This is the placement
hierarchy again, one level down: a docstring saying "batch" does not change
behavior, the tool's shape does. `query(sql: str)` takes a string and a string
invites one statement; `query(statements: list[str])` would make the single
query the awkward case. That is the next experiment, and a better one than
re-running these turns.

## 2026-08-19 — gen-11: the tool-shape lever, and why batching was the wrong one

`query(sql: str)` became `query(statements: list[str])`, so the schema
advertises an array (`fitting/evals/results11-2026-08-19.md`).

**The shape moved behavior, as the hierarchy predicts** — multi-statement calls
went 8% -> 17% (mean statements/call 1.1 -> 1.2) where gen-10's docstring
asking for batching had moved nothing. **But the cost did not follow**: opening
questions 281k -> 360k, the wrong way, and 83% of calls still send one
statement.

**Batching was the wrong lever, and the reason generalises.** SDE lookups are a
dependency chain — typeID before attributes, unitID before the value means
anything. You cannot batch a query whose text depends on the previous result,
so a list parameter only helps for independent lookups, which are the minority.
The tool that already collapses the chain server-side is `attrs` (name ->
typeID -> attributes -> unit correction, one round). It was used 4 times in
gen-10 and **0 times in gen-11**, against 35 `query` calls: subjects reach for
the general-purpose escape hatch and hand-walk the chain rather than take the
specific tool that does it in one call.

So the design rule sharpens: **collapse dependency chains into one tool call;
do not ask the caller to batch independent ones.** The next experiment is the
scope and discoverability of `attrs` against `query` — possibly narrowing
`query` so `attrs` is the obvious path — not another parameter shape.

**The noise floor is the other lesson.** The layer-2 control (the align
question, untouchable by any SDE change) read 420k / 494k / 372k across gens
9-11, a +/-18% swing on a slice that should be flat. Every cost claim across
these three generations sits inside that band. At n=5 this rig cannot resolve
the effects it is being asked to measure; either subject count goes up
substantially or the metric moves to something less variance-prone than billed
tokens per turn (round counts and tool-choice counts behaved far more stably).

Caveat on gen-11: it ran in fresh headless `claude -p` sessions because the
in-session server held a stale schema and did not respawn after `pkill`, so
cross-generation cost comparison carries a harness confound. The batching and
tool-choice numbers are within-generation and clean. Layer 1 is now fully on
the server: zero Bash calls across all five sessions, against 49 in gen-9.

## 2026-08-19 — first real mobile session: two outages, one confirmed win

Owner ran the stack from a phone. Three questions, and the transcript is worth
more than gens 9-11 put together.

**The `attrs` front door works.** "How much cargo does an Iteron Mark V hold
compared to a Bestower?" — the question that took **13 rounds** in gen-11 —
was answered by a **single `attrs` call**. Hull columns and dogma in one
response, which is exactly the collapse the change was for. Most of that turn's
remaining tool use was acquiring the database, not answering.

**Outage 1: the fitting server was absent, and it produced a confident wrong
answer.** Asked whether a max-cargo fit changes which hauler wins, the model
had no engine (pyfa unbuilt -> `import eos` raised -> CONNECTION_CLOSED -> tools
never appeared). It hand-derived the stacking math and got it **backwards**,
asserting cargo capacity is stacking-penalised. It is not. With the engine
later built by hand, the answer flipped: **Bestower 37,117 m3 beats Iteron
35,176 m3**, because the sixth expander applies in full. This is the exact
failure class the project exists to prevent, caused by a silently missing
server. Fixed: the engine import is wrapped, the server starts regardless,
every tool reports the real reason, and `_load_engine()` retries on the next
call (with `importlib.invalidate_caches()` — the first failure otherwise
poisons the import cache) so a late bootstrap needs no restart.

**Outage 2: the SessionStart hook did not run**, so the session had no
databases and fetched the items part from a GitHub release itself. Most likely
because the hook only exists on this branch and main is untouched.

**Incidental: the SDE server self-heals and I had said otherwise.** `_conn()`
globs lazily on first call, so when the databases appeared mid-session it just
worked. My earlier claim that both servers read their data at startup was
wrong for layer 1; it was right for layer 2, which is what needed fixing.

**Build skew showed up live within one session**, as flagged after gen-9:
engine 3424810 against SDE 3473160, noticed only because the model said so in
passing. Nothing compares them. Still unimplemented, now with a real sighting.

**The floor is bigger on the real surface.** Their `/context`: system tools
29.2k + system prompt 11.9k + MCP tools 10.6k + skills 4.1k = **~56k per
round**, against the 41k measured here. Cost is rounds x floor, so the product
number is ~37% worse than this container suggests: a 2-round answer is ~112k,
not ~85k. Item 5 of the cost model should be decided against 56k.

## 2026-08-19 — second mobile run: one clean win, three defects, all fixed

Owner ran five questions with tool calls expanded. The expansion is what made
this useful — the failures are only visible in the call sequence.

**Q1 "sig radius and scan res on a Vexor" — 2 calls (ToolSearch + `attrs`).**
Down from 13 rounds in gen-11 for the equivalent hull question. This is the
floor; the chain-collapse works.

But the response carried 13 lines of "unitID N has no correction rule —
confirm before quoting", including for metres and millimetres. Warning about
every honest unit buried the two that matter. **Fixed:** unit symbols now come
from the `dogmaUnits` table at call time (`145 m`, `280 mm`); overrides still
win for the liars; the warning survives only for units with no label AND no
rule.

**Q2 "which T1 cruiser has the most powergrid" — 8 calls, 6 of them wasted.**
The model wrote `t.typeName`, `g.groupName`, `attributeName` — CCP's canonical
SDE names — and this builder stores all three as `name`. Each miss cost an
error round plus a `SELECT * ... LIMIT 1` discovery round. **Fixed:** a
`no such column` / `no such table` error now returns the actual columns of
every table the statement mentions. The database already knew; it just wasn't
saying. (Enumerating them needs `PRAGMA database_list` — every real table is
in an ATTACHed part and the main `sqlite_master` is empty.)

**Q4 "do three Damage Control IIs stack" — WRONG, and the same root cause as
last time.** The model answered 100%/87%/57% stacking. They do not stack at
all: `Damage Control II` carries **`maxGroupFitted = 1`**, so the second one
cannot be fitted. It reasoned from a general rule because the engine was
absent — the `/context` shows **MCP tools 642**, only the three eve-sde tools,
no eve-fitting at all.

**Root cause, finally.** `.mcp.json` launched the fitting server with
`fitting/work/eosenv/bin/python` — an interpreter inside a gitignored tree. If
the venv is not built the process cannot start at all, so no amount of
in-server error handling helps: the server is absent, not broken. **Fixed:**
`.mcp.json` now launches with plain `python3`; the server re-execs into the
venv when it exists, and falls back to layer 1's stdlib stdio implementation
when it does not, so all 20 tools still appear and every one of them explains
itself. The prefix comparison matters — a venv's `bin/python` is a symlink to
the system interpreter, so comparing `realpath(sys.executable)` says they are
the same file and the hop silently never happens; compare `sys.prefix`.

**Q3 was a near-miss worth noting.** Asked which resist to plug against
Serpentis, the model ran one `query`, found `basePrice` NULL on all 184
hardeners, correctly said the SDE has no market prices — and then answered the
resist half from memory without the engine. Its damage-split claim is
unverified: pyfa's NPC damage patterns live in saveddata defaults, not in
`eve.db`, so neither layer can currently source them. Exposing them is
unbuilt work.

**Floor on the real surface, refined.** Final `/context`: system tools 29.3k +
system prompt 11.9k + skills 4.1k + MCP 0.6k + memory 0.3k = **~46k per round**
with layer 1 only; add the fitting tools' ~3.7k of schemas for **~50k** with
both servers. Against 41k measured in this container. The earlier 56k reading
included several unrelated MCP servers.

## 2026-08-19 — "legal" is not "good": the advisory pass

Owner's ESS-robbing fit request produced a Vindicator that passed
`validate_fit` clean and was, in their words, indefensible. The engine had
every fact needed to catch most of it and was never asked. Verified against
build 3470007:

- **5MN MWD on a battleship**: 179.9 m/s against 1,181.6 m/s for the 500MN,
  and the signature is **2,300 m either way** — the bloom is a flat
  percentage, so an undersized prop mod pays the entire cost for a fraction
  of the speed. Strictly dominated; worse than fitting nothing.
- **Cap Booster 800 vs 3200**: cap "not stable, 93.8 s" becomes **stable at
  36.2%**. Same 12 s module cycle, 4x the capacitor per cycle. The whole
  "93.8 s is your clock" framing was an artifact of the wrong charge.
- **Navy Cap Booster 800**: same 800 GJ in 24 m3 instead of 32.
- Three empty slots, never mentioned.
- No boosters or implants, despite the player explicitly offering them.

**Fix — `advisories`, separate from `problems`.** `problems` stays strictly
legality; `advisories` reports legal-but-pointless choices: slots left empty,
a prop mod undersized for the hull, a charge with a same-value smaller-volume
variant. All computed, none hardcoded.

The prop-mod test is worth recording because the obvious version was wrong. A
speed-gain threshold rejects the 5MN only if you set it above 36%, which would
false-positive on plenty of legitimate fits. **Mass is the honest
discriminator**: a size-matched prop mod adds roughly half the hull's mass and
the boost divides by mass, so the 5MN's 500,000 kg against a 105,200,000 kg
hull (0.5%) is unambiguous where "+36% speed" is not.

**Also fixed**: `Module xN` in EFT. It is drone/fighter/cargo syntax; on a
module pyfa died with `__init__() takes 2 positional arguments but 3 were
given` (constructing a Drone from a module), and the `x6, Void L` variant
missed the quantity regex and returned "unknown item". Both now give a real
message naming the item and the one-line-per-module rule.

**Not mechanised, and named instead** (SKILL.md, "Building a fit"): the
capacitor simulation assumes NO incoming neutralisation, which is exactly why
a triple-rep panel reads well and why resist modules beat a third repper under
neut pressure; and in-space rules like ESS field restrictions are not modeled
at all. The skill now also says to A/B uncertain choices with `compare_fits`
rather than guessing, and to take boosters and implants when the player offers
them.

## 2026-08-19 — the Machariel session: a fit recommended before it was checked

Owner's ESS question again, in a session with **no MCP servers at all**. The
transcript shows it: `find` located no local databases, so it downloaded
`eve-sde-items.sqlite` from the GitHub release into a scratchpad and queried it
with raw `python3 sqlite3` through Bash. Zero `mcp__eve-sde__*` calls, zero
`mcp__eve-fitting__*` calls, and the skill it loaded was **eve-sde, never
eve-fitting**. So the fitting engine was not merely unused — it was absent, for
the third session running, and the hook had not populated the databases.

**The order of operations was backwards.** The first message recommended a
complete Machariel fit from memory. Only when asked for EFT did it look module
names up — and several did not exist: "Adaptive Invulnerability Field II" (long
since renamed Multispectrum), "Faction Large Armor Plate", "Anti-Explosive
Screen Reinforcer". The fit changed materially between the two messages because
half of it had been invented. A name-verification pass ran over 12 items, and
`Republic Fleet Barrage L` — which does not exist, Barrage being T2-only — was
not among them and shipped in the "verified against the current SDE build"
answer.

Run through the engine afterwards, the delivered fit:

- **`Machariel` has 7 turret hardpoints, not "6 turret + 2 missile".** The
  answer left two highs empty and explained them as launcher hardpoints. One
  was a seventh gun. `hardpoints: {turret: [6.0, 7]}`.
- **50MN MWD on a battleship**, up from 5MN in the previous session but still
  two size classes short: 5,000,000 kg on a 94,680,000 kg hull (5.3%),
  385 m/s. With the 500MN: **1,495 m/s**, and the signature is 2,415 m against
  2,520 m — again the bloom does not shrink with the module.
- EHP 212,899, `problems: []`. Legal, and still wrong in three places.

**Two engine bugs this surfaced, both now fixed:**

1. **`Cargo(item, amount)` — every EFT with an ammo or cargo line failed to
   import.** `Cargo.__init__` takes the item only and `amount` is assigned
   after. Killboard and pyfa exports carry cargo lines as a matter of course,
   so this was breaking real pastes, and the symptom was the opaque
   `__init__() takes 2 positional arguments but 3 were given`. That same
   TypeError was what the earlier `Module xN` investigation hit — the module
   diagnosis was right but this was the mechanism underneath it.
2. `Republic Fleet Barrage L` now fails as `unknown item` rather than being
   swallowed, because the import gets far enough to resolve it.

Regression tests added for both: an EFT with ammo and paste must import, and a
module with a quantity suffix must name itself.

**The standing lesson**: three sessions, three different silent absences —
databases missing, engine venv missing, both servers missing — and each time
the model answered anyway, fluently and wrongly. Graceful degradation is now in
both servers, but nothing yet makes a *session with no servers at all* announce
itself, because there is no server present to say so. That is a skill-level
job: the fitting skill should refuse to publish a fit it has not imported.

## 2026-08-19 — the import rule, and cross-layer routing

Two changes from the Machariel session.

**Hard rule, first bullet of eve-fitting's "If you read nothing else":** never
publish a fit you have not imported. Every named module goes through
`import_fit` before it reaches the player — no exceptions for a "quick
suggestion". And if the eve-fitting tools are absent from the session, say the
engine is missing and stop, rather than hand-deriving. The evidence is in the
rule itself: three nonexistent module names, a seventh turret hardpoint left
empty and explained as a launcher slot, and a fit that changed materially
between two messages because half of it was invented.

**Cross-layer routing, owner's suggestion.** The asymmetry was real and
one-directional: eve-fitting's description already said "Pairs with the eve-sde
skill", while eve-sde's description named no successor at all — which is
exactly the direction the Machariel session failed in (loaded eve-sde, needed
eve-fitting, never loaded it).

The refinement worth recording: the cross-reference belongs primarily in the
**description**, not at the top of the body. Descriptions sit in the system
prompt and are what the model reads while *deciding* which skill to load; a
body is read only after that decision is already made. A pointer in eve-sde's
body would never have been seen by a session that never opened eve-sde — and
worse, a pointer in eve-fitting's body is useless to the failure mode where
eve-fitting is never loaded. So: description carries the routing decision, body
carries the mid-task correction for a model that loaded one layer and then
discovers it needs the other. Both now have both.

Honest expectation: this is a **fact**, not a discipline. Every guidance
intervention that failed in gens 8-11 asked for behaviour ("batch your
queries", "verify before asserting"); this one supplies information the model
did not have (another layer exists and answers a different class of question).
Those are different asks, and the second is the kind prose is actually good at.
It should still be measured rather than assumed — the routing claim is testable
by asking a pure fitting question in a session and seeing which skills load.

## 2026-08-19 — the import rule holds; hull selection is the next gap

Fresh solo-hunter question, deliberately unlike the ESS one (whose specifics I
had written into the skill, contaminating it as a test).

**Every mechanism built today fired.** eve-fitting loaded unprompted from a
question that never says "fitting"; both MCP servers were used; the fit was
**imported before it was published**; `edit_fit` iterated against the panel
seven times; `validate_fit` and `required_skills` ran; `advisories` were read
AND answered in prose ("every combat filler blew the CPU budget; leaving them
open beats stripping tank"); and the EFT came from `export_fit` rather than
being retyped from memory. That is the whole chain the Machariel session
skipped.

**The fit is nonetheless weak: 113.5 dps / 7,154 EHP on an assault frigate.**
The model chose the Jaguar from raw `attrs` across four hulls, hit a CPU wall,
and paid for it by deleting a Ballistic Control System, dropping to a single
Small Shield Extender and leaving two slots empty. Sanity-checked against
other hulls in the same role (both my comparison fits needed trimming, so read
these as upper bounds, not recommendations): a Wolf lands near 257 dps at
comparable EHP, an Enyo far higher still. 113 dps is low for the class.

**The gap is that hull selection was never A/B'd.** The skill's "A/B anything
you are unsure of" was applied to module swaps *within* the chosen hull and
never to the hull itself — which was the larger uncertainty. Four hulls were
compared on static attributes; none was built. Choosing on `attrs` and then
degrading the fit to make that choice work is the failure, and it is invisible
to `advisories`, which only sees the fit it is given.

**Correction to my own first instinct**: I assumed meta/compact tackle would
have relieved the CPU crunch. Measured, it saves ~26 CPU (scram 36->30, web
30->20, MWD 25->21) where a second BCS needs ~52. The model's diagnosis that
CPU forced the compromise was right; my assumption was wrong.

**Corroboration from my own analysis**: building comparison fits by hand, I
invented three rig names that do not exist (`Small Anti-EM Screen Reinforcer
II`, `Small Anti-Explosive Pump II` twice) and two of my three fits came back
illegal on CPU or slots. Every one was caught in a single `import_fit`. The
hard rule is not a formality — hand-built fits are unreliable even when the
builder knows exactly what to watch for.

Next lever, and it is a tool rather than prose: `sweep` already enumerates
candidates server-side. A hull-level comparison — same role fit across N hulls,
one call, panels back — would make "which ship" answerable the same way "which
module" already is.

## 2026-08-19 — sweep_hulls: enumeration as a tool call

Both probes failed the same way, and it was neither the math nor the
verification: the model worked from a **remembered candidate set**. Four
destroyers compared when twenty-four exist; four assault frigates compared on
static attributes, none built. Whatever layer it reached for, it used
correctly — on the shortlist that happened to come to mind.

`sweep_hulls(fit_id, group=…|hulls=…)` rebuilds a fit's module list on every
published hull in a class and ranks them. The general lever is the `group`
argument: the caller names a **class**, not members, so enumeration happens
server-side and a shortlist never forms. `sweep` already did this for modules;
this is the same idea on the hull axis, deliberately not a bespoke tool for the
case that surfaced it.

Measured on the delivered Jaguar fit, same modules across Assault Frigate:

| hull | dps | ehp | legal |
| --- | --- | --- | --- |
| Geri | 149.8 | 9,398 | yes |
| Jaguar (delivered) | 113.5 | 7,154 | yes |
| Cambion | 90.8 | 9,431 | yes |
| Vengeance | 124.9 | 7,746 | no (cpu -9.75) |

So the shipped answer was beaten on both axes at once by a hull it never
considered, with no module changes.

**On bonuses** (owner asked whether the sweep should return them): the engine
applies every hull, role and skill bonus when it builds each fit, so they are
already inside the dps/ehp/speed numbers and the ranking needs no correction.
The trait text is returned anyway, because the ranking alone is misleading in
one specific way — a turret-bonused hull scored with a missile fit places low
because the fit is wrong for it, not because the hull is. Enyo, Harpy and
Freki all land at 68.1 dps in the table above for exactly that reason, and the
`bonuses` line is what makes that legible rather than a false verdict.

Rows that do not fit are kept, flagged with their `problems`, and sorted below
the legal ones — a hull that would win with a small adjustment is worth seeing,
so long as nothing reads its numbers as achievable as-is.

## 2026-08-20 — the first real branch test, and three fixes it earned

First mobile session actually on the branch (the previous three ran on `main`,
which carries only the eve-sde skill — web sessions cut from the default branch
unless the source is picked explicitly). Process was the best yet: both servers
used, fit imported before publication, `edit_fit` iterated against the panel,
advisories read and answered, `applied_dps` against two real target profiles,
`required_skills` with `alpha_blocked`, and **the EFT block delivered unasked**.

Five tool calls failed. Two were the system working — the schema hint returned
`columns_available` and the model corrected in one round (six rounds of
`SELECT *` archaeology in the destroyer run), and `import_fit` rejected an
invented `Faint Epsilon Warp Scrambler II`. Three were parameter-name misses
costing a round each, one of them a regression I introduced in gen-11.

**The fit's real error, found by the owner, not the tooling**: it was CPU-bound
at 222.75/225 with 24 MW of powergrid spare, and spent **two rig slots on
Ancillary Current Routers — which boost powergrid**. It solved the constraint
that was not binding, using the exact slots that would have fixed the one that
was, and dropped a gun for CPU as a result. Verified: 3 guns 229.5 dps, and
the four-gun build with a compact MWD, a compact shield extender and ONE
`Small Ancillary Current Router I` is legal at 224.00/225 CPU and 84.30/85.25
PG for **306.1 dps and 10,106 EHP** — more damage AND more tank, with 150
calibration still spare.

Three fixes:

1. **Parameter aliases.** `query` takes `sql`, `compare_fits` takes
   `fit_a`/`fit_b`, `module_attrs` defaults to a useful attribute set instead
   of raising. Charging a round to say "wrong keyword" helps nobody, and the
   canonical name still leads in the schema.
2. **`advisories` now name the binding constraint** and flag a fitting rig
   aimed at the resource with slack. On the delivered Confessor:
   *"cpu is the binding constraint (99% used) while powergrid has 24 spare
   (77% used)"* and *"Small Ancillary Current Router II adds powergrid… 2 rig
   slots does nothing for the fit"*.
3. **`variants(item)` — the meta ladder**, on layer 1. Every published variant
   of a module with fitting cost and the deciding attributes, from
   `types.variationParentTypeID`. This is the general fix for the name-guessing
   loop the owner spotted: guess-then-reject reveals one name per round, while
   the ladder shows in one call that `Medium F-S9 Regolith Compact` is 26 CPU
   for 900 HP next to the II's 35 CPU for 1,100 — exactly the trade a CPU-bound
   fit needs and could not otherwise see.

Standing gap: `sweep_hulls` still went uncalled on a hull-choice question. The
model named the four T3Ds (a complete set, by luck) and built none of them.

## 2026-08-20 — making `sweep_hulls` reachable, and flagging hulls nobody can buy

Closing the standing gap above. The tool existed and was described in the
skill; both are prose-class interventions, and the measured hierarchy is
**tool shape > skill description > router prose > reference file**. So the
sweep now gets handed over as a *ready-to-paste call*, at the two moments a
fit is about to be made worse to fit a hull:

1. **When one resource binds and the other has slack** — the existing
   advisory now ends with `sweep_hulls(fit_id, group="Tactical Destroyer")`.
2. **When the loadout does not physically fit at all** — any capacity
   violation (grid, calibration, rack, hardpoints, drone bay) gets
   *"this loadout does not fit the Rifter as-is (high slots over by 1) —
   before downgrading modules to make it fit, check whether the HULL is what
   is wrong"* plus the same call. Deliberately general: it does not care
   which resource ran out, because the mistake is the same one either way —
   modules and hull chosen independently. Exactly one of the two roads fires,
   never both.

**The suggestion is pre-sized.** `sweep_hulls` caps at 20 hulls and the
Frigate group publishes 51, so an unsized suggestion would spend its round
learning the tool is fussy. `_sweep_call()` counts the class and emits
`sweep_hulls(fit_id, group="Frigate", limit=51)`. Measured end to end: 51
hulls, 1.7 s, ~5k tokens, no errored rows, Rifter first at 165.2 dps. The
smoke test now parses the call out of the advisory and executes it verbatim
— a suggestion that errors is a regression, not a wording nit.

**Availability.** A class sweep enumerates hulls that cannot be bought, and
they rank like everything else: the Frigate sweep returns Gold Magnate,
Silver Magnate, Metamorphosis and Echelon; the Assault Frigate sweep returns
Geri, Freki, Cambion, Malice, Shapash and Utu (6 of 15). Rows in the
`Special Edition Ships` market branch now carry an `availability` note.

What that note deliberately does **not** claim is tournament provenance —
that is not in the SDE. `metaGroup` looked like a discriminator and is not:
it false-positives on Imperial Issue battleships and event corvettes, and
false-negatives on Hydra, Tiamat, Chameleon and Whiptail, all AT prizes
sitting at Tech II. Market-group ancestry is a hard fact and is all the note
asserts; it also covers Praxis, Gnosis and Sunesis, which are cheap and
common, so the note names them and tells the reader to check price rather
than pretending the data can tell prize from freebie.

## 2026-08-20 (later) — the size ladder, and five other things a graded Svipul run exposed

Owner ran the "solo lowsec frigate killer, must be able to disengage" brief.
The answer was *procedurally* the best yet — legality verified, applied DPS
computed, cap instability disclosed, EFT delivered unasked, empty rig slot
explained rather than hidden — and the fit was still wrong, in a way the
tooling had every number to prevent.

**`sweep_hulls` went uncalled again.** The advisory fired four times carrying
the ready-to-paste call. Zero conversions. Putting a literal call in advisory
text is still prose, just prose shaped like code; I am counting the previous
entry's fix as a negative result. Mitigating: running the sweep myself showed
it would have *confirmed* the hull (Svipul 232.8 dps vs 127.3 for Hecate,
Confessor, Jackdaw and Skua, which lack the small-projectile bonus).

**The real miss was the gun size ladder.** It correctly found medium
autocannons will not fit a Svipul, then jumped to the *bottom* rung of the
small line — Republic Fleet 125mm, damage multiplier 2.579, falloff 4,300 m —
skipping 150mm, 200mm, 250mm and 280mm, all of which are small turrets and all
of which take the hull bonus. `Republic Fleet 200mm` is 3.610 and 5,160 m for
+3 MW a gun. Measured, same skills, same engine:

| | delivered (125mm) | 200mm, neuts dropped |
|---|---|---|
| dps (Sharpshooter) | 310.4 | **347.6** |
| applied, webbed frigate @5km | 267.7 | **322.5** |
| applied, webbed destroyer @8km | 177.1 | **248.5** |
| capacitor | 81 s | **stable** |
| ehp / align / speed | 8,322 / 4.98 s / 2,008 | identical |

Head to head at 5 km the corrected fit kills the delivered one in 26 s and
survives 32 s. `variants` could not have shown this: it walks ONE family and
never crosses to the next, so a caller holding a 125mm autocannon has no way
to learn 200mm exists. That is now `size_ladder` — sibling families in the
group, one representative each matched to the tier asked about, tagged
`same_size_as_yours` from the required skill (turrets) or rigSize (rigs).
It generalises the Vindicator prop-mod error: same shape, right family, wrong
rung. Suppressed for rigs, where sibling families are different *effects* and
cross-size rows are unfittable noise.

**Five more, all owner-caught or owner-confirmed:**

1. **Two tech 1 rigs usually beat one tech 2** and nothing in the stack could
   show it. Small Low Friction Nozzle Joints: T1 -11.7% for 50 calibration,
   T2 -14.0% for 75. Stacked, 2xT1 is **-20.7%** for 100 calibration and two
   slots. `upgradeCost` is now in the ladder and rig families carry the
   `stacking` multipliers, so the arithmetic is doable from one call.
2. **Align time was reported with the prop mod running.** `fit.alignTime`
   reflects module states, and a 5MN MWD is +500,000 kg on a 1.4 Mkg hull —
   the graded answer quoted 4.98 s for an align that is really **3.67 s**. You
   align with the prop off, which is the entire reason the mass penalty
   matters. `align_time_prop_off_s` plus a note now ships in every panel; the
   correction is exact because align is linear in mass at fixed agility.
3. **Six rounds of `module_attrs`, one module at a time**, to find which module
   blew the powergrid. The server had every number when it declared the
   overrun. `fitting_breakdown` now rides along with the problem: per-module
   cost of the over resource, largest first.
4. **The binding-constraint advisory named a resource, and a resource is not an
   action.** Told "rig effort should target powergrid", the run restated the
   sentence, reasoned about a *damage* rig, left the rig slot empty. It now
   names the module: *"the rig for it is Small Ancillary Current Router II:
   +15% powergrid for 150 calibration, and you have 1 rig slot free but only
   25 calibration, 125 short — so a cheaper rig has to come out first. A damage
   or speed rig here solves nothing."*
5. **"No fit-relevant module changed between these builds"** — asserted, never
   checked. `engine_info` now reports both builds and a `parity` field that
   states in words that no attribute-level comparison has been run.

**Found while building #5: this checkout's SDE is MIXED** — `misc` at 3470007,
`industry`/`items`/`universe` at 3466501. The build number was being read from
one part as though it were the database's. Both servers now scan every part and
say so when they disagree.

Unknown-name errors also carry `did_you_mean` now (the run invented
`Rage Light Missile` and had to go back to layer 1 with a LIKE query to
recover). eos's own `searchItems` raises under this SQLAlchemy, so it is raw
SQL over the engine's connection, with a difflib fallback for transpositions
that match no LIKE pattern at all.

## 2026-08-20 (third) — two regressions I shipped, and the ammo question nobody asked

Owner re-ran the lowsec brief on the pushed stack. Three of the six fixes
visibly worked; two of my own changes were broken in ways the smoke suite
passed anyway.

**Regression 1: `_sde_build` shipped as an MCP tool.** I inserted it directly
above `engine_info`, so it inherited that function's `@mcp.tool()` and
`@_engine_thread` decorators, and my cleanup pass matched only
`@mcp.tool()\ndef _sde_build` — not the two-decorator stack that was actually
there. A private helper became a callable tool paying standing schema on every
round of every session. The suite now fails on any tool whose name starts with
an underscore.

**Regression 2: the parity chain.** I appended the mixed-build clause *between*
the `elif` and the `else`, so `else` bound to `if mixed:` and parity was
overwritten unconditionally on every non-mixed run. The owner's session showed
the result: `sde_build: "3473160"` sitting next to *"layer 1 databases not
found from here"*. It passed here only because **this checkout is mixed**, so
the run never took the broken path — and the assertion I wrote (`'UNVERIFIED'
in parity`) still held, because the mixed prefix is *prepended* to the
UNVERIFIED text. A test that passes for the wrong reason. The branches are now
`_parity_text(engine_build, sde_build, mixed)`, a pure function, with all four
combinations asserted directly.

Lesson worth keeping: both bugs were in code whose *only* observable behaviour
was branch selection, tested through a tool that could only ever exercise one
branch in a given checkout. Branch logic that matters gets pulled out and
tested as a function.

**What worked.** `align_time_prop_off_s` was quoted correctly and labelled
("align (prop off) 4.76s", "3.17s in Propulsion") — the answer used the right
number unprompted. `fitting_breakdown` appeared on an over-CPU fit and the
reply went straight to a CPU-output module rather than querying modules one at
a time. `did_you_mean` recovered two invented names (`Energized Adaptive Nano
Membrane II`, `Light Pulse Laser II`) in one round each. The `size_ladder`
moved the answer from `Dual Light Pulse Laser II` (2.4) to `Small Focused Pulse
Laser II` (3.6) — the right rung, chosen from data. No fabricated build-parity
claim this time.

**`sweep_hulls` uncalled for the third consecutive run**, two more advisory
impressions. Ran it myself: Confessor 291.5 dps against 159.4 for every other
tactical destroyer, all of which are illegal with this loadout. Third time it
would have confirmed rather than changed the answer, which is worth saying
plainly — the tool has never once been the difference on a real question.

**The real gap this run: ammunition.** The fit shipped Multifrequency S and was
tested against exactly one target at exactly one range. Measured across the
brief it was actually written for:

| crystal | raw | frig@5km | dess@5km | dess@9km | dess@14km |
|---|---|---|---|---|---|
| Multifrequency S | 291.5 | 260.6 | 262.9 | **34.3** | 0.7 |
| Scorch S | 267.2 | **266.3** | **270.4** | **271.0** | **114.5** |
| Conflagration S | 432.4 | **382.5** | **389.2** | 50.8 | 1.0 |

Scorch beats Multifrequency at *every* range including point blank, by 8x at
9 km. Conflagration is +47% applied at brawl range. Lasers swap crystals
instantly with no reload — it is the one thing the weapon system is best at,
and an Amarr laser boat was recommended without it being mentioned. Lower raw
dps, higher applied everywhere: exactly the trap that reading `dps` instead of
`dps_applied` sets.

**A trap in my own ladder, found while checking the guns.** `damageMultiplier`
in `variants` is the base attribute, so faction (3.75) reads as better than
tech 2 (3.6). The engine disagrees: 291.5 dps on T2 versus 276.0 on Imperial
Navy, because tech 2 turrets take the specialization skill (+2%/level, +10% at
V) and faction ones do not. 3.6/3.75 x 1.10 = 1.056, matching the measured
ratio exactly. I nearly repeated the very error I had graded. The ladder needs
to say that tech 2 carries a skill bonus its printed multiplier omits.

## 2026-08-20 (fourth) — charges, pilot effects, and the sweep's structural bias

Five changes, four of them owner-requested and one owner-diagnosed.

**The sweep was asking the wrong question.** The owner spotted it: *"is it just
checking with the same guns it currently has or is it better than trying to
check lasers on a jackdaw"*. It re-parsed the identical EFT body onto every
hull, so a laser fit scored the Jackdaw at `turret hardpoints over by 4` and
every off-race hull at 55% of the Confessor. That is not a fact about the
hulls, it is a fact about the guns being Amarr — and it is biased toward the
hull the fit was built on **by construction**, which finally explains why three
consecutive runs called the sweep and it never once changed an answer. It
literally could not.

`adapt=True` now re-arms each hull with the weapon system its own traits name,
at the tier the fit already flies, filling that hull's hardpoints. Both
readings are legitimate and they answer different questions, so both ship:

| hull | plain | adapted |
|---|---|---|
| Confessor | 291.5 | 291.5 (left alone — already armed as its traits want) |
| Hecate | 159.4, illegal | **385.4** with 5x Light Neutron Blaster II |
| Svipul | 159.4, illegal | 262.7 with 4x 200mm AutoCannon II |
| Skua | 159.4, illegal | 243.3 with 5x Rocket Launcher II |
| Jackdaw | 159.4, no hardpoints | 200.4 with 5x Rocket Launcher II |

Hull traits turn out to name both the weapon system and its size — turret size
lives in the required skill ("Small Energy Turret", verbatim in trait text),
launcher size lives in the group ("Missile Launcher Light" -> "Light Missile").
Two traps found while building it: sorting candidates by meta level reached
straight for **officer modules** (Makra's Modified, Panola's Modified) which is
precisely what a caller excluding officer/abyssal does not want — it now
matches the source fit's tier; and picking the first candidate at that tier put
a *125mm Gatling* on a Svipul, the same wrong-rung error the size ladder
exists to prevent, so it now builds every rung and keeps the best.

**Ammunition was never being swept.** `applied_dps` now ranks every valid
charge at the requested range and always shows the loaded one, whatever it
ranks. On the graded Confessor at 9 km: Scorch S 271.0 applied against the
shipped Multifrequency S at 34.3 — while Scorch shows 24 *less* paper dps.
Reading `dps` picks Multifrequency; only a swept applied number finds Scorch,
and lasers change crystals with no reload, so it was a free choice made wrongly
by default. Cross-validated: the swept Scorch figure equals an independently
built Scorch fit to the decimal, and the smoke test asserts that equality so
the sweep cannot drift from what it claims to measure. Cap raised past the 54
crystals a small pulse laser accepts, with `not_evaluated` reported rather than
truncating in silence.

**`pilot_effects`: implants and combat drugs, measured rather than named.** The
graded answer recommended `Zainou 'Deadeye' Target Navigation Prediction` — a
**missile** hardwiring — to an all-turret fit, calling it a tracking bonus. The
tool fits each candidate to the actual fit and re-runs the panel, listing only
what moved a number: that implant reports **0 of 6 moved a number**. 48 combat
boosters in 2.0 s, 74 slot-10 implants in 2.9 s, 171 slot-6 in 6.8 s. Side
effects roll per dose, are excluded from the deltas, and are listed per row.
Two details worth keeping: the capacitor metric had to split into
`cap_lasts_s` and `cap_stable_pct`, because a cap-stable fit has no `lasts_s`
to improve and a single field reported "no change" for a booster taking it from
60% to 90%; and the skill doc had been *wrong* — it said "`set_booster` applies
boosters", when `set_booster` attaches command-burst fits. Three different
things are called boosters here (combat drugs, command bursts, environment) and
the doc now separates them.

**Two smaller ones.** `damageMultiplier` is a base attribute, so faction reads
better than tech 2 while losing in the engine — rows now carry
`specialization_skill` and the family carries a warning. And the size ladder is
cut to adjacent size classes: a small-turret question was returning Dual Giga
Pulse Laser II at 137,500 MW, which cannot be fitted to anything the caller was
asking about.

## 2026-08-20 (fifth) — how layer 1 desynced, and the guard that lets it

Owner asked how layer 1 got out of sync when it was supposed to rebuild on
every release. **It did rebuild.** The publish pipeline was healthy the whole
time — 54 scheduled runs, all green, marker commits `SDE build 3470007`
(run #37, 08-17 13:11) and `SDE build 3473160` (run #94, 08-19 13:15), with
every later run exiting in ~7 s on "already published; nothing to do".

What desynced was **this checkout's working copy**, and the `builtAt` stamps
prove the parts were never built here at all:

    industry / items / universe   builtAt 2026-08-14T02:51:05Z   build 3466501
    misc                          builtAt 2026-08-17T13:11:44Z   build 3470007

`02:51:05` is a minute before `sde-build.json`'s `publishedAt`, and
`13:11:44` matches workflow run #37 to the second — both are release
artifacts, fetched piecemeal at different times. `misc` is the only part
carrying `fighterAbilities`, which is very likely why it alone was pulled
during the fighter-support work. That last step is inference; the timestamps
are not.

**The actual defect was the guard.** `setup.sh` and the session-start hook both
tested that the files EXIST:

    if ls eve-sde-*.sqlite >/dev/null 2>&1; then echo "already bootstrapped"; exit 0; fi

Existence is not currency and it is not coherence, so a long-lived container
never refreshed and never noticed its parts disagreed. `split_db` compounds it:
it `continue`s past a group whose tables are absent, and the `os.remove(out)`
that would clear a stale part sits *inside* the loop, after the `continue` —
so a partial build leaves older parts standing rather than replacing them.

`sde/freshness.py` now closes it. One ~80-byte manifest fetch says what CCP is
at; every part's `sdeBuildNumber` says what we have. Mixed or behind, `--fix`
rebuilds into a temporary directory and moves the parts into place only once
they all exist, so a failed rebuild leaves the old data untouched instead of
deleting it and dying. Offline, it warns and returns — a set that disagrees
with *itself* is wrong whether or not the network is there. Wired into the hook
and into `setup.sh`'s already-present branch, with `EVE_SDE_NO_REFRESH=1` as
the escape hatch.

Run here it did the whole job in 47 s: 4 mixed parts became 7 coherent ones at
build **3475087** — newer than anything the pipeline had published, because CCP
released again at 11:08 that morning — and it recovered `moons`, `world` and
`cosmetic`, which had been missing entirely.

Two builds of drift then had to be paid off: `verify_claims.py` reported 131 of
138 claims still true and 7 counts moved (published types 26,992 -> 26,981,
unitID 108 gaining an attribute at 58 -> 59, and five others). All updated,
`DOC_BUILD` re-pinned, 138/138. Both smoke suites pass at the new build.

Deliberately out of scope: layer 2's `eve.db` stays at 3470007, so
`engine_info.parity` now reports the skew as UNVERIFIED, which is exactly what
it is for. Refreshing the engine is a pyfa rebuild plus a battery re-pin, and
CI already does that per release; a session-start hook is the wrong place.

## 2026-08-26 — the control arm, run by accident

Owner ran the composed probe. The session had **neither MCP server**: it opened
with `find / -maxdepth 4 -iname "eve-sde*.sqlite*"` returning nothing, fell
through `acquisition.md` to download `eve-sde-items.sqlite` from the public
release into a scratch directory, and queried it with raw `sqlite3` in Python.
Not one `mcp__eve-sde__*` or `mcp__eve-fitting__*` call in either turn. The
repo and skills were present — `eve-sde` loaded and read its own references —
so this is a clean **control-arm measurement**: the stack's docs without the
stack's tools.

Layer 1's acquisition fallback worked exactly as designed, and the trait data
it pulled was read correctly (mode bonuses, hardpoints, slot layout all match).
Everything downstream of "now build a fit" failed.

**Turn 1** recommended `3x 220mm AutoCannon II` on a Svipul. Verified here:

* `220mm AutoCannon II` is not a type — the name is `220mm Vulcan AutoCannon II`
* `Small Focused Warp Disruptor` is not a type
* `Small Anti-EM Screen Reinforcer II` is not a type (`Small EM Shield Reinforcer II`)
* a warp disruptor was placed in a HIGH slot, and duplicated in the mids
* three guns on a hull whose hardpoints it had itself queried as **four**

**Turn 2**, asked for EFT, opened *"All confirmed against the game data"* and
was worse. Imported:

    problems: powergrid over: 323 / 67.5, calibration over: 425 / 400, rig slots over by 1
    slots:    high [4,6]  med [3,4]  low [4,4]  rig [4,3]

`Small Core Defense Field Extender I` is a **rig**, listed in the mid rack, so
the fit carries four rigs on a three-rig hull and lost its shield extender
entirely — a "shield tanked" fit with no buffer. The guns are still medium:
99 MW each against 67.5 MW of grid, **4.8x over**. Correctly built with 200mm
smalls the same hull does **240.0 dps and fits** (53.9/67.5 PG) against 128.9
for the three mediums — the wrong-rung error, third run running, now with a
rationalisation attached ("3 guns, not 4 — leaves CPU/PG headroom").

The ammo advice, which was the whole answer to "works at all ranges", is
inverted. Measured `weaponRangeMultiplier`:

| charge | range mult | tracking | damage |
|---|---|---|---|
| Republic Fleet EMP S | 0.5 | 1.0 | EM |
| Republic Fleet Fusion S | 0.5 | 1.0 | explosive |
| Republic Fleet Phased Plasma S | 0.5 | 1.0 | **thermal** |
| **Barrage S** | **1.0** | 0.75 | explosive/kinetic |

All three it chose are range-**identical**. Barrage, the ammo that actually
doubles range, is absent from the cargo. "Phased Plasma as an EM-resist-punching
option" inverts thermal and EM. `Nanite Repair Paste ... in case you take
structure damage` is not what paste does.

**Two bugs of my own, caught while grading.** `pilot_effects` excluded implant
slots 1-5 as "attribute implants (training speed)". The pirate SETS live there:
15 of the 18 published Snake implants are slots 1-5, so asking about Snakes
returned **0 moved a number** — a false negative from the one tool built to stop
implants being named from memory. Now 18 considered, 15 moved. And `_headline`
had no warp-speed metric, so all twelve Ascendancy implants also read 0; with
`warp_speed_aus` added they report `{'warp_speed_aus': 0.26}` and **nothing
else**, which is the direct refutation of the transcript's "Ascendancy helps
align/warp speed". Align and warp speed are different numbers. Both fixed, both
asserted.

**The standing lesson, again.** `eve-sde/SKILL.md` carries an explicit rule to
load `eve-fitting` when a question crosses into combining a hull with modules.
The question was "what should I fly, how should it be fitted". `eve-sde` loaded;
`eve-fitting` did not. Prose telling the model what to load fails the same way
prose telling it to call `sweep_hulls` failed three times. Worth noting the
control arm is not a wasted run: it is the first clean measurement of what this
question looks like with the documents and none of the tools, and the answer is
three invented module names and a fit five times over its powergrid.
