import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium, expect } from '/Users/a1234/.codex/worktrees/simple-writing-release-20261015/paperguard/frontend/node_modules/@playwright/test/index.mjs'
const plan=JSON.parse(await fs.readFile(new URL('./plan.json',import.meta.url)))
const credentials=JSON.parse(await fs.readFile(plan.credentials))
const out=path.dirname(new URL(import.meta.url).pathname)
const result={recorded_at:new Date().toISOString(),source_revision:plan.source_revision,release_id:plan.release_id,scope:'read-only recheck of known synthetic historical defects',business_writes:0,ai_calls:0,external_notifications:0,checks:[],sessions:[],blocked_requests:[]}
const browser=await chromium.launch({headless:true})
class ReadOnlySession {
  constructor(alias){this.alias=alias;this.account=credentials.accounts[alias];this.token=''}
  async open(){
    this.context=await browser.newContext({baseURL:credentials.target,viewport:{width:1440,height:1000}})
    await this.context.route('**/api/**',async route=>{
      const request=route.request(),u=new URL(request.url())
      if(['GET','HEAD'].includes(request.method())||(request.method()==='POST'&&['/api/auth/login','/api/auth/logout'].includes(u.pathname)))return route.continue()
      result.blocked_requests.push({method:request.method(),path:u.pathname});await route.abort('blockedbyclient')
    })
    this.page=await this.context.newPage();this.page.setDefaultTimeout(20000)
    await this.page.goto('/login?redirect=/',{waitUntil:'domcontentloaded'})
    await this.page.locator('#username').fill(this.account.username);await this.page.locator('#password').fill(this.account.password)
    await this.page.locator('form.auth-form button[type="submit"]').click();await expect(this.page).not.toHaveURL(/\/login/,{timeout:20000})
    this.token=await this.page.evaluate(()=>sessionStorage.getItem('pg_token'))
    const user=await this.page.evaluate(()=>JSON.parse(sessionStorage.getItem('pg_user')));expect(user.id).toBe(this.account.id)
  }
  async get(route){const response=await this.page.request.get(route,{headers:{Authorization:`Bearer ${this.token}`}});expect(response.status()).toBe(200);return response.json()}
  async close(){if(this.token){result.sessions.push({alias:this.alias,logout_status:(await this.page.request.post('/api/auth/logout',{headers:{Authorization:`Bearer ${this.token}`}})).status()});this.token=''}await this.context?.close()}
}
const sessions=[]
try{
  const probe=await browser.newContext();const response=await probe.request.get(credentials.target+'/api/version');expect((await response.json()).source_revision).toBe(plan.source_revision);await probe.close()
  const student=new ReadOnlySession('S-A');sessions.push(student);await student.open()
  const record=await student.get('/api/process-records/record-e44d8b5928b5'),project=await student.get('/api/projects/proj-dfde4747c750')
  result.topic_change={record_id:record.id,record_state:record.state||record.status,new_topic:record.data?.new_topic,project_id:project.id,project_title:project.title,project_title_matches_approved_topic:project.title===record.data?.new_topic,replayed_approval:false}
  expect(result.topic_change.new_topic).toBeTruthy();expect(result.topic_change.record_state).toBe('approved')
  result.checks.push({id:'DEF-F08-001',status:result.topic_change.project_title_matches_approved_topic?'historical_object_now_consistent':'historical_inconsistency_still_present',limitation:'read-only persisted-object check; no new approval transition replayed'})
  await student.close();console.log('Historical topic-change object checked')
  const manager=new ReadOnlySession('COL-A');sessions.push(manager);await manager.open()
  const cohort='cohort-ed0046d38402';const report=await manager.get(`/api/reports/cohorts/${cohort}`)
  await manager.page.goto('/workflow?panel=reports',{waitUntil:'domcontentloaded'})
  await expect(manager.page.locator('.report-filter select')).toBeVisible();await manager.page.locator('.report-filter select').selectOption(cohort)
  await expect(manager.page.locator('.report-metrics strong').first()).toHaveText(Math.round(report.metrics.on_time_first_submission_rate*100)+'%')
  const displayed=await manager.page.locator('.report-metrics').innerText()
  result.report={cohort_id:cohort,member_count:report.member_count,participant_count:report.participant_count,state_counts:report.state_counts,archive_count:report.archive_count,metrics:report.metrics,ui_display:displayed,ui_verified:true,rate_out_of_bounds:report.metrics.on_time_first_submission_rate>1||report.metrics.archive_completion_rate>1}
  await manager.page.locator('.report-panel').screenshot({path:path.join(out,'report-current.png')})
  result.checks.push({id:'DEF-X10-REPORT-001',status:result.report.rate_out_of_bounds?'FAIL-reproduced-current-version':'retest-required-for-full-metric-oracle'})
  await manager.close();expect(result.blocked_requests).toEqual([]);result.status='executed'
}catch(error){let message=String(error.stack||error);for(const account of Object.values(credentials.accounts))message=message.split(account.password).join('[REDACTED]');for(const session of sessions)if(session.token)message=message.split(session.token).join('[REDACTED]');result.error=message;result.status='execution_failed';console.error(message);process.exitCode=1}
finally{for(const session of sessions)if(session.token)await session.close().catch(()=>{});await browser.close();await fs.writeFile(path.join(out,'history-result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({status:result.status,checks:result.checks}))}
