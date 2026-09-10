"""Bounded baseline probes. Uses only a new temporary database."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / "tmp" / "stress-031"
out.mkdir(parents=True, exist_ok=True)
os.environ["CUG_PLANNER_DATABASE_URL"] = f"sqlite:///{(out/'probe-bootstrap.db').as_posix()}"

from app.domain import Course, CourseRequest, Meeting, SchedulingProblem, SectionOption, TeachingSection, WeekMask
from app.infrastructure.database import create_database_engine, initialize_database
from app.infrastructure.security import create_login_session, get_user_for_token
from app.infrastructure.tables import User
from app.scheduling.solver import ScheduleSolver, SolverConfig
from sqlalchemy.orm import Session

results = {}
with tempfile.TemporaryDirectory(prefix="keshi-baseline-", dir=out) as d:
    path=Path(d)/'account.db'
    engine=create_database_engine(f"sqlite:///{path.as_posix()}")
    initialize_database(engine)
    with Session(engine) as db:
        user=User(username='probe_only',password_hash='unused');db.add(user);db.flush()
        token,_=create_login_session(db,user,lifetime_hours=1);db.commit()
    with Session(engine) as reading:
        assert get_user_for_token(reading,token)
        with closing(sqlite3.connect(path,timeout=.15)) as writer:
            try:
                writer.execute("UPDATE users SET username='probe_only' WHERE id=1")
                writer.commit()
                results['authenticated_read_takes_write_lock']=False
            except sqlite3.OperationalError:
                results['authenticated_read_takes_write_lock']=True
    engine.dispose()

weeks=WeekMask.from_weeks([1])
overlap=(Meeting(weeks,1,1,2,room='A101'),Meeting(weeks,1,1,2,room='B202'))
option=SectionOption('s','c',(TeachingSection('s','c','0001',(),overlap),))
result=ScheduleSolver(SolverConfig(max_solutions=1)).solve(SchedulingProblem(
    (Course('c','c','同一班内部撞课'),),(CourseRequest('c'),),(option,)))
results['internally_conflicting_class_selected']=any('s' in p.selected_option_ids for p in result.plans)

courses=tuple(Course(str(i),str(i),f'课程{i}') for i in range(100))
options=tuple(SectionOption(f'{i}:{j}',str(i),(TeachingSection(f'{i}:{j}',str(i),str(j),(),
    (Meeting(WeekMask.all(21),1,1,2),)),)) for i in range(100) for j in range(8))
start=time.perf_counter()
result=ScheduleSolver(SolverConfig(max_solutions=1,time_limit_seconds=.01)).solve(
    SchedulingProblem(courses,tuple(CourseRequest(c.id) for c in courses),options))
results['800_options_with_10ms_solver_budget_seconds']=round(time.perf_counter()-start,3)
results['solver_status']=result.status.value
(out/'latest-probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
