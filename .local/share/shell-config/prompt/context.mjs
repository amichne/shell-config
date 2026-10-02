import {execFile,spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile,writeFile,mkdir,rename,lstat,realpath} from 'node:fs/promises';
import {dirname,resolve,join} from 'node:path';
import {homedir} from 'node:os';
import {createHash,randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {performance} from 'node:perf_hooks';
import {defaults,validateSettings,parseStatus,parseUntracked,parseWorktrees,gitText,gitANSI} from './model.mjs';
const exec=promisify(execFile);
const cacheRoot=join(process.env.XDG_CACHE_HOME||join(homedir(),'.cache'),'shell-config','pull-requests');
const hash=s=>createHash('sha256').update(s).digest('hex');
function git(args,cwd,stage,{absentAllowed=false}={}) {
  // A detached POSIX process group lets the deadline stop Git's helpers as well
  // as Git itself. Never expose stderr, arguments, or repository paths in evidence.
  return new Promise(resolveResult=>{
    const started=performance.now(); let outcome='RUNNING';
    const chunks=[]; let bytes=0;
    const stop=()=>{
      if(child.pid) {
        try {process.kill(-child.pid,'SIGKILL');} catch(e) {if(e.code!=='ESRCH') child.kill('SIGKILL');}
      }
    };
    const child=spawn('git',args,{cwd,detached:true,stdio:['ignore','pipe','pipe'],env:{...process.env,GIT_OPTIONAL_LOCKS:'0',LC_ALL:'C'}});
    const evidence=result=>({stage,outcome:result,elapsedMs:Math.round(performance.now()-started)});
    const collect=(chunk,retain)=>{
      if(outcome!=='RUNNING') return;
      bytes+=chunk.length;
      if(bytes>4*1024*1024) {outcome='OUTPUT_LIMIT';clearTimeout(deadline);stop();return;}
      if(retain) chunks.push(chunk);
    };
    child.stdout.on('data',chunk=>collect(chunk,true));
    child.stderr.on('data',chunk=>collect(chunk,false));
    child.on('error',()=>{
      outcome='SPAWN_FAILED'; clearTimeout(deadline);
      resolveResult({kind:'unavailable',reason:'git-read-failed',evidence:evidence(outcome)});
    });
    child.on('close',code=>{
      clearTimeout(deadline);
      if(outcome==='SPAWN_FAILED') return;
      if(outcome==='TIMED_OUT') return resolveResult({kind:'unavailable',reason:'git-timeout',evidence:evidence(outcome)});
      if(outcome==='OUTPUT_LIMIT') return resolveResult({kind:'unavailable',reason:'git-output-limit',evidence:evidence(outcome)});
      if(code===0) return resolveResult({kind:'complete',stdout:Buffer.concat(chunks).toString('utf8'),evidence:evidence('COMPLETE')});
      if(absentAllowed && code===1) return resolveResult({kind:'complete',stdout:'',evidence:evidence('ABSENT')});
      resolveResult({kind:'unavailable',reason:'git-read-failed',evidence:evidence('FAILED')});
    });
    const deadline=setTimeout(()=>{outcome='TIMED_OUT';stop();},450);
  });
}
async function hasRepository(cwd) {
  if(process.env.GIT_DIR) return true;
  for(let d=resolve(cwd);;d=dirname(d)) {
    try {await lstat(join(d,'.git')); return true;} catch(e) {if(e.code!=='ENOENT') throw e;}
    if(dirname(d)===d) return false;
  }
}
async function readCache(key,oid) {
  try {
    const c=JSON.parse(await readFile(join(cacheRoot,key+'.json'),'utf8'));
    if(!Number.isFinite(c.checkedAt)||!['open','none','unavailable'].includes(c.kind)) return {kind:'unchecked'};
    if(c.kind==='open' && (!Number.isSafeInteger(c.number)||typeof c.base!=='string'||typeof c.draft!=='boolean')) return {kind:'unchecked'};
    return {...c,stale:Date.now()-c.checkedAt>15*60*1000||c.oid!==oid};
  } catch {return {kind:'unchecked'};}
}
export async function readRepository(cwd=process.cwd()) {
  try {
    if(!await hasRepository(cwd)) return {kind:'outside'};
    const [status,paths,worktrees,untrackedRead]=await Promise.all([
      git(['status','--porcelain=v2','--branch','--show-stash','--untracked-files=no','--ignore-submodules=none','-z'],cwd,'status'),
      git(['rev-parse','--path-format=absolute','--show-toplevel','--git-common-dir'],cwd,'paths'),
      git(['worktree','list','--porcelain','-z'],cwd,'worktrees'),
      git(['ls-files','--others','--exclude-standard','-z'],cwd,'untracked')
    ]);
    const evidence=[status,paths,worktrees,untrackedRead].map(read=>read.evidence);
    for(const read of [status,paths,worktrees]) if(read.kind!=='complete') return {kind:'unavailable',reason:read.reason,evidence};
    const parsed=parseStatus(status.stdout,'none'); if(parsed.kind!=='repository') return {...parsed,evidence};
    const untracked=untrackedRead.kind==='complete'?parseUntracked(untrackedRead.stdout):{kind:'unavailable',reason:untrackedRead.reason};
    const parts=paths.stdout.replace(/\n$/,'').split('\n');
    if(parts.length!==2) return {kind:'unavailable',reason:'unsupported-path',evidence};
    const [root,common]=parts;
    const origin=await git(['config','--get','remote.origin.url'],cwd,'origin',{absentAllowed:true});
    evidence.push(origin.evidence);
    if(origin.kind!=='complete') return {kind:'unavailable',reason:origin.reason,evidence};
    const key=hash(common+'\0'+(parsed.branch.name||parsed.branch.oid)+'\0'+origin.stdout);
    return {...parsed,untracked,root,cacheKey:key,evidence,worktree:parseWorktrees(worktrees.stdout,root),pr:await readCache(key,parsed.branch.oid)};
  } catch {return {kind:'unavailable',reason:'git-read-failed'};}
}
export async function refreshPR(cwd=process.cwd()) {
  const g=await readRepository(cwd);
  if(g.kind!=='repository'||g.branch.kind!=='named') return {kind:'unavailable',reason:'named-branch-required'};
  let p;
  try {
    const {stdout}=await exec('gh',['pr','status','--json','number,url,state,isDraft,headRefName,headRefOid,baseRefName','--jq','.currentBranch'],{cwd,encoding:'utf8',timeout:12000,maxBuffer:256*1024,env:{...process.env,GH_PROMPT_DISABLED:'1'}});
    p=JSON.parse(stdout);
  } catch {return {kind:'unavailable',reason:'github-query-failed'};}
  let result;
  if(p===null || (p && ['CLOSED','MERGED'].includes(p.state))) result={kind:'none'};
  else if(p && p.state==='OPEN' && p.headRefName===g.branch.name && Number.isSafeInteger(p.number) && p.number>0 && typeof p.isDraft==='boolean' && typeof p.baseRefName==='string' && /^https:\/\/[^\s]+\/pull\/\d+$/.test(p.url)) {
    result={kind:'open',number:p.number,draft:p.isDraft,base:p.baseRefName,url:p.url};
  } else return {kind:'unavailable',reason:'unsupported-github-response'};
  const record={...result,checkedAt:Date.now(),oid:g.branch.oid};
  await mkdir(cacheRoot,{recursive:true,mode:0o700});
  const tmp=join(cacheRoot,randomUUID()+'.tmp');
  await writeFile(tmp,JSON.stringify(record),{mode:0o600});
  await rename(tmp,join(cacheRoot,g.cacheKey+'.json'));
  return record;
}
async function main() {
  const [mode,...args]=process.argv.slice(2);
  if(mode==='refresh-pr') {
    const result=await refreshPR();
    console.log(JSON.stringify(result)); process.exitCode=result.kind==='unavailable'?1:0; return;
  }
  if(mode!=='context') {console.error('usage: shell-prompt context [--json | --ansi --settings BASE64] | refresh-pr'); process.exitCode=2; return;}
  const ansi=args[0]==='--ansi';
  if(ansi) args.shift();
  let s=defaults;
  if(args[0]==='--settings' && args.length===2) {
    let decoded;
    try {decoded=JSON.parse(Buffer.from(args[1],'base64url').toString('utf8'));} catch {console.error('shell-prompt: stage=settings outcome=invalid'); process.exitCode=2; return;}
    const checked=validateSettings(decoded);
    if(checked.kind!=='valid') {console.error('shell-prompt: stage=settings outcome=invalid'); process.exitCode=2; return;}
    s=checked.value;
  } else if(args.length && !(args.length===1 && args[0]==='--json')) {process.exitCode=2;return;}
  const g=await readRepository();
  if(args[0]==='--json') console.log(JSON.stringify(g));
  else {
    const output=ansi?gitANSI(s,g):gitText(s,g); if(output) console.log(output);
    if(g.kind==='unavailable') console.error(`shell-prompt: stage=git outcome=${g.reason}`);
    else if(g.kind==='repository' && g.untracked.kind==='unavailable') console.error(`shell-prompt: stage=untracked outcome=${g.untracked.reason}`);
  }
}
const modulePath=await realpath(fileURLToPath(import.meta.url));
let entryPath='';
if(process.argv[1]) {
  try {entryPath=await realpath(process.argv[1]);} catch {entryPath=resolve(process.argv[1]);}
}
if(modulePath===entryPath) main().catch(()=>{console.error('shell-prompt: stage=runtime outcome=failed');process.exitCode=1;});
