"""EVE fitting engine MCP server — pyfa's eos behind ~10 terse tools.

    python3 server.py --pyfa /path/to/pyfa-checkout      # stdio transport

Design rules (docs/roadmap-fitting-mcp.md, token budget):
- Fits are stateful server-side objects addressed by short IDs; EFT text is
  the only import/export payload. The conversation never re-sends a fit.
- Tool descriptions are one line each — the teaching lives in the
  fitting-knowledge skill, never here, because schemas are paid every turn.
- Outputs are compact dicts with unit-suffixed keys; empty sections omitted.
- Anything unmodeled is named, never silently ignored.
"""
import argparse
import importlib
import json
import glob
import os
import random as _random
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'engine'))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'spike'))

# `.mcp.json` must not name an interpreter that lives in a gitignored tree: if
# fitting/work/eosenv is not built yet the process cannot even start, the
# server is absent rather than broken, and the model answers fit questions from
# doctrine. Measured 2026-08-19 — a session with no eve-fitting tools said
# three Damage Controls "stack with the standard penalty"; they do not stack at
# all, maxGroupFitted is 1. So: launch under any python3, hop into the venv
# when it exists, and serve a useful error when it does not.
VENV_DIR = os.path.join(ROOT, 'fitting', 'work', 'eosenv')
VENV_PY = os.path.join(VENV_DIR, 'bin', 'python')
# Compare PREFIXES, not interpreter paths: a venv's bin/python is a symlink to
# the system interpreter, so realpath() says they are the same file and the
# hop never happens. sys.prefix is what actually differs.
if (os.path.exists(VENV_PY)
        and os.path.realpath(sys.prefix) != os.path.realpath(VENV_DIR)):
    os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])

try:
    from mcp.server.mcpserver import MCPServer
    _WRONG_INTERPRETER = False
except ImportError:
    # No venv, so no SDK. Serve with layer 1's stdlib implementation instead of
    # dying: the tools appear and every one of them explains itself.
    sys.path.insert(0, os.path.join(ROOT, 'sde', 'mcp'))
    from _stdio import MCPServer
    _WRONG_INTERPRETER = True

try:
    from headless import bootstrap  # noqa: E402
except Exception:                     # engine helpers may pull unavailable deps
    def bootstrap(_path):
        raise RuntimeError('engine support modules unavailable')

_parser = argparse.ArgumentParser()
_parser.add_argument('--pyfa', default=os.environ.get('PYFA_PATH'),
                     required='PYFA_PATH' not in os.environ)
ARGS = _parser.parse_args()

# The engine lives in a gitignored working tree, so a fresh clone has no `eos`
# to import. Crashing here takes the whole server down as CONNECTION_CLOSED,
# the tools never appear, and the model answers fit questions by hand instead
# — measured on 2026-08-19: a max-cargo question got a confidently wrong
# answer (it assumed cargo was stacking-penalised) purely because the engine
# was absent. Start anyway and make every tool say what is wrong.
ENGINE_ERROR = None


def _load_engine():
    """Import the engine and build the tables that depend on it.

    Retryable: a session whose bootstrap is still running (or which was
    started before `./setup.sh` ran) can pick the engine up on a later call
    instead of making the user restart.
    """
    global ENGINE_ERROR, CalcType, FittingModuleState, eftlib, graphlib
    global stat_panel, _FS, STATES, RACKS
    try:
        # A failed first attempt leaves the missing directory in the import
        # system's negative cache, so a later retry would keep "failing" after
        # the bootstrap finished. Drop that cache before every attempt.
        importlib.invalidate_caches()
        bootstrap(ARGS.pyfa)

        # Saveddata must be a file, not :memory:: MCP tools run on worker threads
        # and sqlite :memory: is per-connection, so each thread would see an empty
        # schema.
        import tempfile  # noqa: E402
        import eos.config as _eos_config  # noqa: E402
        _eos_config.saveddata_connectionstring = \
            'sqlite:///' + os.path.join(tempfile.mkdtemp(prefix='eve-fitting-mcp-'), 'saveddata.db')

        import eos.db  # noqa: E402,F401 — must precede eos.saveddata imports
        eos.db.saveddata_meta.create_all()  # what pyfa.py does at startup
        from eos.const import CalcType, FittingModuleState  # noqa: E402
        import eft as eftlib  # noqa: E402
        import graph as graphlib  # noqa: E402
        from panel import stat_panel  # noqa: E402

        graphlib._install_shims(ARGS.pyfa)
    except Exception as _exc:                 # noqa: BLE001 — any import failure
        ENGINE_ERROR = (
            f'the fitting engine is not built ({type(_exc).__name__}: {_exc}). Run '
            '`./setup.sh` at the repo root (~2 min)' +
            (', then start a new session so the server can use the engine venv. '
             if _WRONG_INTERPRETER else
             '; the next call picks it up, no restart needed. ') +
            'Until then this server cannot compute anything — say '
            'so rather than deriving fit numbers by hand. Stacking, calibration and '
            'slot legality are exactly what hand-derivation gets wrong.')

        CalcType = FittingModuleState = _Absent()
        eftlib = graphlib = _Absent()
        stat_panel = None
        _FS = _Absent()
    else:
        ENGINE_ERROR = None
        from eos.const import FittingSlot as _FS
    STATES = {'offline': FittingModuleState.OFFLINE,
              'online': FittingModuleState.ONLINE,
              'active': FittingModuleState.ACTIVE,
              'overheated': FittingModuleState.OVERHEATED}
    RACKS = ((_FS.HIGH, 'high', 'hiSlots'), (_FS.MED, 'med', 'medSlots'),
             (_FS.LOW, 'low', 'lowSlots'), (_FS.RIG, 'rig', 'rigSlots'),
             (_FS.SUBSYSTEM, 'subsystem', 'maxSubSystems'),
             (_FS.SERVICE, 'service', 'serviceSlots'))


class _Absent:
    """Stands in for engine symbols so the module-level tables still build."""
    def __getattr__(self, _name):
        return None


_load_engine()

mcp = MCPServer('eve-fitting')

# All engine work runs on ONE dedicated thread. The MCP SDK dispatches sync
# tools to arbitrary worker threads, and eos's SQLAlchemy sessions hold sqlite
# objects with thread affinity — a long-lived server eventually lands two
# calls on different threads and dies with "SQLite objects created in a
# thread can only be used in that same thread" (first seen on an import that
# touched the saveddata `overrides` table, 2026-08-17). The smoke test never
# caught it: its client happens to dispatch every call to the same thread.
import concurrent.futures  # noqa: E402
import functools  # noqa: E402
import threading  # noqa: E402

_ENGINE_THREAD = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix='eos-engine')


def _engine_thread(fn):
    # Re-entrant: tools call each other (compare_fits -> get_stats), and a
    # submit from the pool's own single thread would deadlock forever.
    @functools.wraps(fn)
    def pinned(*args, **kwargs):
        if ENGINE_ERROR:
            _load_engine()                     # bootstrap may have finished since
        if ENGINE_ERROR:
            raise RuntimeError(ENGINE_ERROR)   # every tool, one honest message
        if threading.current_thread().name.startswith('eos-engine'):
            return fn(*args, **kwargs)
        return _ENGINE_THREAD.submit(fn, *args, **kwargs).result()
    return pinned


FITS = {}
BOOSTS = {}       # fit_id -> [booster fit_id, ...]
PROJECTIONS = {}  # fit_id -> [projector fit_id, ...]
ENVS = {}         # fit_id -> projected env Module
_counter = 0
# Ids are salted per boot: after a restart a stale id must fail loudly,
# never silently resolve to a recycled one (a mid-eval restart once aliased
# one session's resident Vedmak to another session's Thanatos).
_BOOT = ''.join(_random.choices('cdfghjkmnpqrtvwxyz', k=2))

ENV_GROUPS = ('Effect Beacon', 'MassiveEnvironments', 'Abyssal Hazards',
              'Destructible Effect Beacon')



def _new_id():
    global _counter
    _counter += 1
    return f'f{_BOOT}{_counter}'


def _fit(fit_id):
    if fit_id not in FITS:
        raise ValueError(
            f'unknown fit_id {fit_id!r}; known: {sorted(FITS)} '
            '(ids are per-server-boot: if this id worked before, the server '
            'restarted and its fits are gone — re-import and continue)')
    return FITS[fit_id]


def _ship_name(fit):
    return fit.ship.item.typeName


def _recalc(fit, factor_reload=False):
    # Ordering is pyfa's: command bursts BEFORE the local calc (eos consumes
    # commandBonuses as it applies them), projected fits AFTER it (the local
    # calc's clear() would wipe modifications applied earlier).
    fit.factorReload = factor_reload
    fit.clear()
    fit_id = next((k for k, v in FITS.items() if v is fit), None)
    for booster_id in BOOSTS.get(fit_id, []):
        booster = FITS.get(booster_id)
        if booster is None:
            continue
        booster.factorReload = False
        booster.clear()
        booster.calculateModifiedAttributes(targetFit=fit, type=CalcType.COMMAND)
    fit.calculateModifiedAttributes()
    for proj_id, proj_range_m in PROJECTIONS.get(fit_id, []):
        projector = FITS.get(proj_id)
        if projector is None:
            continue
        projector.factorReload = False
        projector.clear()
        if projector.getProjectionInfo(fit.ID) is None:
            from eos.db.saveddata.fit import ProjectedFit
            projector.projectedOnto[fit.ID] = ProjectedFit(projector.ID, projector)
        # None = zero range (calculateRangeFactor returns 1); meters otherwise —
        # falloff-aware strength, 0 beyond optimal + 3x falloff for most ewar
        projector.getProjectionInfo(fit.ID).projectionRange = proj_range_m
        projector.calculateModifiedAttributes(targetFit=fit, type=CalcType.PROJECTED)


_SWEEP_LIMIT = 20


RIG_SIZE_NAMES = {1: 'Small', 2: 'Medium', 3: 'Large', 4: 'Capital'}

# The rig that raises each fitting resource. Both lines exist at every rig size.
FITTING_RIG = {'powergrid': ('Ancillary Current Router', 'powerEngineeringOutputBonus'),
               'cpu': ('Processor Overclocking Unit', 'cpuOutputBonus2')}


