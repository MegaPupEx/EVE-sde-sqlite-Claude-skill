"""EVE SDE MCP server — layer 1 behind two tools.

    python3 server.py --sde /path/to/dir-with-eve-sde-*.sqlite

Design rules (mirrors fitting/mcp/server.py):
- Batch by default: every call re-reads the whole conversation (~45k tokens),
  so the unit of cost is the ROUND, not the query. `query` takes many
  statements at once; measured runs spent two thirds of their budget on
  one-query-per-round shell invocations.
- The gotchas belong in code, not only in prose. Measured over 29 eval
  subjects, the layer-1 reference docs were opened by ONE — so a trap that
  lives only in `references/gotchas-*.md` protects nobody. `attrs` therefore
  returns unit-corrected values, and `query` lints the raw-value traps.
- Outputs stay compact: row caps with true counts, empty sections omitted.
"""
import argparse
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _stdio import MCPServer   # stdlib only: layer 1 installs without a venv

_parser = argparse.ArgumentParser()
_parser.add_argument('--sde', default=os.environ.get('EVE_SDE_DIR', '.'))
ARGS = _parser.parse_args()

mcp = MCPServer('eve-sde')

PARTS, BUILD, MIXED_BUILDS = [], None, None
_db = None


def _conn():
    """One in-memory handle with every part ATTACHed; unqualified names resolve."""
    global _db, PARTS, BUILD
    if _db is not None:
        return _db
    root = os.path.abspath(ARGS.sde)
    PARTS = sorted(f for f in os.listdir(root)
                   if f.startswith('eve-sde-') and f.endswith('.sqlite'))
    if not PARTS:
        raise RuntimeError(
            f'no eve-sde-*.sqlite in {root} — the databases are gitignored, so a '
            'fresh clone has to build them: `./setup.sh` at the repo root (~1 min, '
            'stdlib only). Say so plainly rather than answering from memory.')
    _db = sqlite3.connect(':memory:', check_same_thread=False)
    for f in PARTS:
        alias = f[len('eve-sde-'):-len('.sqlite')].replace('-', '_')
        _db.execute('ATTACH DATABASE ? AS ' + alias, (os.path.join(root, f),))
    alias0 = PARTS[0][len('eve-sde-'):-len('.sqlite')].replace('-', '_')
    global MIXED_BUILDS
    per_part = {}
    for f in PARTS:
        alias = f[len('eve-sde-'):-len('.sqlite')].replace('-', '_')
        row = _db.execute(
            f"SELECT value FROM {alias}.meta WHERE key='sdeBuildNumber'").fetchone()
        if row:
            per_part[alias] = str(row[0])
    # Each part is a separate file from a separate build run. A half-rebuilt set
    # answers "what exists" from one CCP release and "what it costs" from
    # another; reading the build from one part alone hides that entirely.
    BUILD = max(per_part.values()) if per_part else _db.execute(
        f"SELECT value FROM {alias0}.meta WHERE key='sdeBuildNumber'").fetchone()[0]
    MIXED_BUILDS = per_part if len(set(per_part.values())) > 1 else None
    return _db


# unitID semantics that make raw values lie (references/gotchas-dogma.md).
# Kept as data so the conversion and the warning never drift apart.
UNITS = {
    108: ('inverted', 'resonance/resistance: 0.0 = 100%, 1.0 = 0%'),
    101: ('ms', 'milliseconds despite a "s" displayName'),
    109: ('modifier', 'modifier percent: 1.1 = +10%, 0.75 = -25%'),
    105: ('pct_raw', 'percent conventions vary: -50 = -50%'),
    121: ('pct_raw', 'percent conventions vary'),
    124: ('pct_raw', 'percent conventions vary'),
    127: ('pct_raw', 'percent conventions vary'),
}
BIG_TABLES = ('type_dogma', 'types', 'moons', 'planets', 'type_effects')


# Traps this server does NOT mechanise. Named for the same reason the engine
# names `unmodeled`: a tool that silently covers some cases invites the reader
# to assume it covers all of them.
NOT_CORRECTED = [
    'ties — many attributes have huge tie groups (409 of 423 ships share one '
    'web-resist value); never lift a top row as "the best"',
    'attribute families whose NAME lies (resists, sensor strength, tech level) '
    '— match on attributeID, not name',
    'highIsGood is unreliable (remoteRepairImpedance is inverted and flagged 1)',
    'derived values — regen peaks, stacking, skills, hull bonuses: engine work, '
    'not raw attributes',
    'attributeID and unitID are separate ID spaces that overlap',
]


