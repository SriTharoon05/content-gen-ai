"""Build non-secret Worker contracts from canonical defaults, schemas and editorial skills."""
import json
from pathlib import Path
import sys
import os
os.environ.setdefault('DATABASE_URL','postgresql+psycopg://unused:unused@127.0.0.1:1/unused')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.settings_store import DEFAULTS
from app import schemas

data = {
    'defaults': {k:v for k,v in DEFAULTS.items() if k != 'keys'},
    'schemas': {name:getattr(schemas,name).model_json_schema() for name in
        ('UniqueConceptSet','Premise','Script','QAFinding','VoiceDirection','VisualPlan','EditPlan','PublishCopy')},
    'skills': {str(p.relative_to(ROOT/'backend/app/skills')).replace('\\','/'):p.read_text(encoding='utf-8')
        for p in (ROOT/'backend/app/skills').rglob('*.md')},
}
(ROOT/'cloudflare/src/contract.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
print('Exported canonical non-secret generation contract')
