"""Executable examples for a PROPOSED statistical contract, not the live engine.
No Django imports, clinical thresholds or product DB writes. The assertions check
arithmetic and evidence-selection examples; they do not validate medical effects.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import argparse,json


def examples():
    results=[]
    def case(key,actual,expected,meaning):
        assert actual==expected,(key,actual,expected)
        results.append({'case':key,'actual':actual,'expected':expected,'meaning':meaning,'passed':True})
    prior,latest=D('8.45'),D('8.4')
    t0=datetime.fromisoformat('2026-11-03T18:00:00+09:00');t1=datetime.fromisoformat('2026-11-04T00:22:00+09:00')
    case('weight-two-measurements',{'grams':int((latest-prior)*1000),'minutes':int((t1-t0).total_seconds()/60),'percent':str(((latest-prior)/prior*100).quantize(D('.01')))},
         {'grams':-50,'minutes':382,'percent':'-0.59'},'A measured difference over 6h22m is not a proven long-term trend.')
    case('old-measurement-age',(date(2026,11,12)-t1.date()).days,8,'Show actual date/elapsed days, not the chart window as the measurement date.')
    first=[D(x) for x in ['8.6','8.55','8.5','8.55','8.5','8.45']];second=[D('8.45'),D('8.4')]
    case('unequal-measured-days',{'old_mean':str(sum(first)/len(first)),'recent_mean':str(sum(second)/len(second)),'days':(len(first),len(second))},
         {'old_mean':'8.525','recent_mean':'8.425','days':(6,2)},'These arithmetic means do not establish a representative weekly change.')
    snack=[{'g':D('8'),'energy_per_100g':None}]
    case('one-snack-no-empty-comparison',{'events':len(snack),'offered_g':str(sum(x['g'] for x in snack)),'previous_delta':None,'daily_share':None},
         {'events':1,'offered_g':'8','previous_delta':None,'daily_share':None},'One observed record needs one concise summary, not a zero or blank previous-period panel.')
    case('same-mass-different-food',tuple(str(D('10')*e/100) for e in (D('450'),D('250'))),('45','25'),'10g of different products is not equal energy.')
    case('offered-versus-eaten',{'provided':str(D('100')*D('380')/100),'eaten':str(D('40')*D('380')/100)},
         {'provided':'380','eaten':'152'},'Do not put provided energy and consumed energy into the same denominator.')
    known_meal,known_treat=D('380'),D('32')
    case('recorded-energy-share',str((known_treat/(known_meal+known_treat)*100).quantize(D('.01'))),'7.77','Label as recorded provided energy share; this is not evidence that the full day was captured.')
    records=[{'kind':'meal','kcal':known_meal},{'kind':'treat','kcal':known_treat},{'kind':'treat','kcal':None}]
    share=None if any(x['kcal'] is None for x in records) else sum(x['kcal'] for x in records if x['kind']=='treat')/sum(x['kcal'] for x in records)
    case('unknown-energy-blocks-share',share,None,'Do not present a percentage after silently dropping an unknown-energy record.')
    case('pieces-can-have-energy-without-mass',{'pieces':2,'kcal':2*5,'grams':None},{'pieces':2,'kcal':10,'grams':None},'A product kcal-per-piece does not require inventing grams-per-piece.')
    case('missing-versus-zero',(None is None,D('0')==0),(True,True),'A zero explicitly measured is not a missing observation.')
    durations=[D('30'),None]
    case('partial-activity-known-facts',{'known_minutes':str(sum(x for x in durations if x is not None)),'timed':sum(x is not None for x in durations),'events':len(durations)},
         {'known_minutes':'30','timed':1,'events':2},'Retain the known 30 minutes with 1/2 coverage; do not delete the day or claim a full total.')
    intervals=[(D('1410'),D('1470')),(D('1440'),D('1500'))]
    merged=[]
    for left,right in sorted(intervals):
        if merged and left<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],right))
        else:merged.append((left,right))
    case('sleep-overlap-and-midnight',{'minutes':int(sum(b-a for a,b in merged)),'day1':int(sum(max(D(0),min(b,D(1440))-a) for a,b in merged)),'day2':int(sum(max(D(0),b-max(a,D(1440))) for a,b in merged))},
         {'minutes':90,'day1':30,'day2':60},'Only actual observed intervals, not planned sleep or alarm durations.')
    planned=datetime.fromisoformat('2026-11-05T10:00:00+09:00');actual=planned+timedelta(minutes=90);symptom=planned+timedelta(minutes=45);entered=planned+timedelta(hours=2)
    case('actual-event-anchor',{'symptom_before_actual':symptom<actual,'entry_after_actual':entered>actual},
         {'symptom_before_actual':True,'entry_after_actual':True},'A late-entered earlier observation must not be labelled an effect after actual administration.')
    case('explicit-stock-ledger',{'now':1000+500-60-100,'undo_food':1000+500-100,'edit_food_to120':1000+500-120-100},
         {'now':1340,'undo_food':1400,'edit_food_to120':1280},'One source event; edit/cancel changes the same quantity effect, not a duplicate transaction.')
    scheduled=[{'due':True,'state':'done'},{'due':True,'state':'skipped'},{'due':True,'state':'unrecorded'},{'due':False,'state':'future'},{'due':False,'state':'paused'}]
    case('configured-opportunity-denominator',{'due':sum(x['due'] for x in scheduled),'completed':sum(x['due'] and x['state']=='done' for x in scheduled),'skipped':sum(x['due'] and x['state']=='skipped' for x in scheduled)},
         {'due':3,'completed':1,'skipped':1},'Future/paused excluded; no record stays separate from medically non-adherent.')
    birth=date(2026,8,1);event=date(2026,10,1);view=date(2026,11,12)
    case('age-at-event',{'event_days':(event-birth).days,'current_days':(view-birth).days},{'event_days':61,'current_days':103},'Use event age; do not apply the current stage to a historical measurement.')
    condition_start=date(2026,11,5);measurement=date(2026,11,4)
    case('effective-condition-date',condition_start<=measurement,False,'Known recovery starting Nov5 cannot be retroactively attached to Nov4.')
    case('shared-water-not-divided',{'bowl_ml':300,'animals':2,'individual_intake':None},{'bowl_ml':300,'animals':2,'individual_intake':None},'Without individual measurement, 150ml per pet is an invented attribution.')
    case('temperature-humidity-ph',{'fahrenheit32_C':str((D('32')-32)*D(5)/9),'humidity_delta_pp':50-40,'ph_percent':None},
         {'fahrenheit32_C':'0','humidity_delta_pp':10,'ph_percent':None},'Location and metric must match; inappropriate relative percentages are withheld.')
    selected=set(['visible-1','visible-2']);evidence=['visible-1','private-medication','foreign-room','visible-2']
    case('authorization-before-context', [x for x in evidence if x in selected],['visible-1','visible-2'],'Hidden events must not leak even through an event count or a context title.')
    return {'status':'proposal-examples-only; pending-user-confirmation','medical_validity_claim':False,'production_formula_changed':False,'case_count':len(results),'cases':results}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args();result=examples()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'cases':result['case_count'],'all_passed':all(x['passed'] for x in result['cases']),'status':result['status']},ensure_ascii=False))
