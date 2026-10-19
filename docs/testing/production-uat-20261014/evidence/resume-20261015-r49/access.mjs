import fs from 'node:fs/promises'
import { request,expect } from '/Users/a1234/.codex/worktrees/simple-writing-release-20261015/paperguard/frontend/node_modules/@playwright/test/index.mjs'
const plan=JSON.parse(await fs.readFile(new URL('./plan.json',import.meta.url))),credentials=JSON.parse(await fs.readFile(plan.credentials)),writing=JSON.parse(await fs.readFile(new URL('./writing-second-attempt.json',import.meta.url)))
const project=writing.project_id,jobPaths=[...new Set(writing.requests.filter(r=>r.path.endsWith('/download')).map(r=>r.path))]
const result={recorded_at:new Date().toISOString(),source_revision:plan.source_revision,project_id:project,scope:'known new synthetic project and its two exports only',checks:[],sessions:[],business_writes:0,ai_calls:0,external_notifications:0,status:'running'}
try{
 for(const alias of ['S-A','S-B','T-B','COL-B']){
  const account=credentials.accounts[alias],ctx=await request.newContext({baseURL:credentials.target});let token=''
  try{
   const login=await ctx.post('/api/auth/login',{data:{username:account.username,password:account.password}});expect(login.status()).toBe(200)
   const auth=await login.json();token=auth.token;expect(auth.user.id).toBe(account.id)
   const headers={Authorization:`Bearer ${token}`}
   const me=await ctx.get('/api/auth/me',{headers});expect(me.status()).toBe(200);expect((await me.json()).role).toBe(account.role)
   const allowed=alias==='S-A'
   for(const route of [`/api/projects/${project}`,...jobPaths]){
    const r=await ctx.get(route,{headers});const status=r.status();expect(allowed?[200]:[403,404]).toContain(status)
    result.checks.push({alias,path:route,status,expected:allowed?'allow':'deny',result:'PASS'})
   }
  }finally{if(token)result.sessions.push({alias,logout_status:(await ctx.post('/api/auth/logout',{headers:{Authorization:`Bearer ${token}`}})).status()});await ctx.dispose()}
 }
 result.status='passed'
 result.teacher_handoff={alias:'T-A',status:'NOT RUN',observation:'Previous read of this unbound standalone draft returned 403; teacher assignment and cohort binding preconditions have not been established. This test does not grant access based on another cohort supervision relationship.'}
}catch(error){let message=String(error.stack||error);for(const account of Object.values(credentials.accounts))message=message.split(account.password).join('[REDACTED]');result.error=message;result.status='failed';console.error(message);process.exitCode=1}
finally{await fs.writeFile(new URL('./access-result.json',import.meta.url),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({status:result.status,checks:result.checks.length}))}