def _fitting_rig(fit, resource):
    """The concrete rig that raises `resource` on THIS hull, priced.

    Measured 2026-08-20: the advisory named the binding resource and told the
    reader to aim rig effort at it. The reader restated that sentence, reasoned
    about a DAMAGE rig, left the rig slot empty and shipped the weaker fit. A
    resource is not an action. Name the module, its calibration price and what
    is actually free, and there is nothing left to infer.

    Every name is resolved through the engine before it is emitted, so a size
    that does not exist produces silence rather than an invented module.
    """
    spec = FITTING_RIG.get(resource)
    if spec is None:
        return None
    family, bonus_attr = spec
    attr = fit.ship.getModifiedItemAttr
    size = RIG_SIZE_NAMES.get(int(attr('rigSize') or 0))
    if not size:
        return None
    free_calib = (attr('upgradeCapacity') or 0) - fit.calibrationUsed
    used_rigs = sum(1 for m in fit.modules if not m.isEmpty and m.slot is not None
                    and int(m.slot) == int(_FS.RIG))
    free_slots = int(attr('rigSlots') or 0) - used_rigs
    best = None
    for tier in ('II', 'I'):
        item = eftlib._lookup(f'{size} {family} {tier}')
        if item is None:
            continue
        cost = item.attributes.get('upgradeCost')
        bonus = item.attributes.get(bonus_attr)
        if cost is None or bonus is None:
            continue
        cand = {'name': item.typeName, 'calibration': cost.value,
                'bonus_pct': bonus.value, 'affordable': cost.value <= free_calib}
        # prefer the strongest that actually fits the calibration left; fall
        # back to naming the tech 2 so the reader sees the ceiling either way
        if cand['affordable']:
            best = cand
            break
        # nothing fits yet: keep walking down so the fallback is the CHEAPEST
        # tier, which is the smallest calibration gap the reader has to close
        best = cand
    if best is None:
        return None
    best.update({'free_calibration': round(free_calib), 'free_rig_slots': free_slots})
    return best


def _sweep_call(fit):
    """The `sweep_hulls` call for this fit's hull class, pre-sized.

    Naming the call without a limit sends the reader straight into an error on
    any large class — the Frigate group publishes 51 hulls against a default
    limit of 20 — and a suggestion that fails on first use is worse than no
    suggestion, because the round it costs is spent learning the tool is
    fussy rather than learning the answer.
    """
    group = fit.ship.item.group.name
    try:
        grp = _group_by_name(group)
        n = sum(1 for i in grp.items if getattr(i, 'published', True))
    except Exception:                     # noqa: BLE001 — advisory text only
        n = 0
    over = f', limit={n}' if n > _SWEEP_LIMIT else ''
    return f'sweep_hulls(fit_id, group="{group}"{over})'


def _advisories(fit):
    """Choices that are legal but do nothing, or leave free value on the table.

    Distinct from `problems`, which stays strictly about in-game legality — a
    fit can be `problems: []` and still be indefensible. Measured 2026-08-19:
    a generated Vindicator passed validation with a cruiser-size prop mod
    contributing +38% speed for the full signature bloom, three empty slots,
    and a charge whose faction variant is the same capacitor in 25% less
    volume. None of that is illegal; all of it is checkable.
    """
    _recalc(fit)
    out = []
    attr = fit.ship.getModifiedItemAttr

    from collections import Counter
    used_by_slot = Counter(int(m.slot) for m in fit.modules
                           if not m.isEmpty and m.slot is not None)
    empty = []
    for slot, label, attr_name in RACKS:
        total = int(attr(attr_name) or 0)
        free = total - used_by_slot.get(int(slot), 0)
        if free > 0:
            empty.append(f'{free} {label}')
    if empty:
        out.append('slots left empty: ' + ', '.join(empty)
                   + ' — say why, or fill them')

    # A prop mod divides its boost by hull mass, so an undersized one buys
    # almost no speed while still paying the full signature bloom.
    # Size-matching is mass, not the speed gain: a size-matched prop mod adds
    # roughly half the hull's mass, and the boost divides by mass. A 5MN on a
    # battleship adds ~0.5% and buys ~+38% speed where the 500MN buys ~+800%,
    # while the signature bloom is a flat percentage either way — so a raw
    # "+38%" reads acceptable and is not.
    base_m = fit.ship.item.attributes.get('mass')
    base_m = base_m.value if base_m is not None else None
    base_v = fit.ship.item.attributes.get('maxVelocity')
    base_v = base_v.value if base_v is not None else None
    now_v = attr('maxVelocity')
    for prop in (m for m in fit.modules if not m.isEmpty
                 and m.item.group.name == 'Propulsion Module'):
        added = prop.getModifiedItemAttr('massAddition') or 0
        if not base_m or added / base_m >= 0.10:
            continue
        gain = f'{(now_v / base_v - 1) * 100:.0f}%' if (base_v and now_v) else 'little'
        out.append(
            f'{prop.item.typeName} is undersized for this hull: it adds {added:g} kg '
            f'to a {base_m:g} kg ship ({added / base_m * 100:.1f}%), so it buys only '
            f'+{gain} max velocity. The signature bloom is a flat percentage and does '
            'NOT shrink with the module — an undersized prop mod pays the full '
            'signature cost for a fraction of the speed. Compare the size-matched one.')

    # Which resource is actually binding, and whether any fitting rig is
    # aimed at the other one. Measured 2026-08-19: a Confessor sat at 99% CPU
    # with 24 MW of powergrid spare and spent TWO rig slots on powergrid rigs
    # — solving the constraint that was not binding, using the exact slots
    # that would have fixed the one that was.
    named_sweep = False
    cpu_max, pg_max = attr('cpuOutput') or 0, attr('powerOutput') or 0
    if cpu_max and pg_max:
        cpu_pct, pg_pct = fit.cpuUsed / cpu_max, fit.pgUsed / pg_max
        tight, slack = ('cpu', 'powergrid') if cpu_pct > pg_pct else ('powergrid', 'cpu')
        tight_pct, slack_pct = max(cpu_pct, pg_pct), min(cpu_pct, pg_pct)
        if tight_pct >= 0.90 and slack_pct <= 0.85:
            free = (pg_max - fit.pgUsed) if slack == 'powergrid' else (cpu_max - fit.cpuUsed)
            rig = _fitting_rig(fit, tight)
            if rig is None:
                aim = f'fitting effort, rigs included, should target {tight}. '
            else:
                if rig['free_rig_slots'] <= 0:
                    where = ('no rig slot is free, so this is a rig SWAP, not an '
                             'addition')
                elif rig['affordable']:
                    where = (f"you have {rig['free_rig_slots']} rig slot(s) and "
                             f"{rig['free_calibration']} calibration free — it fits "
                             'as things stand')
                else:
                    short = rig['calibration'] - rig['free_calibration']
                    where = (f"you have {rig['free_rig_slots']} rig slot(s) free but "
                             f"only {rig['free_calibration']} calibration, {short:.0f} "
                             'short — so a cheaper rig has to come out first')
                aim = (f"the rig for it is {rig['name']}: +{rig['bonus_pct']:g}% "
                       f"{tight} for {rig['calibration']:g} calibration, and {where}. "
                       'A damage or speed rig here solves nothing — it is '
                       f'{tight} that is stopping the fit. ')
            out.append(
                f'{tight} is the binding constraint ({tight_pct * 100:.0f}% used) while '
                f'{slack} has {free:.0f} spare ({slack_pct * 100:.0f}% used) — {aim}'
                f'If modules are being '
                f'dropped to make this hull work, that is a hull question: '
                f'{_sweep_call(fit)} rebuilds this exact loadout on '
                f'every hull in the class and ranks them.')
            named_sweep = True
            # a fitting rig pointed at the resource that is NOT binding
            RIG_FOR = {'powerEngineeringOutputBonus': 'powergrid',
                       'cpuOutputBonus2': 'cpu', 'cpuOutputBonus': 'cpu'}
            misaimed = []
            for mod in fit.modules:
                # `slot` is None for anything not in a rack — guard as the
                # slot counter above does, or int() raises on it.
                if mod.isEmpty or mod.slot is None or int(mod.slot) != int(_FS.RIG):
                    continue
                for a, resource in RIG_FOR.items():
                    if mod.getModifiedItemAttr(a) and resource == slack:
                        misaimed.append(mod.item.typeName)
                        break
            if misaimed:
                names = ', '.join(sorted(set(misaimed)))
                count = f'{len(misaimed)} rig slots' if len(misaimed) > 1 else 'that rig slot'
                out.append(
                    f'{names} adds {slack}, which is not the binding constraint here '
                    f'— {count} does nothing for the fit')

    # The loadout does not physically fit this hull. Downgrading modules until
    # it does is one answer; the other is that the hull is wrong — and that one
    # never gets considered unless something names it, because the fit in hand
    # is already anchored to the hull it was typed on. This generalises the
    # binding-constraint case above: ANY capacity violation (grid, calibration,
    # rack, hardpoints, drone bay) means the modules and the hull were chosen
    # independently, which is the same mistake whichever resource ran out.
    if not named_sweep:
        blocking = [p for p in _problems(fit) if ' over' in p]
        if blocking:
            out.append(
                f'this loadout does not fit the {_ship_name(fit)} as-is '
                f'({blocking[0]}) — before downgrading modules to make it fit, '
                f'check whether the HULL is what is wrong: {_sweep_call(fit)} '
                f'rebuilds this exact loadout on every hull in the class and '
                f'ranks them.')

    # Same capacitor, less cargo volume: more reloads carried per m3.
    for mod in fit.modules:
        if mod.isEmpty or mod.charge is None:
            continue
        loaded = mod.charge
        cap_now = loaded.attributes.get('capacitorBonus')
        vol_now = loaded.attributes.get('volume')
        if cap_now is None or vol_now is None:
            continue
        try:
            candidates = list(mod.getValidCharges())
        except Exception:                    # noqa: BLE001 — advisory only
            continue
        for cand in candidates:
            cap = cand.attributes.get('capacitorBonus')
            vol = cand.attributes.get('volume')
            if cap is None or vol is None or cand.typeName == loaded.typeName:
                continue
            if cap.value == cap_now.value and vol.value < vol_now.value:
                out.append(
                    f'{cand.typeName} carries the same {cap.value:g} GJ in '
                    f'{vol.value:g} m3 instead of {vol_now.value:g} — same cap per '
                    'cycle, more reloads per hold')
                break
    return out


