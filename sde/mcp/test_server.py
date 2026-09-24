"""Smoke test for the eve-sde MCP server, driven over real stdio.

    python3 test_server.py --sde <dir with eve-sde-*.sqlite>

Asserts the batch runner, the unit corrections (the traps that measured eval
subjects got wrong), and reports response sizes in tokens.
"""
import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))


def tokens(obj):
    return len(json.dumps(obj)) // 4


def unwrap(result):
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get('result', sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


def deployed_command(sde):
    """Launch the server exactly the way `.mcp.json` does.

    Launching with `sys.executable` hid a real outage: the test ran under
    layer 2's virtualenv (which has the `mcp` SDK) while `.mcp.json` used a
    bare `python3` (which does not), so the server failed to connect in every
    real session while the test stayed green. Read the command from the
    config so the two can never drift again.
    """
    cfg = os.path.join(os.path.dirname(os.path.dirname(HERE)), '.mcp.json')
    with open(cfg) as fh:
        entry = json.load(fh)['mcpServers']['eve-sde']
    args = [a if a != '.' else sde for a in entry['args']]
    return entry['command'], args


async def main(sde):
    command, args = deployed_command(sde)
    print(f'launching as .mcp.json does: {command} {" ".join(args)}')
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            schema = [{'name': t.name, 'description': t.description,
                       'inputSchema': t.input_schema} for t in tools.tools]
            print(f'{len(tools.tools)} tools; standing schema ~{tokens(schema)} tokens')

            async def call(_tool, **kw):
                out = unwrap(await s.call_tool(_tool, kw))
                print(f'  {_tool:10} -> ~{tokens(out)} tokens')
                return out

            # batch: three statements as separate list elements, one round,
            # one bad statement isolated
            q = await call('query', statements=[
                "-- rifter hull\nSELECT name, mass FROM types WHERE name = 'Rifter'",
                "-- scram strengths\n"
                'SELECT t.name, d.value FROM type_dogma d JOIN types t ON t.typeID = d.typeID\n'
                'JOIN dogma_attributes a ON a.attributeID = d.attributeID\n'
                "WHERE a.name = 'warpScrambleStrength' AND t.name LIKE 'Warp Scrambler%'",
                '-- deliberately broken\nSELECT * FROM no_such_table',
            ])
            r = q['results']
            assert len(r) == 3, r
            assert r[0]['label'] == 'rifter hull' and r[0]['data'][0][0] == 'Rifter', r[0]
            assert r[1]['rows'] == 2, r[1]
            assert 'error' in r[2] and r[0].get('data'), 'a bad statement must not kill the batch'
            # the table hint is itself capped; 107 tables shown as 40 with no
            # note reads as "those are the tables"
            hint = r[2]
            if 'tables_available' in hint and len(hint['tables_available']) == 40:
                assert hint.get('tables_not_listed', 0) > 0, hint
            assert q['sde_build'], q
            # the raw-value lint fires on statement 2 (selects `value` by attribute)
            assert any('unitID' in n for n in r[1].get('notes', [])), r[1]

            # the default panel: one call must answer a hull question whole,
            # hull columns included. Gen-11 burned 13 rounds on "how much
            # cargo" because `capacity` is a types column, not a dogma attr.
            pan = await call('attrs', items=['Iteron Mark V'])
            t0 = pan['types'][0]
            assert t0['hull']['capacity'].startswith('5800'), t0['hull']
            assert t0['hull']['group'] == 'Hauler', t0['hull']
            assert t0['hull']['category'] == 'Ship', t0['hull']
            assert 20 < len(t0['attributes']) < 40, len(t0['attributes'])
            assert 'more' in t0, 'the panel must say what it left out'
            # the eight resonances share one note, not eight copies
            assert sum(1 for n in t0['unit_notes'] if 'resonance' in n) == 1, t0['unit_notes']
            assert sum(1 for n in t0['unit_notes'] if 'no correction rule' in n) == 1, t0['unit_notes']
            # non-ship categories get their own panels
            for name, cat in (('Warp Scrambler II', 'Module'),
                              ('Hammerhead II', 'Drone')):
                one = (await call('attrs', items=[name]))['types'][0]
                assert one['hull']['category'] == cat, one['hull']
                assert one['attributes'], name

            # the meta ladder: pick a variant from a list instead of guessing a
            # name and waiting for "unknown item" — that loop costs a round per
            # guess and never reveals the cheaper compact sitting beside the II
            lad = await call('variants', items=['Medium Shield Extender II'])
            fam = lad['families'][0]['variants']
            assert len(fam) > 4, fam
            names = [v['name'] for v in fam]
            assert 'Medium F-S9 Regolith Compact Shield Extender' in names, names
            assert any(v.get('tier') == 'Faction' for v in fam), fam
            assert all('cpu' in v for v in fam), fam

            # The SIZE ladder, which the meta ladder cannot show: `variants` walks
            # one family and never crosses to the next, so a caller holding a
            # 125mm autocannon has no way to learn 150/200/280 exist. Measured
            # 2026-08-20: a graded run found mediums would not fit its hull and
            # dropped to the SMALLEST small gun, shipping 40% less applied damage
            # than the same fit with guns costing 3 MW more.
            gun = (await call('variants', items=['Republic Fleet 125mm Autocannon']))
            sl = gun['families'][0]['size_ladder']
            assert sl['your_size_class'] == 'Small Projectile Turret', sl
            by_name = {f['name']: f for f in sl['families']}
            same = [f for f in sl['families'] if f.get('same_size_as_yours')]
            assert any('200mm' in n for n in by_name), list(by_name)
            assert len(same) >= 3, same
            # ...and the size class must be read from the data, not the millimetres
            # in the name: 220mm is MEDIUM and 280mm is small.
            assert not by_name['Domination 220mm Autocannon']['same_size_as_yours']
            assert by_name['Domination 280mm Howitzer Artillery']['same_size_as_yours']
            # every row carries what decides the swap
            assert all('power' in f for f in sl['families']), sl['families'][0]

            # Rigs get no size ladder — you cannot fit a medium rig to a frigate
            # and their sibling families are different EFFECTS, not rungs. What a
            # rig caller needs is count-vs-tier, so calibration and the stacking
            # curve ride along instead.
            rig = (await call('variants', items=['Small Low Friction Nozzle Joints II']))
            fam = rig['families'][0]
            assert 'size_ladder' not in fam, fam.keys()
            assert fam['stacking'][:2] == [1.0, 0.8691], fam['stacking']
            costs = {v['name']: v['upgradeCost'] for v in fam['variants']}
            assert costs['Small Low Friction Nozzle Joints I'] == 50, costs
            assert costs['Small Low Friction Nozzle Joints II'] == 75, costs
            # two tech 1 beat one tech 2 here, and the data must make that
            # arithmetic possible: -11.7 twice (stacked) vs -14.0 once
            bonus = {v['name']: v['agilityBonus'] for v in fam['variants']}
            t1, t2 = (bonus['Small Low Friction Nozzle Joints I'],
                      bonus['Small Low Friction Nozzle Joints II'])
            two_t1 = (1 + t1 / 100) * (1 + t1 / 100 * fam['stacking'][1])
            assert two_t1 < (1 + t2 / 100), (two_t1, t2)

            # `sql` is the name callers reach for; taking it saves a round
            aliased = await call('query', sql="SELECT name FROM types WHERE typeID = 587")
            assert aliased['results'][0]['data'] == [['Rifter']], aliased
            assert 'note' in aliased, 'the string form must nudge toward the list'

            # unit corrections — the traps measured subjects actually got wrong
            a = await call('attrs', items=['Rifter'],
                           attributes=['shieldEmDamageResonance', 'shieldRechargeRate'])
            at = a['types'][0]['attributes']
            res = at['shieldEmDamageResonance']
            assert res['raw'] == 1.0 and res['value'] == '0.0%', res
            rech = at['shieldRechargeRate']
            assert rech['raw'] == 625000 and rech['value'] == '625.0 s', rech

            # a genuinely inverted, non-obviously-named resistance
            web = await call('attrs', items=['Erebus'], attributes=[2115])
            v = list(web['types'][0]['attributes'].values())[0]
            assert v['value'] == '80.0%', v   # titans resist webs 80%, raw 0.2

            # unknown type suggests neighbours instead of failing blind
            bad = await call('attrs', items=['Riftr'])
            assert bad['types'][0].get('did_you_mean'), bad

            info = await call('sde_info')
            assert '108' in info['unit_corrections'], info
            # This field declares what the server does NOT correct — the
            # mitigation taken when trap knowledge moved from docs into code.
            # It was capped at LIMIT 8 and reported 8 of 38 silently, so the
            # honesty mechanism itself under-reported by 79%.
            assert info['units_without_a_rule_count'] >= len(info['units_without_a_rule'])
            if info['units_without_a_rule_count'] > len(info['units_without_a_rule']):
                assert info['units_without_a_rule_not_listed'] == (
                    info['units_without_a_rule_count']
                    - len(info['units_without_a_rule'])), info
            # the parts are separate files from separate build runs; a set that
            # disagrees with itself must say so rather than report one part's
            # number as though it were the database's
            if info.get('MIXED_BUILDS'):
                assert 'DIFFERENT builds' in info['warning'], info
                assert info['sde_build'] == max(info['MIXED_BUILDS'].values()), info
                print(f"  NOTE: mixed SDE build {info['MIXED_BUILDS']}")
            print(f"\nsde_build: {info['sde_build']} | parts: {','.join(info['parts'])}"
                  f" | all assertions passed")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sde', default=os.environ.get('EVE_SDE_DIR', '.'))
    asyncio.run(main(ap.parse_args().sde))
