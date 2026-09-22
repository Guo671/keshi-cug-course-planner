"""Bounded release checks using an extracted ZIP and synthetic download provenance."""
import hashlib
import json
import subprocess
import tempfile
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'tmp/startup-036'
OUTPUT.mkdir(parents=True,exist_ok=True)
checks=[]
with tempfile.TemporaryDirectory(prefix='课石 中文路径 !!! ',dir=OUTPUT) as directory:
    folder=Path(directory).resolve()
    with ZipFile(ROOT/'release/keshi-v0.3.6-win64.zip') as archive:
        for name in archive.namelist():
            assert (folder/name).resolve().is_relative_to(folder)
        archive.extractall(folder)
    exe=next(folder.glob('*/Keshi.exe'))
    def run(label,mode,expected=0):
        result=subprocess.run([str(exe),'--check-ui',mode],capture_output=True,timeout=100)
        output=result.stdout.decode('utf-8',errors='replace')
        (OUTPUT/(label+'.stdout')).write_bytes(result.stdout)
        (OUTPUT/(label+'.stderr')).write_bytes(result.stderr)
        print(label,'exit',result.returncode,flush=True)
        lines=(output+'\n'+result.stderr.decode('utf-8',errors='replace')).splitlines()
        records=[line for line in lines if line.startswith('{"status":')]
        assert records,(label,result.returncode,lines[-10:])
        payload=json.loads(records[-1])
        assert result.returncode==expected,(label,result.returncode,output,result.stderr[-1000:])
        checks.append({'case':label,'exit_code':result.returncode,'result':payload})
        print(label,json.dumps(payload,ensure_ascii=True),flush=True)
        return payload
    if '--marked-only' not in sys.argv:
        run('clean-native','native')
        run('clean-edge','edge')
        run('clean-chrome','chrome')
    dll=exe.parent/'_internal/pythonnet/runtime/Python.Runtime.dll'
    original=hashlib.sha256(dll.read_bytes()).hexdigest()
    mark=Path(str(dll)+':Zone.Identifier')
    mark.write_text('[ZoneTransfer]\nZoneId=3\n',encoding='ascii')
    failure=run('marked-native-reproduces-original','native',1)
    assert 'Python.Runtime.Loader.Initialize' in failure['error']
    success=run('marked-auto-fallback','auto')
    assert success['mode']=='browser' and success['reason']=='internet-marked-window-dependency'
    assert mark.read_text().strip().endswith('ZoneId=3')
    assert hashlib.sha256(dll.read_bytes()).hexdigest()==original
    required=exe.parent/'_internal/frontend/memory-actions.js'
    original_bytes=required.read_bytes();required.unlink()
    try:
        result=run('missing-resource-is-actionable','auto',2)
        assert '程序文件不完整' in result['error']
    finally:required.write_bytes(original_bytes)
(OUTPUT/'release-startup.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'All {len(checks)} release checks passed; download mark was preserved')