def _problems(fit):
    """Named in-game legality violations. Empty list = legal."""
    from eos.const import FittingHardpoint, FittingSlot
    _recalc(fit)
    out = []
    attr = fit.ship.getModifiedItemAttr
    for used, total, name in (
            (fit.cpuUsed, attr('cpuOutput'), 'cpu'),
            (fit.pgUsed, attr('powerOutput'), 'powergrid'),
            (fit.calibrationUsed, attr('upgradeCapacity') or 0, 'calibration')):
        if used > (total or 0):
            out.append(f'{name} over: {used:g} / {total or 0:g}')
    # Count rack usage by slot VALUE: eos getSlotsUsed compares `mod.slot is
    # type` (enum identity), and EFT-built modules carry plain ints, so it
    # silently counts zero. Found by eval run 3 — a 4-mid fit validated clean.
    from collections import Counter
    used_by_slot = Counter(int(m.slot) for m in fit.modules
                           if not m.isEmpty and m.slot is not None)
    for slot, label, attr_name in RACKS:
        total = attr(attr_name) or 0
        over = used_by_slot.get(int(slot), 0) - total
        if over > 0:
            out.append(f'{label} slots over by {over:g}')
    for hp, label in ((FittingHardpoint.TURRET, 'turret'), (FittingHardpoint.MISSILE, 'launcher')):
        free = fit.getHardpointsFree(hp)
        if free < 0:
            out.append(f'{label} hardpoints over by {-free}')
    # maxGroupFitted: the game caps some module groups at N fitted per ship
    # (Warp Core Stabilizers = 1, etc.); eos doesn't enforce it — gen-7 key
    # derivation found two WCS validating clean
    group_fitted = Counter()
    group_cap = {}
    for mod in fit.modules:
        if mod.isEmpty:
            continue
        cap = mod.getModifiedItemAttr('maxGroupFitted')
        gname = mod.item.group.name
        group_fitted[gname] += 1
        if cap:
            group_cap[gname] = min(cap, group_cap.get(gname, cap))
    for gname, cap in group_cap.items():
        if group_fitted[gname] > cap:
            out.append(f'{group_fitted[gname]:g}x {gname} fitted; '
                       f'game allows {cap:g} (maxGroupFitted)')
    # Rig size must match the hull's rigSize (a Large rig on a battlecruiser
    # validated clean — found during gen-7 key derivation)
    ship_rig_size = attr('rigSize')
    if ship_rig_size:
        for mod in fit.modules:
            if mod.isEmpty or int(mod.slot or 0) != int(FittingSlot.RIG):
                continue
            rs = mod.getModifiedItemAttr('rigSize')
            if rs and rs != ship_rig_size:
                sizes = {1: 'small', 2: 'medium', 3: 'large', 4: 'capital'}
                out.append(f'{mod.item.typeName} is a {sizes.get(int(rs), rs)} rig; '
                           f'{fit.ship.item.typeName} takes {sizes.get(int(ship_rig_size), ship_rig_size)}')
    # Hull restrictions (canFitShipType/Group, fitsToShipType, Standup split)
    # and the capital-size rule — a Bastion Module on a Rifter must not
    # validate clean. eos's own checks, module by module.
    from eos.saveddata.citadel import Citadel
    capital_hull = isinstance(fit.ship, Citadel) or (attr('isCapitalSize') or 0) == 1
    for mod in fit.modules:
        if mod.isEmpty:
            continue
        if not fit.canFit(mod.item):
            out.append(f'{mod.item.typeName} cannot be fitted to {fit.ship.item.typeName}')
        elif not capital_hull and mod.isCapitalSize:
            out.append(f'{mod.item.typeName} is capital-sized; {fit.ship.item.typeName} is not')
    bw = sum(d.getModifiedItemAttr('droneBandwidthUsed') * d.amountActive for d in fit.drones)
    if bw > (attr('droneBandwidth') or 0):
        out.append(f'drone bandwidth over: {bw:g} / {attr("droneBandwidth") or 0:g}')
    vol = sum(d.item.attributes['volume'].value * d.amount for d in fit.drones)
    if vol > (attr('droneCapacity') or 0):
        out.append(f'drone bay over: {vol:g} / {attr("droneCapacity") or 0:g} m3')
    if fit.fighters:
        tubes = attr('fighterTubes') or 0
        if len(fit.fighters) > tubes:
            out.append(f'fighter tubes over: {len(fit.fighters)} / {tubes:g}')
        # class-split tubes: light/support/heavy, ship-side and standup —
        # a Standup fighter on a carrier (or a ship fighter on a structure)
        # lands in a class the hull has zero slots for, so this also gives
        # cross-legality for free
        for flag, slot_attr, label in (
                ('fighterSquadronIsLight', 'fighterLightSlots', 'light'),
                ('fighterSquadronIsSupport', 'fighterSupportSlots', 'support'),
                ('fighterSquadronIsHeavy', 'fighterHeavySlots', 'heavy'),
                ('fighterSquadronIsStandupLight', 'fighterStandupLightSlots', 'standup light'),
                ('fighterSquadronIsStandupSupport', 'fighterStandupSupportSlots', 'standup support'),
                ('fighterSquadronIsStandupHeavy', 'fighterStandupHeavySlots', 'standup heavy')):
            n = sum(1 for f in fit.fighters if f.getModifiedItemAttr(flag))
            have = attr(slot_attr) or 0
            if n > have:
                out.append(f'{label} fighter tubes over: {n} / {have:g}')
        fvol = sum(f.item.attributes['volume'].value * max(f.amount, 0) for f in fit.fighters)
        fbay = attr('fighterCapacity') or 0
        if fvol > fbay:
            out.append(f'fighter bay over: {fvol:g} / {fbay:g} m3')
    return out


def _fitting_breakdown(fit, problems):
    """Per-module cost of whichever resource the fit is over, biggest first.

    Measured 2026-08-20: a graded run chasing a powergrid overrun spent SIX
    consecutive rounds on `module_attrs`, one module at a time, working out
    which module was expensive. The server holds every one of those numbers at
    the instant it declares the overrun — withholding them turns one answer
    into six round trips, and the caller reaches the fix six rounds later with
    no more information than this costs to send.
    """
    ATTR = {'cpu': 'cpu', 'powergrid': 'power'}
    over = [r for r in ATTR if any(p.startswith(r + ' over') for p in problems)]
    if not over:
        return None
    out = {}
    for res in over:
        tally = {}
        for mod in fit.modules:
            if mod.isEmpty:
                continue
            each = mod.getModifiedItemAttr(ATTR[res]) or 0
            if not each:
                continue
            name = mod.item.typeName
            count, _ = tally.get(name, (0, each))
            tally[name] = (count + 1, each)
        rows = sorted(([n, c, round(e, 2), round(c * e, 2)]
                       for n, (c, e) in tally.items()),
                      key=lambda r: -r[3])
        out[res] = {'columns': ['item', 'count', 'each', 'total'], 'rows': rows}
    return out


def _summary(fit_id):
    from collections import Counter
    from eos.const import FittingHardpoint, FittingSlot
    fit = _fit(fit_id)
    _recalc(fit)
    attr = fit.ship.getModifiedItemAttr
    used = Counter(int(m.slot) for m in fit.modules
                   if not m.isEmpty and m.slot is not None)
    slots = {}
    for slot, label, attr_name in RACKS:
        total = int(attr(attr_name) or 0)
        if total or used.get(int(slot)):
            slots[label] = [used.get(int(slot), 0), total]
    hardpoints = {}
    for hpoint, label, attr_name in (
            (FittingHardpoint.TURRET, 'turret', 'turretSlotsLeft'),
            (FittingHardpoint.MISSILE, 'launcher', 'launcherSlotsLeft')):
        total = int(attr(attr_name) or 0)
        hp_used = total - fit.getHardpointsFree(hpoint)
        if total or hp_used:
            hardpoints[label] = [hp_used, total]
    out = {
        'fit_id': fit_id,
        'ship': fit.ship.item.typeName,
        'name': fit.name,
        'cpu': [round(fit.cpuUsed, 2), round(attr('cpuOutput'), 2)],
        'powergrid': [round(fit.pgUsed, 2), round(attr('powerOutput'), 2)],
        'slots': slots,
        'problems': _problems(fit),
    }
    breakdown = _fitting_breakdown(fit, out['problems'])
    if breakdown:
        out['fitting_breakdown'] = breakdown
    if hardpoints:
        out['hardpoints'] = hardpoints
    return out


def _summary_stats(fit_id, stats=True):
    """Summary plus the full stat panel — folding the near-universal
    mutate-then-get_stats pair into one round. Measured over the eval
    corpus, edit_fit->get_stats was the single most common call pair (75
    occurrences) and 90% of requests carried exactly one call, so the
    extra ~290 tokens of panel buys back a whole ~45k context re-read.
    Pass stats=False when the id alone is wanted."""
    out = _summary(fit_id)
    if stats:
        out['stats'] = get_stats(fit_id)
    return out


@mcp.tool()
@_engine_thread
def import_fit(eft: str, stats: bool = True) -> dict:
    """Import an EFT-format fit; returns fit_id + fitting summary + the full stat panel (no separate get_stats round needed). Multi-fit text imports all, panels only on stats=True. stats=False for the id alone."""
    specs = eftlib.parse_eft(eft)
    out = []
    for spec in specs:
        fit_id = _new_id()
        FITS[fit_id] = eftlib.build_fit(spec)
        out.append(_summary_stats(fit_id, stats and len(specs) == 1))
    return out[0] if len(out) == 1 else {'fits': out}


@mcp.tool()
@_engine_thread
def create_fit(ship: str, name: str = 'unnamed', stats: bool = True) -> dict:
    """Create an empty fit for a ship type; returns fit_id + fitting summary."""
    spec = eftlib.FitSpec(ship, name)
    fit_id = _new_id()
    FITS[fit_id] = eftlib.build_fit(spec)
    return _summary_stats(fit_id, stats)


@mcp.tool()
@_engine_thread
def clone_fit(fit_id: str, name: str = '', stats: bool = True) -> dict:
    """Copy an existing fit; returns the new fit_id."""
    fit = _fit(fit_id)
    spec = eftlib.parse_eft(eftlib.render_eft(fit))[0]
    if name:
        spec.name = name
    new_id = _new_id()
    FITS[new_id] = eftlib.build_fit(spec)
    return _summary_stats(new_id, stats)


@mcp.tool()
@_engine_thread
def delete_fit(fit_id: str) -> dict:
    """Discard a fit."""
    _fit(fit_id)
    del FITS[fit_id]
    BOOSTS.pop(fit_id, None)
    PROJECTIONS.pop(fit_id, None)
    ENVS.pop(fit_id, None)
    for ids in BOOSTS.values():
        if fit_id in ids:
            ids.remove(fit_id)
    for key in PROJECTIONS:
        PROJECTIONS[key] = [(p, r) for p, r in PROJECTIONS[key] if p != fit_id]
    return {'deleted': fit_id}


@mcp.tool()
@_engine_thread
def export_fit(fit_id: str) -> str:
    """Export a fit as EFT text (game-client / zkillboard / pyfa interop)."""
    return eftlib.render_eft(_fit(fit_id))


