#!/usr/bin/env python3
"""Account boundaries and review cleanup in disposable homes and repositories."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "terminal/work.py"

GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['auth', 'status']:
    print(json.dumps({'hosts': {'github.com': [{'login':'alice','active':True,'token':'masked-secret'}, {'login':'bob','active':False}], 'git.example.test':[{'login':'carol','active':True}]}}))
    raise SystemExit(0)
if args[:2] == ['auth','token']:
    assert not any(os.getenv(k) for k in ['GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN','GH_HOST'])
    host = args[args.index('--hostname')+1]
    login = args[args.index('--user')+1]
    print('secret-'+host+'-'+login)
    raise SystemExit(0)
host = os.environ['GH_HOST']
key = 'GH_TOKEN' if host == 'github.com' or host.endswith('.ghe.com') else 'GH_ENTERPRISE_TOKEN'
token = os.environ[key]
assert token.startswith('secret-'+host+'-')
assert not os.getenv('GH_ENTERPRISE_TOKEN' if key == 'GH_TOKEN' else 'GH_TOKEN')
assert not os.getenv('GITHUB_TOKEN') and not os.getenv('GITHUB_ENTERPRISE_TOKEN')
login = token.removeprefix('secret-'+host+'-')
with open(os.environ['FIXTURE_LOG'], 'a') as stream:
    stream.write(json.dumps({'args':args,'host':host,'login':login})+'\n')
if args[:2] == ['api','user']:
    print('wrong-user' if os.getenv('CONFUSED_IDENTITY') else login)
elif args[:2] == ['search','prs']:
    if os.getenv('GITHUB_UNAVAILABLE'):
        print(token, file=sys.stderr)
        raise SystemExit(1)
    n = 2 if '--review-requested' in args else 1
    print(json.dumps([{'number':n,'title':login+' PR','url':'https://'+host+'/owner/repo/pull/'+str(n),'repository':{'nameWithOwner':'owner/repo'},'updatedAt':'2026-10-02T00:00:00Z','isDraft':False}]))
elif args[:2] == ['pr','view']:
    value = {'number':1,'title':'Review','url':'https://'+host+'/owner/repo/pull/1','headRefOid':os.environ['FIXTURE_HEAD'],'state':'OPEN'}
    if 'statusCheckRollup' in args[args.index('--json')+1]:
        value.update({'body':'A bounded PR description','reviewDecision':'APPROVED','statusCheckRollup':[{'__typename':'CheckRun','name':'tests','status':'COMPLETED','conclusion':'SUCCESS'}]})
        if os.getenv('UNKNOWN_CHECK'):
            value['statusCheckRollup'][0]['conclusion'] = 'FUTURE_VALUE'
    print(json.dumps(value))
elif args[:2] == ['pr','checkout']:
    assert '--detach' in args and '--force' not in args
    path = args[args.index('--worktree')+1]
    subprocess.run(['git','worktree','add','--detach',path,os.environ['FIXTURE_HEAD']], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
else:
    raise SystemExit('Unexpected fixture gh command')
'''

ACLI = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:3] == ['jira','workitem','search']:
    if os.getenv('JIRA_UNAVAILABLE'):
        print('auth token private-value',file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps([{'key':'TEAM-1','fields':{'summary':'Assigned task','status':{'name':'In Progress'}}}]))
elif args[:3] == ['jira','workitem','create']:
    assert '--json' in args and '--description-file' in args
    from pathlib import Path
    description = Path(args[args.index('--description-file')+1]).read_text()
    assert description == 'Reviewed description\n'
    print(json.dumps({'unexpected':True} if os.getenv('JIRA_CREATE_MALFORMED') else {'key':'TEAM-9','id':'10001','self':'https://jira.example.test/rest/api/3/issue/10001'}))
else:
    raise SystemExit('Ticket mutation not authorized by fixture')
'''


class WorkTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="work panel tests ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repository with spaces"
        self.repo.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        editor = '#!/usr/bin/env python3\nfrom pathlib import Path\nimport os, sys\np=Path(sys.argv[-1])\nif p.is_file(): p.write_text("Reviewed description\\n")\nelif os.getenv("EDIT_REVIEW"): (p/"tracked").write_text("Editor changes\\n")\n'
        for name, body in [('gh',GH),('acli',ACLI),('fixture-editor',editor)]:
            file = self.bin / name
            file.write_text(body)
            file.chmod(0o755)
        self.env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update({'HOME':str(self.home),'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],
                         'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1',
                         'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@example.test',
                         'GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@example.test',
                         'FIXTURE_LOG':str(self.root/'commands.jsonl'),
                         'GH_TOKEN':'ambient-secret','GITHUB_TOKEN':'ambient-secret',
                         'GH_ENTERPRISE_TOKEN':'ambient-secret','GITHUB_ENTERPRISE_TOKEN':'ambient-secret',
                         'GH_HOST':'ambient-host.test'})
        self.git(self.repo,'init','-b','main')
        self.git(self.repo,'config','core.hooksPath',os.devnull)
        (self.repo/'tracked').write_text('baseline\n')
        (self.repo/'.gitignore').write_text('ignored\n')
        self.git(self.repo,'add','--all')
        self.git(self.repo,'commit','-m','fixture')
        self.git(self.repo,'remote','add','origin','https://github.com/owner/repo.git')
        self.env['FIXTURE_HEAD'] = self.git(self.repo,'rev-parse','HEAD').strip()
        self.state = self.root/'state'
        self.config = self.root/'config.json'
        self.write_config([
            {'name':'personal','hostname':'github.com','login':'alice'},
            {'name':'work','hostname':'github.com','login':'bob'},
            {'name':'enterprise','hostname':'git.example.test','login':'carol'},
        ])

    def write_config(self,accounts):
        self.config.write_text(json.dumps({'type':'WORK_CONFIG','version':1,'accounts':accounts,
            'repositories':[{'hostname':'github.com','owner':'owner','name':'repo','path':str(self.repo)}],
            'jira':{'project':'TEAM'},'editor':['fixture-editor']}))

    def git(self,path,*args):
        run = subprocess.run(['git','-C',str(path),*args],env=self.env,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr)
        return run.stdout

    def cli(self,*args,expected=0,extra=None):
        self.assertTrue(CLI.is_file(),'The requested work panel entry point is absent')
        run = subprocess.run([sys.executable,str(CLI),'--config',str(self.config),'--state-dir',str(self.state),*args],
            cwd=self.repo,env={**self.env,**(extra or {})},text=True,capture_output=True,timeout=20)
        if expected is None:
            self.assertNotEqual(run.returncode,0,run.stdout+run.stderr)
        else:
            self.assertEqual(run.returncode,expected,run.stdout+run.stderr)
        self.assertNotIn('secret-',run.stdout+run.stderr)
        self.assertNotIn('private-value',run.stdout+run.stderr)
        return run

    def review(self):
        run = self.cli('review','https://github.com/owner/repo/pull/1','--account','personal','--json','--no-editor')
        return json.loads(run.stdout)

    def test_list_scopes_three_accounts_and_preserves_jira_identity(self):
        payload = json.loads(self.cli('list','--json').stdout)
        self.assertEqual(payload['type'],'WORK_SNAPSHOT')
        self.assertEqual({item['account'] for item in payload['items'] if item['type']=='PULL_REQUEST'},
                         {'personal','work','enterprise'})
        self.assertEqual([item['key'] for item in payload['items'] if item['type']=='JIRA_TICKET'],['TEAM-1'])
        calls = [json.loads(line) for line in (self.root/'commands.jsonl').read_text().splitlines()]
        self.assertEqual({(call['host'],call['login']) for call in calls},
                         {('github.com','alice'),('github.com','bob'),('git.example.test','carol')})
        self.assertFalse(any(call['args'][:2]==['auth','switch'] for call in calls))

    def test_source_failure_is_visible_and_not_empty_success(self):
        run = self.cli('list','--json',expected=None,extra={'JIRA_UNAVAILABLE':'1'})
        payload = json.loads(run.stdout)
        self.assertTrue(any(source['type']=='SOURCE_FAILURE' and source['name']=='jira' for source in payload['sources']))
        self.assertTrue(payload['items'])

    def test_confused_identity_fails_before_pr_read(self):
        run = self.cli('list','--json',expected=None,extra={'CONFUSED_IDENTITY':'1'})
        self.assertIn('IDENTITY_MISMATCH',run.stdout+run.stderr)
        calls = [json.loads(line) for line in (self.root/'commands.jsonl').read_text().splitlines()]
        self.assertFalse(any(call['args'][:2]==['search','prs'] for call in calls))

    def test_accounts_only_prints_safe_identity_fields(self):
        self.write_config([])
        payload = json.loads(self.cli('accounts','--json').stdout)
        self.assertEqual(len(payload['accounts']),3)
        self.assertNotIn('masked-secret',json.dumps(payload))

    def test_review_and_clean_cleanup_use_real_detached_worktree(self):
        reviewed = self.review()
        path = Path(reviewed['path'])
        self.assertTrue(path.is_dir())
        self.assertEqual(self.git(path,'rev-parse','HEAD').strip(),self.env['FIXTURE_HEAD'])
        branch = subprocess.run(['git','-C',str(path),'symbolic-ref','-q','HEAD'],env=self.env,capture_output=True)
        self.assertNotEqual(branch.returncode,0)
        self.cli('cleanup',reviewed['session'])
        self.assertFalse(path.exists())
        self.assertNotIn(str(path),self.git(self.repo,'worktree','list','--porcelain'))
        self.assertFalse(any('secret-' in file.read_text() for file in self.state.rglob('*.json')))

    def test_editor_close_removes_clean_review_and_preserves_editor_changes(self):
        closed = json.loads(self.cli('review','1','--account','personal','--repo',str(self.repo),'--json').stdout)
        self.assertEqual(closed['lifecycle']['type'],'REVIEW_REMOVED')
        self.assertFalse(Path(closed['path']).exists())
        self.assertNotIn(closed['path'],self.git(self.repo,'worktree','list','--porcelain'))
        preserved = self.cli('review','1','--account','personal','--repo',str(self.repo),'--json',expected=None,extra={'EDIT_REVIEW':'1'})
        retained = json.loads(preserved.stdout)
        self.assertEqual(retained['lifecycle']['type'],'REVIEW_PRESERVED')
        self.assertEqual(retained['lifecycle']['failure']['reason'],'WORKTREE_DIRTY')
        self.assertIn(retained['session'],preserved.stderr)
        self.assertEqual((Path(retained['path'])/'tracked').read_text(),'Editor changes\n')
        self.assertTrue((self.state/'sessions'/f"{retained['session']}.json").exists())

    def test_cleanup_preserves_modified_untracked_ignored_and_new_commits(self):
        for change in ('modified','untracked','ignored','commit'):
            with self.subTest(change=change):
                reviewed = self.review()
                path = Path(reviewed['path'])
                if change=='modified':
                    (path/'tracked').write_text('edited\n')
                elif change in ('untracked','ignored'):
                    (path/change).write_text('user data\n')
                else:
                    (path/'tracked').write_text('committed\n')
                    self.git(path,'add','tracked')
                    self.git(path,'commit','-m','new review work')
                self.cli('cleanup',reviewed['session'],expected=None)
                self.assertTrue(path.is_dir())
                self.assertIn(str(path),self.git(self.repo,'worktree','list','--porcelain'))

    def test_review_requires_account_when_host_has_multiple_logins(self):
        self.cli('review','https://github.com/owner/repo/pull/1','--json',expected=None)
        self.assertFalse((self.state/'reviews').exists())

    def test_cleanup_rejects_manifest_path_tampering(self):
        reviewed = self.review()
        manifest = self.state/'sessions'/f"{reviewed['session']}.json"
        data = json.loads(manifest.read_text())
        data['path'] = str(self.repo)
        manifest.write_text(json.dumps(data))
        self.cli('cleanup',reviewed['session'],expected=None)
        self.assertTrue(self.repo.is_dir())
        self.assertTrue(Path(reviewed['path']).is_dir())

    def test_cleanup_rejects_lost_administrative_ownership_and_hidden_edits(self):
        reviewed = self.review()
        path = Path(reviewed['path'])
        administrative = Path(self.git(path,'rev-parse','--absolute-git-dir').strip())
        marker = administrative/'shell-config-work-session.json'
        original = marker.read_text()
        marker.write_text(json.dumps({'type':'WORKTREE_OWNERSHIP','session':'another-session'}))
        self.cli('cleanup',reviewed['session'],expected=None)
        self.assertTrue(path.exists())
        marker.write_text(original)
        self.git(path,'update-index','--assume-unchanged','tracked')
        (path/'tracked').write_text('hidden edit\n')
        self.assertEqual(self.git(path,'status','--porcelain'),'')
        self.cli('cleanup',reviewed['session'],expected=None)
        self.assertEqual((path/'tracked').read_text(),'hidden edit\n')

    def test_closed_local_overlay_and_selected_account_do_not_switch_gh(self):
        overlay = self.config.with_name('config.local.json')
        overlay.write_text(json.dumps({'type':'WORK_CONFIG_LOCAL','accounts':[{'name':'chosen','hostname':'github.com','login':'bob'}]}))
        self.cli('accounts','chosen')
        selection = json.loads((self.state/'account.json').read_text())
        self.assertEqual(selection['type'],'WORK_ACCOUNT_SELECTION')
        reviewed = json.loads(self.cli('review','1','--repo',str(self.repo),'--json','--no-editor').stdout)
        self.assertEqual(reviewed['account'],'chosen')
        overlay.write_text(json.dumps({'type':'WORK_CONFIG_LOCAL','token':'never allowed'}))
        self.cli('list','--json',expected=None)

    def test_enterprise_cloud_uses_cloud_token_family(self):
        self.write_config([{'name':'cloud-enterprise','hostname':'tenant.ghe.com','login':'cloud-user'}])
        payload = json.loads(self.cli('list','--json').stdout)
        self.assertEqual({item['hostname'] for item in payload['items'] if item['type']=='PULL_REQUEST'}, {'tenant.ghe.com'})

    def test_new_uses_editor_description_and_structured_creation_proof(self):
        created = json.loads(self.cli('new','--summary','A ticket','--project','TEAM').stdout)
        self.assertEqual(created['type'],'JIRA_CREATED')
        self.assertEqual(created['key'],'TEAM-9')
        run = self.cli('new','--summary','A ticket','--project','TEAM',expected=None,extra={'JIRA_CREATE_MALFORMED':'1'})
        self.assertIn('CREATE_UNVERIFIED',run.stderr)

    def test_preview_loads_checks_on_demand_and_unknown_states_fail_closed(self):
        listing = json.loads(self.cli('list','--json').stdout)
        calls = [json.loads(line) for line in (self.root/'commands.jsonl').read_text().splitlines()]
        self.assertFalse(any(call['args'][:2]==['pr','view'] for call in calls))
        data = self.root/'preview.json'
        data.write_text(json.dumps(listing['items']))
        argv = [sys.executable,str(CLI),'_preview',str(data),str(self.config),'0']
        run = subprocess.run(argv,env=self.env,text=True,capture_output=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('Checks: PASSING',run.stdout)
        self.assertIn('Review: APPROVED',run.stdout)
        self.assertIn('A bounded PR description',run.stdout)
        failed = subprocess.run(argv,env={**self.env,'UNKNOWN_CHECK':'1'},text=True,capture_output=True,timeout=15)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('INVALID_OUTPUT',failed.stdout+failed.stderr)


if __name__=='__main__':
    unittest.main()
