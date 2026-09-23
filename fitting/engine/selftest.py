"""EFT parser self-test against the pinned reference battery.

    python3 selftest.py --pyfa <pyfa-checkout>

Two checks GATE, one REPORTS:
1. parse (gates): reference/battery.eft parses into the exact module, charge
   and drone lists battery.py defines.
2. panel drift (reports only): fits built from the PARSED text are compared to
   the pinned reference JSONs. A difference here is almost always CCP moving a
   number, not this code breaking -- and CCP moves numbers every few days. Run
   with --accept to re-pin.
3. round-trip (gates): render_eft(build_fit(parse_eft(x))) reparses to the
   same spec.

Check 2 used to gate, and used to `continue` past a drifted fit -- so a single
balance change both failed the build AND silently skipped the round-trip check
for that fit, turning a data change into a hole in the structural test.
Measured 2026-09-22: CCP cut the Golem's mass 157,000,000 -> 125,600,000 kg,
normalising it against the other Marauders. 616 panel leaves compared, exactly
one differed, and it reddened three consecutive releases.

The rule this encodes: gate on relationships that only WE can break; report
magnitudes that CCP owns. The workflow already publishes the same diff as its
balance-change report, so nothing is lost by not failing here.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.join(os.path.dirname(HERE), 'spike')
sys.path.insert(0, HERE)
sys.path.insert(0, SPIKE)

from headless import bootstrap  # noqa: E402  (spike dir)
from eft import parse_eft, build_fit, render_eft  # noqa: E402


def entry_set(spec):
    # parse is text-only: "Module, Charge" stays one line until build-time lookup
    mods = sorted(e['name'] for e in spec.entries if e['quantity'] is None)
    stacks = sorted((e['name'], e['quantity']) for e in spec.entries if e['quantity'] is not None)
    return mods, stacks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pyfa', default=os.environ.get('PYFA_PATH'), required='PYFA_PATH' not in os.environ)
    ap.add_argument('--accept', action='store_true',
                    help='re-pin the reference panels to what the engine now '
                         'computes; use after confirming a drift is CCP, not us')
    args = ap.parse_args()
    bootstrap(args.pyfa)
    import eos.db  # noqa: F401 — must precede eos.saveddata imports
    import run_battery  # spike's statPanel, so panels are computed identically

    failures = drifted = 0

    eft_text = open(os.path.join(SPIKE, 'reference', 'battery.eft')).read()
    specs = parse_eft(eft_text)
    from battery import FITS
    assert len(specs) == len(FITS), f'{len(specs)} parsed vs {len(FITS)} defined'

    for spec, defined in zip(specs, FITS):
        # 1: parse fidelity vs battery.py definition (same joined-line form)
        want_mods = sorted(f'{n}, {c}' if c else n for n, c in defined['modules'])
        want_stacks = sorted((n, q) for n, q in defined.get('drones', ()))
        got_mods, got_stacks = entry_set(spec)
        if (got_mods, got_stacks) != (want_mods, want_stacks):
            print(f'PARSE MISMATCH {spec.name}: {got_mods} {got_stacks}')
            failures += 1
            continue

        # 2: panel drift vs pinned reference — REPORTS, never gates, and never
        # skips check 3: a CCP balance pass must not put a hole in the
        # structural test for the fit it touched.
        fit = build_fit(spec)
        panel = run_battery.statPanel(fit)
        ref_path = os.path.join(SPIKE, 'reference', f'{spec.name}.json')
        doc = json.load(open(ref_path))
        if panel != doc['stats']:
            drifted += 1
            print(f'PANEL DRIFT {spec.name}' + ('  (re-pinned)' if args.accept else ''))
            for section in doc['stats']:
                if panel.get(section) != doc['stats'][section]:
                    print(f'  {section}: {doc["stats"][section]} -> {panel.get(section)}')
            if args.accept:
                doc['stats'] = panel
                with open(ref_path, 'w') as fh:
                    json.dump(doc, fh, indent=2)
                    fh.write('\n')

        # 3: render round-trip
        rendered = render_eft(fit)
        respec = parse_eft(rendered)[0]
        if entry_set(respec) != (got_mods, got_stacks) or respec.ship != spec.ship:
            print(f'ROUNDTRIP MISMATCH {spec.name}')
            failures += 1
            continue

        print(f'ok {spec.name}')

    print(f'\n{len(specs) - failures}/{len(specs)} fits pass parse -> build -> render round-trip')
    if drifted:
        print(f'{drifted} panel(s) drifted from the pinned references — that is a '
              'CCP balance report, not a failure.'
              + ('' if args.accept else ' Re-pin with --accept once confirmed.'))
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