@mcp.tool()
@_engine_thread
def edit_fit(fit_id: str, ops: list, stats: bool = True) -> dict:
    """Apply ops to a fit. Each op: {op:'add'|'remove'|'charge'|'state'|'mode'|'ability', item:name, charge?:name, state?:'offline'|'online'|'active'|'overheated', quantity?:int(drones), keep_slot?:bool (remove: leave an [Empty] gap), ability?:name-substring+enabled?:bool (fighter ability toggle)}. 'charge'/'state' apply to every matching module; 'add' fills the first gap in the rack; 'mode' sets a tactical-destroyer mode item. Returns summary + problems + the full stat panel, so an edit and its numbers are ONE round — do not follow this with get_stats. stats=False to skip the panel."""
    from eos.saveddata.drone import Drone
    from eos.saveddata.module import Module
    fit = _fit(fit_id)
    for op in ops:
        kind = op.get('op')
        item_name = op.get('item', '')
        if kind == 'add':
            item = eftlib._lookup(item_name)
            if item is None:
                raise ValueError(f'unknown item {item_name!r}')
            if item.category.name == 'Drone':
                qty = int(op.get('quantity', 1))
                drone = Drone(item)
                drone.amount = qty
                drone.amountActive = qty
                fit.drones.append(drone)
                drone.owner = fit
            elif item.category.name == 'Fighter':
                from eos.saveddata.fighter import Fighter
                fighter = Fighter(item)
                if op.get('quantity'):
                    fighter.amount = int(op['quantity'])
                fit.fighters.append(fighter)
                fighter.owner = fit
            elif item.category.name == 'Implant':
                if item.group.name == 'Booster':
                    from eos.saveddata.booster import Booster
                    fit.boosters.append(Booster(item))
                else:
                    from eos.saveddata.implant import Implant
                    fit.implants.append(Implant(item))
            else:
                mod = Module(item)
                if op.get('charge'):
                    charge = eftlib._lookup(op['charge'])
                    if charge is None:
                        raise ValueError(f'unknown charge {op["charge"]!r}')
                    if not mod.isValidCharge(charge):
                        raise ValueError(f'{charge.typeName!r} does not fit {item.typeName!r} '
                                         '(wrong size or charge group)')
                    mod.charge = charge
                if mod.isValidState(FittingModuleState.ACTIVE):
                    mod.state = FittingModuleState.ACTIVE
                fit.modules.append(mod)
                mod.owner = fit
        elif kind == 'remove':
            for kept in (fit.fighters, fit.drones, fit.implants, fit.boosters):
                hit = next((x for x in kept if x.item.typeName == item_name), None)
                if hit is not None:
                    kept.remove(hit)
                    break
            else:
                for mod in list(fit.modules):
                    if not mod.isEmpty and mod.item.typeName == item_name:
                        if op.get('keep_slot'):
                            slot_idx = fit.modules.index(mod)
                            fit.modules.free(slot_idx)
                            # eos's dummy has no owner; calc paths read
                            # module.owner.factorReload even on empties
                            fit.modules[slot_idx].owner = fit
                        else:
                            fit.modules.remove(mod)
                        break
                else:
                    raise ValueError(f'{item_name!r} not fitted')
        elif kind == 'charge':
            charge = eftlib._lookup(op.get('charge', ''))
            if charge is None:
                raise ValueError(f'unknown charge {op.get("charge")!r}')
            hits = 0
            for mod in fit.modules:
                if not mod.isEmpty and mod.item.typeName == item_name:
                    if not mod.isValidCharge(charge):
                        raise ValueError(f'{charge.typeName!r} does not fit {item_name!r} '
                                         '(wrong size or charge group)')
                    mod.charge = charge
                    hits += 1
            if not hits:
                raise ValueError(f'{item_name!r} not fitted')
        elif kind == 'mode':
            from eos.saveddata.mode import Mode
            item = eftlib._lookup(item_name)
            if item is None or item.group.name != 'Ship Modifiers':
                raise ValueError(f'unknown mode {item_name!r}; want e.g. "Confessor Defense Mode"')
            fit.mode = Mode(item)
        elif kind == 'ability':
            want = op.get('ability', '').lower()
            enabled = bool(op.get('enabled', True))
            hits = 0
            names = set()
            for fighter in fit.fighters:
                if fighter.item.typeName != item_name:
                    continue
                for ability in fighter.abilities:
                    names.add(ability.name)
                    if want and want in ability.name.lower():
                        ability.active = enabled
                        hits += 1
            if not hits:
                if names:
                    raise ValueError(f'no ability matching {want!r} on {item_name!r}; '
                                     f'has: {sorted(names)}')
                raise ValueError(f'{item_name!r} not fitted as a fighter')
        elif kind == 'state':
            state = STATES.get(op.get('state', ''))
            if state is None:
                raise ValueError(f'bad state {op.get("state")!r}; use {sorted(STATES)}')
            hits = 0
            for mod in fit.modules:
                if not mod.isEmpty and mod.item.typeName == item_name:
                    mod.state = state if mod.isValidState(state) else mod.state
                    hits += 1
            if not hits:
                raise ValueError(f'{item_name!r} not fitted')
        else:
            raise ValueError(f'bad op {kind!r}; use add/remove/charge/state/mode/ability')
    return _summary_stats(fit_id, stats)


_alpha_char = None
_all0_char = None


@mcp.tool()
@_engine_thread
def set_skills(fit_id: str, preset: str) -> dict:
    """Set the pilot skills: 'all-0' | 'alpha' | 'all-5'. Default on import/create is all-5 (omega assumption)."""
    # The alpha pilot must be its own Character: getAll5() returns a shared
    # saveddata object, and flipping alphaCloneID on it silently turns every
    # fit alpha (found by the eval harness, 2026-08-17).
    from eos.saveddata.character import Character
    global _alpha_char, _all0_char
    fit = _fit(fit_id)
    if preset == 'all-5':
        char = Character.getAll5()
        char.alphaCloneID = None
        fit.character = char
    elif preset == 'all-0':
        if _all0_char is None:
            _all0_char = Character('MCP All 0')   # in-memory; getAll0() would hit saveddata
        fit.character = _all0_char
    elif preset == 'alpha':
        if _alpha_char is None:
            _alpha_char = Character('MCP Alpha', 5)   # in-memory only, never saved
            _alpha_char.alphaCloneID = 1
        fit.character = _alpha_char
    else:
        raise ValueError("preset must be 'all-0', 'alpha' or 'all-5'")
    return {'fit_id': fit_id, 'skills': preset}


def _env_candidates(text):
    rows = sqlite3.connect(os.path.join(ARGS.pyfa, 'eve.db')).execute(
        """SELECT t.typeName FROM invtypes t JOIN invgroups g ON g.groupID=t.groupID
           WHERE g.name IN (?,?,?,?) AND t.typeName LIKE '%'||?||'%' AND t.published=1
           ORDER BY t.typeName LIMIT 8""", (*ENV_GROUPS, text)).fetchall()
    return [r[0] for r in rows]


@mcp.tool()
@_engine_thread
def set_env(fit_id: str, effect: str = '') -> dict:
    """Set the system-wide environment on a fit ('Class 5 Wolf Rayet Effects', 'Strong Metaliminal Dark Storm Environment', ...); '' clears. Affects this fit only — set the same env on any fit you compare it to."""
    from eos.saveddata.module import Module
    fit = _fit(fit_id)
    prev = ENVS.pop(fit_id, None)
    if prev is not None and prev in fit.projectedModules:
        fit.projectedModules.remove(prev)
    if not effect:
        return {'fit_id': fit_id, 'env': None}
    item = eftlib._lookup(effect)
    if item is None or item.group.name not in ENV_GROUPS:
        raise ValueError(f'unknown environment {effect!r}; candidates: {_env_candidates(effect)}')
    mod = Module(item)
    fit.projectedModules.append(mod)
    if mod not in fit.projectedModules:
        raise ValueError(f'{effect!r} is not projectable')
    mod.owner = fit
    ENVS[fit_id] = mod
    return {'fit_id': fit_id, 'env': item.typeName}


@mcp.tool()
@_engine_thread
def set_projected(fit_id: str, projector_fit_ids: list) -> dict:
    """Project other fits' active modules/drones onto this fit ([] clears): remote reps, ewar, neuts. Entries: 'f2' (zero range = full strength) or {fit_id:'f2', range_km:20} — strength then follows each module's optimal+falloff (most ewar is zero past optimal+3x falloff). The projector's own skills/hull scale everything."""
    _fit(fit_id)
    entries = []
    for p in projector_fit_ids:
        pid = p.get('fit_id') if isinstance(p, dict) else p
        rng_km = p.get('range_km') if isinstance(p, dict) else None
        if pid == fit_id:
            raise ValueError('a fit cannot project onto itself here; fit a second copy')
        _fit(pid)
        entries.append((pid, None if rng_km is None else float(rng_km) * 1000))
    PROJECTIONS[fit_id] = entries
    return {'fit_id': fit_id, 'projected_by': [
        {'fit_id': pid, **({} if rng is None else {'range_km': rng / 1000})}
        for pid, rng in entries]}


def _pilot_candidates(kind, slot, search):
    """(typeName, slot) for implants or combat boosters, from the engine's db.

    Raw SQL over the engine connection for the same reason `_suggest` uses it:
    importing eos.gamedata lazily re-enters a partially initialised module.
    """
    import eos.db
    if kind == 'boosters':
        where, params = ("g.name = 'Booster' AND a.attributeName = 'boosterness' "
                         'AND ta.value <= 3'), []
    elif kind == 'implants':
        # ALL slots, 1-10. Slots 1-5 were excluded on the reasoning that they
        # hold attribute implants which cannot touch a fit -- wrong, and caught
        # while grading on 2026-08-26: the pirate SETS live there. 15 of the 18
        # published Snake implants are slots 1-5, so a caller asking about
        # Snakes got "0 moved a number" -- a false negative from the one tool
        # built to stop implants being named from memory. Pure attribute
        # implants move nothing and measurement drops them anyway, which is the
        # point of measuring rather than classifying by slot.
        where = ("c.name = 'Implant' AND g.name != 'Booster' "
                 "AND a.attributeName = 'implantness' AND ta.value BETWEEN 1 AND 10")
        params = []
        if slot is not None:
            where += ' AND ta.value = ?'
            params.append(float(slot))
    else:
        raise ValueError("kind must be 'boosters' or 'implants'")
    if search:
        where += ' AND t.typeName LIKE ?'
        params.append(f'%{search}%')
    conn = eos.db.gamedata_engine.raw_connection()
    try:
        rows = conn.cursor().execute(
            'SELECT t.typeName, ta.value FROM invtypes t '
            'JOIN invgroups g ON g.groupID = t.groupID '
            'JOIN invcategories c ON c.categoryID = g.categoryID '
            'JOIN dgmtypeattribs ta ON ta.typeID = t.typeID '
            'JOIN dgmattribs a ON a.attributeID = ta.attributeID '
            f'WHERE t.published = 1 AND {where} ORDER BY t.typeName', params).fetchall()
    finally:
        conn.close()
    return [(n, int(v)) for n, v in rows]


def _headline(fit):
    """The few numbers a pilot effect could plausibly move."""
    panel = stat_panel(fit, recalc=lambda f, factor_reload: _recalc(f, factor_reload))
    cap = panel['capacitor']
    return {
        'dps': panel['offense']['dps'],
        'ehp': panel['defense']['ehp']['total'],
        'reps_hps': round(sum(panel['defense'].get('reps_hps', {}).values()), 1),
        'speed_ms': panel['navigation']['max_velocity_ms'],
        'align_s': panel['navigation'].get('align_time_prop_off_s',
                                           panel['navigation']['align_time_s']),
        # Warp speed is the entire point of the Ascendancy set, and without it
        # here the tool reported "0 moved a number" for all twelve of them —
        # the same false negative as the slot filter, one metric further down.
        # Align and warp speed are different numbers; a transcript conflated
        # them and the tool could not contradict it.
        'warp_speed_aus': panel['navigation']['warp_speed_aus'],
        # Two keys, not one: a cap-stable fit has no `lasts_s` to improve, so a
        # single field would report "no change" for a booster that took the fit
        # from 60% to 90% stable. Both are 0 when they do not apply, so both
        # diff cleanly.
        'cap_lasts_s': 0 if cap.get('stable') else cap.get('lasts_s', 0),
        'cap_stable_pct': cap.get('stable_pct', 0) if cap.get('stable') else 0,
    }


