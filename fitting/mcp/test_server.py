"""Smoke test: drive the MCP server over real stdio like a Claude session would.

    <venv>/bin/python test_server.py --pyfa <pyfa-checkout>

Asserts the full tool surface works and reports the token economics: the
standing schema overhead and the size of every response. Panel numbers are
checked against the pinned reference battery.
"""
import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.join(os.path.dirname(HERE), 'spike')


def tokens(obj):
    return len(json.dumps(obj)) // 4


def unwrap(result):
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    if result.structured_content is not None:
        sc = result.structured_content
        return sc.get('result', sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


async def main(pyfa):
    ref = json.load(open(os.path.join(SPIKE, 'reference', 'rifter-ac-brawler.json')))
    eft_text = open(os.path.join(SPIKE, 'reference', 'battery.eft')).read()
    rifter_eft = eft_text.split('\n\n\n')[0]

    params = StdioServerParameters(
        command=sys.executable, args=[os.path.join(HERE, 'server.py'), '--pyfa', pyfa])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            tools = await s.list_tools()
            # Private helpers must not leak onto the tool surface: _sde_build
            # shipped as a callable tool because it inherited the decorator
            # stack of the function it was inserted above, and every leaked
            # tool costs standing schema on every round of every session.
            leaked = [t.name for t in tools.tools if t.name.startswith('_')]
            assert not leaked, leaked
            schema_json = [{'name': t.name, 'description': t.description,
                            'inputSchema': t.input_schema} for t in tools.tools]
            print(f'{len(tools.tools)} tools; standing schema overhead ~{tokens(schema_json)} tokens')

            async def call(_tool, **kw):
                out = unwrap(await s.call_tool(_tool, kw))
                print(f'  {_tool:14} -> ~{tokens(out)} tokens')
                return out

            # import + stats vs pinned reference
            imp = await call('import_fit', eft=rifter_eft)
            fid = imp['fit_id']
            # ids are boot-salted (f<salt><n>) so stale handles from before a
            # server restart fail loudly instead of resolving to recycled ids
            import re as _re
            assert _re.fullmatch(r'f[a-z]{2}\d+', fid), fid
            assert imp['problems'] == [], imp['problems']
            assert imp['slots']['low'][1] == 4 and imp['slots']['high'][1] == 3, imp['slots']
            assert imp['hardpoints']['turret'] == [3, 3], imp.get('hardpoints')
            # the mutate-then-stats pair is folded: import/create/clone/edit
            # return the panel inline, so a round is not spent on get_stats
            assert 'stats' in imp and imp['stats']['offense']['dps'] > 0, list(imp)
            # cargo/ammo lines: Cargo takes the item only and sets `amount`
            # after, so passing it positionally made EVERY EFT carrying ammo
            # fail to import — which killboard and pyfa exports routinely do.
            cargo_fit = await call('import_fit', stats=False, eft=(
                '[Rifter, cargo]\n'
                '200mm AutoCannon II, Republic Fleet EMP S\n'
                'Republic Fleet EMP S x2000\n'
                'Nanite Repair Paste x50\n'))
            assert cargo_fit.get('fit_id'), cargo_fit

            # a quantity suffix on a MODULE is the commonest paste error and
            # must name itself, not die inside pyfa's drone constructor
            try:
                await call('import_fit', stats=False,
                           eft='[Rifter, bad]\n200mm AutoCannon II x3\n')
                raise AssertionError('module xN must be rejected')
            except RuntimeError as exc:
                assert 'own line' in str(exc) or 'once per module' in str(exc), exc

            # hull sweep: name a CLASS, not remembered candidates. The whole
            # point is that the caller never enumerates — a hull picked from a
            # recalled shortlist and then made to work is the commonest way a
            # fit answer goes wrong.
            sw = await call('sweep_hulls', fit_id=fid, group='Assault Frigate')
            assert len(sw['hulls']) > 8, sw['hulls']
            ok = [h for h in sw['hulls'] if 'problems' not in h and 'error' not in h]
            assert ok, 'at least one hull must carry the fit legally'
            # bonuses ride along: they are already applied in the numbers, and
            # are shown so a low rank reads as "wrong weapons for this hull"
            assert any(h.get('bonuses') for h in sw['hulls']), sw['hulls'][0]
            assert sw['ranked_by'] == 'offense.dps', sw['ranked_by']
            # a class sweep enumerates the whole group, tournament prizes and
            # event hulls included — Geri and Malice rank fine and cannot be
            # bought. Market-group ancestry is the only hard signal the data
            # carries (metaGroup false-positives on Imperial Issue hulls and
            # misses Hydra/Tiamat entirely), so flag the branch and let the
            # reader judge rather than guessing provenance.
            avail = {h['hull'] for h in sw['hulls'] if h.get('availability')}
            assert {'Geri', 'Malice', 'Utu'} <= avail, avail
            # ...and it must not smear onto the mainline hulls of the class
            assert not ({'Enyo', 'Jaguar', 'Wolf', 'Harpy'} & avail), avail
            try:
                await call('sweep_hulls', fit_id=fid, group='Not A Real Group')
                raise AssertionError('unknown group must be rejected')
            except RuntimeError as exc:
                assert 'group' in str(exc).lower(), exc

            # the binding constraint, and the pointer out of it. Measured
            # 2026-08-19: a Confessor sat at 98% CPU with powergrid to spare
            # and spent two rig slots on POWERGRID rigs — solving the
            # constraint that was not binding with the exact slots that would
            # have fixed the one that was. And when a fit only fits by
            # dropping modules, the real question is the hull, so the advisory
            # names the sweep call verbatim: prose asking for the behaviour
            # did not produce it, a ready-to-paste call is what gets used.
            conf = await call('import_fit', eft=(
                '[Confessor, cpu-bound]\n'
                'Damage Control II\nHeat Sink II\nHeat Sink II\n'
                'Small Armor Repairer II\nNanofiber Internal Structure II\n'
                '\n'
                '5MN Y-T8 Compact Microwarpdrive\nWarp Scrambler II\n'
                'Fleeting Compact Stasis Webifier\n'
                '\n'
                'Small Focused Pulse Laser II, Imperial Navy Multifrequency S\n'
                'Small Focused Pulse Laser II, Imperial Navy Multifrequency S\n'
                'Small Focused Pulse Laser II, Imperial Navy Multifrequency S\n'
                'Small Focused Pulse Laser II, Imperial Navy Multifrequency S\n'
                '\n'
                'Small Ancillary Current Router I\nSmall Ancillary Current Router I\n'
                'Small Auxiliary Nano Pump I\n'))
            adv = (await call('get_stats', fit_id=conf['fit_id'])).get('advisories', [])
            binding = [a for a in adv if 'binding constraint' in a]
            assert binding and binding[0].startswith('cpu is'), adv
            assert 'sweep_hulls(fit_id, group="Tactical Destroyer")' in binding[0], binding
            # ...and it must name the MODULE, not just the resource. Measured
            # 2026-08-20: handed "rig effort should target powergrid", a graded
            # run restated the sentence, reasoned about a DAMAGE rig, left the
            # rig slot empty and shipped the weaker fit. A resource is not an
            # action; a named rig with a price is.
            assert 'Processor Overclocking Unit' in binding[0], binding
            assert 'calibration' in binding[0], binding
            assert 'solves nothing' in binding[0], binding
            assert any('not the binding constraint' in a for a in adv), adv
            await call('delete_fit', fit_id=conf['fit_id'])

            # A loadout that does not physically fit reaches the same pointer by
            # the other road: any capacity violation means the modules and the
            # hull were picked independently, whichever resource ran out.
            # a hardpoint violation with grid to spare: nothing is "binding",
            # the hull simply cannot hold these guns
            over = await call('import_fit', eft=(
                '[Rifter, over]\nDamage Control II\n\n'
                '1MN Afterburner II\n\n'
                + '125mm Gatling AutoCannon II\n' * 4))
            # the overrun answer must carry the per-module costs with it: a
            # graded run spent SIX rounds on module_attrs, one module at a time,
            # to learn which module was expensive
            pgfat = await call('import_fit', eft=(
                '[Svipul, over]\nDamage Control II\n\n'
                '5MN Y-T8 Compact Microwarpdrive\n\n'
                + 'Republic Fleet 220mm Autocannon\n' * 4))
            bd = pgfat['fitting_breakdown']['powergrid']
            assert bd['columns'] == ['item', 'count', 'each', 'total'], bd
            assert bd['rows'][0][0] == 'Republic Fleet 220mm Autocannon', bd['rows']
            assert bd['rows'][0][1] == 4 and bd['rows'][0][3] > 300, bd['rows'][0]
            assert bd['rows'] == sorted(bd['rows'], key=lambda r: -r[3]), bd['rows']
            assert 'cpu' not in pgfat['fitting_breakdown'], 'only the over resource'
            await call('delete_fit', fit_id=pgfat['fit_id'])

            oadv = over['stats'].get('advisories', [])
            assert any('does not fit the Rifter as-is' in a and 'over by' in a
                       and 'sweep_hulls(' in a for a in oadv), oadv
            # ...and never both roads at once
            assert sum('sweep_hulls(' in a for a in oadv) == 1, oadv
            await call('delete_fit', fit_id=over['fit_id'])

            # The suggested call must RUN. `sweep_hulls` caps at 20 hulls by
            # default and the Frigate group publishes 51, so an unsized
            # suggestion spends its round discovering the tool is fussy
            # instead of getting the answer — the advisory sizes it itself.
            big = await call('import_fit', eft=(
                '[Rifter, tight]\nDamage Control II\nGyrostabilizer II\n\n'
                '1MN Afterburner II\nWarp Scrambler II\nStasis Webifier II\n\n'
                + '200mm AutoCannon II, Republic Fleet EMP S\n' * 3))
            for a in big['stats'].get('advisories', []):
                m = _re.search(r'sweep_hulls\(fit_id, group="([^"]+)"(?:, limit=(\d+))?\)', a)
                if not m:
                    continue
                kw = {'fit_id': big['fit_id'], 'group': m.group(1)}
                if m.group(2):
                    kw['limit'] = int(m.group(2))
                verbatim = await call('sweep_hulls', **kw)
                assert kw.get('limit') == 51, kw    # Frigate: over the default
                assert len(verbatim['hulls']) == 51, len(verbatim['hulls'])
                assert not [h for h in verbatim['hulls'] if h.get('error')], verbatim['hulls']
                break
            else:
                raise AssertionError('no advisory named the sweep on the rifter fit')
            await call('delete_fit', fit_id=big['fit_id'])

            # ---- charge selection, the thing raw dps hides ----------------
            # Measured 2026-08-20: an answer shipped Multifrequency S tested at
            # one range. Scorch S beats it at EVERY range on this hull while
            # showing LESS paper dps, so only a swept applied number finds it.
            laser = await call('import_fit', stats=False, eft=(
                '[Confessor, crystals]\nHeat Sink II\nHeat Sink II\n\n'
                '5MN Microwarpdrive II\n\n'
                + 'Small Focused Pulse Laser II, Multifrequency S\n' * 4))
            lid = laser['fit_id']
            far = await call('applied_dps', fit_id=lid, distance_km=9,
                             target={'sig_m': 62, 'speed_ms': 100})
            tab = far['charges']['Small Focused Pulse Laser II']
            assert tab['evaluated'] > 20, tab['evaluated']
            assert not tab.get('not_evaluated'), 'silent truncation'
            names = [r['charge'] for r in tab['ranked']]
            assert names[0] == 'Scorch S', names
            assert any(r.get('loaded') for r in
                       (await call('applied_dps', fit_id=lid, distance_km=1,
                                   target={'sig_m': 62, 'speed_ms': 100})
                        )['charges']['Small Focused Pulse Laser II']['ranked']), 'loaded unmarked'
            assert 'better_than_loaded' in tab, tab
            # the swept number must equal an independently built fit, or the
            # sweep is measuring something other than what it claims
            direct = await call('import_fit', stats=False, eft=(
                '[Confessor, crystals]\nHeat Sink II\nHeat Sink II\n\n'
                '5MN Microwarpdrive II\n\n'
                + 'Small Focused Pulse Laser II, Scorch S\n' * 4))
            solo = await call('applied_dps', fit_id=direct['fit_id'], distance_km=9,
                              charges=False, target={'sig_m': 62, 'speed_ms': 100})
            swept = next(r['dps_applied'] for r in tab['ranked'] if r['charge'] == 'Scorch S')
            assert abs(solo['dps_applied'] - swept) < 0.05, (solo['dps_applied'], swept)
            await call('delete_fit', fit_id=direct['fit_id'])

            # ---- implants and boosters, measured not guessed ---------------
            # A missile hardwiring was recommended for an all-turret fit. The
            # only defence is fitting the thing and looking at the panel.
            dead = await call('pilot_effects', fit_id=lid, kind='implants',
                              search='Target Navigation Prediction')
            assert dead['considered'] > 0 and dead['moved_a_number'] == 0, dead
            # a fit with an actual tank, so repair and capacitor boosters have
            # something to move — the bare gun fit above cannot show them
            tanked = await call('import_fit', stats=False, eft=(
                '[Confessor, tanked]\nHeat Sink II\nSmall Armor Repairer II\n'
                'Multispectrum Energized Membrane II\n\n5MN Microwarpdrive II\n\n'
                + 'Small Focused Pulse Laser II, Multifrequency S\n' * 4))
            # ...and the mirror failure: a candidate that DOES work must not be
            # filtered out before it is measured. Slots 1-5 were excluded as
            # "attribute implants"; 15 of the 18 Snake implants live there, so
            # asking about Snakes returned "0 moved a number" from the very tool
            # built to stop implants being named from memory.
            snakes = await call('pilot_effects', fit_id=lid, kind='implants',
                                search='Snake', limit=4)
            assert snakes['considered'] > 10, snakes['considered']
            assert snakes['moved_a_number'] > 5, snakes
            assert any(r['slot'] <= 5 for r in snakes['results']), snakes['results']
            # Ascendancy is warp speed and ONLY warp speed — a transcript sold it
            # as "align/warp speed", and the panel could not contradict that
            # until warp speed was one of the measured numbers.
            asc = await call('pilot_effects', fit_id=lid, kind='implants',
                             search='Ascendancy', limit=3)
            assert asc['moved_a_number'] > 5, asc
            assert all(set(r['deltas']) == {'warp_speed_aus'} for r in asc['results']), asc

            drugs = await call('pilot_effects', fit_id=tanked['fit_id'],
                               kind='boosters', limit=5)
            assert drugs['moved_a_number'] > 3, drugs
            assert all(r['deltas'] for r in drugs['results']), 'zero-delta row listed'
            assert any(r.get('may_roll_side_effects') for r in drugs['results']), drugs
            assert drugs['results'] == sorted(
                drugs['results'], key=lambda r: -r['best_relative_gain_pct']), 'unranked'
            await call('delete_fit', fit_id=tanked['fit_id'])

            # ---- the sweep must be able to change the weapon system ---------
            # Without this it asks "which hull carries THIS loadout", which is
            # biased to the hull the fit was built on: a laser fit scored the
            # Jackdaw at "turret hardpoints over by 4" and every off-race hull
            # at 55% of the Confessor, saying nothing about the hulls.
            plain = await call('sweep_hulls', fit_id=lid, group='Tactical Destroyer')
            jack = next(h for h in plain['hulls'] if h['hull'] == 'Jackdaw')
            assert any('turret hardpoints' in p for p in jack['problems']), jack
            armed = await call('sweep_hulls', fit_id=lid, group='Tactical Destroyer',
                               adapt=True)
            jack2 = next(h for h in armed['hulls'] if h['hull'] == 'Jackdaw')
            assert 'Rocket Launcher' in jack2['adapted']['weapons'], jack2
            assert not any('turret hardpoints' in p
                           for p in jack2.get('problems', [])), jack2
            # the source hull is already armed the way it wants: left alone, so
            # it stays an honest baseline in its own sweep
            conf = next(h for h in armed['hulls'] if h['hull'] == 'Confessor')
            assert 'adapted' not in conf, conf
            # tier follows the fit, not the price list — a caller who excluded
            # officer modules must not be handed Makra's Modified anything
            for h in armed['hulls']:
                if 'adapted' in h:
                    assert 'Modified' not in h['adapted']['weapons'], h['adapted']
                    assert h['adapted']['rungs_tried'] >= 1, h['adapted']
            await call('delete_fit', fit_id=lid)

            # aliases: the names callers reach for must not cost a round
            clone_id = (await call('clone_fit', fit_id=fid, stats=False))['fit_id']
            cmp_aliased = await call('compare_fits', fit_a=fid, fit_b=clone_id)
            assert 'diffs' in cmp_aliased, cmp_aliased
            defaults = await call('module_attrs', fit_id=fid, item='150mm Light AutoCannon II')
            assert defaults['modules'][0]['attrs'], defaults

            # Align time is quoted for the one manoeuvre it describes, and you
            # do that manoeuvre with the prop OFF — the mass a running MWD adds
            # is precisely why. Measured 2026-08-20: a graded answer quoted
            # 4.98 s for an align that is really 3.67 s.
            nav = (await call('get_stats', fit_id=fid))['navigation']
            assert nav['align_time_prop_off_s'] < nav['align_time_s'], nav
            note = [n for n in (await call('get_stats', fit_id=fid)).get('notes', [])
                    if 'prop mod RUNNING' in n]
            assert note and 'You align with the prop OFF' in note[0], note

            # A bare "unknown item" costs a round and reveals nothing: the caller
            # guesses again out of the same memory that produced the miss.
            for eft_bad, want in (
                    ('[Rifter, x]\nRapid Light Missile Launcher II, Rage Light Missile\n',
                     'Light Missile'),
                    ('[Rifetr, x]\n200mm AutoCannon II\n', 'Rifter')):
                try:
                    await call('import_fit', eft=eft_bad, stats=False)
                    raise AssertionError('must reject: ' + eft_bad)
                except RuntimeError as exc:
                    assert 'did you mean' in str(exc), exc
                    assert want in str(exc), exc

            lean = await call('import_fit', eft=rifter_eft, stats=False)
            assert 'stats' not in lean, 'stats=False must return the id alone'
            await call('delete_fit', fit_id=lean['fit_id'])
            stats = await call('get_stats', fit_id=fid)
            # every fit-scoped response echoes the ship, so a wrong/stale id
            # is visible at a glance
            assert stats['ship'] == 'Rifter', stats.get('ship')
            assert imp['stats']['offense']['dps'] == stats['offense']['dps'], 'folded panel must match'
            assert stats['offense']['dps'] == round(ref['stats']['offense']['dps_burst'], 1), stats['offense']
            assert stats['defense']['ehp']['total'] == round(ref['stats']['defense']['ehp_total_uniform']), stats['defense']['ehp']
            assert 'reps_hps' in stats['defense'], 'AAR rep rate missing'

            # damage profile changes EHP
            em = await call('get_stats', fit_id=fid, profile={'em': 100})
            assert em['defense']['ehp']['total'] != stats['defense']['ehp']['total']

            # edit: swap ammo -> dps moves; offline MWD -> speed drops
            base_speed = stats['navigation']['max_velocity_ms']
            await call('edit_fit', fit_id=fid, ops=[
                {'op': 'charge', 'item': '150mm Light AutoCannon II', 'charge': 'Barrage S'},
                {'op': 'state', 'item': '5MN Y-T8 Compact Microwarpdrive', 'state': 'online'}])
            s2 = await call('get_stats', fit_id=fid)
            assert s2['offense']['dps'] != stats['offense']['dps']
            assert s2['navigation']['max_velocity_ms'] < base_speed / 3

            # alpha skills weaken the fit — and switching back must fully restore:
            # the alpha preset once mutated the shared All-5 character, silently
            # turning every fit alpha for the rest of the session
            await call('set_skills', fit_id=fid, preset='alpha')
            s3 = await call('get_stats', fit_id=fid)
            assert s3['offense']['dps'] < s2['offense']['dps'], (s3['offense'], s2['offense'])
            await call('set_skills', fit_id=fid, preset='all-0')
            s3z = await call('get_stats', fit_id=fid)
            assert s3z['offense']['dps'] < s3['offense']['dps'], 'all-0 must be below alpha'
            await call('set_skills', fit_id=fid, preset='all-5')
            s3b = await call('get_stats', fit_id=fid)
            assert s3b['offense']['dps'] == s2['offense']['dps'], 'all-5 not restored after alpha'
            fresh = await call('import_fit', eft=rifter_eft)
            fresh_stats = await call('get_stats', fit_id=fresh['fit_id'])
            assert fresh_stats['offense']['dps'] == stats['offense']['dps'], \
                'alpha preset leaked into a freshly imported fit'
            await call('delete_fit', fit_id=fresh['fit_id'])

            # clone + compare: the diff names what changed
            c = await call('clone_fit', fit_id=fid, name='variant')
            await call('edit_fit', fit_id=c['fit_id'], ops=[
                {'op': 'remove', 'item': 'Gyrostabilizer II'},
                {'op': 'add', 'item': 'Damage Control II'}])
            cmp_out = await call('compare_fits', fit_id_a=fid, fit_id_b=c['fit_id'])
            assert any('dps' in k for k in cmp_out['diffs']), cmp_out['diffs']

            # validation catches hardpoint overflow
            await call('edit_fit', fit_id=c['fit_id'], ops=[
                {'op': 'add', 'item': '150mm Light AutoCannon II'}])
            v = await call('validate_fit', fit_id=c['fit_id'])
            assert not v['legal'] and any('turret' in p for p in v['problems']), v

            # export round-trips through import
            eft_out = await call('export_fit', fit_id=fid)
            eft_str = eft_out if isinstance(eft_out, str) else eft_out['result']
            re_imp = await call('import_fit', eft=eft_str)
            assert re_imp['ship'] == 'Rifter'

            # environment: C5 wolf-rayet multiplies small-turret dps; clearing restores
            base = await call('get_stats', fit_id=fid)
            env = await call('set_env', fit_id=fid, effect='Class 5 Wolf Rayet Effects')
            assert env['env'] == 'Class 5 Wolf Rayet Effects'
            wr = await call('get_stats', fit_id=fid)
            ratio = wr['offense']['dps'] / base['offense']['dps']
            assert 2.5 < ratio < 2.8, f'WR dps ratio {ratio}'
            await call('set_env', fit_id=fid, effect='')
            back = await call('get_stats', fit_id=fid)
            assert back['offense']['dps'] == base['offense']['dps'], 'env did not clear'
            try:
                await call('set_env', fit_id=fid, effect='Wolf Rayet')
                raise AssertionError('fuzzy env name should error with candidates')
            except RuntimeError as e:
                assert 'Class 1 Wolf Rayet Effects' in str(e), e

            # command bursts: shield burst raises shield cap; strongest booster wins
            subj = await call('import_fit', eft='[Caracal, subj]\nLarge Shield Extender II')
            drake = await call('import_fit', eft='[Drake, boostA]\nShield Command Burst II, Shield Extension Charge')
            vult = await call('import_fit', eft='[Vulture, boostB]\nShield Command Burst II, Shield Extension Charge')
            s_base = await call('get_stats', fit_id=subj['fit_id'])
            await call('set_booster', fit_id=subj['fit_id'], booster_fit_ids=[drake['fit_id']])
            s_one = await call('get_stats', fit_id=subj['fit_id'])
            r1 = s_one['defense']['hp']['shield'] / s_base['defense']['hp']['shield']
            assert 1.10 < r1 < 1.20, f'drake burst ratio {r1}'
            await call('set_booster', fit_id=subj['fit_id'],
                       booster_fit_ids=[drake['fit_id'], vult['fit_id']])
            s_two = await call('get_stats', fit_id=subj['fit_id'])
            await call('set_booster', fit_id=subj['fit_id'], booster_fit_ids=[vult['fit_id']])
            s_vult = await call('get_stats', fit_id=subj['fit_id'])
            assert s_two['defense']['hp']['shield'] == s_vult['defense']['hp']['shield'], \
                'two same bursts must not stack (strongest wins)'
            assert s_vult['defense']['hp']['shield'] > s_one['defense']['hp']['shield'], \
                'command-ship hull must scale the burst'

            # projected fits: a web halves speed; a neut kills the cap; [] restores
            vic = await call('import_fit', eft='[Rifter, victim]\n5MN Y-T8 Compact Microwarpdrive')
            v_base = await call('get_stats', fit_id=vic['fit_id'])
            ewar = await call('import_fit', eft='[Vigil, ew]\nStasis Webifier I')
            await call('set_projected', fit_id=vic['fit_id'], projector_fit_ids=[ewar['fit_id']])
            v_web = await call('get_stats', fit_id=vic['fit_id'])
            wr_ratio = v_web['navigation']['max_velocity_ms'] / v_base['navigation']['max_velocity_ms']
            assert 0.45 < wr_ratio < 0.55, f'projected web ratio {wr_ratio}'
            neut = await call('import_fit', eft='[Curse, neut]\nMedium Energy Neutralizer II')
            await call('set_projected', fit_id=vic['fit_id'],
                       projector_fit_ids=[ewar['fit_id'], neut['fit_id']])
            v_neut = await call('get_stats', fit_id=vic['fit_id'])
            assert not v_neut['capacitor']['stable'] and \
                v_neut['capacitor']['lasts_s'] < v_base['capacitor'].get('lasts_s', 1e9), v_neut['capacitor']
            # projection at range: inside optimal = full web; far beyond
            # optimal + 3x falloff = no effect; the curve names the band
            ma_web = await call('module_attrs', fit_id=ewar['fit_id'],
                                item='Stasis Webifier I', attrs=['maxRange', 'falloffEffectiveness'])
            web_opt_km = ma_web['modules'][0]['attrs']['maxRange'] / 1000
            await call('set_projected', fit_id=vic['fit_id'],
                       projector_fit_ids=[{'fit_id': ewar['fit_id'], 'range_km': web_opt_km / 2}])
            v_in = await call('get_stats', fit_id=vic['fit_id'])
            assert v_in['navigation']['max_velocity_ms'] == v_web['navigation']['max_velocity_ms'], \
                'inside optimal must equal zero-range strength'
            await call('set_projected', fit_id=vic['fit_id'],
                       projector_fit_ids=[{'fit_id': ewar['fit_id'], 'range_km': web_opt_km * 8}])
            v_out = await call('get_stats', fit_id=vic['fit_id'])
            assert v_out['navigation']['max_velocity_ms'] == v_base['navigation']['max_velocity_ms'], \
                'far beyond falloff must be no effect'
            g_ew = await call('graph', fit_id=ewar['fit_id'], kind='ewar_vs_range',
                              item='Stasis Webifier I')
            assert g_ew['summary']['optimal_km'] == web_opt_km, g_ew['summary']
            assert g_ew['points'][0][1] == 100.0 and g_ew['points'][-1][1] < 5, g_ew['points'][-3:]
            await call('set_projected', fit_id=vic['fit_id'], projector_fit_ids=[])
            v_clear = await call('get_stats', fit_id=vic['fit_id'])
            assert v_clear['navigation']['max_velocity_ms'] == v_base['navigation']['max_velocity_ms']

            # applied_dps: application collapses against a small fast target and
            # recovers against a big slow one; missiles and turrets both modeled
            ad_frig = await call('applied_dps', fit_id=fid, distance_km=1.5,
                                 target={'sig_m': 35, 'speed_ms': 700})
            ad_bs = await call('applied_dps', fit_id=fid, distance_km=1.5,
                               target={'sig_m': 400, 'speed_ms': 100})
            # perfect turret application runs ~1.015x paper (wrecking-shot
            # expectation, pyfa's own model) — allow it, catch anything larger
            assert ad_frig['dps_applied'] < ad_bs['dps_applied'] <= ad_bs['dps_raw'] * 1.02, \
                (ad_frig, ad_bs)
            assert 'turrets' in ad_bs['by_source'], ad_bs
            assert ad_bs['ship'] == 'Rifter', ad_bs.get('ship')
            # versus: both directions in one call — applied dps into resist-
            # weighted EHP, reps subtracted; projecting a web onto the victim
            # slows it, so the attacker applies MORE
            pun = await call('import_fit', eft='[Punisher, duel]\n'
                             '400mm Rolled Tungsten Compact Plates\nSmall Armor Repairer II\n'
                             'Damage Control II\nMultispectrum Coating II\n\n'
                             '1MN Afterburner II\n\n'
                             'Small Focused Pulse Laser II, Imperial Navy Multifrequency S\n'
                             'Small Focused Pulse Laser II, Imperial Navy Multifrequency S\n'
                             'Small Focused Pulse Laser II, Imperial Navy Multifrequency S')
            vs = await call('versus', fit_id_a=fid, fit_id_b=pun['fit_id'], distance_km=1)
            ab, ba = vs['a_vs_b'], vs['b_vs_a']
            assert vs['ships'] == {fid: 'Rifter', pun['fit_id']: 'Punisher'}, vs.get('ships')
            assert ab['applied_dps'] > 0 and ba['applied_dps'] > 0, vs
            assert ab['applied_dps'] <= ab['raw_dps'] * 1.02, ab
            assert abs(sum(ab['damage_mix_pct'].values()) - 100) <= 2, ab['damage_mix_pct']
            assert ('time_to_kill_s' in ab) or ab.get('tanked'), ab
            web2 = await call('import_fit', eft='[Vigil, w2]\nStasis Webifier I')
            await call('set_projected', fit_id=pun['fit_id'],
                       projector_fit_ids=[web2['fit_id']])
            vs_web = await call('versus', fit_id_a=fid, fit_id_b=pun['fit_id'], distance_km=1)
            assert vs_web['a_vs_b']['applied_dps'] >= ab['applied_dps'], \
                (vs_web['a_vs_b']['applied_dps'], ab['applied_dps'])
            await call('delete_fit', fit_id=web2['fit_id'])
            await call('delete_fit', fit_id=pun['fit_id'])

            mis = await call('import_fit', eft='[Caracal, rlml]\n'
                             'Rapid Light Missile Launcher II, Caldari Navy Scourge Light Missile\n'
                             'Rapid Light Missile Launcher II, Caldari Navy Scourge Light Missile')
            am_frig = await call('applied_dps', fit_id=mis['fit_id'], distance_km=10,
                                 target={'sig_m': 35, 'speed_ms': 700})
            am_bs = await call('applied_dps', fit_id=mis['fit_id'], distance_km=10,
                               target={'sig_m': 400, 'speed_ms': 100})
            assert 'missiles' in am_frig['by_source'], am_frig
            assert am_frig['application_pct'] < am_bs['application_pct'], (am_frig, am_bs)
            await call('delete_fit', fit_id=mis['fit_id'])

            # fighters: squadron dps lands in the panel, tube overflow is named
            than = await call('create_fit', ship='Thanatos')
            await call('edit_fit', fit_id=than['fit_id'], ops=[
                {'op': 'add', 'item': 'Firbolg I'}])
            f_stats = await call('get_stats', fit_id=than['fit_id'])
            assert f_stats['offense'].get('dps_fighters', 0) > 300, f_stats['offense']
            for _ in range(6):
                await call('edit_fit', fit_id=than['fit_id'], ops=[
                    {'op': 'add', 'item': 'Firbolg I'}])
            f_val = await call('validate_fit', fit_id=than['fit_id'])
            assert any('fighter tubes' in p for p in f_val['problems']), f_val
            assert any('light fighter tubes over' in p for p in f_val['problems']), f_val

            # fighter abilities: visible in module_attrs, toggleable, dps moves
            th2 = await call('create_fit', ship='Thanatos')
            await call('edit_fit', fit_id=th2['fit_id'], ops=[
                {'op': 'add', 'item': 'Einherji II'}])
            fa = await call('module_attrs', fit_id=th2['fit_id'], item='Einherji II',
                            attrs=['maxVelocity'])
            ab_names = {a['name']: a['active'] for a in fa['modules'][0]['abilities']}
            assert ab_names.get('Missile Attack') is True, ab_names  # eos default: on
            base_speed = fa['modules'][0]['attrs']['maxVelocity']
            base_fdps = (await call('get_stats', fit_id=th2['fit_id']))['offense']['dps_fighters']
            await call('edit_fit', fit_id=th2['fit_id'], ops=[
                {'op': 'ability', 'item': 'Einherji II',
                 'ability': 'missile', 'enabled': False}])
            no_mis = (await call('get_stats', fit_id=th2['fit_id']))['offense']['dps_fighters']
            assert no_mis < base_fdps, (base_fdps, no_mis)
            await call('edit_fit', fit_id=th2['fit_id'], ops=[
                {'op': 'ability', 'item': 'Einherji II',
                 'ability': 'microwarp', 'enabled': True}])
            fa2 = await call('module_attrs', fit_id=th2['fit_id'], item='Einherji II',
                             attrs=['maxVelocity'])
            assert fa2['modules'][0]['attrs']['maxVelocity'] > base_speed, \
                (base_speed, fa2['modules'][0]['attrs'])
            try:
                await call('edit_fit', fit_id=th2['fit_id'], ops=[
                    {'op': 'ability', 'item': 'Einherji II', 'ability': 'nosuch'}])
                raise AssertionError('bad ability name must be rejected with the list')
            except RuntimeError as e:
                assert 'has:' in str(e), e
            # standup fighters: wrong-way tube classes fail, right way flies
            await call('edit_fit', fit_id=th2['fit_id'], ops=[
                {'op': 'add', 'item': 'Standup Einherji I'}])
            sv = await call('validate_fit', fit_id=th2['fit_id'])
            assert any('standup light fighter tubes over' in p for p in sv['problems']), sv
            ast2 = await call('create_fit', ship='Astrahus')
            await call('edit_fit', fit_id=ast2['fit_id'], ops=[
                {'op': 'add', 'item': 'Standup Einherji I'}])
            as_stats = await call('get_stats', fit_id=ast2['fit_id'])
            assert as_stats['offense'].get('dps_fighters', 0) > 0, as_stats['offense']
            assert (await call('validate_fit', fit_id=ast2['fit_id']))['legal']
            await call('edit_fit', fit_id=ast2['fit_id'], ops=[
                {'op': 'add', 'item': 'Einherji II'}])
            av2f = await call('validate_fit', fit_id=ast2['fit_id'])
            assert any('light fighter tubes over' in p and 'standup' not in p
                       for p in av2f['problems']), av2f
            # review findings pinned: fighters count in applied_dps/versus,
            # survive export/clone with their quantity, and spool detection
            # ignores offline disintegrators
            fad = await call('applied_dps', fit_id=th2['fit_id'], distance_km=10,
                             target={'sig_m': 400, 'speed_ms': 100})
            assert fad['by_source'].get('fighters', [0, 0])[1] > 100, fad
            fx = await call('export_fit', fit_id=th2['fit_id'])
            assert 'Einherji II x' in fx and 'Standup Einherji I x' in fx, fx
            fclone = await call('import_fit', eft='[Thanatos, part]\nEinherji II x3')
            fma = await call('module_attrs', fit_id=fclone['fit_id'],
                             item='Einherji II', attrs=['maxVelocity'])
            assert fma['modules'][0]['amount'] == 3, fma
            fcx = await call('export_fit', fit_id=fclone['fit_id'])
            assert 'Einherji II x3' in fcx, fcx
            vs_f = await call('versus', fit_id_a=th2['fit_id'], fit_id_b=fclone['fit_id'],
                              distance_km=10)
            assert vs_f['a_vs_b']['applied_dps'] > 100, vs_f['a_vs_b']
            await call('delete_fit', fit_id=fclone['fit_id'])
            ved_off = await call('import_fit',
                                 eft='[Vedmak, off]\nHeavy Entropic Disintegrator II /offline')
            vo_stats = await call('get_stats', fit_id=ved_off['fit_id'])
            assert not any('spool' in n for n in vo_stats.get('notes', [])), vo_stats.get('notes')
            try:
                await call('graph', fit_id=ved_off['fit_id'], kind='dps_vs_time')
                raise AssertionError('offline disintegrator must not graph a ramp')
            except RuntimeError as e:
                assert 'no active spool-up' in str(e), e
            await call('delete_fit', fit_id=ved_off['fit_id'])
            await call('delete_fit', fit_id=th2['fit_id'])
            await call('delete_fit', fit_id=ast2['fit_id'])

            # implants and drugs apply and remove cleanly
            imp_fit = await call('import_fit', eft='[Rifter, pods]')
            i_base = await call('get_stats', fit_id=imp_fit['fit_id'])
            await call('edit_fit', fit_id=imp_fit['fit_id'], ops=[
                {'op': 'add', 'item': "Zainou 'Gnome' Shield Management SM-703"},
                {'op': 'add', 'item': 'Quafe Zero Classic'}])
            i_on = await call('get_stats', fit_id=imp_fit['fit_id'])
            assert abs(i_on['defense']['hp']['shield'] / i_base['defense']['hp']['shield'] - 1.03) < 0.005
            assert abs(i_on['navigation']['max_velocity_ms'] / i_base['navigation']['max_velocity_ms'] - 1.05) < 0.005
            await call('edit_fit', fit_id=imp_fit['fit_id'], ops=[
                {'op': 'remove', 'item': 'Quafe Zero Classic'}])
            i_off = await call('get_stats', fit_id=imp_fit['fit_id'])
            assert i_off['navigation']['max_velocity_ms'] == i_base['navigation']['max_velocity_ms']

            # spool weapons get a named note; wrong-size charges are rejected
            ved = await call('create_fit', ship='Vedmak')
            await call('edit_fit', fit_id=ved['fit_id'], ops=[
                {'op': 'add', 'item': 'Heavy Entropic Disintegrator II', 'charge': 'Occult M'}])
            v_stats = await call('get_stats', fit_id=ved['fit_id'])
            assert any('spool' in n for n in v_stats.get('notes', [])), 'spool note missing'
            # spool is modeled: default full, floor + ramp named, param moves dps,
            # dps_vs_time is the monotone ramp ending at the full-spool number
            sp = v_stats['offense']['spool']
            assert sp['level'] == 1.0 and sp['dps_zero_spool'] < v_stats['offense']['dps'], sp
            assert sp['time_to_full_s'] > 0, sp
            v0 = await call('get_stats', fit_id=ved['fit_id'], spool=0)
            assert v0['offense']['dps'] == sp['dps_zero_spool'], (v0['offense'], sp)
            gt = await call('graph', fit_id=ved['fit_id'], kind='dps_vs_time')
            ys = [y for _, y in gt['points']]
            assert ys == sorted(ys) and ys[0] < ys[-1], gt['points']
            assert gt['summary']['dps_full_spool'] == v_stats['offense']['dps'], gt['summary']
            try:
                await call('graph', fit_id=fid, kind='dps_vs_time')
                raise AssertionError('dps_vs_time on a non-spool fit must be rejected')
            except RuntimeError as e:
                assert 'no active spool-up' in str(e), e
            try:
                await call('edit_fit', fit_id=ved['fit_id'], ops=[
                    {'op': 'charge', 'item': 'Heavy Entropic Disintegrator II', 'charge': 'Occult L'}])
                raise AssertionError('L charge in an M gun must be rejected')
            except RuntimeError as e:
                assert 'does not fit' in str(e), e

            # rack overflow is flagged (eval run 3: a 4-mid fit once validated clean)
            oni = await call('import_fit', eft='[Omen Navy Issue, slots]\n'
                             '10MN Afterburner II\nWarp Scrambler II\n'
                             'X5 Enduring Stasis Webifier\nCap Recharger II')
            assert oni['slots']['med'] == [4, 3], oni['slots']  # summary shows the rack
            oni_val = await call('validate_fit', fit_id=oni['fit_id'])
            assert any('med slots over by 1' in p for p in oni_val['problems']), oni_val
            await call('delete_fit', fit_id=oni['fit_id'])

            # T3D mode swap moves signature
            conf = await call('create_fit', ship='Confessor')
            await call('edit_fit', fit_id=conf['fit_id'], ops=[
                {'op': 'mode', 'item': 'Confessor Defense Mode'}])
            m_def = await call('get_stats', fit_id=conf['fit_id'])
            await call('edit_fit', fit_id=conf['fit_id'], ops=[
                {'op': 'mode', 'item': 'Confessor Sharpshooter Mode'}])
            m_sharp = await call('get_stats', fit_id=conf['fit_id'])
            assert m_def['navigation']['signature_m'] < m_sharp['navigation']['signature_m'], \
                'defense mode must shrink sig vs sharpshooter'

            # graphs: bounded, with summaries
            g = await call('graph', fit_id=fid, kind='dps_vs_range')
            assert len(g['points']) <= 32 and g['summary']['peak_dps'] > 0, g['summary']
            g2 = await call('graph', fit_id=fid, kind='cap_vs_time')
            assert len(g2['points']) <= 32 and g2['summary']['capacity_gj'] > 0
            assert not g2['summary']['stable'] and g2['points'][-1][1] == 0, g2['summary']
            g3 = await call('graph', fit_id=fid, kind='dps_vs_target_speed',
                            target={'sig_m': 40}, distance_km=2)
            assert g3['points'][0][1] >= g3['points'][-1][1], 'dps must not rise with target speed'

            # full-fit skill requirements: ends by default, closure on full=true
            req = await call('required_skills', fit_id=fid)
            ends = req['skills']
            assert 'Small Autocannon Specialization' in ends, ends  # the AC II leaf
            assert 'Gunnery' not in ends, f'implied prereq not pruned: {ends}'
            assert req.get('implied_prereqs', 0) > 0, req
            req_full = await call('required_skills', fit_id=fid, full=True)
            closure = req_full['skills']
            assert closure.get('Small Projectile Turret') == 5, closure
            assert 'Gunnery' in closure and 'Minmatar Frigate' in closure, closure
            assert len(closure) > len(ends), (len(closure), len(ends))

            # mutated (abyssal) modules: pyfa's [N] dialect, absolute rolled
            # values, eos clamping, and an identical export->reimport round trip
            plain_eft = ('[Rifter, plain]\nGyrostabilizer II\n\n'
                         '150mm Light AutoCannon II, Republic Fleet EMP S')
            muta_eft = ('[Rifter, muta]\nGyrostabilizer II [1]\n\n'
                        '150mm Light AutoCannon II, Republic Fleet EMP S\n\n'
                        '[1] Gyrostabilizer II\n'
                        '  Decayed Gyrostabilizer Mutaplasmid\n'
                        '  damageMultiplier 1.1088\n')  # max roll: 1.008 x 1.1
            mp = await call('import_fit', eft=plain_eft)
            mm = await call('import_fit', eft=muta_eft)
            p_dps = (await call('get_stats', fit_id=mp['fit_id']))['offense']['dps']
            m_dps = (await call('get_stats', fit_id=mm['fit_id']))['offense']['dps']
            assert m_dps > p_dps, (m_dps, p_dps)
            mx = await call('export_fit', fit_id=mm['fit_id'])
            assert '[1] Gyrostabilizer II' in mx and 'Mutaplasmid' in mx, mx
            mr = await call('import_fit', eft=mx)
            r_dps = (await call('get_stats', fit_id=mr['fit_id']))['offense']['dps']
            assert r_dps == m_dps, f'round trip drifted: {r_dps} != {m_dps}'
            mc = await call('import_fit',
                            eft=muta_eft.replace('damageMultiplier 1.1088',
                                                 'damageMultiplier 2.0'))
            c_dps = (await call('get_stats', fit_id=mc['fit_id']))['offense']['dps']
            assert c_dps == m_dps, f'absurd roll must clamp to max: {c_dps} != {m_dps}'
            try:
                await call('import_fit', eft='[Rifter, bare]\nAbyssal Gyrostabilizer')
                raise AssertionError('bare abyssal item name must be rejected')
            except RuntimeError as e:
                assert 'mutation block' in str(e), e
            # drones mutate through the same dialect (base dmgMult is 1.92 here
            # — roll above it or the test proves nothing)
            md = await call('import_fit', eft=(
                '[Tristan, mutdrone]\nDrone Damage Amplifier II\n\n'
                'Hobgoblin II x5 [1]\n\n'
                '[1] Hobgoblin II\n'
                '  Exigent Light Drone Firepower Mutaplasmid\n'
                '  damageMultiplier 2.3\n'))
            d_dps = (await call('get_stats', fit_id=md['fit_id']))['offense']['dps_drones']
            dx = await call('export_fit', fit_id=md['fit_id'])
            dr = await call('import_fit', eft=dx)
            dr_dps = (await call('get_stats', fit_id=dr['fit_id']))['offense']['dps_drones']
            assert dr_dps == d_dps, f'drone round trip drifted: {dr_dps} != {d_dps}'
            # a mutated drone line WITHOUT 'xN' used to fall into the module
            # branch and die on eos's opaque 'Passed item is not a Module'
            # (eval gen 6 hit it) — it now imports as one drone
            md1 = await call('import_fit', eft=(
                '[Tristan, mutdrone1]\n\n'
                'Hobgoblin II [1]\n\n'
                '[1] Hobgoblin II\n'
                '  Exigent Light Drone Firepower Mutaplasmid\n'
                '  damageMultiplier 2.3\n'))
            md1_stats = await call('get_stats', fit_id=md1['fit_id'])
            assert md1_stats['offense'].get('dps_drones', 0) > 0, md1_stats['offense']
            for f in (mp, mm, mr, mc, md, dr, md1):
                await call('delete_fit', fit_id=f['fit_id'])

            # module_attrs: per-module modified values, heat-aware (the class
            # of question: "web range vs point range, both overheated")
            web = await call('import_fit', eft='[Vigilant, webtest]\n'
                             'Federation Navy Stasis Webifier\nWarp Disruptor II')
            ma = await call('module_attrs', fit_id=web['fit_id'],
                            item='Federation Navy Stasis Webifier', attrs=['maxRange'])
            cold = ma['modules'][0]['attrs']['maxRange']
            await call('edit_fit', fit_id=web['fit_id'], ops=[
                {'op': 'state', 'item': 'Federation Navy Stasis Webifier', 'state': 'overheated'},
                {'op': 'state', 'item': 'Warp Disruptor II', 'state': 'overheated'}])
            hot_web = await call('module_attrs', fit_id=web['fit_id'],
                                 item='Federation Navy Stasis Webifier', attrs=['maxRange'])
            hot_pt = await call('module_attrs', fit_id=web['fit_id'],
                                item='Warp Disruptor II', attrs=['maxRange'])
            assert hot_web['modules'][0]['state'] == 'overheated', hot_web
            assert abs(hot_web['modules'][0]['attrs']['maxRange'] / cold - 1.3) < 0.01, \
                (cold, hot_web)  # web overload: +30% range
            assert abs(hot_pt['modules'][0]['attrs']['maxRange'] / 24000 - 1.2) < 0.01, \
                hot_pt  # point overload: +20% range
            try:
                await call('module_attrs', fit_id=web['fit_id'],
                           item='Warp Disruptor II', attrs=['maxRnge'])
                raise AssertionError('typo attribute name must be rejected')
            except RuntimeError as e:
                assert 'unknown attribute' in str(e), e
            await call('delete_fit', fit_id=web['fit_id'])

            # sweep: candidate enumeration in one call, fit restored afterwards
            # (the class of question: "meta plate to free fitting for a better rep?")
            sw_fit = await call('import_fit', eft='[Rifter, sweeptest]\n'
                                '200mm Steel Plates II\nGyrostabilizer II\n\n'
                                '5MN Y-T8 Compact Microwarpdrive\n\n'
                                '150mm Light AutoCannon II, Republic Fleet EMP S\n'
                                '150mm Light AutoCannon II, Republic Fleet EMP S\n'
                                '150mm Light AutoCannon II, Republic Fleet EMP S')
            before = await call('get_stats', fit_id=sw_fit['fit_id'])
            sw = await call('sweep', fit_id=sw_fit['fit_id'], item='Gyrostabilizer II',
                            candidates=['Counterbalanced Compact Gyrostabilizer',
                                        'Gyrostabilizer I', 'Hobgoblin II'],
                            metrics=['offense.dps'])
            rows = {r['candidate']: r for r in sw['rows']}
            base_dps = rows['Gyrostabilizer II (fitted)']['offense.dps']
            assert base_dps > rows['Counterbalanced Compact Gyrostabilizer']['offense.dps'] \
                > 0, rows
            assert rows['Gyrostabilizer I']['offense.dps'] < base_dps, rows
            assert 'error' in rows['Hobgoblin II'], 'a drone is not a module candidate'
            assert 'cpu_free' in rows['Gyrostabilizer I'], rows
            after = await call('get_stats', fit_id=sw_fit['fit_id'])
            assert after['offense']['dps'] == before['offense']['dps'], \
                'sweep must restore the fit'
            await call('delete_fit', fit_id=sw_fit['fit_id'])

            # rack layout: [Empty ... slot] placeholders survive round-trip in
            # position (heat-conscious layouts), and edit add fills the gap
            lay = await call('import_fit', eft='[Rifter, layout]\n'
                             '150mm Light AutoCannon II\n[Empty High slot]\n'
                             '150mm Light AutoCannon II')
            lx = await call('export_fit', fit_id=lay['fit_id'])
            lay_lines = [l for l in lx.splitlines() if 'AutoCannon' in l or 'Empty High' in l]
            assert lay_lines == ['150mm Light AutoCannon II', '[Empty High slot]',
                                 '150mm Light AutoCannon II'], lay_lines
            await call('edit_fit', fit_id=lay['fit_id'], ops=[
                {'op': 'add', 'item': '150mm Light AutoCannon II'}])
            lx2 = await call('export_fit', fit_id=lay['fit_id'])
            assert '[Empty High slot]' not in lx2, 'edit add should fill the gap'
            # keep_slot remove leaves the gap in position (in-game semantics)
            await call('edit_fit', fit_id=lay['fit_id'], ops=[
                {'op': 'remove', 'item': '150mm Light AutoCannon II', 'keep_slot': True}])
            lx3 = await call('export_fit', fit_id=lay['fit_id'])
            lay_lines3 = [l for l in lx3.splitlines() if 'AutoCannon' in l or 'Empty High' in l]
            assert lay_lines3 == ['[Empty High slot]', '150mm Light AutoCannon II',
                                  '150mm Light AutoCannon II'], lay_lines3
            # sweep replaces in position: layout untouched afterwards
            sw_lay = await call('sweep', fit_id=lay['fit_id'],
                                item='150mm Light AutoCannon II',
                                candidates=['200mm AutoCannon II'],
                                metrics=['offense.dps'])
            assert len(sw_lay['rows']) == 2, sw_lay
            lx4 = await call('export_fit', fit_id=lay['fit_id'])
            assert lx4 == lx3, 'sweep must not disturb rack layout'
            await call('delete_fit', fit_id=lay['fit_id'])

            # siege-class states: bastion's preMul resist chain multiplies the
            # hardener's postPercent chain at full strength (engine-verified
            # 0.675 x 0.700 = 0.4725); hull restriction rejects bastion
            # off-marauder; siege multiplies dps and pins speed to 0; triage
            # boosts remote rep amount and cycle
            gol = await call('import_fit', eft='[Golem, bast]\n'
                             'Multispectrum Shield Hardener II\n\nBastion Module I')
            g_stats = await call('get_stats', fit_id=gol['fit_id'])
            em = g_stats['defense']['resists']['shield']['em']
            assert abs(em - 0.5275) < 0.002, f'bastion+hardener em resist {em}'
            assert any('Bastion' in n for n in g_stats.get('notes', [])), g_stats.get('notes')
            gv = await call('validate_fit', fit_id=gol['fit_id'])
            assert gv['legal'], gv
            bad = await call('import_fit', eft='[Rifter, bad]\nBastion Module I')
            bv = await call('validate_fit', fit_id=bad['fit_id'])
            assert any('cannot be fitted' in p for p in bv['problems']), bv
            # the same check covers the whole canFitShipType/Group class
            cloak = await call('import_fit', eft='[Rifter, cloak]\nCovert Ops Cloaking Device II')
            cv = await call('validate_fit', fit_id=cloak['fit_id'])
            assert any('cannot be fitted' in p for p in cv['problems']), cv
            await call('delete_fit', fit_id=cloak['fit_id'])
            phx = await call('import_fit', eft='[Phoenix, siege]\n'
                             'XL Torpedo Launcher II, Mjolnir XL Torpedo\nSiege Module II')
            p_stats = await call('get_stats', fit_id=phx['fit_id'])
            assert p_stats['offense']['dps'] > 1500, p_stats['offense']
            assert p_stats['navigation']['max_velocity_ms'] == 0, p_stats['navigation']
            mino = await call('import_fit', eft='[Minokawa, tri]\n'
                              'Capital Remote Shield Booster II\nTriage Module II')
            tri = await call('module_attrs', fit_id=mino['fit_id'],
                             item='Capital Remote Shield Booster II',
                             attrs=['shieldBonus', 'duration'])
            assert tri['modules'][0]['attrs']['shieldBonus'] > 7000, tri
            assert tri['modules'][0]['attrs']['duration'] == 5000, tri
            for f in (gol, bad, phx, mino):
                await call('delete_fit', fit_id=f['fit_id'])

            # Upwell structures: Citadel branch, standup weapons, service fuel,
            # service rack in summaries, legality in both directions
            ast = await call('import_fit', eft='[Astrahus, home]\n'
                             'Standup Ballistic Control System I\n\n'
                             'Standup Multirole Missile Launcher I, Standup Cruise Missile\n\n'
                             'Standup Cloning Center I')
            assert ast['slots'].get('service') == [1, 3], ast['slots']
            a_stats = await call('get_stats', fit_id=ast['fit_id'])
            assert a_stats['offense']['dps'] > 900, a_stats['offense']
            assert a_stats['defense']['ehp']['total'] > 20_000_000, a_stats['defense']['ehp']
            assert a_stats['navigation']['max_velocity_ms'] == 0, a_stats['navigation']
            svc = a_stats['services']
            assert svc['fuel_blocks_per_hour'] == 10 and svc['fitted'][0]['fuel_to_online'] == 720, svc
            # armor/hull carry real caps; the shield "cap" equals full shield
            # HP, i.e. no practical cap — reported as 'none' (eval gen 5:
            # three of four subjects misread the raw 14.4M as a hard cap)
            assert a_stats['defense']['incoming_dps_cap'] == \
                {'shield': 'none', 'armor': 5000, 'hull': 5000}, \
                a_stats['defense'].get('incoming_dps_cap')
            av = await call('validate_fit', fit_id=ast['fit_id'])
            assert av['legal'], av
            await call('edit_fit', fit_id=ast['fit_id'], ops=[
                {'op': 'add', 'item': 'Standup Cloning Center I'},
                {'op': 'add', 'item': 'Standup Reprocessing Facility I'},
                {'op': 'add', 'item': 'Standup Market Hub I'}])
            av2 = await call('validate_fit', fit_id=ast['fit_id'])
            assert any('service slots over by 1' in p for p in av2['problems']), av2
            bad_s = await call('import_fit', eft='[Astrahus, bad]\nGyrostabilizer II')
            bs_val = await call('validate_fit', fit_id=bad_s['fit_id'])
            assert any('cannot be fitted' in p for p in bs_val['problems']), bs_val
            bad_r = await call('import_fit', eft='[Rifter, bad2]\nStandup Cloning Center I')
            br_val = await call('validate_fit', fit_id=bad_r['fit_id'])
            assert any('cannot be fitted' in p for p in br_val['problems']), br_val
            # maxGroupFitted: two Warp Core Stabilizers validated clean before
            # gen-7 key derivation caught it — the game allows one per ship
            wcs2 = await call('import_fit', eft='[Rifter, wcs2]\n'
                              'Warp Core Stabilizer I\nWarp Core Stabilizer I')
            wcs_val = await call('validate_fit', fit_id=wcs2['fit_id'])
            assert any('maxGroupFitted' in p for p in wcs_val['problems']), wcs_val
            wcs1 = await call('import_fit', eft='[Rifter, wcs1]\n'
                              'Warp Core Stabilizer I')
            assert (await call('validate_fit', fit_id=wcs1['fit_id']))['legal']
            await call('delete_fit', fit_id=wcs2['fit_id'])
            await call('delete_fit', fit_id=wcs1['fit_id'])
            # rig size: a Large rig on a battlecruiser hull is illegal in
            # game (second gen-7 derivation find)
            bigrig = await call('import_fit', eft='[Drake, bigrig]\n\n\n\n'
                                'Large Core Defense Field Extender I')
            br2 = await call('validate_fit', fit_id=bigrig['fit_id'])
            assert any('rig' in p and 'medium' in p for p in br2['problems']), br2
            okrig = await call('import_fit', eft='[Drake, okrig]\n\n\n\n'
                               'Medium Core Defense Field Extender I')
            assert (await call('validate_fit', fit_id=okrig['fit_id']))['legal']
            await call('delete_fit', fit_id=bigrig['fit_id'])
            await call('delete_fit', fit_id=okrig['fit_id'])
            for f in (ast, bad_s, bad_r):
                await call('delete_fit', fit_id=f['fit_id'])

            # Parity has four branches and they are the entire point of the
            # field. The first version wedged the mixed-build clause between
            # the elif and the else, so `else` bound to `if mixed` and every
            # non-mixed run was overwritten with "not found" while still
            # printing a build number next to it — and the smoke test passed
            # because this checkout happened to be mixed. Exercise all four.
            sys.path.insert(0, HERE)
            from server import _parity_text                     # noqa: E402
            skew = _parity_text('340', '350', None)
            assert skew.startswith('UNVERIFIED') and '350' in skew, skew
            assert _parity_text('340', '340', None) == \
                'engine and layer 1 are both at build 340'
            assert 'not found' in _parity_text('340', None, None)
            both = _parity_text('340', '350', {'a.sqlite': '340', 'b.sqlite': '350'})
            assert both.startswith('layer 1 is MIXED') and 'UNVERIFIED' in both, both

            info = await call('engine_info')
            # Two build numbers side by side invite "nothing relevant changed",
            # which a graded answer duly asserted without checking. The field
            # carrying them carries the refusal to draw that inference.
            assert 'parity' in info, info
            if info.get('sde_build') and info['sde_build'] != info['engine_build']:
                assert 'UNVERIFIED' in info['parity'], info['parity']
            assert info['engine_build'], info
            assert 'environment effects' not in info['unmodeled'], 'env is modeled now'
            assert 'mutated modules' not in info['unmodeled'], 'mutations are modeled now'
            assert not any('siege' in u for u in info['unmodeled']), 'siege states modeled now'
            await call('delete_fit', fit_id=c['fit_id'])

            print(f"\nengine_build: {info['engine_build']} | all assertions passed")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--pyfa', default=os.environ.get('PYFA_PATH'), required='PYFA_PATH' not in os.environ)
    asyncio.run(main(ap.parse_args().pyfa))
