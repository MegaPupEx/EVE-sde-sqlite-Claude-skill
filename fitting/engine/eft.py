"""EFT parse / build / render over pyfa's eos — no service layer, no GUI.

pyfa's own EFT importer (service/port/eft.py) imports wx-entangled service
and gui modules, so the MCP wraps this instead. Three functions:

    parse_eft(text)   -> list of FitSpec (neutral dicts; no eos needed)
    build_fit(spec)   -> eos Fit, calculated-ready (bootstrap first)
    render_eft(fit)   -> EFT text from a live eos fit

Dialect: standard EFT as the game client and pyfa emit it. Lines are
classified by item category (the pyfa approach), not by section position, so
shuffled sections import fine. "Module, Charge" splits are resolved by
lookup, trying rightmost commas first, which survives item names containing
commas. "/offline" suffix is handled. Quantity lines ("Hobgoblin II x5")
become drones/fighters/cargo by category.

Rack layout is preserved: within a section, line order is slot order (the
game client fills slots in sequence on import), and "[Empty ... slot]"
placeholders hold gaps — players space overloaded modules apart because
heat damage spreads to *adjacent* slots. The engine's numbers are
order-independent (heat over time is unmodeled), so this is interop
fidelity: an ordered fit survives import -> export un-scrambled.

Mutated (abyssal) modules and drones use pyfa's dialect exactly, because
interop is the point: the fitted line carries the BASE item name plus an
` [N]` reference, and a trailing section maps each N to three lines —
base item, mutaplasmid item, and `attr value, attr value` rolled stats.
Rolled values outside the mutaplasmid's range are clamped by eos's own
Mutator validator, same as pyfa.
"""
import re

_MUTANT_HEAD = re.compile(r'^\[(\d+)\]\s+(.+)$')
_MUTATION_REF = re.compile(r'\s*\[(\d+)\]$')
_EMPTY_SLOT = re.compile(r'^\[Empty (Low|Med|High|Rig|Subsystem|Service) slot\]$', re.IGNORECASE)


class EftError(ValueError):
    pass


class FitSpec:
    def __init__(self, ship, name):
        self.ship = ship
        self.name = name
        self.entries = []    # dicts: {'name', 'charge', 'offline', 'quantity', 'mutation'}
        self.mutations = {}  # ref -> {'base', 'mutaplasmid', 'attrs': {name: value}}

    def __repr__(self):
        return f'FitSpec({self.ship!r}, {self.name!r}, {len(self.entries)} entries)'


def parse_eft(text):
    """Parse EFT text (one or many fits) into FitSpecs. Pure text handling."""
    fits = []
    fit = None
    pending = None  # (ref, lines_still_expected) while inside a mutation block
    for rawline in text.splitlines():
        line = rawline.strip()
        if not line:
            continue
        if line.startswith('[') and ',' in line and line.endswith(']'):
            ship, name = line[1:-1].split(',', 1)
            fit = FitSpec(ship.strip(), name.strip())
            fits.append(fit)
            pending = None
            continue
        empty = _EMPTY_SLOT.match(line)
        if empty and fit is not None:
            fit.entries.append({'empty': empty.group(1).lower()})
            continue
        if line.startswith('[') and line.endswith(']'):  # other bracketed noise
            continue
        mut_head = _MUTANT_HEAD.match(line)
        if mut_head and fit is not None:
            ref = int(mut_head.group(1))
            fit.mutations[ref] = {'base': mut_head.group(2).strip(),
                                  'mutaplasmid': None, 'attrs': {}}
            pending = (ref, 2)
            continue
        if pending is not None:
            ref, left = pending
            mut = fit.mutations[ref]
            if mut['mutaplasmid'] is None:
                mut['mutaplasmid'] = line
            else:
                for pair in line.split(','):
                    bits = pair.strip().rsplit(' ', 1)
                    if len(bits) != 2:
                        raise EftError(f'malformed mutation attribute {pair.strip()!r} in [{ref}]')
                    try:
                        mut['attrs'][bits[0].strip()] = float(bits[1])
                    except ValueError:
                        raise EftError(f'non-numeric mutation value in [{ref}]: {pair.strip()!r}')
            pending = (ref, left - 1) if left > 1 else None
            continue
        if fit is None:
            raise EftError(f'module line before any [Ship, name] header: {line!r}')
        entry = {'name': line, 'charge': None, 'offline': False,
                 'quantity': None, 'mutation': None}
        mut_ref = _MUTATION_REF.search(entry['name'])
        if mut_ref:
            entry['mutation'] = int(mut_ref.group(1))
            entry['name'] = entry['name'][:mut_ref.start()].strip()
        if entry['name'].endswith('/offline'):
            entry['offline'] = True
            entry['name'] = entry['name'][:-len('/offline')].strip()
        qty = re.match(r'^(?P<name>.+?) x(?P<qty>\d+)(?P<rest>\s*,.*)?$', entry['name'])
        if qty:
            entry['name'] = qty.group('name').strip() + (qty.group('rest') or '')
            entry['quantity'] = int(qty.group('qty'))
        fit.entries.append(entry)
    if not fits:
        raise EftError('no [Ship, name] header found')
    return fits


