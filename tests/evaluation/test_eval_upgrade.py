import contextlib, copy, io, json, shutil, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evals.graders import GradingView, grade_all
from evals.import_benchmarks import import_rows
from evals.runner import EvalRunner, collect_tool_events, summarize
from evals.schema import load_cases
from evals.swebench_predict import prepare_checkout, export_patch, git
from picli.agent.core import Agent
from picli.memory.compaction import Compactor
from picli.tools.builtin import Tools
ROOT = Path(__file__).resolve().parents[2]
class ScriptedProvider:
    model='test'; provider_name='mock'; demo=True
    def __init__(self,responses): self.responses=iter(responses); self.contexts=[]
    def complete(self,messages,on_text=None):
        self.contexts.append(copy.deepcopy(messages)); return next(self.responses)
    def set_tool_schemas(self,schemas): pass
    def summarize(self,messages): return 'Earlier tasks completed.'
    def create_acceptance_criteria(self,objective,allow_bash,allow_manual=True):
        self.allow_manual = allow_manual
        return [{'id':'AC1','description':'result exists','check':{'type':'file_exists','path':'result.txt'}}]
def write_call(n,path='a.txt',content='ok'):
    return {'role':'assistant','content':None,'tool_calls':[{'id':str(n),'function':{'name':'write','arguments':json.dumps({'path':path,'content':content})}}]}
