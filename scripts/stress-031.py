"""Isolated stress suites. Invoke through run-stress-guarded.py for resource supervision."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'tmp/stress-031'
OUT.mkdir(exist_ok=True,parents=True)
os.environ['CUG_PLANNER_DATABASE_URL']=f"sqlite:///{(OUT/'bootstrap.db').as_posix()}"

from app.domain import Course,CourseRequest,Meeting,SchedulingProblem,SectionOption,TeachingSection,WeekMask
from app.scheduling.solver import ScheduleSolver,SolverConfig
from app.domain.models import TimePrecision


def slots(option):
    return {(w,m.weekday,p) for m in option.meetings if m.precision!=TimePrecision.NON_BLOCKING for w in m.weeks.weeks
            for p in range(m.start_period,m.end_period+1)}


def oracle(problem):
    groups=[[None]+[o for o in problem.options if o.course_id==r.course_id] for r in problem.requests]
    valid=[]
    for combination in itertools.product(*groups):
        occupied=set();score=0;ok=True
        for request,option in zip(problem.requests,combination):
            if option is None:
                if request.required:ok=False;break
                continue
            new=slots(option)
            if occupied & new:ok=False;break
            occupied |= new;score+=request.priority
        if ok:valid.append((score,frozenset(o.id for o in combination if o)))
    return valid


def random_problem(rng,n=5):
    courses=tuple(Course(f'c{i}',f'C{i}',f'课程{i}') for i in range(rng.randint(1,n)))
    requests=tuple(CourseRequest(c.id,priority=rng.randint(0,10),required=rng.random()<.2) for c in courses)
    options=[]
    for c in courses:
        for j in range(rng.randint(1,3)):
            weeks=WeekMask.from_weeks(rng.sample([1,2,3,4],rng.randint(1,3)))
            start=rng.randint(1,5)
            m=Meeting(weeks,rng.randint(1,3),start,start+rng.randint(0,1))
            oid=f'{c.id}:{j}'
            options.append(SectionOption(oid,c.id,(TeachingSection(oid,c.id,str(j),(),(m,)),)))
    return SchedulingProblem(courses,requests,tuple(options))


def suite_oracle():
    import app.scheduling.solver as module
    rng=random.Random(310031);validated=0;fallback=0
    for i in range(1000):
        problem=random_problem(rng)
        expected=oracle(problem)
        solver=ScheduleSolver(SolverConfig(max_solutions=3,time_limit_seconds=1))
        result=solver.solve(problem)
        assert result.status.value in ('optimal','infeasible'),(i,result.status)
        possibilities={selection:score for score,selection in expected}
        if expected:
            assert result.plans and result.plans[0].coverage_score==max(score for score,_ in expected),i
        else:assert not result.plans,i
        for plan in result.plans:
            assert possibilities[frozenset(plan.selected_option_ids)]==plan.coverage_score,i
            validated+=1
        if i % 10==0:
            original=module.cp_model
            try:
                module.cp_model=None
                alternative=solver.solve(problem)
            finally:module.cp_model=original
            assert bool(alternative.plans)==bool(expected)
            if expected:assert alternative.plans[0].coverage_score==max(score for score,_ in expected)
            fallback+=1
    return {'random_problems':1000,'independently_validated_plans':validated,'fallback_cross_checks':fallback}


def minimum_leave(options):
    events=[]
    for o in options:
        for m in o.meetings:
            for w in m.weeks.weeks:
                events.append((w,m.weekday,m.start_period,m.end_period))
    edges=[(i,j) for i,a in enumerate(events) for j,b in enumerate(events[:i])
           if a[:2]==b[:2] and a[2]<=b[3] and b[2]<=a[3]]
    for count in range(len(events)+1):
        for chosen in itertools.combinations(range(len(events)),count):
            cover=set(chosen)
            if all(i in cover or j in cover for i,j in edges):return count
    raise AssertionError


def suite_leave():
    from app.scheduling.adjustments import suggest_adjustment
    rng=random.Random(931);cases=200
    for i in range(cases):
        courses=tuple(Course(str(c),str(c),f'课{c}') for c in range(4))
        groups=[]
        for c in courses:
            group=[]
            for j in range(2):
                oid=f'{c.id}:{j}';start=rng.randint(1,3)
                m=Meeting(WeekMask.from_weeks(rng.sample([1,2],rng.randint(1,2))),1,start,start+1)
                group.append(SectionOption(oid,c.id,(TeachingSection(oid,c.id,str(j),(),(m,)),)))
            groups.append(group)
        expected=min(minimum_leave(selection) for selection in itertools.product(*groups))
        problem=SchedulingProblem(courses,tuple(CourseRequest(c.id) for c in courses),tuple(itertools.chain.from_iterable(groups)))
        actual=suggest_adjustment(problem)
        assert actual['optimal'] and actual['leave_count']==expected,(i,actual,expected)
        assert not actual['remove_courses']
    return {'independent_minimum_leave_cases':cases}


def suite_scale():
    readings=[]
    for count,alternatives,budget,required in [(200,10,.05,False),(100,8,.01,False),(100,8,.05,True),(200,11,.05,False)]:
        courses=tuple(Course(str(i),str(i),str(i)) for i in range(count))
        options=tuple(SectionOption(f'{i}:{j}',str(i),(TeachingSection(f'{i}:{j}',str(i),str(j),(),
                     (Meeting(WeekMask.all(21),1,1,2),)),)) for i in range(count) for j in range(alternatives))
        start=time.perf_counter()
        result=ScheduleSolver(SolverConfig(max_solutions=100,time_limit_seconds=budget)).solve(
            SchedulingProblem(courses,tuple(CourseRequest(c.id,required=required) for c in courses),options))
        elapsed=time.perf_counter()-start
        assert elapsed<5,(count,alternatives,elapsed)
        if count*alternatives>2000:assert result.status.value=='data_error'
        readings.append({'courses':count,'options':len(options),'budget_seconds':budget,'wall_seconds':round(elapsed,4),'status':result.status.value})
    return {'scale_cases':readings}


def api_context(directory):
    from contextlib import contextmanager
    from app.main import create_app
    from app.api.dependencies import get_session
    from app.infrastructure.database import create_database_engine,initialize_database
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient

    @contextmanager
    def manager():
        engine=create_database_engine(f"sqlite:///{(Path(directory)/'isolated.db').as_posix()}")
        initialize_database(engine);factory=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
        app=create_app(serve_frontend=False)
        def session():
            with factory() as db:
                try:yield db;db.commit()
                except Exception:db.rollback();raise
        app.dependency_overrides[get_session]=session
        try:
            with TestClient(app,raise_server_exceptions=False) as client:yield client
        finally:engine.dispose()
    return manager()


def register(client,name):
    response=client.post('/api/auth/register',json={'username':name,'password':'isolated-test-password-031'})
    assert response.status_code==201,response.text[:250]
    return {'Authorization':'Bearer '+response.json()['access_token']}


def suite_accounts():
    with tempfile.TemporaryDirectory(dir=OUT,prefix='accounts-') as directory,api_context(directory) as client:
        headers=[register(client,f'stress_student_{i}') for i in range(25)]
        codes=[]
        def login(i):
            for _ in range(100):
                response=client.post('/api/auth/login',json={'username':f'stress_student_{i}','password':'isolated-test-password-031'})
                codes.append(response.status_code)
                if response.status_code==429:time.sleep(.05);continue
                assert response.status_code==200,response.text[:100]
                return response.json()['access_token']
            raise AssertionError('login retry budget exhausted')
        with ThreadPoolExecutor(max_workers=8) as pool:
            tokens=list(pool.map(login,range(25)))
        assert len(set(tokens))==25
        for i,h in enumerate(headers):
            value={'input_mode':'manual','manual_courses':[{'course_id':f'private-{i}'}]}
            assert client.put('/api/plans/draft',headers=h,json=value).status_code==200
        def read(i):
            h=headers[i%25]
            r=client.get('/api/plans/draft',headers=h)
            assert r.status_code==200,r.text[:100]
            assert r.json()['draft']['manual_courses'][0]['course_id']==f'private-{i%25}'
            return r.status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert len(list(pool.map(read,range(400))))==400
        def duplicate(_):
            return client.post('/api/auth/register',json={'username':'same_new_username','password':'isolated-test-password-031'}).status_code
        with ThreadPoolExecutor(max_workers=8) as pool:duplicates=list(pool.map(duplicate,range(25)))
        assert duplicates.count(201)==1 and set(duplicates)<={201,409,429},duplicates
        r=client.get('/api/plans/draft',headers={'Authorization':'Bearer invalid'})
        assert r.status_code==401
        return {'local_accounts':25,'same_password_logins':25,'login_http_codes':dict(Counter(codes)),
                'concurrent_isolated_draft_reads':400,'duplicate_registration_http_codes':dict(Counter(duplicates))}


def workbook(code='90000000'):
    from openpyxl import Workbook
    from app.importers.standard_excel import STANDARD_HEADERS
    book=Workbook();sheet=book.active;sheet.append(STANDARD_HEADERS)
    sheet.append([code,'压力测试课'+code,'0001','','1-5,7-8','周二',5,6,'',''])
    output=BytesIO();book.save(output);return output.getvalue()


def suite_import():
    with tempfile.TemporaryDirectory(dir=OUT,prefix='imports-') as directory,api_context(directory) as client:
        h=register(client,'import_stress');cycles=[]
        real=[('files',(f'课程课表 ({i}).zip',(Path(os.environ.get('KESHI_CATALOG_INPUT', str(ROOT.parents[1]/'资料/2026秋课程总库')))/f'课程课表 ({i}).zip').read_bytes())) for i in (1,2,3)]
        for i in range(3):
            start=time.perf_counter();r=client.post('/api/catalog/import',headers=h,files=real)
            assert r.status_code==200,r.text[:500]
            assert r.json()['courses']==1212 and r.json()['sections']==3318,r.text[:500]
            before=client.get('/api/catalog/status',headers=h).json()['snapshots']
            failed=client.post('/api/catalog/import',headers=h,files=[real[0],('files',('broken.zip',b'broken'))])
            assert failed.status_code==422
            assert client.get('/api/catalog/status',headers=h).json()['snapshots']==before
            cycles.append(round(time.perf_counter()-start,3))
        files=[]
        for package in range(5):
            archive=BytesIO()
            with ZipFile(archive,'w',ZIP_DEFLATED) as z:
                for i in range(1000):
                    code=str(91000000+package*1000+i)
                    z.writestr(f'课表/{code}.xlsx',workbook(code))
            files.append(('files',(f'part{package}.zip',archive.getvalue())))
        start=time.perf_counter();r=client.post('/api/catalog/import',headers=h,files=files)
        assert r.status_code==200,r.text[:500]
        assert r.json()['courses']==5000 and r.json()['sections']==5000,r.text[:500]
        elapsed=round(time.perf_counter()-start,3)
        assert client.delete('/api/catalog',headers=h).status_code==204
        assert client.get('/api/catalog/status',headers=h).json()['course_count']==0
        assert client.get('/api/auth/me',headers=h).status_code==200
        return {'real_1212_course_import_replace_cycles':3,'cycle_wall_seconds':cycles,
                'atomic_failure_checks':3,'synthetic_workbooks_in_5_zips':5000,'synthetic_import_seconds':elapsed}


def suite_malformed():
    with tempfile.TemporaryDirectory(dir=OUT,prefix='malformed-') as directory,api_context(directory) as client:
        h=register(client,'malformed_stress');rng=random.Random(931031)
        assert client.put('/api/profile',headers=h,json={'college':'测试学院','major':'测试专业','cohort_year':2024}).status_code==200
        statuses=[]
        for i in range(300):
            choice={'course_id':'custom:stress','custom':{'name':'压力测试','sections':[{'id':'one','meetings':[{'weeks':[1,3,5],'weekday':1,'start_period':1,'end_period':2}]}]}}
            meeting=choice['custom']['sections'][0]['meetings'][0]
            key=rng.choice(['weeks','weekday','start_period','end_period'])
            meeting[key]=rng.choice([None,{},[], -999,999999,'not a time',[0,65],True])
            r=client.post('/api/plans/generate',headers=h,json={'input_mode':'manual','manual_courses':[choice]})
            assert r.status_code in {200,422},(i,r.status_code,r.text[:200])
            statuses.append(r.status_code)
        for malformed_index,data in enumerate([b'\x00',b'['*2000+b']'*2000,b'{bad json',b'\xff',b'"unterminated']):
            r=client.put('/api/plans/draft',headers={**h,'Content-Type':'application/json'},content=data)
            assert r.status_code in {400,422},(malformed_index,r.status_code,r.text[:100])
        r=client.put('/api/plans/draft',headers={**h,'Content-Type':'application/json','Content-Length':str(4*1024*1024+1)},content=b'{}')
        assert r.status_code==413
        direct=client.post('/api/catalog/import-courses',headers=h,files=[('files',(f'{i}.xlsx',workbook())) for i in range(21)])
        assert direct.status_code==422
        zbuf=BytesIO()
        with ZipFile(zbuf,'w',ZIP_DEFLATED) as z:z.writestr('../outside.xlsx',workbook())
        r=client.post('/api/catalog/import',headers=h,files=[('files',('traversal.zip',zbuf.getvalue()))])
        assert r.status_code==422
        zbuf=BytesIO()
        with ZipFile(zbuf,'w',ZIP_DEFLATED) as z:
            for i in range(2501):z.writestr(str(i)+'.txt',b'')
        r=client.post('/api/catalog/import',headers=h,files=[('files',('many.zip',zbuf.getvalue()))])
        assert r.status_code==422
        # Header advertises a much larger decompressed member; rejection precedes expansion.
        zbuf=BytesIO()
        with ZipFile(zbuf,'w',ZIP_DEFLATED) as z:z.writestr('oversized.xlsx',b'x')
        raw=bytearray(zbuf.getvalue());pos=raw.index(b'PK\x01\x02');raw[pos+24:pos+28]=(64*1024*1024).to_bytes(4,'little')
        r=client.post('/api/catalog/import',headers=h,files=[('files',('oversized.zip',bytes(raw)))])
        assert r.status_code==422
        assert client.get('/api/health').status_code==200
        return {'malformed_time_payloads':300,'time_payload_http_codes':dict(Counter(statuses)),
                'malformed_json_cases':5,'oversized_request_rejected':True,'upload_count_traversal_entry_size_guards':4}


def suite_non_blocking():
    from dataclasses import replace
    rng=random.Random(931915);validated=0
    for _ in range(300):
        original=random_problem(rng)
        options=[]
        for option in original.options:
            m=option.meetings[0]
            if rng.random()<.5:
                m=Meeting(m.weeks,precision=TimePrecision.NON_BLOCKING)
            options.append(replace(option,sections=(replace(option.sections[0],meetings=(m,)),)))
        problem=replace(original,options=tuple(options))
        expected=oracle(problem)
        result=ScheduleSolver(SolverConfig(max_solutions=3,time_limit_seconds=1)).solve(problem)
        possible={selection:score for score,selection in expected}
        if expected:
            assert result.plans and result.plans[0].coverage_score==max(s for s,_ in expected)
        else:assert not result.plans
        for plan in result.plans:
            assert possible[frozenset(plan.selected_option_ids)]==plan.coverage_score
            validated+=1
    return {'mixed_exact_and_non_blocking_problems':300,'independently_checked_plans':validated}


SUITES={'oracle':suite_oracle,'leave':suite_leave,'scale':suite_scale,'accounts':suite_accounts,'import':suite_import,'malformed':suite_malformed,'non_blocking':suite_non_blocking}
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('suite',choices=SUITES);args=parser.parse_args()
    start=time.perf_counter();data=SUITES[args.suite]();data['wall_seconds']=round(time.perf_counter()-start,3)
    (OUT/f'{args.suite}-result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False,indent=2))