def _lookup(name):
    import eos.db
    return eos.db.getItem(name)


def _suggest(name, limit=5):
    """Published type names close to `name`, best first.

    A bare "unknown item" costs a round and reveals nothing: the caller guesses
    again from the same memory that produced the miss. Measured 2026-08-20: a
    graded run invented `Rage Light Missile` and had to go back to layer 1 with
    a LIKE query to recover. eos's own searchItems is unusable here (it raises
    under this SQLAlchemy), so match on the query's longest words and rank what
    comes back by string similarity.
    """
    import difflib
    import eos.db
    # Raw SQL over the engine's own connection rather than the ORM: importing
    # eos.gamedata lazily re-enters a partially initialised module and raises
    # ImportError, which the best-effort guard would then swallow into silence.
    try:
        conn = eos.db.gamedata_engine.raw_connection()
        cur = conn.cursor()
    except Exception:                  # noqa: BLE001 — suggestions are best-effort
        return []
    words = sorted((w.strip(',') for w in str(name).split()), key=len, reverse=True)
    seen = {}
    try:
        for word in words[:2]:
            if len(word) < 3:
                continue
            cur.execute('SELECT typeName FROM invtypes WHERE typeName LIKE ? '
                        'AND published = 1 LIMIT 400', (f'%{word}%',))
            for (found,) in cur.fetchall():
                seen.setdefault(found, difflib.SequenceMatcher(
                    None, str(name).lower(), found.lower()).ratio())
        if not seen and words:
            # A transposition ('Rifetr') matches no LIKE pattern at all, which is
            # exactly the case a suggestion is most useful for. Fall back to
            # similarity over the names starting with the same letter — typos
            # keep the first character far more often than any other.
            cur.execute('SELECT typeName FROM invtypes WHERE published = 1 '
                        'AND typeName LIKE ?', (str(name)[:1] + '%',))
            pool = [row[0] for row in cur.fetchall()]
            for found in difflib.get_close_matches(str(name), pool, limit, 0.6):
                seen[found] = difflib.SequenceMatcher(
                    None, str(name).lower(), found.lower()).ratio()
    except Exception:                  # noqa: BLE001
        return []
    finally:
        conn.close()
    return [n for n, _ in sorted(seen.items(), key=lambda kv: -kv[1])[:limit]]


def _unknown(kind, name, extra=''):
    near = _suggest(name)
    hint = f' — did you mean: {", ".join(near)}?' if near else ''
    return EftError(f'unknown {kind}: {name!r}{extra}{hint}')


def _resolve(entry):
    """Return (item, charge_item) for an entry, resolving 'Module, Charge'."""
    item = _lookup(entry['name'])
    if item is not None:
        return item, None
    # try splitting at each comma, rightmost first: module name may contain commas
    parts = entry['name'].split(',')
    for i in range(len(parts) - 1, 0, -1):
        mod_name = ','.join(parts[:i]).strip()
        charge_name = ','.join(parts[i:]).strip()
        item = _lookup(mod_name)
        if item is not None:
            charge = _lookup(charge_name)
            if charge is None:
                raise _unknown('charge', charge_name, f' on {mod_name!r}')
            return item, charge
    raise _unknown('item', entry['name'])


