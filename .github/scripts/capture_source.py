"""Capture the imported source before architectural changes; not a benchmark updater."""
from pathlib import Path
import copy
import hashlib
import json
import platform
import subprocess

import numpy as np
import pandas as pd
from fusion.config import CONFIG
from fusion.engine import FusionEngine
from fusion.metrics import summarize
from benchmark.targets import BASELINES, HOLDOUT
from benchmark.scorecard import SWEEP_CAPITALS

root = Path.cwd()
if (root / 'gquant').exists():
    raise SystemExit('capture is only permitted before the runtime refactor')
expected = subprocess.check_output(['git', 'show', '9bba9dee1562333ded076c75a5a4a472666f89d0:fusion/engine.py'])
if (root / 'fusion/engine.py').read_bytes() != expected:
    raise SystemExit('source engine differs from the byte-preserving import')
subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=root/'data', check=True)
requests = {}
def add(name, start, end, capital, slippage=10.0):
    requests.setdefault((start, end, capital, slippage), []).append(name)
add('full', CONFIG['start'], CONFIG['end'], CONFIG['initial_capital'])
add('diagnostic', HOLDOUT['start'], HOLDOUT['end'], CONFIG['initial_capital'])
for ref in BASELINES:
    add(ref['key'], ref['start'], ref['end'], ref['initial_capital'])
    for capital, label in SWEEP_CAPITALS:
        add(f"capital:{ref['key']}:{capital}", ref['start'], ref['end'], capital)
    if ref['key'] in ('track_trend_b', 'glmcsm_6'):
        for slippage in (15.0, 20.0):
            add(f"cost:{ref['key']}:{slippage}", ref['start'], ref['end'], ref['initial_capital'], slippage)
output = {'source_repository':'ychenracing/trades', 'source_sha':'69d508811bc5a25f94e9ab05d21db908b5adb305',
          'runtime':{'python':platform.python_version(), 'numpy':np.__version__, 'pandas':pd.__version__},
          'source_engine_sha256':hashlib.sha256(expected).hexdigest(),
          'data_sha256sums':hashlib.sha256((root/'data/SHA256SUMS').read_bytes()).hexdigest(),
          'default_config':CONFIG, 'references':BASELINES, 'cases':[]}
for (start, end, capital, slippage), names in requests.items():
    request = {'start':start, 'end':end, 'initial_capital':capital, 'slippage_bps':slippage, 'max_adv_participation':0.08}
    cfg=copy.deepcopy(CONFIG)
    cfg.update(request)
    result=FusionEngine(cfg).run()
    output['cases'].append({'names':names, 'request':request, 'metrics':summarize(result),
        'dates':[str(d.date()) for d in result.equity_curve.index], 'equity':list(result.equity_curve),
        'exposure':list(result.daily_exposure), 'regime':list(result.regime_series),
        'fills':[{**vars(fill), 'date':str(fill.date.date())} for fill in result.trades], 'events':result.events})
    print('captured', names, len(result.trades), flush=True)
if len(output['cases']) != 24:
    raise SystemExit('unexpected economic case count')
path=root/'tests/fixtures/economic_sequences.json'
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('x', encoding='utf-8') as stream:
    json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
    stream.write('\n')
print('baseline sha256', hashlib.sha256(path.read_bytes()).hexdigest())
