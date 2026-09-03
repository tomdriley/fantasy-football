"""Capture current draft behaviour as a golden file.

This is the guard on the validated result: if any later change alters which
players the engine drafts, the diff shows up immediately instead of silently
degrading the strategy.
"""
import json, collections
from concurrent.futures import ProcessPoolExecutor
from ffopt import backtest, client, config, pool, season

CASES=[(y,seat,seed) for y in (2023,2024,2025) for seat in (1,5,10) for seed in (0,1)]

def one(a):
    y,seat,seed=a
    cfg=config.load(); pre=client.projections(y)
    backtest.STRATEGIES['_o']=lambda r,al,c,**k: backtest.pick_optimizer(r,al,c,horizon=8,**k)
    ctx,_=backtest.build_context(cfg,pre,seed=1000*seed+seat,lam=0.7)
    ctx.waivers=season.objective_waivers([])
    rr=backtest.run_draft(ctx,{seat:'_o'})[seat]
    return f"{y}|{seat}|{seed}", [i.player_id for i in rr]

if __name__=="__main__":
    out={}
    with ProcessPoolExecutor(max_workers=14) as ex:
        for k,v in ex.map(one,CASES): out[k]=v
    with open('tests/golden/draft_behaviour.json','w') as f:
        json.dump(out,f,indent=1,sort_keys=True)
    print("captured %d cases, %d picks each"%(len(out),len(next(iter(out.values())))))