def _side_effects(item):
    """Penalty attributes a combat booster MAY roll. Not applied, not certain."""
    return sorted(k[7:] for k in item.attributes
                  if k.startswith('booster') and k.endswith('Penalty'))


@mcp.tool()
@_engine_thread
def pilot_effects(fit_id: str, kind: str = 'boosters', slot: int = None,
                  search: str = None, limit: int = 12) -> dict:
    """Which implants or combat boosters actually do anything FOR THIS FIT, measured by fitting each one and re-running the panel. kind: 'boosters' (combat drugs, slots 1-3) | 'implants' (hardwirings, slots 6-10; narrow with `slot` or `search` to cut the run short). Anything that moves no number is dropped, so a missile hardwiring never gets recommended to a turret boat. Ranked by the largest relative gain; `deltas` are against the fit as it stands. Booster side effects roll per dose, are NOT in these numbers, and are listed per row."""
    fit = _fit(fit_id)
    base = _headline(fit)
    cands = _pilot_candidates(kind, slot, search)
    bucket = fit.boosters if kind == 'boosters' else fit.implants
    if kind == 'boosters':
        from eos.saveddata.booster import Booster as Wrap
    else:
        from eos.saveddata.implant import Implant as Wrap
    rows, failed = [], 0
    for name, islot in cands:
        item = eftlib._lookup(name)
        if item is None:
            continue
        try:
            obj = Wrap(item)
            bucket.append(obj)
            after = _headline(fit)
        except Exception:                  # noqa: BLE001 — a candidate that will
            failed += 1                    # not construct is simply not offered
            after = base
        finally:
            if obj in bucket:
                bucket.remove(obj)
        delta = {k: round(after[k] - base[k], 2) for k in base
                 if abs(after[k] - base[k]) > 0.005}
        if not delta:
            continue                       # does nothing here: never offer it
        gain = max(abs(after[k] - base[k]) / abs(base[k] or 1) for k in delta)
        row = {'item': name, 'slot': islot, 'deltas': delta,
               'best_relative_gain_pct': round(gain * 100, 1)}
        if kind == 'boosters':
            se = _side_effects(item)
            if se:
                row['may_roll_side_effects'] = se
        rows.append(row)
    _recalc(fit)
    rows.sort(key=lambda r: -r['best_relative_gain_pct'])
    shown = rows[:limit]
    hidden = len(rows) - len(shown)
    return {'fit_id': fit_id, 'ship': _ship_name(fit), 'kind': kind,
            'baseline': base, 'considered': len(cands),
            'moved_a_number': len(rows), 'results': shown,
            # A truncated list read as an exhaustive one on 2026-08-26: 183
            # implants moved a number, 12 were shown, and the answer reported
            # "the only things that moved a number were warp-speed and
            # capacitor implants" -- while its own payload listed a pirate-set
            # implant it then said did nothing. Say the quiet part in the data.
            **({'not_listed': hidden,
                'results_are_not_exhaustive': (
                    f'{hidden} more candidates moved a number and are not in '
                    '`results`. Raise `limit`, or narrow with `slot`/`search`, '
                    'before saying what does or does not affect this fit.')}
               if hidden else {}),
            'note': 'measured by fitting each candidate to THIS fit and re-running '
                    'the panel — nothing here is inferred from a name. Candidates '
                    'that changed nothing are not listed at all. '
                    + ('Side effects are possible per dose, not guaranteed, and are '
                       'excluded from `deltas`.' if kind == 'boosters'
                       else 'All ten slots are considered: the pirate sets '
                            '(Snake, Crystal, Ascendancy) sit in slots 1-5 beside the '
                            'attribute implants, and only measurement tells them '
                            'apart. Narrow with `slot` or `search` to cut the run '
                            'short.')}


@mcp.tool()
@_engine_thread
def set_booster(fit_id: str, booster_fit_ids: list) -> dict:
    """Attach command-burst booster fits by fit_id ([] clears). The booster fit's own hull/skills/mindlink scale its bursts; strongest same buff wins, bursts never stack."""
    _fit(fit_id)
    for b in booster_fit_ids:
        if b == fit_id:
            raise ValueError('a fit cannot boost itself')
        _fit(b)
    BOOSTS[fit_id] = list(booster_fit_ids)
    return {'fit_id': fit_id, 'boosters': list(booster_fit_ids)}


@mcp.tool()
@_engine_thread
def graph(fit_id: str, kind: str, target: dict = None, distance_km: float = 5.0,
          item: str = None) -> dict:
    """Bounded curve: <=30 points + summary + named assumptions. kind: 'dps_vs_range' | 'dps_vs_target_speed' | 'cap_vs_time' | 'dps_vs_time' (spool ramp) | 'ewar_vs_range' (needs item: a projected module on THIS fit). target for dps kinds: {speed_ms, sig_m, atk_speed_ms}; distance_km applies to dps_vs_target_speed."""
    fit = _fit(fit_id)
    _recalc(fit)
    t = target or {}
    if kind == 'dps_vs_range':
        out = graphlib.dps_vs_range(fit, tgt_speed=t.get('speed_ms', 0.0),
                                    tgt_sig=t.get('sig_m'), atk_speed=t.get('atk_speed_ms', 0.0))
    elif kind == 'dps_vs_target_speed':
        out = graphlib.dps_vs_target_speed(fit, distance_km=distance_km, tgt_sig=t.get('sig_m'))
    elif kind == 'cap_vs_time':
        out = graphlib.cap_vs_time(fit)
    elif kind == 'dps_vs_time':
        out = graphlib.dps_vs_time(fit)
    elif kind == 'ewar_vs_range':
        if not item:
            raise ValueError("ewar_vs_range needs item: the projected module's name")
        out = graphlib.ewar_vs_range(fit, item)
    else:
        raise ValueError("kind must be 'dps_vs_range', 'dps_vs_target_speed', "
                         "'cap_vs_time', 'dps_vs_time' or 'ewar_vs_range'")
    return {'ship': _ship_name(fit), **out}


# Small pulse lasers alone offer 54 valid crystals. A cap below that silently
# drops the answer: Scorch S sits at index 10 and is the best charge past
# optimal, so a truncated sweep can read as "the loaded one is fine".
CHARGE_CANDIDATE_CAP = 80


def _charge_options(fit, totals, baseline):
    """Rank every charge the weapons can load, at the range just asked about.

    Measured 2026-08-20: an answer shipped Multifrequency S and tested it
    against one target at one range. Across the brief it was written for,
    Scorch S beat it at EVERY range — 266 vs 261 applied at 5 km, 271 vs 34 at
    9 km — while showing 24 LESS paper dps. Reading `dps` picks Multifrequency;
    only `dps_applied`, swept, finds Scorch. Lasers change crystals with no
    reload at all, so this is a free choice made wrongly by default.

    Weapons are swept in groups of identical modules: sweeping each gun
    independently would be combinatorial and nobody flies mixed crystals.
    Charges are restored before returning, whatever happens.
    """
    groups = {}
    for mod in fit.modules:
        if mod.isEmpty or mod.charge is None:
            continue
        groups.setdefault(mod.item.typeName, []).append(mod)
    out = {}
    for weapon, mods in groups.items():
        loaded = mods[0].charge
        try:
            cands = list(mods[0].getValidCharges())
        except Exception:                  # noqa: BLE001 — advisory only
            continue
        seen, ranked = set(), []
        skipped = max(0, len(cands) - CHARGE_CANDIDATE_CAP)
        for cand in cands[:CHARGE_CANDIDATE_CAP]:
            if cand.typeName in seen:
                continue
            seen.add(cand.typeName)
            try:
                for mod in mods:
                    mod.charge = cand
                _recalc(fit)
                _, applied, _ = totals()
            except Exception:              # noqa: BLE001 — a charge that breaks
                continue                   # the calc is simply not a candidate
            ranked.append({'charge': cand.typeName, 'dps_applied': round(applied, 1),
                           **({'loaded': True} if cand.typeName == loaded.typeName else {})})
        for mod in mods:
            mod.charge = loaded
        _recalc(fit)
        if len(ranked) < 2:
            continue
        ranked.sort(key=lambda r: -r['dps_applied'])
        best = ranked[0]
        # The loaded charge always shows, whatever it ranks: the caller needs to
        # see where their current choice sits, and "not in the top 8" is the
        # case where that matters most.
        top = ranked[:8]
        if not any(r.get('loaded') for r in top):
            here = next((r for r in ranked if r.get('loaded')), None)
            if here is not None:
                top = top[:7] + [{**here, 'rank': ranked.index(here) + 1}]
        entry = {'ranked': top, 'evaluated': len(ranked)}
        if skipped:
            entry['not_evaluated'] = skipped
        if not best.get('loaded'):
            now = next((r['dps_applied'] for r in ranked if r.get('loaded')), baseline)
            gain = best['dps_applied'] - now
            if gain > 0.5:
                entry['better_than_loaded'] = (
                    f"{best['charge']} applies {best['dps_applied']} here against "
                    f"{now} for the loaded {loaded.typeName} (+{gain:.1f}). Check the "
                    'other ranges you expect to fight at before switching — this is '
                    'one distance, and the ranking moves with it.')
        out[weapon] = entry
    return out or None