_UNIT_NAMES = None


def _unit_names(db):
    """unitID -> display symbol, read from the DB rather than baked in.

    Most units are honest: metres, mm, MW, GJ, HP. Warning about every one of
    them buried the two that matter — a measured session got 13 lines of "no
    correction rule" on one Vexor, including metres and millimetres. Label
    what CCP labels, override the liars, warn only on genuine unknowns.
    """
    global _UNIT_NAMES
    if _UNIT_NAMES is None:
        _UNIT_NAMES = {}
        try:
            for key, display in db.execute('SELECT _key, displayName FROM dogmaUnits'):
                if display:
                    _UNIT_NAMES[key] = display
        except sqlite3.Error:
            pass                      # a renamed table costs labels, not answers
    return _UNIT_NAMES


def _interpret(value, unit_id, db=None):
    """Return (human_value, note) for a raw dogma value."""
    if value is None:
        return None, None
    if unit_id is not None and unit_id not in UNITS:
        symbol = _unit_names(db).get(unit_id) if db is not None else None
        if symbol:
            return f'{value} {symbol}', None
        # Silence here would read as "no correction needed". A unit with no
        # rule AND no label is exactly where a future SDE build breaks this.
        return None, (f'unitID {unit_id} has no label in dogmaUnits and no correction '
                      'rule here — raw value shown; confirm it before quoting')
    if unit_id not in UNITS:
        return None, None
    kind, why = UNITS[unit_id]
    value = float(value)          # ints must still format as 0.0%, not 0%
    if kind == 'inverted':
        return f'{round((1 - value) * 100, 1)}%', why
    if kind == 'ms':
        return f'{round(value / 1000, 3)} s', why
    if kind == 'modifier':
        return f'{round((value - 1) * 100, 1)}%', why
    return None, why


