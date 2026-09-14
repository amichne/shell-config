// Pure settings, Git parsing, and display rules shared by the editor and CLI.
export const defaults = Object.freeze({
  version: 1, lines: 2, frame: 'none', marker: '❯', pathDepth: 3,
  blankLine: true, duration: true, exitStatus: true, durationMs: 2000,
  branch: true, remote: true, sync: true, changes: true, clean: true,
  worktree: true, pr: true, labels: 'words',
  colors: { directory: '#3a83f7', git: '#bd93f9', muted: '#9296ad', success: '#50fa7b', error: '#ff5555', background: '#282a36' }
});
const flags = ['blankLine','duration','exitStatus','branch','remote','sync','changes','clean','worktree','pr'];
export function validateSettings(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {kind:'invalid', field:'settings'};
  if (Object.keys(value).some(k => !(k in defaults))) return {kind:'invalid', field:'unknown setting'};
  for (const k of Object.keys(defaults)) if (!(k in value)) return {kind:'invalid', field:k};
  for (const k of flags) if (typeof value[k] !== 'boolean') return {kind:'invalid', field:k};
  if (value.version !== 1 || ![1,2].includes(value.lines)) return {kind:'invalid',field:'version or lines'};
  if (!['none','subtle'].includes(value.frame) || !['❯','>','➜','$'].includes(value.marker) || !['words','compact'].includes(value.labels)) return {kind:'invalid',field:'style'};
  if (!Number.isInteger(value.pathDepth) || value.pathDepth<1 || value.pathDepth>6 || !Number.isInteger(value.durationMs) || value.durationMs<500 || value.durationMs>10000) return {kind:'invalid',field:'range'};
  if (!value.colors || Object.keys(value.colors).length!==Object.keys(defaults.colors).length) return {kind:'invalid',field:'colors'};
  for (const k of Object.keys(defaults.colors)) if (!/^#[\da-f]{6}$/i.test(value.colors[k] || '')) return {kind:'invalid',field:`color ${k}`};
  return {kind:'valid', value:structuredClone(value)};
}
// Preserve identity internally; remove terminal control characters only on output.
export const display = s => String(s).replace(/[\x00-\x1f\x7f-\x9f]/g,'�').slice(0,160);
export function parseStatus(raw) {
  const rows=raw.split('\0'); if (rows.pop()!=='') return {kind:'unavailable',reason:'incomplete-status'};
  let head,oid,upstream,ab;
  const counts={staged:0,modified:0,untracked:0,conflicted:0,stashed:0};
  for(let i=0;i<rows.length;i++) {
    const r=rows[i];
    if(r.startsWith('# branch.head ')) head=r.slice(14);
    else if(r.startsWith('# branch.oid ')) oid=r.slice(13);
    else if(r.startsWith('# branch.upstream ')) upstream=r.slice(18);
    else if(r.startsWith('# branch.ab ')) {
      const m=/^# branch\.ab \+(\d+) -(\d+)$/.exec(r); if(!m) return {kind:'unavailable',reason:'invalid-tracking'};
      ab={ahead:Number(m[1]),behind:Number(m[2])};
    } else if(r.startsWith('# stash ')) {
      if(!/^# stash \d+$/.test(r)) return {kind:'unavailable',reason:'invalid-stash'};
      counts.stashed=Number(r.slice(8));
    } else if(r.startsWith('# ')) continue; // Git permits additional headers.
    else if(r.startsWith('? ')) counts.untracked++;
    else if(r.startsWith('! ')) continue;
    else if(/^[12] /.test(r)) {
      const fields=r.split(' '); const xy=fields[1];
      if(!/^[.MADRCUT]{2}$/.test(xy) || fields.length<(r[0]==='1'?9:10)) return {kind:'unavailable',reason:'invalid-entry'};
      if(xy[0]!=='.') counts.staged++;
      if(xy[1]!=='.' || /^S.[MU]/.test(fields[2]) || /^S..U/.test(fields[2])) counts.modified++;
      if(r[0]==='2' && (!rows[++i])) return {kind:'unavailable',reason:'incomplete-rename'};
    } else if(r.startsWith('u ') && r.split(' ').length>=11) counts.conflicted++;
    else return {kind:'unavailable',reason:'unsupported-entry'};
  }
  if(!head || !oid || (oid!=='(initial)' && !/^[a-f0-9]{40,64}$/.test(oid))) return {kind:'unavailable',reason:'missing-head'};
  const branch=head==='(detached)'?{kind:'detached',oid}:{kind:'named',name:head,oid};
  const tracking=upstream ? (ab?{kind:'tracked',name:upstream,...ab}:{kind:'unknown',name:upstream}) : {kind:'untracked'};
  return {kind:'repository',branch,tracking,counts,worktree:{kind:'unknown'},pr:{kind:'unchecked'}};
}
export function parseWorktrees(raw,root) {
  const blocks=raw.split('\0\0').filter(Boolean), trees=[];
  for(const b of blocks) {
    const entries=b.split('\0').filter(Boolean);
    if(!entries[0]?.startsWith('worktree ')) return {kind:'unknown'};
    trees.push({path:entries[0].slice(9),locked:entries.some(x=>x==='locked'||x.startsWith('locked ')),prunable:entries.some(x=>x==='prunable'||x.startsWith('prunable '))});
  }
  const idx=trees.findIndex(t=>t.path===root);
  if(idx<0) return {kind:'unknown'};
  return {kind:idx===0?'main':'linked',name:root.split('/').at(-1),total:trees.length,locked:trees[idx].locked,prunable:trees[idx].prunable};
}
export function gitParts(s,g) {
  if(g.kind==='outside') return [];
  if(g.kind!=='repository') return [{type:'error',text:'Git unavailable'}];
  const result=[]; const add=(type,text)=>result.push({type,text}); const compact=s.labels==='compact';
  if(s.branch) add('branch',g.branch.kind==='detached'?`detached ${g.branch.oid.slice(0,7)}`:display(g.branch.name));
  if(s.remote && g.tracking.kind!=='untracked') add('remote',`→ ${display(g.tracking.name)}`);
  if(s.sync) {
    switch(g.tracking.kind) {
      case 'untracked': add('sync','no upstream'); break;
      case 'unknown': add('sync','upstream unavailable'); break;
      case 'tracked': {
        const {ahead:a,behind:b}=g.tracking;
        add('sync',a||b?[a?(compact?`↑${a}`:`${a} ahead`):'',b?(compact?`↓${b}`:`${b} behind`):''].filter(Boolean).join(' / '):'synced'); break;
      }
    }
  }
  if(s.changes) {
    const labels={staged:compact?'+':'staged',modified:compact?'!':'modified',untracked:compact?'?':'untracked',conflicted:compact?'conflict:':'conflicted',stashed:compact?'stash:':'stashed'};
    const parts=Object.entries(g.counts).filter(([,v])=>v>0).map(([k,v])=>compact?`${labels[k]}${v}`:`${v} ${labels[k]}`);
    if(parts.length) add('changes',parts.join(' ')); else if(s.clean) add('changes','clean');
  }
  if(s.worktree) {
    const w=g.worktree;
    if(w.kind==='unknown') add('worktree','worktree unknown');
    else if(w.kind==='linked' || w.total>1) add('worktree',`${w.kind==='main'?'main worktree':`wt:${display(w.name)}`} / ${w.total} total${w.locked?' / locked':''}${w.prunable?' / prunable':''}`);
  }
  if(s.pr) {
    const p=g.pr;
    switch(p.kind) {
      case 'open': add('pr',`PR #${p.number}${p.draft?' draft':''} → ${display(p.base)}${p.stale?' / stale':''}`); break;
      case 'none': add('pr',p.stale?'PR cache stale':'no open PR'); break;
      case 'unavailable': add('pr','PR unavailable'); break;
      default: add('pr','PR not checked');
    }
  }
  return result;
}
export const gitText=(s,g)=>gitParts(s,g).map(p=>p.text).join(' · ');
function hexRGB(hex) {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}
export function gitANSI(s,g) {
  const parts=gitParts(s,g);
  return parts.map(part=>{
    const color = ['branch'].includes(part.type) ? s.colors.git
      : ['changes'].includes(part.type) ? '#f1fa8c'
      : ['pr'].includes(part.type) ? s.colors.directory
      : ['error'].includes(part.type) ? s.colors.error
      : s.colors.muted;
    const [r,g,b]=hexRGB(color);
    const weight=part.type==='branch'?';1':'';
    return `\u001b[38;2;${r};${g};${b}${weight}m${part.text}\u001b[0m`;
  }).join(' · ');
}
export function encodeSettings(s) {
  const bytes = new TextEncoder().encode(JSON.stringify(s));
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
}
const literal=s=>`'${s}'`;
export function generateTOML(s) {
  const valid=validateSettings(s); if(valid.kind!=='valid') throw new Error(`Invalid ${valid.field}`);
  const lead=s.frame==='subtle'?'[╭─](muted) ':'';
  const second=s.frame==='subtle'?'[╰─](muted) ':'';
  const context='$directory${custom.git_context}'+(s.duration?'$cmd_duration':'')+(s.exitStatus?'$status':'');
  const marker=s.marker==='$'?'\\$':s.marker;
  const fmt=s.lines===2?lead+context+'$line_break'+second+'$character':context+'$character';
  return `# Generated by the local prompt editor.\n# Remote counts use local tracking refs; PR information is explicitly refreshed.\n"$schema" = "https://starship.rs/config-schema.json"\npalette = "codex"\nformat = ${literal(fmt)}\nadd_newline = ${s.blankLine}\ncommand_timeout = 1200\n\n[palettes.codex]\n${Object.entries(s.colors).map(([k,v])=>`${k} = "${v}"`).join('\n')}\n\n[directory]\nformat = '[$path]($style)[$read_only]($read_only_style) '\nstyle = 'bold directory'\ntruncation_length = ${s.pathDepth}\ntruncate_to_repo = false\ntruncation_symbol = '…/'\nread_only = ' read-only'\n\n[custom.git_context]\ncommand = 'shell-prompt context --ansi --settings ${encodeSettings(s)}'\nwhen = true\nshell = ['sh']\nformat = '$output ' \n\n[cmd_duration]\nmin_time = ${s.durationMs}\nformat = '[took $duration](muted) '\n\n[status]\ndisabled = false\nformat = '[exit:$status](error) '\n\n[character]\nsuccess_symbol = ${literal(`[${marker}](success)`)}\nerror_symbol = ${literal(`[${marker}](error)`)}\nvimcmd_symbol = '[❮](success)'\n`;
}
const repository={kind:'repository',branch:{kind:'named',name:'feature/prompt-editor',oid:'a'.repeat(40)},tracking:{kind:'tracked',name:'origin/feature/prompt-editor',ahead:2,behind:1},counts:{staged:1,modified:3,untracked:0,conflicted:0,stashed:0},worktree:{kind:'linked',name:'shell-config.prompt',total:3,locked:false,prunable:false},pr:{kind:'open',number:42,draft:false,base:'main',stale:false}};
export const scenarios={
  working:{name:'Working branch',directory:'~/code/shell-config',command:'git diff',duration:2400,exit:0,git:repository},
  synced:{name:'Clean & synced',directory:'~/code/shell-config',command:'git status',duration:200,exit:0,git:{...repository,branch:{...repository.branch,name:'main'},tracking:{kind:'tracked',name:'origin/main',ahead:0,behind:0},counts:{staged:0,modified:0,untracked:0,conflicted:0,stashed:0},worktree:{kind:'main',name:'shell-config',total:1},pr:{kind:'none'}}},
  conflict:{name:'Conflicts & divergence',directory:'~/code/shell-config',command:'git merge origin/main',duration:800,exit:1,git:{...repository,tracking:{...repository.tracking,ahead:3,behind:5},counts:{staged:0,modified:2,untracked:1,conflicted:2,stashed:1},pr:{...repository.pr,draft:true}}},
  new:{name:'No upstream',directory:'~/code/shell-config',command:'git switch -c try-layout',duration:200,exit:0,git:{...repository,branch:{...repository.branch,name:'try-layout'},tracking:{kind:'untracked'},pr:{kind:'unchecked'}}},
  detached:{name:'Detached HEAD',directory:'~/code/shell-config',command:'git switch --detach HEAD~1',duration:200,exit:0,git:{...repository,branch:{kind:'detached',oid:'a90d74b'+'0'.repeat(33)},tracking:{kind:'untracked'},pr:{kind:'unchecked'}}},
  stale:{name:'Stale PR cache',directory:'~/code/shell-config',command:'npm test',duration:5200,exit:0,git:{...repository,pr:{...repository.pr,stale:true}}},
  outside:{name:'Outside Git',directory:'~/Downloads',command:'ls',duration:100,exit:0,git:{kind:'outside'}}
};
