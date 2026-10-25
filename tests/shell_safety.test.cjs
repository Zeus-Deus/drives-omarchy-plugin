const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),cp=require('node:child_process');
const repo=path.resolve(__dirname,'..');
function model(){const c=vm.createContext({});vm.runInContext(fs.readFileSync(path.join(repo,'Model.js'),'utf8'),c);return c;}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
// Publish complete identities atomically. An empty, merely-created receipt
// parses to PID0 with Number(''), and signalling 0 kills the test's whole group.
function publisher(file){return `def publish(pids):
 records=[{'pid':p,'start':open('/proc/'+str(p)+'/stat').read().rsplit(')',1)[1].split()[19]} for p in pids]
 with open(${JSON.stringify(file+'.pending')},'w') as f: json.dump(records,f)
 os.replace(${JSON.stringify(file+'.pending')},${JSON.stringify(file)})`;}
function receipt(raw){const rows=JSON.parse(raw);assert.ok(Array.isArray(rows)&&rows.length>0);for(const r of rows)assert.ok(Number.isSafeInteger(r.pid)&&r.pid>1&&typeof r.start==='string'&&/^\d+$/.test(r.start));assert.equal(new Set(rows.map(r=>r.pid)).size,rows.length);return rows;}
function alive(r){try{return fs.readFileSync('/proc/'+r.pid+'/stat','utf8').split(')').slice(1).join(')').trim().split(/\s+/)[19]===r.start;}catch(e){if(e.code==='ENOENT'||e.code==='ESRCH')return false;throw e;}}
function cleanupOwned(rows){
 if(!rows.length)return;
 receipt(JSON.stringify(rows));
 // Pin the identity before signalling; a reaped child's PID can be reused.
 const script=`import json,os,signal,sys
for r in json.load(sys.stdin):
 assert type(r['pid']) is int and r['pid']>1
 fd=None
 try:
  fd=os.pidfd_open(r['pid'])
  current=open('/proc/'+str(r['pid'])+'/stat').read().rsplit(')',1)[1].split()[19]
  if current==r['start']:signal.pidfd_send_signal(fd,signal.SIGKILL)
 except (FileNotFoundError,ProcessLookupError):pass
 finally:
  if fd is not None:os.close(fd)`;
 const result=cp.spawnSync('/usr/bin/python3',['-B','-c',script],{input:JSON.stringify(rows),encoding:'utf8',timeout:5000});
 assert.equal(result.status,0,result.stderr||String(result.error));
}
test('producer receipts reject empty, partial, zero and nonidentity PIDs before cleanup',()=>{for(const raw of ['', '[]','[0]','[{"pid":0,"start":"1"}]','[{"pid":1,"start":"1"}]','[{"pid":42,"start":1}]'])assert.throws(()=>receipt(raw));assert.deepEqual(receipt('[{"pid":42,"start":"100"}]'),[{pid:42,start:'100'}]);});
// Execute only in the owned guest copy. Producer is harmless and self expires.
for(const signal of ['SIGTERM','SIGKILL']) test('bounded producer cleanup includes nested setsid children on '+signal,{skip:process.env.DRIVES_SHELL_VM!=='1'},async()=>{
 const dir=fs.mkdtempSync(path.join(process.env.TMPDIR||process.cwd(),'tree-')), pidfile=path.join(dir,'pids');
 const program=`import os,time,json\n${publisher(pidfile)}\na=os.fork()\nif a==0:\n os.setsid(); b=os.fork()\n if b==0:\n  publish([os.getpid()])\n  time.sleep(12)\n else: time.sleep(12)\nelse: time.sleep(12)`;
 const argv=model().boundedArgv(['/usr/bin/python3','-c',program],path.join(repo,'supervisor.py'),2,4096);
 const child=cp.spawn(argv[0],argv.slice(1),{stdio:['ignore','pipe','ignore'],detached:true});
 let owned=[];
 try {
  for(let i=0;i<100&&!fs.existsSync(pidfile);i++)await sleep(20);
  assert.ok(fs.existsSync(pidfile),'producer ready'); owned=receipt(fs.readFileSync(pidfile,'utf8')); child.kill(signal);
  for(let i=0;i<100&&owned.some(alive);i++)await sleep(20);
  assert.deepEqual(owned.filter(alive),[],'new-session descendant was killed and reaped');
 } finally {child.kill('SIGKILL');cleanupOwned(owned);fs.rmSync(dir,{recursive:true,force:true});}
});

for (const mode of ['deadline','overflow','leader-exit','owner-destruction']) test('whole producer tree is reaped after '+mode,{skip:process.env.DRIVES_SHELL_VM!=='1'},async()=>{
 const dir=fs.mkdtempSync(path.join(process.env.TMPDIR||process.cwd(),'tree-more-')),file=path.join(dir,'pids');
 const body=`import os,time,json
${publisher(file)}
root=os.getpid(); a=os.fork()
if a==0:
 os.setsid(); b=os.fork()
 if b==0:
  publish([root,os.getppid(),os.getpid()])
  ${mode==='leader-exit'?'os.close(1)':'pass'}
  time.sleep(8)
 else:
  ${mode==='leader-exit'?'os.close(1)':'pass'}
  time.sleep(8)
else:
 time.sleep(.15)
 ${mode==='overflow'?"print('a'*8192,flush=True)":mode==='leader-exit'?'os._exit(0)':'pass'}
 ${mode==='leader-exit'?'pass':'time.sleep(8)'}`;
 const argv=model().boundedArgv(['/usr/bin/python3','-c',body],path.join(repo,'supervisor.py'),mode==='deadline'?.35:2,4096);
 let child,owned=[];
 try{
  if(mode==='owner-destruction'){
   const parent=`const c=require('node:child_process').spawn(${JSON.stringify(argv[0])},${JSON.stringify(Array.from(argv.slice(1)))},{stdio:['ignore','pipe','ignore']});setInterval(()=>{},1000);`;
   child=cp.spawn(process.execPath,['-e',parent],{stdio:'ignore'});
  }else child=cp.spawn(argv[0],argv.slice(1),{stdio:['ignore','pipe','ignore']});
  if(child.stdout)child.stdout.resume();
  for(let i=0;i<100&&!fs.existsSync(file);i++)await sleep(10);
  assert.ok(fs.existsSync(file),'producer tree ready');owned=receipt(fs.readFileSync(file,'utf8'));
  if(mode==='owner-destruction')child.kill('SIGKILL');
  for(let i=0;i<300&&owned.some(alive);i++)await sleep(10);
  assert.deepEqual(owned.filter(alive),[],'all descendants already reaped');
 }finally{if(child)child.kill('SIGKILL');cleanupOwned(owned);fs.rmSync(dir,{recursive:true,force:true});}
});
