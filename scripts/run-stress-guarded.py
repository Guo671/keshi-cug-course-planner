"""One low-priority child at a time, 768 MiB working-set guard and a 180s deadline."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'tmp/stress-031';OUT.mkdir(exist_ok=True,parents=True)
class MemoryCounters(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
get_memory=ctypes.windll.psapi.GetProcessMemoryInfo
get_memory.argtypes=[wintypes.HANDLE,ctypes.POINTER(MemoryCounters),wintypes.DWORD]
get_memory.restype=wintypes.BOOL

def run(name):
    env=os.environ.copy();env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_MAX_THREADS='1')
    # Windows venv python.exe is a redirector that spawns another process.
    # Launch the real interpreter so the monitored handle is the actual worker.
    env['PYTHONPATH']=os.pathsep.join([str(ROOT/'backend'),str(ROOT),str(ROOT/'.venv/Lib/site-packages')])
    interpreter=getattr(sys,'_base_executable',sys.executable)
    started=time.monotonic();peak=0;termination=None
    with (OUT/f'{name}.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([interpreter,'-X','utf8',str(ROOT/'scripts/stress-031.py'),name],cwd=ROOT,env=env,
          stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        while process.poll() is None:
            memory=MemoryCounters();memory.cb=ctypes.sizeof(memory)
            if not get_memory(wintypes.HANDLE(int(process._handle)),ctypes.byref(memory),memory.cb):
                if process.poll() is None:termination='memory monitor unavailable';process.kill()
                break
            peak=max(peak,memory.PeakWorkingSetSize,memory.WorkingSetSize)
            if peak>768*1024*1024:termination='memory guard 768 MiB';process.kill();break
            if time.monotonic()-started>180:termination='time guard 180s';process.kill();break
            time.sleep(.05)
        code=process.wait()
    result={'suite':name,'exit_code':code,'termination':termination,'peak_working_set_mib':round(peak/1024/1024,1),'supervised_seconds':round(time.monotonic()-started,3)}
    print(json.dumps(result,ensure_ascii=False),flush=True)
    (OUT/f'{name}-supervision.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if code:print((OUT/f'{name}.log').read_text(encoding='utf-8')[-2500:],flush=True)
    return result

if __name__=='__main__':
    suites=sys.argv[1:] or ['oracle','leave','scale','accounts','malformed','non_blocking','import']
    results=[]
    for name in suites:
        item=run(name);results.append(item)
        if item['exit_code'] or item['termination']:break
    (OUT/'supervision.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(0 if all(r['exit_code']==0 and not r['termination'] for r in results) else 1)