def _mutation_parts(entry, spec):
    """Resolve a mutated entry's mutaplasmid; returns (dynamic_item, attrs {ID: value})."""
    from eos.db.gamedata.queries import getAttributeInfo, getDynamicItem
    mut = spec.mutations.get(entry['mutation'])
    if mut is None:
        raise EftError(f'{entry["name"]!r} references mutation [{entry["mutation"]}] '
                       'but no such block exists')
    muta_item = _lookup(mut['mutaplasmid'] or '')
    if muta_item is None:
        raise EftError(f'unknown mutaplasmid {mut["mutaplasmid"]!r} in [{entry["mutation"]}]')
    dyn = getDynamicItem(muta_item.ID)
    if dyn is None:
        raise EftError(f'{muta_item.typeName!r} is not a mutaplasmid')
    attrs = {}
    for name, value in mut['attrs'].items():
        info = getAttributeInfo(name)
        if info is None:
            raise EftError(f'unknown attribute {name!r} in mutation [{entry["mutation"]}]')
        attrs[info.ID] = value
    return dyn, attrs


def build_fit(spec):
    """Build a calculated-ready eos Fit from a FitSpec. Call bootstrap() first."""
    import eos.db  # noqa: F401 — must precede eos.saveddata imports
    from eos.const import FittingModuleState
    from eos.saveddata.booster import Booster
    from eos.saveddata.cargo import Cargo
    from eos.saveddata.character import Character
    from eos.saveddata.damagePattern import DamagePattern
    from eos.saveddata.drone import Drone
    from eos.saveddata.fighter import Fighter
    from eos.saveddata.fit import Fit
    from eos.saveddata.implant import Implant
    from eos.saveddata.module import Module
    from eos.saveddata.ship import Ship

    ship_item = _lookup(spec.ship)
    if ship_item is None:
        raise _unknown('ship', spec.ship)
    if ship_item.category.name == 'Structure':
        from eos.saveddata.citadel import Citadel
        fit = Fit(Citadel(ship_item), name=spec.name)
    else:
        fit = Fit(Ship(ship_item), name=spec.name)
    fit.character = Character.getAll5()
    fit.damagePattern = DamagePattern(emAmount=25, thermalAmount=25,
                                      kineticAmount=25, explosiveAmount=25)
    from eos.const import FittingSlot
    empty_slots = {'low': FittingSlot.LOW, 'med': FittingSlot.MED,
                   'high': FittingSlot.HIGH, 'rig': FittingSlot.RIG,
                   'subsystem': FittingSlot.SUBSYSTEM,
                   'service': FittingSlot.SERVICE}
    for entry in spec.entries:
        if entry.get('empty'):
            placeholder = Module.buildEmpty(empty_slots[entry['empty']])
            fit.modules.appendIgnoreEmpty(placeholder)
            placeholder.owner = fit
            continue
        item, charge = _resolve(entry)
        category = item.category.name
        if entry['mutation'] is None and getattr(item, 'isAbyssal', False):
            raise EftError(
                f'{item.typeName!r} is a mutated item with no [N] mutation block — '
                'its stats are the roll, not the type. Paste the fit with its '
                'mutation section (base item, mutaplasmid, rolled attributes).')
        if category == 'Drone':
            # route by category, not by quantity: a drone line without 'xN'
            # (common on hand-typed mutated drones) used to fall into the
            # module branch and die on eos's opaque 'Passed item is not a
            # Module' — treat it as one drone instead
            if entry['mutation'] is not None:
                dyn, attrs = _mutation_parts(entry, spec)
                drone = Drone(dyn.resultingItem, item, dyn)
                for attr_id, value in attrs.items():
                    if attr_id in drone.mutators:
                        drone.mutators[attr_id].value = value
            else:
                drone = Drone(item)
            qty = 1 if entry['quantity'] is None else entry['quantity']
            drone.amount = qty
            drone.amountActive = qty
            fit.drones.append(drone)
            drone.owner = fit
        elif category == 'Fighter':
            fighter = Fighter(item)
            if entry['quantity'] is not None:
                fighter.amount = entry['quantity']
            fit.fighters.append(fighter)
            fighter.owner = fit
        elif entry['quantity'] is not None:
            # ` xN` means drones, fighters or cargo. On a module it is the
            # commonest paste error, and pyfa's own importer dies on it with an
            # opaque TypeError from constructing a Drone out of a module.
            if category in ('Module', 'Subsystem'):
                raise EftError(
                    f'{item.typeName!r} is a module, and EFT has no quantity form '
                    'for fitted modules — repeat the line once per module. " xN" '
                    'is drone/fighter/cargo syntax; on a module it silently '
                    'misfiles the line instead of filling slots.')
            # Cargo.__init__ takes the item only; `amount` is a separate
            # attribute. Passing it positionally raised a TypeError, so every
            # EFT carrying an ammo or cargo line — which killboard and pyfa
            # exports routinely do — failed to import at all.
            cargo = Cargo(item)
            cargo.amount = entry['quantity']
            fit.cargo.append(cargo)
        elif category == 'Implant':
            if item.group.name == 'Booster':
                fit.boosters.append(Booster(item))
            else:
                fit.implants.append(Implant(item))
        elif category == 'Charge':
            cargo = Cargo(item)
            cargo.amount = 1
            fit.cargo.append(cargo)
        else:
            if entry['mutation'] is not None:
                dyn, attrs = _mutation_parts(entry, spec)
                mod = Module(dyn.resultingItem, item, dyn)
                for attr_id, value in attrs.items():
                    if attr_id in mod.mutators:
                        mod.mutators[attr_id].value = value
            else:
                mod = Module(item)
            if charge is not None:
                mod.charge = charge
            if entry['offline']:
                mod.state = FittingModuleState.OFFLINE
            elif mod.isValidState(FittingModuleState.ACTIVE):
                mod.state = FittingModuleState.ACTIVE
            # appendIgnoreEmpty, not append: append() fills the first empty
            # position in the rack, which would swallow authored [Empty ...]
            # placeholders and scramble heat-conscious layouts
            fit.modules.appendIgnoreEmpty(mod)
            mod.owner = fit
    return fit


