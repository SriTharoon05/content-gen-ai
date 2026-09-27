"""Copy only the existing news key to the Worker; never print its value."""
import argparse
from pathlib import Path
import subprocess
import shutil
from dotenv import dotenv_values

parser=argparse.ArgumentParser()
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
env=dotenv_values(root/'backend'/'.env')
value=next((v for k,v in env.items() if k.lower() in ('news_api','newsdata_api_key') and v),'')
print('NewsData key configured:',bool(value))
if args.apply:
    if not value:raise SystemExit('No NewsData key found')
    result=subprocess.run([shutil.which('npx.cmd') or shutil.which('npx'),'wrangler','secret','put','NEWSDATA_API_KEY'],cwd=root/'cloudflare',input=value,text=True,capture_output=True,encoding='utf-8',errors='replace')
    if result.returncode:raise SystemExit('Secret provisioning failed; check Wrangler authentication')
    print('NewsData secret provisioned without exposing its value')
