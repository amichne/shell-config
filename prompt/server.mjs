import http from 'node:http';
import {readFile,writeFile,mkdir,rename,realpath} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes,createHash} from 'node:crypto';
import {homedir} from 'node:os';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {defaults,validateSettings,generateTOML} from './model.mjs';
import {readRepository,refreshPR} from './context.mjs';
const here=dirname(fileURLToPath(import.meta.url)),previewCwd=process.cwd();
const configPath=join(process.env.XDG_CONFIG_HOME||join(homedir(),'.config'),'starship.toml');
let target;
try {target=await realpath(configPath);} catch {target=configPath;}
const settingsFile=join(dirname(target),'prompt.json');
const sourceBin=join(dirname(dirname(target)),'bin');
const token=randomBytes(24).toString('hex'),digest=s=>createHash('sha256').update(s).digest('hex');
let applied,writing=false;
const backupDir=join(process.env.XDG_STATE_HOME||join(homedir(),'.local/state'),'shell-config','prompt-backups');
const exec=promisify(execFile);
let starshipPath;
async function resolveStarship() {
  if (starshipPath) return starshipPath;
  try {
    starshipPath=(await exec('mise',['which','starship'],{encoding:'utf8',timeout:3000})).stdout.trim();
  } catch {
    starshipPath='starship';
  }
  return starshipPath;
}
async function savedSettings(){try{const s=JSON.parse(await readFile(settingsFile,'utf8'));return validateSettings(s).kind==='valid'?s:defaults;}catch{return defaults;}}
async function body(req){let body='';for await(const chunk of req){body+=chunk;if(body.length>16384)throw new Error('Request too large');}return JSON.parse(body);}
async function apply(s){
  const valid=validateSettings(s);if(valid.kind!=='valid')throw new Error(`Invalid setting: ${valid.field}`);
  const toml=generateTOML(valid.value),old=await readFile(target,'utf8');
  await mkdir(backupDir,{recursive:true,mode:0o700});
  const stamp=Date.now(),backup=join(backupDir,`${stamp}.toml`),tmp=join(backupDir,`${stamp}.new.toml`);
  await writeFile(backup,old,{mode:0o600});await writeFile(tmp,toml,{mode:0o600});
  const rendered=await exec(await resolveStarship(),['prompt','--status=1'],{cwd:previewCwd,encoding:'utf8',timeout:4000,maxBuffer:128*1024,env:{...process.env,PATH:`${sourceBin}:${process.env.PATH||''}`,STARSHIP_CONFIG:tmp}});
  if(rendered.stderr)throw new Error('Starship reported an error; the active config was not changed.');
  if((await readFile(target,'utf8'))!==old)throw new Error('The config changed while validating. Try again.');
  const tempTarget=target+'.editor-tmp';await writeFile(tempTarget,toml,{mode:0o600});await rename(tempTarget,target);
  const tempSettings=settingsFile+'.editor-tmp';await writeFile(tempSettings,JSON.stringify(valid.value,null,2)+'\n',{mode:0o600});await rename(tempSettings,settingsFile);
  applied={old,hash:digest(toml)};return {kind:'applied',backup};
}
const types={'.html':'text/html; charset=utf-8','.mjs':'text/javascript; charset=utf-8'};
const server=http.createServer(async(req,res)=>{
  const host=`127.0.0.1:${server.address().port}`;
  const send=(status,data,type='application/json')=>{res.writeHead(status,{'Content-Type':type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"});res.end(type==='application/json'?JSON.stringify(data):data);};
  if(req.headers.host!==host)return send(403,{error:'Invalid host'});
  try {
    const url=new URL(req.url,`http://${host}`),route=url.pathname;
    if(req.method==='GET'){
      if(route==='/api/state')return send(200,{settings:await savedSettings(),git:await readRepository(previewCwd),canUndo:!!applied});
      const files={'/':'editor.html','/index.html':'editor.html','/editor.mjs':'editor.mjs','/model.mjs':'model.mjs'};
      if(!files[route])return send(404,{error:'Not found'});
      const file=files[route];return send(200,await readFile(join(here,file)),file.endsWith('.html')?types['.html']:types['.mjs']);
    }
    if(req.method!=='POST')return send(405,{error:'Method not allowed'});
    if(req.headers.origin!==`http://${host}`||req.headers['x-editor-token']!==token)return send(403,{error:'Open the editor from its launch URL.'});
    if(writing)return send(409,{error:'An update is already running.'});
    const input=await body(req);writing=true;
    try {
      if(route==='/api/apply')return send(200,await apply(input));
      if(route==='/api/refresh-pr')return send(200,{result:await refreshPR(previewCwd),git:await readRepository(previewCwd)});
      if(route==='/api/undo'){
        if(!applied)return send(409,{error:'Nothing to undo in this editor session.'});
        if(digest(await readFile(target,'utf8'))!==applied.hash)return send(409,{error:'The config was edited elsewhere; undo would overwrite those changes.'});
        await writeFile(target+'.editor-tmp',applied.old,{mode:0o600});await rename(target+'.editor-tmp',target);applied=undefined;
        return send(200,{kind:'restored'});
      }
      return send(404,{error:'Not found'});
    }finally{writing=false;}
  }catch(e){return send(400,{error:e.message});}
});
server.listen(Number(process.env.PROMPT_EDITOR_PORT||0),'127.0.0.1',()=>{console.log(`Prompt editor: http://127.0.0.1:${server.address().port}/#${token}`);console.log('Ctrl-C stops the editor. Apply changes only when you choose Apply to shell.');});
