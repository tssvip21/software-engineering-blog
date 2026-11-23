"""Reproducible local verification. Never publishes or changes the user's databases."""
from pathlib import Path
import argparse,json,os,re,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/handoff/phase4';OUT.mkdir(parents=True,exist_ok=True)

def main():
    parser=argparse.ArgumentParser(description='테일리 4차 로컬 검사')
    parser.add_argument('--browser',action='store_true',help='분리된 검증 DB에서 실제 브라우저 검사')
    args=parser.parse_args()
    env=os.environ.copy();env.update(CARE_DATA_DIR=str(OUT/'check-db'),CARE_DEMO_MODE='0',DJANGO_DEBUG='1',DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1,testserver',PYTHONUTF8='1')
    commands=[('system',[sys.executable,'manage.py','check']),('unit',[sys.executable,'manage.py','test','care.tests','--noinput','--verbosity','1']),('migrations',[sys.executable,'manage.py','makemigrations','--check','--dry-run']),('lint',[sys.executable,'-m','ruff','check','care','scripts','config','--select','E9,F63,F7,F82']),('dependencies',[sys.executable,'-m','pip','check'])]
    commands += [('javascript-'+name,['node','--check',f'static/care/{name}.js']) for name in ('app','taily','phase3','phase4')]
    if args.browser:commands += [('browser',[sys.executable,'scripts/verify_phase4_browser.py']),('visual',[sys.executable,'scripts/verify_phase4_visual.py'])]
    results=[]
    for name,command in commands:
        start=time.monotonic()
        try:
            proc=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=300 if name=='browser' else 120)
            output=proc.stdout+'\n'+proc.stderr;code=proc.returncode
        except (OSError,subprocess.TimeoutExpired) as error:output=str(error);code=-1
        (OUT/(name+'-verification.log')).write_text(output,encoding='utf8')
        results.append({'name':name,'exit':code,'seconds':round(time.monotonic()-start,2),'tests':re.findall(r'Ran (\d+) tests',output),'tail':output[-1500:] if code else output[-180:]})
        (OUT/'verification-run.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
        print(f'{name}: '+('PASS' if code==0 else 'FAIL'),flush=True)
        if code:raise SystemExit(1)
    print('All requested checks passed. Logs: tmp/handoff/phase4')
if __name__=='__main__':main()