def _schema_hint(db, message, stmt):
    """Turn a column/table error into the answer instead of another round.

    Measured 2026-08-19: "which T1 cruiser has the most powergrid" took eight
    calls, six of them rediscovering that this builder stores `name` where
    CCP's SDE says typeName/groupName/attributeName. The database already
    knows; saying so costs nothing and saves a round each time.
    """
    # Every real table lives in an ATTACHed part, and each schema has its own
    # sqlite_master — the main one is empty, so walk database_list.
    tables = []
    for _, schema, _path in db.execute('PRAGMA database_list'):
        try:
            tables += [r[0] for r in db.execute(
                f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")]
        except sqlite3.Error:
            pass
    tables = sorted(set(tables))
    if 'no such table' in message:
        missing = message.rsplit(':', 1)[-1].strip()
        near = [t for t in tables if missing.lower().strip('_') in t.lower()]
        listed = sorted(near or tables)
        return {'tables_available': listed[:40],
                **({'tables_not_listed': len(listed) - 40} if len(listed) > 40 else {})}
    if 'no such column' not in message:
        return {}
    # Report the columns of every table this statement actually mentions.
    hint = {}
    for t in tables:
        if re.search(r'\b' + re.escape(t) + r'\b', stmt):
            try:
                hint[t] = [r[1] for r in db.execute(f'PRAGMA table_info({t})')]
            except sqlite3.Error:
                pass
    if hint:
        return {'columns_available': hint}
    listed = sorted(tables)
    return {'tables_available': listed[:40],
            **({'tables_not_listed': len(listed) - 40} if len(listed) > 40 else {})}


@mcp.tool()
def query(statements: list[str] = None, limit: int = 40, sql: str = None) -> dict:
    """SQL escape hatch for set and aggregate questions — "which ore", "how many jumps", "list every hull that…". For the stats of a type you can NAME, use `attrs` instead: it answers in one call where SQL takes several. `statements` is a LIST — put every query you already know you need in one call, because a second call re-reads the whole conversation. A '-- comment' line above a statement labels it. Every eve-sde part is pre-ATTACHed so table names need no prefix (except `meta`, in all of them); note the group table is `groups_`. Rows are capped with the true count reported; raw dogma values are linted for the unit traps."""
    # Measured over gen-10: 22 of 24 calls to the old `sql: str` form sent a
    # single statement. A string invites one statement no matter what the
    # docstring asks for, so the parameter is a list and the schema says so.
    # A bare string still works -- refusing it would cost the round the shape
    # change is meant to save -- but it is answered with a nudge.
    # `sql` is the name callers reach for first — it was this tool's own
    # parameter until gen-11. Charging a wasted round to say "wrong keyword"
    # helps nobody; take it, and nudge toward the list form in the result.
    if statements is None and sql is not None:
        statements = sql
    if statements is None:
        raise ValueError('pass `statements`: a list of SQL statements')
    coerced = isinstance(statements, str)
    sql = statements if coerced else '\n'.join(
        s if s.rstrip().endswith(';') else s + ';' for s in statements)

    db = _conn()
    blocks, label, buf = [], None, []
    for line in sql.splitlines():
        s = line.strip()
        pending = any(x.strip() for x in buf)
        if not s and not pending:
            continue                      # leading blank lines are not a statement
        if s.startswith('--') and not pending:
            label = s.lstrip('-').strip()
            continue
        buf.append(line)
        if s.endswith(';'):
            stmt = '\n'.join(buf).strip()
            if stmt.rstrip(';').strip():
                blocks.append((label, stmt))
            label, buf = None, []
    if '\n'.join(buf).strip():
        blocks.append((label, '\n'.join(buf).strip()))
    if not blocks:
        raise ValueError('no statements; separate them with ";"')

    out = []
    for label, stmt in blocks:
        item = {'label': label or 'query'}
        try:
            cur = db.execute(stmt)
        except sqlite3.Error as e:
            item['error'] = str(e)          # one bad statement never kills the batch
            item.update(_schema_hint(db, str(e), stmt))
            out.append(item)
            continue
        if cur.description is None:
            item['rows'] = 0
            out.append(item)
            continue
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        item['columns'] = cols
        item['rows'] = len(rows)
        item['data'] = [list(r) for r in rows[:limit]]
        if len(rows) > limit:
            item['truncated'] = f'showing {limit} of {len(rows)}; aggregate or LIMIT in SQL'
        notes = []
        low = stmt.lower()
        if 'value' in [c.lower() for c in cols] and 'attributeid' in low:
            notes.append('raw dogma `value` selected: unitID decides meaning '
                         '(108 inverted, 101 ms, 109 modifier-%). Use the `attrs` '
                         'tool for unit-corrected numbers.')
        if re.search(r'select\s+\*', low) and any(t in low for t in BIG_TABLES):
            notes.append('SELECT * on a large table — aggregate instead of printing rows.')
        if re.search(r'a\.name\s*(=|like)', low) and 'resonance' not in low:
            notes.append('matching attributes by name: several families lie '
                         '(resists, sensor strength, tech level). Prefer attributeIDs.')
        if notes:
            item['notes'] = notes
        out.append(item)
    result = {'sde_build': BUILD, 'parts': [p[8:-7] for p in PARTS], 'results': out}
    if coerced:
        result['note'] = ('`statements` is a list — pass each query as its own '
                          'element, and send every one you already know you need '
                          'in a single call.')
    return result


# Hull facts live on `types` as ordinary columns, not in dogma. Measured in
# gen-11: a "how much cargo" question burned 13 rounds because the model went
# looking for a dogma attribute that does not exist. Returning both halves is
# the point of this tool.
HULL_COLUMNS = ['mass', 'volume', 'capacity', 'packagedVolume', 'radius',
                'basePrice', 'portionSize', 'metaLevel', 'techLevel', 'published']
HULL_HINTS = {'capacity': 'cargo hold, m3', 'volume': 'assembled volume, m3',
              'packagedVolume': 'packaged volume, m3', 'mass': 'kg'}

# The attributes people actually ask about, by category. Anything absent on a
# given type is simply skipped; `full=True` returns everything.
_SHIP = [
    'shieldCapacity', 'armorHP', 'hp',
    'shieldEmDamageResonance', 'shieldThermalDamageResonance',
    'shieldKineticDamageResonance', 'shieldExplosiveDamageResonance',
    'armorEmDamageResonance', 'armorThermalDamageResonance',
    'armorKineticDamageResonance', 'armorExplosiveDamageResonance',
    'maxVelocity', 'agility', 'signatureRadius', 'scanResolution',
    'maxTargetRange', 'maxLockedTargets',
    'capacitorCapacity', 'rechargeRate', 'shieldRechargeRate',
    'hiSlots', 'medSlots', 'lowSlots', 'rigSlots', 'upgradeCapacity',
    'turretSlotsLeft', 'launcherSlotsLeft',
    'droneCapacity', 'droneBandwidth',
    'powerOutput', 'cpuOutput', 'warpSpeedMultiplier',
]
_MODULE = [
    'cpu', 'power', 'capacitorNeed', 'duration', 'maxRange', 'falloff',
    'trackingSpeed', 'damageMultiplier', 'speedFactor', 'speedBoostFactor',
    'shieldBonus', 'armorDamageAmount', 'massAddition', 'maxVelocityBonus',
    'signatureRadiusBonus', 'warpScrambleStrength', 'activationBlockedStrenght',
    'metaLevel', 'techLevel', 'hp',
]
_CHARGE = [
    'emDamage', 'thermalDamage', 'kineticDamage', 'explosiveDamage',
    'weaponRangeMultiplier', 'fallofMultiplier', 'aoeCloudSize', 'aoeVelocity',
    'explosionDelay', 'capacityNeeded', 'metaLevel', 'techLevel',
]
_DRONE = [
    'emDamage', 'thermalDamage', 'kineticDamage', 'explosiveDamage',
    'damageMultiplier', 'maxVelocity', 'droneBandwidthUsed', 'entityFlyRange',
    'trackingSpeed', 'optimalSigRadius', 'hp', 'armorHP', 'shieldCapacity',
]
PANELS = {6: _SHIP, 7: _MODULE, 8: _CHARGE, 18: _DRONE}


def _type_row(db, type_id):
    """The `types` row plus group/category names. Returns (dict, categoryID).

    The group table is `groups_` — `groups` is a SQL keyword, so the builder
    renamed it. Exactly the kind of thing a caller should not have to discover.
    """
    cols = {r[1] for r in db.execute('PRAGMA table_info(types)')}
    want = [c for c in HULL_COLUMNS if c in cols]
    row = db.execute(f'SELECT groupID, categoryID, {", ".join(want)} '
                     'FROM types WHERE typeID = ?', (type_id,)).fetchone()
    group_id, category_id = row[0], row[1]
    hull = {}
    for key, value in zip(want, row[2:]):
        if value is None:
            continue
        hint = HULL_HINTS.get(key)
        hull[key] = f'{value} ({hint})' if hint else value
    for label, table, key, ident in (('group', 'groups_', 'groupID', group_id),
                                     ('category', 'categories', 'categoryID', category_id)):
        try:
            got = db.execute(f'SELECT name FROM {table} WHERE {key} = ?', (ident,)).fetchone()
            if got:
                hull[label] = got[0]
        except sqlite3.Error:
            pass                              # a renamed table must not break the lookup
    return hull, category_id


# Attributes worth seeing when choosing between variants of one module. Any
# that a given family does not carry are simply absent from its rows.
LADDER_ATTRS = ['cpu', 'power', 'capacitorNeed', 'duration', 'capacityBonus',
                'damageMultiplier', 'speedFactor', 'maxRange', 'falloff',
                'trackingSpeed', 'armorDamageAmount', 'shieldBonus',
                'armorHPBonusAdd', 'signatureRadiusBonus', 'warpScrambleRange',
                # Rigs: the calibration price and the effect it buys. Without
                # upgradeCost the "N x tech1 vs 1 x tech2" trade is invisible,
                # and that trade usually favours COUNT: two Small Low Friction
                # Nozzle Joints I are -20.7% inertia for 100 calibration and two
                # rig slots, where the II alone is -14.0% for 75 and one slot.
                # Tech 2 is stronger per slot, never 2x stronger.
                'upgradeCost', 'agilityBonus', 'maxVelocityBonus',
                'powerEngineeringOutputBonus', 'cpuOutputBonus2',
                'shieldCapacityBonus', 'armorHpBonus', 'scanResolutionBonus']

# Stacking multipliers applied to the 1st, 2nd, ... strongest of a set of
# same-effect modules. Quoted so a caller can do the count-vs-tier arithmetic
# instead of assuming two rigs are twice one.
STACKING = [1.0, 0.8691, 0.5706, 0.2830, 0.1060]

SIZE_LADDER_CAP = 24

RIG_SIZES = {1: 'small', 2: 'medium', 3: 'large', 4: 'capital'}

# Ordering of the size words that lead a size_class label, so the ladder can be
# cut to the neighbouring classes instead of running to capital.
SIZE_RANK = {'small': 1, 'Small': 1, 'medium': 2, 'Medium': 2,
             'large': 3, 'Large': 3, 'capital': 4, 'Capital': 4, 'XL': 4}


def _specialization(db, type_id):
    """The "* Specialization" skill this item requires, or None.

    Tech 2 turrets and launchers require one (+2% damage per level, +10% at V);
    faction and meta ones do not. That bonus is NOT in `damageMultiplier`, so
    the printed attribute says faction beats tech 2 when the engine says the
    reverse — measured 2026-08-20, Small Focused Pulse Laser II (3.6) does
    291.5 dps where Imperial Navy (3.75) does 276.0, exactly the 10% the
    specialization adds. A ladder that shows the multiplier without the skill
    is actively misleading, so name the skill on the rows that get it.
    """
    rows = db.execute(
        'SELECT d.value FROM type_dogma d JOIN dogma_attributes a '
        'ON a.attributeID = d.attributeID WHERE d.typeID = ? '
        "AND a.name IN ('requiredSkill1', 'requiredSkill2', 'requiredSkill3')",
        (type_id,)).fetchall()
    for (skill_id,) in rows:
        got = db.execute('SELECT name FROM types WHERE typeID = ?',
                         (int(skill_id),)).fetchone()
        if got and got[0].endswith('Specialization'):
            return got[0]
    return None


def _size_class(db, type_id):
    """How this item's size is expressed in the data, or None.

    Turrets and launchers carry it as the required skill ("Small Projectile
    Turret"); rigs carry it as rigSize. Nothing else carries one, and inferring
    size from the millimetres in the name is exactly how a 220mm autocannon
    gets read as small.
    """
    rows = dict(db.execute(
        'SELECT a.name, d.value FROM type_dogma d '
        'JOIN dogma_attributes a ON a.attributeID = d.attributeID '
        "WHERE d.typeID = ? AND a.name IN ('requiredSkill1', 'rigSize')", (type_id,)))
    if 'rigSize' in rows:
        n = int(rows['rigSize'])
        return f'{RIG_SIZES.get(n, n)} rig'
    skill = rows.get('requiredSkill1')
    if skill:
        got = db.execute('SELECT name FROM types WHERE typeID = ?',
                         (int(skill),)).fetchone()
        if got and any(w in got[0] for w in ('Turret', 'Launcher', 'Missile')):
            return got[0]
    return None


def _size_ladder(db, type_id, group_id, meta_group, want):
    """The OTHER families in this item's group — its size ladder.

    `variants` walks one family (tech1, the metas, tech2, faction) and never
    crosses to a neighbouring family, so a caller holding a 125mm autocannon
    cannot see from it that 150mm, 200mm, 250mm and 280mm exist. Measured
    2026-08-20: a graded run correctly found that MEDIUM turrets will not fit a
    Svipul, then dropped to the SMALLEST small turret and shipped a fit doing
    40% less applied damage than the same fit with 200mm guns costing 3 MW
    more. Same shape as fitting a 5MN prop mod to a battleship: right family,
    wrong rung. One representative per family, matched to the tier that was
    asked about so the comparison is like for like.
    """
    fams = {}
    for tid, tname, meta, mg, parent in db.execute(
            'SELECT typeID, name, metaLevel, metaGroupID, variationParentTypeID '
            'FROM types WHERE groupID = ? AND published = 1', (group_id,)):
        fams.setdefault(parent or tid, []).append((tid, tname, meta or 0, mg))
    mine = db.execute('SELECT variationParentTypeID FROM types WHERE typeID = ?',
                      (type_id,)).fetchone()
    mine = (mine and mine[0]) or type_id
    reps = []
    for parent, members in fams.items():
        if parent == mine:
            continue
        same_tier = [m for m in members if m[3] == meta_group]
        rep = max(same_tier or members, key=lambda m: m[2])
        reps.append(rep)
    if not reps:
        return None
    by_id = {r[0]: r for r in reps}
    vals = {r[0]: {} for r in reps}
    ph = ','.join('?' * len(reps))
    aph = ','.join('?' * len(want))
    for tid, aname, value, unit in db.execute(
            'SELECT d.typeID, a.name, d.value, a.unitID FROM type_dogma d '
            'JOIN dogma_attributes a ON a.attributeID = d.attributeID '
            f'WHERE d.typeID IN ({ph}) AND a.name IN ({aph})',
            [r[0] for r in reps] + list(want)):
        human, _ = _interpret(value, unit, db)
        vals[tid][aname] = human if human is not None else value
    here = _size_class(db, type_id)
    # Rigs have a size axis but no useful size LADDER: you cannot fit a medium
    # rig to a frigate, and the sibling families in a rig group are different
    # EFFECTS (cargo, fuel, thrusters), not different rungs of one. The trade a
    # rig caller actually faces is count vs tier, which the meta ladder plus
    # upgradeCost and the stacking curve already answer.
    if here and here.endswith('rig'):
        return None
    rows = []
    for tid, tname, meta, mg in reps:
        if not vals[tid]:
            # placeholder types (abyssal base items) carry no fitting attributes;
            # they would head the list on a null sort key and say nothing
            continue
        entry = {'name': tname, 'metaLevel': meta}
        spec = _specialization(db, tid)
        if spec:
            entry['specialization_skill'] = spec
            entry['damage_not_in_multiplier'] = '+2%/level, +10% at V'
        size = _size_class(db, tid)
        if size:
            entry['size_class'] = size
            entry['same_size_as_yours'] = (size == here)
        entry.update(vals[tid])
        rows.append(entry)
    # same size class first (those are the swaps that keep the hull's bonus and
    # its hardpoints), then cheapest to fit
    def pg(r):
        v = r.get('power')
        try:
            return float(str(v).split()[0])
        except (TypeError, ValueError, IndexError):
            return 0.0
    if not rows:
        return None
    # Keep only the neighbouring size classes. A small-turret question does not
    # need Dual Giga Pulse Laser II at 137,500 MW — measured 2026-08-20, those
    # rows were most of a ~2k-token response and could not be fitted to
    # anything the caller was asking about. One step either way covers the real
    # decision (can I go up a class, should I come down one); two steps never
    # fits the same hull.
    def rank(label):
        # a missing or unlabelled size class must not index an empty split
        words = (label or '').split()
        return SIZE_RANK.get(words[0], None) if words else None

    here_rank = rank(here)
    if here_rank is not None:
        near = [r for r in rows
                if abs((rank(r.get('size_class')) or 99) - here_rank) <= 1]
        dropped = len(rows) - len(near)
        rows = near
    else:
        dropped = 0
    rows.sort(key=lambda r: (not r.get('same_size_as_yours', False), pg(r), r['name']))
    return {'your_size_class': here, 'families': rows[:SIZE_LADDER_CAP],
            'truncated': max(0, len(rows) - SIZE_LADDER_CAP),
            **({'size_classes_omitted': dropped} if dropped else {}),
            'note': 'one representative per family, matched to the tier you asked '
                    'about. Rows flagged same_size_as_yours use the same hardpoints '
                    'and the same hull bonus as what you have — those are the '
                    'straight swaps; the rest change size class and usually grid.'}


@mcp.tool()
def variants(items: list[str], attributes: list = None) -> dict:
    """Both ladders for a module. `variants` is the META ladder — every published variant of the same item (tech 1, compact/enduring/restrained metas, tech 2, storyline, faction) with fitting cost and the attributes that decide between them. `size_ladder` is the OTHER axis: the neighbouring families in the same group, so a 125mm autocannon shows you 150mm/200mm/250mm/280mm and a 5MN prop mod shows you 50MN/100MN/500MN, each tagged with whether it is the same size class as yours. Use this BEFORE naming a module: guessing a name and waiting for "unknown item" costs a round per guess and reveals exactly one name, and picking the wrong RUNG is the commonest fitting error there is — the meta ladder alone will not show it to you. Rig rows carry `upgradeCost` (calibration) so the "two tech 1 vs one tech 2" trade is visible; `stacking` gives the multipliers to do that arithmetic."""
    db = _conn()
    want = attributes or LADDER_ATTRS
    out = []
    for it in items:
        row = db.execute('SELECT typeID, name, variationParentTypeID, groupID, '
                         'metaGroupID FROM types WHERE name = ? COLLATE NOCASE',
                         (str(it),)).fetchone()
        if row is None:
            near = db.execute('SELECT name FROM types WHERE name LIKE ? AND published = 1 '
                              'ORDER BY length(name) LIMIT 5', (f'%{it}%',)).fetchall()
            out.append({'item': str(it), 'error': 'no such type',
                        'did_you_mean': [n for (n,) in near]})
            continue
        type_id, name, parent, group_id, meta_group = row
        parent = parent or type_id
        fam = db.execute(
            'SELECT typeID, name, metaLevel, metaGroupID FROM types '
            'WHERE (variationParentTypeID = ? OR typeID = ?) AND published = 1 '
            'ORDER BY metaLevel, name', (parent, parent)).fetchall()
        rows = []
        for tid, tname, meta, mgroup in fam:
            vals = {}
            for aname, value, unit in db.execute(
                    'SELECT a.name, d.value, a.unitID FROM type_dogma d '
                    'JOIN dogma_attributes a ON a.attributeID = d.attributeID '
                    'WHERE d.typeID = ? AND a.name IN (%s)' % ','.join('?' * len(want)),
                    [tid] + list(want)):
                human, _ = _interpret(value, unit, db)
                vals[aname] = human if human is not None else value
            entry = {'name': tname, 'metaLevel': meta}
            grp = db.execute('SELECT name FROM meta_groups WHERE metaGroupID = ?',
                             (mgroup,)).fetchone() if mgroup else None
            if grp:
                entry['tier'] = grp[0]
            spec = _specialization(db, tid)
            if spec:
                entry['specialization_skill'] = spec
                entry['damage_not_in_multiplier'] = '+2%/level, +10% at V'
            entry.update(vals)
            rows.append(entry)
        entry = {'item': name, 'variants': rows}
        if any('specialization_skill' in r for r in rows) and \
                any('specialization_skill' not in r for r in rows):
            entry['damageMultiplier_warning'] = (
                'damageMultiplier is the BASE attribute and does not include the '
                'specialization skill. Rows carrying `specialization_skill` get a '
                'further +2%/level (+10% at all V) that is not printed here, so a '
                'faction row can show a higher multiplier and still lose to the '
                'tech 2 in the engine. Compare with get_stats, not by eye.')
        ladder = _size_ladder(db, type_id, group_id, meta_group, want)
        if ladder:
            entry['size_ladder'] = ladder
        # A rig's calibration price only means something next to the stacking
        # curve: two tech 1 rigs are never twice one, but they are reliably
        # MORE than a single tech 2, which is the call this data has to support.
        if (_size_class(db, type_id) or '').endswith('rig'):
            entry['stacking'] = STACKING
            entry['stacking_note'] = (
                'same-effect rigs stack penalised at these multipliers, strongest '
                'first. Two tech 1 rigs usually beat one tech 2 if a rig slot and '
                'the calibration are free — compare upgradeCost, not just tier.')
        out.append(entry)
    return {'sde_build': BUILD, 'families': out}


@mcp.tool()
def attrs(items: list, attributes: list = None, full: bool = False) -> dict:
    """START HERE for any question about a named ship, module, charge or drone. One call walks the whole chain — name to typeID, hull columns AND dogma attributes, unit-corrected (resonances as resist %, millisecond attributes as seconds, modifier percents as ±%), each with its raw value beside it. Omit `attributes` for the panel people actually ask about; pass names/IDs for specific ones; `full` for every published attribute. Hull stats like cargo `capacity`, `mass` and `volume` are columns on `types`, NOT dogma attributes — this returns both, which hand-written SQL usually misses."""
    db = _conn()
    if not items:
        raise ValueError('items: at least one type name or typeID')
    out = []
    for it in items:
        if isinstance(it, int) or str(it).isdigit():
            row = db.execute('SELECT typeID, name FROM types WHERE typeID = ?',
                             (int(it),)).fetchone()
        else:
            row = db.execute('SELECT typeID, name FROM types WHERE name = ? COLLATE NOCASE',
                             (str(it),)).fetchone()
        if row is None:
            # substring first, then progressively shorter prefixes, so a
            # trailing typo ('Riftr') still finds 'Rifter'
            near = []
            probe = str(it)
            for pat in [f'%{probe}%'] + [probe[:n] + '%' for n in
                                         range(len(probe) - 1, 2, -1)]:
                near = db.execute(
                    'SELECT name FROM types WHERE name LIKE ? AND published = 1 '
                    'ORDER BY length(name) LIMIT 5', (pat,)).fetchall()
                if near:
                    break
            out.append({'item': str(it), 'error': 'no such type',
                        'did_you_mean': [n for (n,) in near]})
            continue
        type_id, name = row
        hull, category_id = _type_row(db, type_id)
        panel = None if (attributes or full) else PANELS.get(category_id)
        sql = ('SELECT a.attributeID, a.name, a.unitID, d.value FROM type_dogma d '
               'JOIN dogma_attributes a ON a.attributeID = d.attributeID WHERE d.typeID = ?')
        params = [type_id]
        if panel:
            sql += ' AND a.name IN (%s) COLLATE NOCASE' % ','.join('?' * len(panel))
            params += list(panel)
        elif attributes:
            ids = [int(a) for a in attributes if str(a).isdigit()]
            names = [str(a) for a in attributes if not str(a).isdigit()]
            clauses = []
            if ids:
                clauses.append('a.attributeID IN (%s)' % ','.join('?' * len(ids)))
                params += ids
            if names:
                clauses.append('a.name IN (%s) COLLATE NOCASE' % ','.join('?' * len(names)))
                params += names
            sql += ' AND (' + ' OR '.join(clauses) + ')'
        else:
            sql += ' AND a.published = 1'
        vals, hoisted, uncorrected = {}, [], set()
        for attr_id, attr_name, unit_id, value in db.execute(sql, params):
            human, why = _interpret(value, unit_id, db)
            entry = {'raw': value, 'attributeID': attr_id}
            if human is not None:
                entry['value'] = human
            if why:
                # A panel repeats the same sentence for all eight resonances,
                # and once per uncorrected unit on top of that. Say each real
                # correction once, and roll the "no rule" cases into one line.
                if not panel:
                    entry['unit_note'] = why
                elif unit_id is not None and unit_id not in UNITS:
                    uncorrected.add(unit_id)
                elif why not in hoisted:
                    hoisted.append(why)
            vals[attr_name] = entry
        if uncorrected:
            hoisted.append(
                'raw values shown for unitID ' + ', '.join(str(u) for u in sorted(uncorrected))
                + ' — no correction rule in this server; confirm meaning before quoting')
        missing = []
        if attributes:
            asked = {str(a).lower() for a in attributes if not str(a).isdigit()}
            missing = sorted(a for a in asked if a not in {k.lower() for k in vals})
        rec = {'item': name, 'typeID': type_id, 'hull': hull, 'attributes': vals}
        if hoisted:
            rec['unit_notes'] = hoisted
        if missing:
            rec['not_on_this_type'] = missing
        if panel:
            total = db.execute('SELECT COUNT(*) FROM type_dogma d JOIN dogma_attributes a '
                               'ON a.attributeID = d.attributeID '
                               'WHERE d.typeID = ? AND a.published = 1', (type_id,)).fetchone()[0]
            if total > len(vals):
                rec['more'] = (f'{total - len(vals)} further published attributes — name them '
                               'in `attributes`, or pass full=true')
        out.append(rec)
    return {'sde_build': BUILD, 'types': out}


@mcp.tool()
def sde_info() -> dict:
    """SDE build number, the parts present, and the traps this server corrects for."""
    _conn()
    db = _conn()
    unknown = db.execute(
        'SELECT unitID, COUNT(*) FROM dogma_attributes WHERE unitID IS NOT NULL '
        'AND unitID NOT IN (%s) GROUP BY unitID ORDER BY COUNT(*) DESC'
        % ','.join(str(k) for k in UNITS)).fetchall()
    return {
        'sde_build': BUILD,
        **({'MIXED_BUILDS': MIXED_BUILDS,
            'warning': 'the parts are at DIFFERENT builds — this database was '
                       'rebuilt in pieces. Cross-part answers may mix releases; '
                       'rebuild before trusting them.'} if MIXED_BUILDS else {}),
        'parts': [p[8:-7] for p in PARTS],
        'unit_corrections': {str(k): v[1] for k, v in UNITS.items()},
        # This field is the server's own declaration of what it does NOT fix --
        # the mitigation accepted when trap knowledge moved out of docs and into
        # code. It was capped at LIMIT 8 and showed 8 of 38 with no hint that 30
        # were hidden, so the honesty mechanism under-reported by 79%. Now the
        # count is exact and the list is what gets abbreviated, loudly.
        'units_without_a_rule_count': len(unknown),
        'units_without_a_rule': [{'unitID': u, 'attributes': n}
                                 for u, n in unknown[:12]],
        **({'units_without_a_rule_not_listed': len(unknown) - 12}
           if len(unknown) > 12 else {}),
        'not_corrected': NOT_CORRECTED,
        'reminder': 'batch statements into one `query` call; each call re-reads the conversation',
    }


if __name__ == '__main__':
    mcp.run()
