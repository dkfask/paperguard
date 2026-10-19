import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { chromium, expect } from '/Users/a1234/.codex/worktrees/simple-writing-release-20261015/paperguard/frontend/node_modules/@playwright/test/index.mjs'
const plan=JSON.parse(await fs.readFile(new URL('./plan.json',import.meta.url)))
const credentials=JSON.parse(await fs.readFile(plan.credentials))
const account=credentials.accounts['S-A']
const out=path.dirname(new URL(import.meta.url).pathname)
const prior=JSON.parse(await fs.readFile(new URL('./writing-first-attempt.json',import.meta.url)))
const state={recorded_at:new Date().toISOString(),source_revision:plan.source_revision,release_id:plan.release_id,role_alias:'S-A',allowed_writes:plan.allowed,checks:[],requests:[],blocked_requests:[],page_errors:[],console_errors:[],project_id:prior.project_id,external_ai_calls:0,external_notifications:0,status:'running'}
let token='',browser,page
const redact=value=>String(value).split(account.password).join('[REDACTED]').split(token||'__never_token__').join('[REDACTED]')
const checkpoint=()=>fs.writeFile(path.join(out,'writing-result.json'),JSON.stringify(state,null,2)+'\n')
async function step(id,action){console.log('START '+id);await action();state.checks.push({id,status:'PASS'});await checkpoint();console.log('PASS '+id)}
class WritingPage {
  constructor(page){this.page=page}
  async login(){
    await this.page.goto('/login?redirect=/',{waitUntil:'domcontentloaded'})
    await this.page.locator('#username').fill(account.username)
    await this.page.locator('#password').fill(account.password)
    await this.page.locator('form.auth-form button[type="submit"]').click()
    await expect(this.page).not.toHaveURL(/\/login/,{timeout:20000})
    token=await this.page.evaluate(()=>sessionStorage.getItem('pg_token'))
    const user=await this.page.evaluate(()=>JSON.parse(sessionStorage.getItem('pg_user')))
    expect(user.id).toBe(account.id);expect(user.role).toBe('student')
  }
  async saved(){await expect(this.page.locator('.writer-save-status')).not.toHaveClass(/pending/,{timeout:20000})}
  async section(title){await this.page.locator('.nav-title').filter({hasText:title}).first().click()}
  async text(text){
    const response=this.page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().includes(`/api/projects/${state.project_id}/section/`))
    await this.page.locator('textarea.para-text').first().fill(text)
    expect((await response).status()).toBe(200);await this.saved()
  }
  async closeContext(){await this.page.getByRole('dialog').filter({has:this.page.locator('.writer-context')}).getByRole('button',{name:'关闭',exact:true}).click()}
  async check(){
    const response=this.page.waitForResponse(r=>r.url().endsWith(`/api/projects/${state.project_id}/template-check`)&&r.request().method()==='GET')
    await this.page.getByRole('button',{name:'检查',exact:true}).first().click()
    expect((await response).status()).toBe(200)
    return (await response).json()
  }
  async exportMissing(text){
    const response=this.page.waitForResponse(r=>new URL(r.url()).pathname===`/api/projects/${state.project_id}/export-jobs`&&r.request().method()==='POST')
    await this.page.getByRole('button',{name:'导出',exact:true}).click()
    expect((await response).status()).toBe(409)
    await expect(this.page.locator('.missing-panel')).toContainText(text)
    await expect(this.page.locator('.missing-panel button')).toHaveCount(1)
    await this.page.locator('.missing-panel button').click()
  }
  async download(name){
    const pending=this.page.waitForEvent('download',{timeout:90000})
    await this.page.getByRole('button',{name:'导出',exact:true}).click()
    const download=await pending
    expect(await download.failure()).toBeNull()
    const file=path.join(out,name);await download.saveAs(file)
    const data=await fs.readFile(file)
    state[name]={bytes:data.length,sha256:createHash('sha256').update(data).digest('hex')}
  }
}
try {
  browser=await chromium.launch({headless:true})
  const context=await browser.newContext({baseURL:credentials.target,viewport:{width:1440,height:1000},acceptDownloads:true})
  page=await context.newPage();page.setDefaultTimeout(20000)
  page.on('pageerror',error=>state.page_errors.push(redact(error.message)))
  page.on('console',message=>{if(message.type()==='error')state.console_errors.push(redact(message.text()))})
  page.on('response',response=>{const u=new URL(response.url());if(u.pathname.startsWith('/api/'))state.requests.push({method:response.request().method(),path:u.pathname,status:response.status()})})
  await context.route('**/api/**',async route=>{
    const request=route.request(),u=new URL(request.url()),method=request.method()
    if(['GET','HEAD'].includes(method))return route.continue()
    const auth=method==='POST'&&['/api/auth/login','/api/auth/logout'].includes(u.pathname)
    const create=method==='POST'&&u.pathname==='/api/projects'&&request.postDataJSON()?.title?.startsWith('UAT-20261015-模板闭环-')
    const mine=state.project_id&&u.pathname.startsWith(`/api/projects/${state.project_id}/`)&&((method==='PUT'&&(/\/(meta|sections)$/.test(u.pathname)||u.pathname.includes('/section/')))||(method==='POST'&&/\/export-jobs$/.test(u.pathname)))
    if(auth||create||mine)return route.continue()
    state.blocked_requests.push({method,path:u.pathname});await route.abort('blockedbyclient')
  })
  const ui=new WritingPage(page)
  let project,required,abstract,texts={}
  await step('R49-01-version-and-student-login',async()=>{
    const response=await page.request.get('/api/version');expect(response.status()).toBe(200)
    const version=await response.json();expect(version.source_revision).toBe(plan.source_revision);expect(version.release_id).toBe(plan.release_id)
    await ui.login()
  })
  await step('R49-02-ui-create-and-incomplete-export-gate',async()=>{
    state.title=prior.title
    const response=await page.request.get(`/api/projects/${state.project_id}`,{headers:{Authorization:`Bearer ${token}`}})
    expect(response.status()).toBe(200);project=await response.json();expect(project.title).toBe(state.title)
    await page.goto(`/write/${state.project_id}`,{waitUntil:'domcontentloaded'})
    state.creation_evidence='writing-first-attempt.json: POST /api/projects 201; same project resumed after TEST-DEFECT correction'
    await expect(page.locator('.writer-shell')).toBeVisible();await ui.saved()
    required=project.sections.filter(s=>s.required);abstract=required.find(s=>s.type==='abstract')
    state.template_id=project.template_id;state.required_sections=required.map(s=>({key:s.key,type:s.type,title:s.title}));state.expected_styles=project.template_snapshot.styles
    await ui.exportMissing('封面')
  })
  await step('R49-03-ui-cover-save',async()=>{
    for(const field of project.template_snapshot.meta_fields)await page.locator('.meta-row').filter({hasText:field.label}).locator('input').fill(field.key==='title'?state.title:`合成${field.label}`)
    const response=page.waitForResponse(r=>r.url().endsWith(`/api/projects/${state.project_id}/meta`)&&r.request().method()==='PUT')
    await page.getByRole('button',{name:/保存封面信息/}).click();expect((await response).status()).toBe(200);await ui.saved()
  })
  await step('R49-04-required-content-autosave',async()=>{
    for(const section of required){
      await ui.section(section.title)
      texts[section.key]=section.type.startsWith('english')?`Synthetic UAT text for ${section.key}. Saved through the browser.`:`合成模板闭环正文：${section.key}。通过生产网页填写，验证格式约束与保存；不含真实学生内容。`
      await ui.text(texts[section.key])
    }
    state.expected_texts=texts;await expect(page.locator('.stats-value')).toHaveText(`${required.length}/${required.length}`)
  })
  await step('R49-05-empty-required-section-check-and-export-rejection',async()=>{
    await ui.section(abstract.title)
    const response=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().includes(`/api/projects/${state.project_id}/section/`))
    await page.locator('.para-block .para-toolbar button.danger').click();expect((await response).status()).toBe(200);await ui.saved()
    await expect(page.locator('.stats-value')).toHaveText(`${required.length-1}/${required.length}`)
    const check=await ui.check();expect(check.findings.some(f=>f.rule_id.endsWith('abstract.empty'))).toBe(true)
    await expect(page.locator('.check-list')).toContainText('模板要求的章节尚未填写: 摘要');await ui.closeContext()
    await ui.exportMissing('摘要')
    await page.locator('.insert-row button.primary').first().click();await ui.text(texts[abstract.key])
  })
  await step('R49-06-reload-persistence-and-complete-check',async()=>{
    await page.reload({waitUntil:'domcontentloaded'});await ui.section(abstract.title)
    await expect(page.locator('textarea.para-text').first()).toHaveValue(texts[abstract.key])
    const check=await ui.check();expect(check.findings.filter(f=>f.rule_id.startsWith('TPL.')&&f.severity==='error')).toEqual([])
    state.complete_template_findings=check.findings.map(f=>({rule_id:f.rule_id,severity:f.severity}))
    await ui.closeContext();await page.screenshot({path:path.join(out,'writing-desktop.png'),fullPage:true})
  })
  await step('R49-07-desktop-word-download',()=>ui.download('writing-desktop.docx'))
  await step('R49-08-mobile-edit-reload-and-word-download',async()=>{
    await page.setViewportSize({width:390,height:844});await ui.section(abstract.title)
    texts[abstract.key]+=' 手机视口继续编辑并保存。';await ui.text(texts[abstract.key])
    await page.reload({waitUntil:'domcontentloaded'});await ui.section(abstract.title)
    await expect(page.locator('textarea.para-text').first()).toHaveValue(texts[abstract.key])
    state.mobile_overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);expect(state.mobile_overflow).toBe(false)
    const check=await ui.check();expect(check.findings.filter(f=>f.rule_id.startsWith('TPL.')&&f.severity==='error')).toEqual([]);await ui.closeContext()
    await ui.download('writing-mobile.docx');await page.screenshot({path:path.join(out,'writing-mobile.png'),fullPage:true})
  })
  await step('R49-09-relogin-persistence-and-no-unexpected-mutations',async()=>{
    expect((await page.request.post('/api/auth/logout',{headers:{Authorization:`Bearer ${token}`}})).status()).toBe(200);token=''
    await ui.login();await page.goto(`/write/${state.project_id}`,{waitUntil:'domcontentloaded'});await ui.section(abstract.title)
    await expect(page.locator('textarea.para-text').first()).toHaveValue(texts[abstract.key])
    expect(state.page_errors).toEqual([]);expect(state.blocked_requests).toEqual([])
    const bad=state.requests.filter(r=>r.status>=400&&!(r.status===409&&r.path.endsWith('/export-jobs')));expect(bad).toEqual([])
  })
  state.status='passed'
}catch(error){state.status='failed';state.error=redact(error.stack||error);console.error(state.error);if(page)await page.screenshot({path:path.join(out,'writing-failure.png'),fullPage:true}).catch(()=>{});process.exitCode=1}
finally{
  if(token&&page){try{state.logout_status=(await page.request.post('/api/auth/logout',{headers:{Authorization:`Bearer ${token}`}})).status()}catch(error){state.logout_error=redact(error.message)}}
  await checkpoint();if(browser)await browser.close();console.log(JSON.stringify({status:state.status,passed:state.checks.length,project_id:state.project_id,result:path.join(out,'writing-result.json')}))
}