@mcp.tool()
@_engine_thread
def applied_dps(fit_id: str, distance_km: float, target: dict,
                charges: bool = True) -> dict:
    """Applied (not paper) dps vs a real target: target {sig_m required, speed_ms?, atk_speed_ms?}. pyfa's full application model — turret tracking/sig, missile explosion radius+velocity, drone mobility — at full spool; raw vs applied split per source class. Also ranks EVERY charge the weapons can load at this range (`charges`, set false to skip): the loaded one is rarely the best, and the best one usually has LOWER paper dps — measured, Scorch S beats Multifrequency S at every range including point blank while showing 24 less raw."""
    from eos.saveddata.drone import Drone
    from eos.saveddata.fighter import Fighter
    from eos.saveddata.module import Module
    from eos.const import FittingHardpoint
    fit = _fit(fit_id)
    _recalc(fit)
    if not target or target.get('sig_m') is None:
        raise ValueError("target needs sig_m (and usually speed_ms) — "
                         "pull the hull's base values from layer 1")
    dist = float(distance_km) * 1000
    tgt_speed = float(target.get('speed_ms', 0))
    dmg = graphlib._dmg_map(fit)
    amap = graphlib._application_map(fit, dist, tgt_speed, float(target['sig_m']),
                                     float(target.get('atk_speed_ms', 0)))

    def bucket(key):
        if isinstance(key, tuple):  # fighters key as (fighter, effectID)
            key = key[0]
        if isinstance(key, Drone):
            return 'drones'
        if isinstance(key, Fighter):
            return 'fighters'
        if isinstance(key, Module) and key.hardpoint == FittingHardpoint.MISSILE:
            return 'missiles'
        if isinstance(key, Module) and key.hardpoint == FittingHardpoint.TURRET:
            return 'turrets'
        return 'other'

    def totals():
        """(raw, applied, per-bucket) for the fit exactly as it stands."""
        local_dmg = graphlib._dmg_map(fit)
        local_amap = graphlib._application_map(
            fit, dist, tgt_speed, float(target['sig_m']),
            float(target.get('atk_speed_ms', 0)))
        raw_t = app_t = 0.0
        buckets = {}
        for key, d in local_dmg.items():
            raw = d.total
            applied = (d * local_amap.get(key, 0)).total
            b = buckets.setdefault(bucket(key), [0.0, 0.0])
            b[0] += raw
            b[1] += applied
            raw_t += raw
            app_t += applied
        return raw_t, app_t, buckets

    raw_total, applied_total, by = totals()
    out = {'fit_id': fit_id, 'ship': _ship_name(fit), 'distance_km': distance_km,
           'target': {'sig_m': target['sig_m'], 'speed_ms': tgt_speed},
           'dps_raw': round(raw_total, 1), 'dps_applied': round(applied_total, 1),
           'application_pct': round(100 * applied_total / raw_total, 1) if raw_total else 0,
           'by_source': {k: [round(r, 1), round(a, 1)] for k, (r, a) in sorted(by.items())}}
    if charges:
        alt = _charge_options(fit, totals, applied_total)
        if alt:
            out['charges'] = alt
    return out


@mcp.tool()
@_engine_thread
def get_stats(fit_id: str, profile: dict = None, spool: float = None) -> dict:
    """Full stat panel. profile: optional damage weights {em,thermal,kinetic,explosive}, default uniform. spool: 0..1 for spool-up weapons (default 1 = full spool; floor and ramp time ride in offense.spool). All ship values include skills/modules; resists as fractions."""
    from eos.saveddata.damagePattern import DamagePattern
    fit = _fit(fit_id)
    p = profile or {}
    fit.damagePattern = DamagePattern(
        emAmount=p.get('em', 25), thermalAmount=p.get('thermal', 25),
        kineticAmount=p.get('kinetic', 25), explosiveAmount=p.get('explosive', 25))
    panel = stat_panel(fit, recalc=lambda f, factor_reload: _recalc(f, factor_reload),
                       spool=spool)
    panel = {'ship': _ship_name(fit), **panel}
    panel['problems'] = _problems(fit)
    breakdown = _fitting_breakdown(fit, panel['problems'])
    if breakdown:
        panel['fitting_breakdown'] = breakdown
    advisories = _advisories(fit)
    if advisories:
        panel['advisories'] = advisories
    # A silent zero-spool number once cost a graded eval miss: name the level.
    spool_info = panel['offense'].get('spool')
    if spool_info:
        panel.setdefault('notes', []).append(
            f"spool-up weapons: dps/volley at {round(spool_info['level'] * 100)}% spool "
            f"(floor {spool_info['dps_zero_spool']}, full ramp {spool_info['time_to_full_s']} s)")
    # Siege-class states (bastion/siege/triage share dogma group 'Siege
    # Module'): the numbers assume the state is running; name what it costs.
    siege = sorted({m.item.typeName for m in fit.modules
                    if not m.isEmpty and m.item.group.name == 'Siege Module'
                    and m.state >= FittingModuleState.ACTIVE})
    if siege:
        panel.setdefault('notes', []).append(
            f'{", ".join(siege)} active: stats assume the state is running; '
            'ship is immobile and remote assistance is impeded for the duration')
    return panel


@mcp.tool()
@_engine_thread
def module_attrs(fit_id: str, item: str, attrs: list = None) -> dict:
    """Modified per-module attribute values (skills/ship bonuses/heat/mutations applied) for every fitted module or drone named `item`. attrs: dogma attribute names, e.g. ['maxRange','speedFactor']; null = not on that module. Overheat first via edit_fit state op to read heated values."""
    from eos.db.gamedata.queries import getAttributeInfo
    # Omitting `attrs` used to be a validation error and a wasted round. Answer
    # with the attributes that usually decide a module instead; anything absent
    # on this module simply comes back null.
    attrs = attrs or ['cpu', 'power', 'capacitorNeed', 'duration', 'maxRange',
                      'falloff', 'trackingSpeed', 'damageMultiplier',
                      'speedFactor', 'capacityBonus', 'armorDamageAmount',
                      'shieldBonus', 'massAddition']
    for name in attrs:
        if getAttributeInfo(name) is None:
            raise ValueError(f'unknown attribute {name!r} (dogma names, e.g. maxRange)')
    fit = _fit(fit_id)
    _recalc(fit)
    state_names = {v: k for k, v in STATES.items()}
    out = []
    for mod in fit.modules:
        if not mod.isEmpty and mod.item.typeName == item:
            vals = {n: (round(v, 4) if isinstance(v, float) else v)
                    for n in attrs for v in [mod.getModifiedItemAttr(n)]}
            out.append({'item': mod.item.typeName,
                        'state': state_names.get(mod.state, str(mod.state)),
                        'attrs': vals})
    for drone in fit.drones:
        if drone.item.typeName == item:
            vals = {n: (round(v, 4) if isinstance(v, float) else v)
                    for n in attrs for v in [drone.getModifiedItemAttr(n)]}
            out.append({'item': drone.item.typeName, 'amount': drone.amount,
                        'attrs': vals})
    for fighter in fit.fighters:
        if fighter.item.typeName == item:
            vals = {n: (round(v, 4) if isinstance(v, float) else v)
                    for n in attrs for v in [fighter.getModifiedItemAttr(n)]}
            out.append({'item': fighter.item.typeName, 'amount': fighter.amount,
                        'abilities': [{'name': a.name, 'active': bool(a.active)}
                                      for a in fighter.abilities],
                        'attrs': vals})
    if not out:
        raise ValueError(f'{item!r} not fitted')
    return {'fit_id': fit_id, 'ship': _ship_name(fit), 'modules': out}


@mcp.tool()
@_engine_thread
def sweep(fit_id: str, item: str, candidates: list, metrics: list = None) -> dict:
    """Try each candidate module in place of fitted module `item` (every copy swapped; charge/state carried when valid) and return one compact row per candidate with the named panel metrics (dotted paths, e.g. 'offense.dps', 'defense.ehp.total'; default dps/ehp/speed) plus cpu_free/pg_free/problems. The fit is restored afterwards. Max 20 candidates."""
    from eos.saveddata.module import Module
    if len(candidates) > 20:
        raise ValueError(f'{len(candidates)} candidates; cap is 20 per sweep')
    metrics = metrics or ['offense.dps', 'defense.ehp.total',
                          'navigation.max_velocity_ms']
    fit = _fit(fit_id)
    idxs = [i for i, m in enumerate(fit.modules)
            if not m.isEmpty and m.item.typeName == item]
    if not idxs:
        raise ValueError(f'{item!r} not fitted')
    originals = [fit.modules[i] for i in idxs]

    def pick(panel, path):
        node = panel
        for part in path.split('.'):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def row(label):
        panel = stat_panel(fit, recalc=lambda f, factor_reload: _recalc(f, factor_reload))
        attr = fit.ship.getModifiedItemAttr
        r = {'candidate': label}
        r.update({p: pick(panel, p) for p in metrics})
        r['cpu_free'] = round((attr('cpuOutput') or 0) - fit.cpuUsed, 2)
        r['pg_free'] = round((attr('powerOutput') or 0) - fit.pgUsed, 2)
        r['problems'] = len(_problems(fit))
        return r

    rows = [row(f'{item} (fitted)')]
    try:
        for name in candidates:
            cand_item = eftlib._lookup(name)
            if cand_item is None:
                rows.append({'candidate': name, 'error': 'unknown item'})
                continue
            # construct all candidate copies BEFORE touching the fit, so a
            # bad candidate (a drone name, say) leaves the fit untouched
            try:
                cands = []
                for orig in originals:
                    mod = Module(cand_item)
                    if orig.charge is not None and mod.isValidCharge(orig.charge):
                        mod.charge = orig.charge
                    if mod.isValidState(orig.state):
                        mod.state = orig.state
                    elif mod.isValidState(FittingModuleState.ACTIVE):
                        mod.state = FittingModuleState.ACTIVE
                    cands.append(mod)
            except ValueError as e:
                rows.append({'candidate': name, 'error': str(e)})
                continue
            # replace in position — never remove/append, which would disturb
            # rack layout ([Empty ...] gaps) on layout-conscious fits
            for i, mod in zip(idxs, cands):
                fit.modules.replace(i, mod)
                mod.owner = fit
            rows.append(row(name))
            for i, orig in zip(idxs, originals):
                fit.modules.replace(i, orig)
    finally:
        for i, orig in zip(idxs, originals):
            if fit.modules[i] is not orig:
                fit.modules.replace(i, orig)
        _recalc(fit)
    return {'fit_id': fit_id, 'ship': _ship_name(fit),
            'swapped_count': len(originals), 'rows': rows}


def _group_by_name(name):
    """Find a ship group by name without assuming a member is known.

    eos.db.getGroup takes the name directly; the Group class is a classical
    mapping with no __table__, so a hand-built query does not work here.
    """
    import eos.db
    try:
        return eos.db.getGroup(name)
    except Exception:                     # noqa: BLE001 — unknown name is a miss
        return None


def _availability_note(item):
    """Flag hulls that are not normally obtainable.

    Market-group ancestry is a hard SDE fact; tournament provenance is not in
    the data at all. metaGroup looked like a discriminator and is not — it
    false-positives on Imperial Issue battleships and event corvettes, and
    false-negatives on Hydra, Tiamat, Chameleon and Whiptail, which are all
    tournament prizes sitting at Tech II. So flag the branch and let the
    reader judge, rather than guessing a provenance the data cannot support.
    """
    node = getattr(item, 'marketGroup', None)
    while node is not None:
        if node.name == 'Special Edition Ships':
            return ('special edition hull — not a normally obtainable ship; some of '
                    'this branch are tournament prizes worth hundreds of billions, '
                    'others (Praxis, Gnosis, Sunesis) are cheap and common. Check '
                    'availability and price before recommending it.')
        node = getattr(node, 'parent', None)
    return None



def _trait_text(item, cap=170):
    """Hull bonuses as plain text, compressed.

    Bonuses are already IN every number this server returns — the engine
    applies them — but the ranking alone hides WHY a hull placed where it did.
    A hull whose damage bonus does not touch the weapons in the fit looks
    simply bad, when it is really "bad with these guns". Naming the bonus is
    what stops that reading.
    """
    traits = getattr(item, 'traits', None)
    raw = getattr(traits, 'traitText', None) if traits is not None else None
    if not raw:
        return None
    txt = re.sub(r'<[^>]+>', ' ', raw)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return txt[:cap] + ('…' if len(txt) > cap else '')