def render_eft(fit):
    """Render a live eos fit as EFT text, slot-grouped the way pyfa exports."""
    from eos.const import FittingModuleState, FittingSlot

    mutants = {}  # ref -> mutated Module/Drone; section rendered at the end

    def _mut_suffix(thing):
        if not getattr(thing, 'isMutated', False):
            return ''
        ref = len(mutants) + 1
        mutants[ref] = thing
        return f' [{ref}]'

    order = (FittingSlot.LOW, FittingSlot.MED, FittingSlot.HIGH,
             FittingSlot.RIG, FittingSlot.SUBSYSTEM, FittingSlot.SERVICE)
    slots = {s: [] for s in order}
    for mod in fit.modules:
        if mod.isEmpty:
            # placeholder from an imported layout: keep the gap in position
            if mod.slot is not None:
                slots.setdefault(mod.slot, []).append(
                    f'[Empty {FittingSlot(mod.slot).name.capitalize()} slot]')
            continue
        # mutated modules export under the BASE item name; the [N] section
        # carries the mutaplasmid and rolls (pyfa's dialect)
        line = mod.baseItem.typeName if mod.isMutated else mod.item.typeName
        if mod.charge is not None:
            line += f', {mod.charge.typeName}'
        if mod.state == FittingModuleState.OFFLINE:
            line += ' /offline'
        line += _mut_suffix(mod)
        slots.setdefault(mod.slot, []).append(line)
    out = [f'[{fit.ship.item.typeName}, {fit.name}]']
    blocks = ['\n'.join(slots[s]) for s in order if slots[s]]
    out.append('\n\n'.join(blocks))
    extras = []
    for implant in fit.implants:
        extras.append(implant.item.typeName)
    for booster in fit.boosters:
        extras.append(booster.item.typeName)
    for drone in fit.drones:
        name = drone.baseItem.typeName if drone.isMutated else drone.item.typeName
        extras.append(f'{name} x{drone.amount}{_mut_suffix(drone)}')
    for fighter in fit.fighters:
        amt = fighter.amount if (fighter.amount or 0) > 0 else \
            int(fighter.getModifiedItemAttr('fighterSquadronMaxSize') or 1)
        extras.append(f'{fighter.item.typeName} x{amt}')
    for cargo in fit.cargo:
        extras.append(f'{cargo.item.typeName} x{cargo.amount}')
    if extras:
        out.append('\n' + '\n'.join(extras))
    if mutants:
        from eos.db.gamedata.queries import getAttributeInfo
        from eos.utils.float import floatUnerr
        blocks = []
        for ref in sorted(mutants):
            mutant = mutants[ref]
            rolls = {getAttributeInfo(attr_id).name: mut.value
                     for attr_id, mut in mutant.mutators.items()}
            roll_line = ', '.join(f'{n} {floatUnerr(rolls[n])}' for n in sorted(rolls))
            blocks.append(f'[{ref}] {mutant.baseItem.typeName}\n'
                          f'  {mutant.mutaplasmid.item.typeName}\n'
                          f'  {roll_line}')
        out.append('\n' + '\n'.join(blocks))
    return '\n'.join(out) + '\n'