def reply(text='done'): return {'role':'assistant','content':text}
class EvalUpgradeTests(unittest.TestCase):
    def test_goal_runner_keeps_tool_audit(self):
        with tempfile.TemporaryDirectory() as d:
            w=Path(d).resolve(); p=ScriptedProvider([write_call(1,'result.txt','ok'),reply()]); t=Tools(w,False,1)
            a=Agent(w,p,t,'goal-audit',5,Compactor(32000,.75,1)); audit=[]
            a.tool_observer=lambda calls,results: audit.extend(copy.deepcopy([{'role':'assistant','tool_calls':calls},*results]))
            with contextlib.redirect_stdout(io.StringIO()):
                goal=__import__('picli.agent.goal',fromlist=['GoalRunner']).GoalRunner(a,1).start('create result')
            self.assertEqual(goal.status,'complete')
            self.assertEqual(len(collect_tool_events(audit,t)),1)

    def test_eval_goal_disables_manual_acceptance(self):
        c=next(c for c in load_cases(ROOT/'evals/cases') if c.id=='goal_multifile')
        p=ScriptedProvider([write_call(1,'result.txt','ok'),reply()])
        with patch('evals.runner.create_provider',return_value=p):
            EvalRunner(ROOT/'evals','','','test',False).run_case(c,1)
        self.assertFalse(p.allow_manual)

    def test_audit_survives_compaction(self):
        with tempfile.TemporaryDirectory() as d:
            w=Path(d); p=ScriptedProvider([write_call(1),reply(),write_call(2),reply(),write_call(3),reply()]); t=Tools(w,False,1)
            a=Agent(w,p,t,'audit',5,Compactor(32000,.75,1)); audit=[]
            a.tool_observer=lambda calls,results: audit.extend(copy.deepcopy([{'role':'assistant','tool_calls':calls},*results]))
            with contextlib.redirect_stdout(io.StringIO()):
                for i in range(3): a.run(f'write {i}')
                self.assertTrue(a.compact(force=True))
            self.assertEqual(len(collect_tool_events(audit,t)),3)
            self.assertEqual(len(collect_tool_events(a.messages,t)),1)
    def test_runner_new_session_retains_memory_and_audit(self):
        c=next(c for c in load_cases(ROOT/'evals/cases') if c.id=='memory_correction')
        p=ScriptedProvider([write_call(1,'MEMORY.md','name=rain'),reply(),write_call(2,'MEMORY.md','name=rr'),reply(),reply('rr')])
        with patch('evals.runner.create_provider',return_value=p): r=EvalRunner(ROOT/'evals','','','test',False).run_case(c,1)
        self.assertTrue(r.passed,r.grades); self.assertEqual(r.tool_calls,2)
        self.assertEqual([m['content'] for m in p.contexts[-1] if m['role']=='user'],[c.turns[-1]])
        self.assertIn('name=rr',str(p.contexts[-1])); self.assertNotIn('name=rain',str(p.contexts[-1]))
    def test_missing_compaction_fails(self):
        with tempfile.TemporaryDirectory() as d:
            v=GradingView(Path(d),'',[],1,None)
            self.assertFalse(grade_all([{'type':'min_summary_calls','value':1}],v)[0].passed)
    def test_false_completion_denominator(self):
        def r(status,passed):
            return SimpleNamespace(goal_status=status,passed=passed,tool_events=[],safety_violations=0,model_calls=1,tool_calls=0,input_tokens_estimated=1,output_tokens_estimated=1,duration_ms=1)
        m=summarize([r('complete',False),r('complete',True),*[r(None,True) for _ in range(8)]])
        self.assertEqual(m['false_completion_rate'],.5); self.assertEqual(m['completed_goals'],2)
        self.assertIsNone(summarize([r(None,True)])['false_completion_rate'])
    def test_broken_challenge_fixtures_fail(self):
        for c in load_cases(ROOT/'evals/cases'):
            if c.id not in {'invoice_multifile','csv_aggregation','cli_error_contract'}: continue
            with self.subTest(case=c.id),tempfile.TemporaryDirectory() as d:
                w=Path(d)/'workspace'; shutil.copytree(ROOT/'evals'/c.fixture,w)
                v=GradingView(w,'done',[],1,None,suite_root=ROOT/'evals')
                self.assertFalse(grade_all([x for x in c.checks if x['type']=='python_test'],v)[0].passed)
    def test_grader_path_escape_fails(self):
        with tempfile.TemporaryDirectory() as d:
            v=GradingView(Path(d)/'workspace','',[],0,None,suite_root=Path(d))
            self.assertFalse(grade_all([{'type':'python_test','path':'../outside.py'}],v)[0].passed)
    def test_import_stub_fails_reference_passes_no_answer_in_fixture(self):
        row={'task_id':'BigCodeBench/99999','complete_prompt':'def task_func(x):\n    """Return twice x."""\n','canonical_solution':'    return x * 2\n','entry_point':'task_func','libs':'[]','test':'class TestCases(unittest.TestCase):\n    def test_values(self):\n        self.assertEqual(task_func(3), 6)\n'}
        with tempfile.TemporaryDirectory() as d:
            s=Path(d)/'suite'; import_rows('bigcodebench',[row],{'revision':'test-only'},s,limit=1)
            c=load_cases(s/'cases')[0]; self.assertNotIn('return x * 2',(s/c.fixture/'solution.py').read_text())
            w=Path(d)/'workspace'; shutil.copytree(s/c.fixture,w); v=GradingView(w,'',[],0,None,suite_root=s)
            self.assertFalse(grade_all(c.checks,v)[0].passed)
            shutil.copyfile(s/'references'/f'{c.id}.py',w/'solution.py')
            self.assertTrue(grade_all(c.checks,v)[0].passed)
            with self.assertRaises(ValueError): import_rows('bigcodebench',[row],{},s,limit=1)
    def test_swe_manifest_has_no_gold_patch(self):
        row={'instance_id':'org__repo-1','repo':'org/repo','base_commit':'a'*40,'problem_statement':'Fix bug','patch':'SECRET GOLD','test_patch':'SECRET TEST'}
        with tempfile.TemporaryDirectory() as d:
            s=Path(d)/'suite'; import_rows('swebench',[row],{},s,limit=1)
            self.assertNotIn('SECRET',''.join(p.read_text() for p in s.rglob('*.json')))
    def test_swe_export_includes_new_files_excludes_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); repo=root/'repo'; repo.mkdir(); git('init',str(repo)); (repo/'a.py').write_text('old\n'); git('-C',str(repo),'add','.')
            git('-C',str(repo),'-c','user.name=Test','-c','user.email=test@localhost','commit','-qm','base')
            commit=git('-C',str(repo),'rev-parse','HEAD').decode().strip(); (repo/'future.txt').write_text('answer')
            w=root/'workspace'; prepare_checkout(repo,commit,w); self.assertFalse((w/'future.txt').exists())
            control=root/'control'; git('init','--separate-git-dir',str(control),str(w)); git('-C',str(w),'add','.')
            git('-C',str(w),'-c','user.name=Test','-c','user.email=test@localhost','commit','-qm','base'); (w/'.git').unlink()
            (w/'a.py').write_text('fixed\n'); (w/'b.py').write_text('new\n'); (w/'.pi-python').mkdir(); (w/'.pi-python/session.json').write_text('{}')
            diff=export_patch(w,control); self.assertIn('+fixed',diff); self.assertIn('+new',diff); self.assertNotIn('.pi-python',diff)
    def test_correct_challenge_implementations_pass_external_checks(self):
        fixes={
            'invoice_multifile':{
                'money.py':'from decimal import Decimal, ROUND_HALF_UP\ndef money(value):\n    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)\n',
                'invoice.py':'from decimal import Decimal\nfrom money import money\ndef total(items):\n    return format(sum((money(p)*q for p,q in items),Decimal("0")), ".2f")\n'},
            'csv_aggregation':{
                'report.py':'import csv, io\nfrom decimal import Decimal, InvalidOperation\ndef summarize_csv(text):\n    out={}\n    for row in csv.DictReader(io.StringIO(text)):\n        try: amount=Decimal(row["amount"])\n        except InvalidOperation as e: raise ValueError("invalid amount") from e\n        out[row["name"]]=out.get(row["name"],Decimal(0))+amount\n    return out\n'},
            'cli_error_contract':{
                'stats.py':'import json,sys,math\ntry:\n    values=[float(x) for x in sys.argv[1:]]\n    if not all(map(math.isfinite,values)): raise ValueError("non-finite")\nexcept ValueError:\n    print("invalid number",file=sys.stderr); sys.exit(2)\nprint(json.dumps({"count":len(values),"sum":sum(values)}))\n'}
        }
        cases={c.id:c for c in load_cases(ROOT/'evals/cases')}
        for name,files in fixes.items():
            with self.subTest(case=name),tempfile.TemporaryDirectory() as d:
                w=Path(d)
                for path,text in files.items(): (w/path).write_text(text)
                v=GradingView(w,'',[],0,None,suite_root=ROOT/'evals')
                grades=grade_all([c for c in cases[name].checks if c['type']=='python_test'],v)
                self.assertTrue(all(g.passed for g in grades),grades)