ADAPT_CANDIDATE_CAP = 8
TURRET_GROUPS = ('Energy Weapon', 'Hybrid Weapon', 'Projectile Weapon')
_WEAPONS = None


def _weapon_catalogue():
    """Every published weapon, tagged with the phrase a hull's traits would use.

    Turret size lives in the required skill ("Small Energy Turret") and appears
    verbatim in trait text. Launchers all require "Missile Launcher Operation",
    so their size lives in the group instead ("Missile Launcher Light" ->
    "Light Missile"). Between the two, a hull's own traits name both the weapon
    system and its size, which is all the adaptation needs.
    """
    global _WEAPONS
    if _WEAPONS is not None:
        return _WEAPONS
    import eos.db
    out = []
    for gname in TURRET_GROUPS:
        grp = eos.db.getGroup(gname)
        for item in (grp.items if grp else ()):
            if not getattr(item, 'published', True):
                continue
            skill = item.attributes.get('requiredSkill1')
            if skill is None:
                continue
            sk = eos.db.getItem(int(skill.value))
            if sk is None or 'Turret' not in sk.typeName:
                continue
            out.append({'name': item.typeName, 'phrase': sk.typeName,
                        'hardpoint': 'turret', 'meta': item.metaLevel or 0})
    conn = eos.db.gamedata_engine.raw_connection()
    try:
        rows = conn.cursor().execute(
            "SELECT DISTINCT g.name FROM invgroups g WHERE g.name LIKE 'Missile Launcher %'"
        ).fetchall()
    finally:
        conn.close()
    for (gname,) in rows:
        grp = eos.db.getGroup(gname)
        kind = gname[len('Missile Launcher '):]
        for item in (grp.items if grp else ()):
            if not getattr(item, 'published', True):
                continue
            out.append({'name': item.typeName, 'phrase': f'{kind} Missile',
                        'alt_phrase': f'{kind} launcher', 'hardpoint': 'launcher',
                        'meta': item.metaLevel or 0})
    _WEAPONS = out
    return out


def _hull_weapons(hull_item):
    """Weapon names this hull is actually bonused to fly, best tech first."""
    traits = getattr(hull_item, 'traits', None)
    raw = getattr(traits, 'traitText', None) if traits is not None else None
    if not raw:
        return []
    txt = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', raw)).lower()
    hits = [w for w in _weapon_catalogue()
            if w['phrase'].lower() in txt or w.get('alt_phrase', '~').lower() in txt]
    hits.sort(key=lambda w: -w['meta'])
    return hits


def _best_charge(mod):
    """The valid charge with the most raw damage, or None.

    A default, not an answer: which charge is actually best depends on the
    range being fought at, which a hull sweep does not know. `applied_dps`
    ranks them properly once a hull has been chosen.
    """
    try:
        cands = list(mod.getValidCharges())
    except Exception:                      # noqa: BLE001
        return None
    def dmg(item):
        return sum(item.attributes[a].value for a in
                   ('emDamage', 'thermalDamage', 'kineticDamage', 'explosiveDamage')
                   if a in item.attributes)
    cands = [c for c in cands if dmg(c)]
    return max(cands, key=dmg) if cands else None


def _adapt_to_hull(body, hull_item, src_weapons, src_meta=5):
    """Rewrite the EFT body so the guns are the ones this hull is bonused for.

    Without this a sweep only ever answers "which hull carries THIS loadout",
    which is a different and much less useful question than "which hull is best
    for this job" — and it is biased to the hull the fit was built on by
    construction. A laser fit swept across tactical destroyers scored the
    Jackdaw at "turret hardpoints over by 4" and every non-Amarr hull at 55% of
    the Confessor's dps, which says nothing about the hulls and everything
    about the guns being Amarr.
    """
    picks = _hull_weapons(hull_item)
    if not picks or not src_weapons:
        return []
    attr = hull_item.attributes
    slots = {'turret': int(attr['turretSlotsLeft'].value) if 'turretSlotsLeft' in attr else 0,
             'launcher': int(attr['launcherSlotsLeft'].value) if 'launcherSlotsLeft' in attr else 0}
    picks = [p for p in picks if slots.get(p['hardpoint'], 0) > 0]
    if not picks:
        return []
    # Already flying this hull's weapon system: leave the loadout exactly alone
    # so the source hull stays an honest baseline in its own sweep. Matching on
    # the trait PHRASE, not the module name — a Confessor carrying any small
    # energy turret is already armed the way the hull wants.
    src_phrases = {w['phrase'] for w in _weapon_catalogue() if w['name'] in src_weapons}
    if any(p['phrase'] in src_phrases for p in picks):
        return []
    # Match the tier the fit already flies. Sorting by meta alone reaches for
    # officer modules, which are not what "the same fit on another hull" means
    # and are exactly what a caller excluding officer/abyssal does not want.
    # Same tier as the fit already flies, then EVERY rung at that tier: which
    # rung wins is the same question the size ladder exists for, and picking
    # whichever sorted first put a 125mm Gatling on a Svipul.
    picks.sort(key=lambda w: (abs(w['meta'] - src_meta), -w['meta']))
    tier = abs(picks[0]['meta'] - src_meta)
    picks = [p for p in picks if abs(p['meta'] - src_meta) == tier][:ADAPT_CANDIDATE_CAP]
    from eos.saveddata.module import Module
    kept = [ln for ln in body.split('\n')
            if ln.strip().split(',')[0].strip() not in src_weapons]
    out = []
    for cand in picks:
        count = slots[cand['hardpoint']]
        charge = _best_charge(Module(eftlib._lookup(cand['name'])))
        line = cand['name'] + (f', {charge.typeName}' if charge is not None else '')
        out.append(('\n'.join([line] * count + kept), {
            'weapons': f"{count}x {cand['name']}",
            'replaced': f"{len(src_weapons)} {'/'.join(sorted(src_weapons))}",
            'charge': charge.typeName if charge is not None else None,
            'rungs_tried': len(picks)}))
    return out


@mcp.tool()
@_engine_thread
def sweep_hulls(fit_id: str, hulls: list = None, group: str = None,
                metrics: list = None, limit: int = _SWEEP_LIMIT,
                adapt: bool = False) -> dict:
    """Rebuild this fit's modules on OTHER hulls and rank them. Name a `group` ("Destroyer", "Assault Frigate", "Combat Battlecruiser") to enumerate every published hull in it server-side, or pass explicit `hulls`. Use this instead of picking a hull from remembered candidates: a hull chosen on static attributes and then made to work is the commonest way a fit answer goes wrong. Rows carry the named panel metrics (default dps/ehp/speed), cpu_free/pg_free, any legality problems, and the hull's per-skill and role bonuses — those are already applied in the numbers, and are shown so a low rank reads as "wrong weapons for this hull" when that is what it is. A class contains hulls that cannot be bought (tournament prizes, event hulls) and they rank like any other, so rows in the Special Edition branch carry an `availability` note — check it before recommending the winner. `adapt=True` re-arms each hull with the weapon system its OWN traits are bonused for, filling its hardpoints — without it this asks "which hull carries THIS loadout", which is biased to the hull the fit was built on and scores every off-race hull as bad guns rather than a bad hull. Run it BOTH ways when the question is "what should I fly": adapt=False says how portable your loadout is, adapt=True says which hull does the job best."""
    if not hulls and not group:
        raise ValueError('name `group` to enumerate a class, or pass `hulls`')
    src = _fit(fit_id)
    metrics = metrics or ['offense.dps', 'defense.ehp.total',
                          'navigation.max_velocity_ms']
    eft = eftlib.render_eft(src)
    body = eft.split('\n', 1)[1] if '\n' in eft else ''
    from eos.const import FittingHardpoint
    src_weapons = {m.item.typeName for m in src.modules
                   if not m.isEmpty and m.hardpoint in (FittingHardpoint.TURRET,
                                                        FittingHardpoint.MISSILE)}
    src_meta = max((m.item.metaLevel or 0) for m in src.modules
                   if not m.isEmpty and m.item.typeName in src_weapons) \
        if src_weapons else 5

    names = list(hulls or [])
    if group:
        grp = _group_by_name(group)
        if grp is None:
            raise ValueError(f'no ship group named {group!r} — group names are the '
                             'engine\'s own ("Destroyer", "Assault Frigate", '
                             '"Combat Battlecruiser")')
        names += sorted(i.typeName for i in grp.items
                        if getattr(i, 'published', True) and i.typeName not in names)
    if len(names) > limit:
        raise ValueError(f'{len(names)} hulls; raise `limit` (currently {limit}) '
                         'or narrow the group')

    def pick(panel, path):
        node = panel
        for part in path.split('.'):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    rows = []
    for name in names:
        item = eftlib._lookup(name)
        if item is None:
            rows.append({'hull': name, 'error': 'unknown hull'})
            continue
        options = (_adapt_to_hull(body, item, src_weapons, src_meta)
                   if adapt else []) or [(body, None)]
        best_row, best_score, last_err = None, None, None
        for hull_body, swap in options:
            try:
                spec = eftlib.parse_eft(f'[{name}, sweep]\n{hull_body}')[0]
                fit = eftlib.build_fit(spec)
            except Exception as exc:      # noqa: BLE001 — a hull that cannot take
                last_err = str(exc)[:120]
                continue
            panel = stat_panel(fit,
                               recalc=lambda f, factor_reload: _recalc(f, factor_reload))
            attr = fit.ship.getModifiedItemAttr
            row = {'hull': name}
            row.update({m: pick(panel, m) for m in metrics})
            row['cpu_free'] = round((attr('cpuOutput') or 0) - fit.cpuUsed, 2)
            row['pg_free'] = round((attr('powerOutput') or 0) - fit.pgUsed, 2)
            probs = _problems(fit)
            if probs:
                row['problems'] = probs
            if swap:
                row['adapted'] = swap
            # a legal build always beats an illegal one, then the ranking metric
            score = (not probs, row.get(metrics[0]) or 0)
            if best_score is None or score > best_score:
                best_row, best_score = row, score
        if best_row is None:
            rows.append({'hull': name, 'error': last_err or 'could not build'})
            continue
        bonus = _trait_text(item)
        if bonus:
            best_row['bonuses'] = bonus
        avail = _availability_note(item)
        if avail:
            best_row['availability'] = avail
        rows.append(best_row)

    key = metrics[0]
    rows.sort(key=lambda r: (r.get('problems') is not None, -(r.get(key) or 0)))
    return {'from_fit': fit_id, 'modules_of': _ship_name(src),
            'ranked_by': key, 'hulls': rows,
            'note': 'rows with `problems` do not fit as-is — the module list came '
                    'from another hull, so slots, hardpoints and grid differ. Treat '
                    'those numbers as an upper bound until the fit is adjusted.'}


