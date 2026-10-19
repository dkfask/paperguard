import fs from 'node:fs/promises'
import path from 'node:path'
import { chromium, expect } from '/Users/a1234/.codex/worktrees/simple-writing-release-20261015/paperguard/frontend/node_modules/@playwright/test/index.mjs'
const plan=JSON.parse(await fs.readFile(new URL('./plan.json',import.meta.url))),credentials=JSON.parse(await fs.readFile(plan.credentials)),account=credentials.accounts['S-A']
const saved=JSON.parse(await fs.readFile(new URL('./writing-second-attempt.json',import.meta.url)))
const result={recorded_at:new Date().toISOString(),source_revision:plan.source_revision,project_id:saved.project_id,check:'R49-09-relogin-persistence',business_writes:0,page_errors:[],unexpected_api_errors:[],status:'running'}
const browser=await chromium.launch({headless:true});let token=''
const page=await browser.newPage({baseURL:credentials.target,viewport:{width:1440,height:1000}})
try{
 page.on('pageerror',e=>result.page_errors.push(e.message))
 page.on('response',r=>{if(new URL(r.url()).pathname.startsWith('/api/')&&r.status()>=400)result.unexpected_api_errors.push({path:new URL(r.url()).pathname,status:r.status()})})
 await page.route('**/api/**',route=>{const req=route.request(),p=new URL(req.url()).pathname;if(['GET','HEAD'].includes(req.method())||(req.method()==='POST'&&['/api/auth/login','/api/auth/logout'].includes(p)))return route.continue();return route.abort('blockedbyclient')})
 await page.goto('/login?redirect=/',{waitUntil:'domcontentloaded'});await page.locator('#username').fill(account.username);await page.locator('#password').fill(account.password)
 await page.locator('form.auth-form button[type="submit"]').click();await expect(page).not.toHaveURL(/\/login/,{timeout:20000})
 token=await page.evaluate(()=>sessionStorage.getItem('pg_token'))
 await page.goto(`/write/${saved.project_id}`,{waitUntil:'domcontentloaded'})
 await page.locator('.nav-title').filter({hasText:'摘要'}).first().click()
 const key=saved.required_sections.find(s=>s.type==='abstract').key
 await expect(page.locator('textarea.para-text').first()).toHaveValue(saved.expected_texts[key])
 await expect(page.locator('.stats-value')).toHaveText(`${saved.required_sections.length}/${saved.required_sections.length}`)
 expect(result.page_errors).toEqual([]);expect(result.unexpected_api_errors).toEqual([])
 result.status='passed';await page.screenshot({path:new URL('./writing-relogin.png',import.meta.url).pathname,fullPage:true})
}catch(error){let message=String(error.stack||error).split(account.password).join('[REDACTED]');if(token)message=message.split(token).join('[REDACTED]');result.error=message;result.status='failed';console.error(message);process.exitCode=1}
finally{if(token)result.logout_status=(await page.request.post('/api/auth/logout',{headers:{Authorization:`Bearer ${token}`}})).status();await browser.close();await fs.writeFile(new URL('./relogin-result.json',import.meta.url),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result))}