@mcp.tool()
@_engine_thread
def versus(fit_id_a: str, fit_id_b: str, distance_km: float) -> dict:
    """The duel question, both directions: each fit's APPLIED dps into the other (application vs the victim's current sig/speed — set_projected ewar first to include it) and the victim's EHP weighted by the attacker's actual damage mix, reps, and time-to-kill (reps subtracted; structure damage caps applied)."""
    from eos.saveddata.damagePattern import DamagePattern
    if fit_id_a == fit_id_b:
        raise ValueError('a fit cannot fight itself; clone_fit it first')
    fa, fb = _fit(fit_id_a), _fit(fit_id_b)
    dist = float(distance_km) * 1000

    def direction(att, vic):
        _recalc(att)
        _recalc(vic)
        vic_speed = vic.maxSpeed or 0
        vic_sig = vic.ship.getModifiedItemAttr('signatureRadius') or 0
        dmg = graphlib._dmg_map(att)
        amap = graphlib._application_map(att, dist, vic_speed, vic_sig, 0.0)
        mix = {'em': 0.0, 'thermal': 0.0, 'kinetic': 0.0, 'explosive': 0.0}
        raw = 0.0
        for key, d in dmg.items():
            mult = amap.get(key, 0)
            for t in mix:
                mix[t] += getattr(d, t) * mult
            raw += d.total
        applied = sum(mix.values())
        # weigh the victim's EHP by this actual mix, then RESTORE the
        # pattern — a leaked pattern would silently skew later sweep and
        # module_attrs reads that use the fit's resident damagePattern
        saved_pattern = vic.damagePattern
        vic.damagePattern = DamagePattern(
            emAmount=mix['em'] or (0 if applied else 25),
            thermalAmount=mix['thermal'] or (0 if applied else 25),
            kineticAmount=mix['kinetic'] or (0 if applied else 25),
            explosiveAmount=mix['explosive'] or (0 if applied else 25))
        _recalc(vic)
        ehp = sum(vic.ehp.values())
        reps_total = sum(v for v in vic.effectiveTank.values() if v)
        vic.damagePattern = saved_pattern
        caps = [vic.ship.getModifiedItemAttr(a) for a in
                ('shieldDamageLimit', 'armorDamageLimit', 'structureDamageLimit')]
        caps = [c for c in caps if c]
        effective = min([applied] + caps) if caps else applied
        out = {'applied_dps': round(applied, 1), 'raw_dps': round(raw, 1),
               'damage_mix_pct': {t: round(100 * v / applied)
                                  for t, v in mix.items() if applied and v / applied >= 0.005},
               'victim_ehp_vs_this_mix': round(ehp),
               'victim_reps_hps_total': round(reps_total, 1)}
        if caps:
            out['victim_incoming_dps_cap'] = round(min(caps))
        if effective > reps_total > 0 or (effective and not reps_total):
            out['time_to_kill_s'] = round(ehp / (effective - reps_total))
        elif effective:
            out['tanked'] = True  # reps outpace what lands
        return out

    return {'distance_km': distance_km,
            'ships': {fit_id_a: _ship_name(fa), fit_id_b: _ship_name(fb)},
            'a_vs_b': {'attacker': fit_id_a, 'victim': fit_id_b, **direction(fa, fb)},
            'b_vs_a': {'attacker': fit_id_b, 'victim': fit_id_a, **direction(fb, fa)},
            'assumptions': [
                'victim moving at its current max speed, 90 deg (max transversal)',
                'reps as one continuous pool across layers (favors the defender)',
                'ewar/links only if already set via set_projected/set_booster',
                'spool-up weapons at full spool (the ramp favors the Triglavian side)']}


@mcp.tool()
@_engine_thread
def compare_fits(fit_id_a: str = None, fit_id_b: str = None,
                 fit_a: str = None, fit_b: str = None) -> dict:
    """Stat panels diffed: only figures differing >0.1%, as {stat: [a, b]}."""
    # `fit_a`/`fit_b` are what callers reach for; taking them costs nothing and
    # saves a round spent reading a validation error.
    fit_id_a, fit_id_b = fit_id_a or fit_a, fit_id_b or fit_b
    if not fit_id_a or not fit_id_b:
        raise ValueError('pass two fit ids: fit_id_a and fit_id_b')
    diffs = {}
    panels = [get_stats(f) for f in (fit_id_a, fit_id_b)]

    def walk(a, b, prefix):
        keys = set(a) | set(b)
        for k in sorted(keys):
            path = f'{prefix}.{k}' if prefix else str(k)
            va, vb = a.get(k), b.get(k)
            if isinstance(va, dict) and isinstance(vb, dict):
                walk(va, vb, path)
            elif isinstance(va, (int, float)) and isinstance(vb, (int, float)) \
                    and not isinstance(va, bool) and not isinstance(vb, bool):
                if abs(va - vb) / max(abs(va), abs(vb), 1e-9) > 0.001:
                    diffs[path] = [va, vb]
            elif va != vb:
                diffs[path] = [va, vb]
    walk(panels[0], panels[1], '')
    return {'a': fit_id_a, 'b': fit_id_b, 'diffs': diffs}


@mcp.tool()
@_engine_thread
def validate_fit(fit_id: str) -> dict:
    """In-game legality: fitting resources, slots, hardpoints, drone limits."""
    fit = _fit(fit_id)
    problems = _problems(fit)
    return {'fit_id': fit_id, 'ship': _ship_name(fit),
            'legal': not problems, 'problems': problems}


@mcp.tool()
@_engine_thread
def required_skills(fit_id: str, full: bool = False) -> dict:
    """Skills (with levels) needed to use the whole fit. Default lists only the training-queue ends (prerequisites implied by other entries are pruned) plus any skills an alpha clone cannot train high enough; full=true returns the entire prerequisite closure."""
    fit = _fit(fit_id)
    need, items = {}, {}

    def walk(item):
        for skill_item, level in item.requiredSkills.items():
            name = skill_item.typeName
            if need.get(name, 0) < int(level):
                need[name] = int(level)
                items[name] = skill_item
                walk(skill_item)

    walk(fit.ship.item)
    for mod in fit.modules:
        if not mod.isEmpty:
            walk(mod.item)
            if mod.charge is not None:
                walk(mod.charge)
    for group in (fit.drones, fit.fighters, fit.boosters, fit.implants):
        for thing in group:
            walk(thing.item)

    # static prerequisite closure of a single skill (levels are fixed:
    # training a skill to any level needs its whole prereq tree)
    closures = {}

    def closure_of(skill_item):
        name = skill_item.typeName
        if name not in closures:
            closures[name] = {}
            for s2, l2 in skill_item.requiredSkills.items():
                closures[name][s2.typeName] = max(closures[name].get(s2.typeName, 0), int(l2))
                for n3, l3 in closure_of(s2).items():
                    closures[name][n3] = max(closures[name].get(n3, 0), l3)
        return closures[name]

    ends = {s: lvl for s, lvl in need.items()
            if not any(closure_of(items[o]).get(s, 0) >= lvl
                       for o in need if o != s)}

    caps = dict(sqlite3.connect(os.path.join(ARGS.pyfa, 'eve.db')).execute(
        'SELECT typeID, level FROM alphaCloneSkills').fetchall())
    alpha_blocked = {s: lvl for s, lvl in need.items()
                     if caps.get(items[s].ID, 0) < lvl}

    out = {'fit_id': fit_id, 'skills': dict(sorted(need.items() if full else ends.items()))}
    if not full and len(need) > len(ends):
        out['implied_prereqs'] = len(need) - len(ends)
    if alpha_blocked:
        out['alpha_blocked'] = dict(sorted(alpha_blocked.items()))
    out['note'] = 'minimums to use, not to use well'
    return out


def _parity_text(engine_build, sde_build, mixed):
    """What may and may not be said about the two builds.

    Measured 2026-08-20: a graded answer closed with "SDE build 3473160 is
    newer — no fit-relevant module changed between them", an attribute-level
    claim nothing in the stack had checked. Two build numbers side by side
    invite exactly that inference, so the field carrying them carries the
    refusal to draw it.

    Pulled out of `engine_info` because the branches are the whole point and
    were unreachable from a test: the first version of this shipped with the
    mixed-build clause wedged between the elif and the else, so `else` bound
    to `if mixed` and every non-mixed run was overwritten with "not found"
    while still reporting a build number beside it. A smoke test that only
    ever ran against a mixed checkout passed it.
    """
    if sde_build and engine_build and str(sde_build) != str(engine_build):
        parity = (f'UNVERIFIED. The engine is at build {engine_build}, layer 1 at '
                  f'{sde_build}. No attribute-level comparison between these builds '
                  'has been run by anything in this stack, so you cannot state that '
                  'nothing fit-relevant changed between them. Engine numbers are '
                  'authoritative for fit output; layer 1 is authoritative for what '
                  'exists and what it is called. Say the builds differ, or say '
                  'nothing about it.')
    elif sde_build:
        parity = f'engine and layer 1 are both at build {engine_build}'
    else:
        parity = ('layer 1 databases not found from here — build skew is unknown, '
                  'not zero')
    if mixed:
        parity = ('layer 1 is MIXED — its parts are at different builds ('
                  + ', '.join(f'{k}={v}' for k, v in sorted(mixed.items()))
                  + '). Rebuild it before trusting cross-part answers. ') + parity
    return parity


def _sde_build():
    """Layer 1's build number, if its databases are sitting next to this repo.

    Best-effort: the fitting server does not own the SDE and must work without
    it. Returns None rather than guessing.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    found = {}
    for cand in sorted(glob.glob(os.path.join(root, 'eve-sde-*.sqlite'))):
        try:
            with sqlite3.connect(f'file:{cand}?mode=ro', uri=True) as db:
                row = db.execute("SELECT value FROM meta WHERE key='sdeBuildNumber'").fetchone()
            if row:
                found[os.path.basename(cand)] = str(row[0])
        except sqlite3.Error:
            continue
    if not found:
        return None, None
    builds = set(found.values())
    # The parts are separate files built by separate runs. A half-rebuilt set
    # answers "what exists" from one CCP release and "what it costs" from
    # another, and reporting either number alone hides that completely.
    return max(builds), (found if len(builds) > 1 else None)


@mcp.tool()
def engine_info() -> dict:
    """Engine + data build, and whether it matches layer 1's SDE build. Any skew means the two layers may disagree; `parity` says plainly that no attribute-level comparison has been run, because none has."""
    meta = dict(sqlite3.connect(os.path.join(ARGS.pyfa, 'eve.db'))
                .execute('SELECT field_name, field_value FROM metadata').fetchall())
    engine_build = meta.get('client_build')
    sde_build, mixed = _sde_build()
    parity = _parity_text(engine_build, sde_build, mixed)
    return {
        'engine': 'pyfa-eos (headless)',
        'engine_build': engine_build,
        'sde_build': sde_build,
        'parity': parity,
        'unmodeled': ['industrial core state',
                      'structure reinforcement/low-power cycles (fitting, combat and fuel ARE modeled)',
                      'custom skill sheets',
                      'heat burnout timers (overload bonuses ARE modeled: state overheated)'],
        'skills_presets': ['all-0', 'alpha', 'all-5'],
    }


if __name__ == '__main__':
    mcp.run()
