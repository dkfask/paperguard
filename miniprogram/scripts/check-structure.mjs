import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(new URL('..', import.meta.url).pathname)
const required = [
  'src/manifest.json',
  'src/pages.json',
  'src/main.ts',
  'src/App.vue',
  'src/api/request.ts',
  'src/api/submissions.ts',
  'src/stores/session.ts',
  'src/utils/session-guard.ts',
  'src/utils/idempotency.ts',
  'src/utils/format.ts',
  'src/utils/student-flow.ts',
  'src/utils/teacher-flow.ts',
  'src/utils/subscribe.ts',
  'src/utils/error-messages.ts',
  'src/utils/wechat-uat-preflight.ts',
  'src/utils/privacy.ts',
  'src/utils/tab-badge.ts',
  'src/pages/login/index.vue',
  'src/pages/tasks/index.vue',
  'src/pages/messages/index.vue',
  'src/pages/profile/index.vue',
  'src/pages/profile/about.vue',
  'src/pages/requirements/index.vue',
  'src/pages/submit/index.vue',
  'src/pages/submit/success.vue',
  'src/pages/submissions/detail.vue',
  'src/pages/submissions/history.vue',
  'src/pages/submissions/comments.vue',
  'src/pages/review/index.vue',
]

const missing = required.filter((file) => !existsSync(resolve(root, file)))
if (missing.length) {
  console.error(`Missing mini-program files: ${missing.join(', ')}`)
  process.exit(1)
}

const pages = readFileSync(resolve(root, 'src/pages.json'), 'utf8')
const registered = [
  'pages/login/index',
  'pages/tasks/index',
  'pages/messages/index',
  'pages/profile/index',
  'pages/profile/about',
  'pages/requirements/index',
  'pages/submit/index',
  'pages/submissions/detail',
  'pages/submissions/comments',
  'pages/submissions/history',
  'pages/review/index',
]
for (const path of registered) {
  if (!pages.includes(path)) {
    console.error(`Missing page registration: ${path}`)
    process.exit(1)
  }
}

const studentSource = [
  readFileSync(resolve(root, 'src/pages/tasks/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/requirements/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/login/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/submit/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/submissions/detail.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/submissions/history.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/messages/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/utils/student-flow.ts'), 'utf8'),
].join('\n')
for (const token of ['summarizeRequirementItems', 'pages/requirements/index', 'revision=1', 'notificationTarget', 'idempotencyKey', 'latestReturnReason', 'submitRecoveryAction', '退回理由', 'splitStudentTasks', 'canWithdrawSubmission', 'withdrawSubmission', '历史阶段', '修改说明', '论文卫士Pro', 'deadlineCountdownLabel', 'materialGateItems', '必交材料', 'submissionTimeline', '有效截止']) {
  if (!studentSource.includes(token)) {
    console.error(`Missing MP1 student-loop token: ${token}`)
    process.exit(1)
  }
}

const teacherSource = [
  readFileSync(resolve(root, 'src/pages/tasks/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/review/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/utils/teacher-flow.ts'), 'utf8'),
].join('\n')
for (const token of ['teacherQueue', 'waitDurationLabel', 'canApproveReview', 'buildReturnPayload', 'groupComments', 'materialGateItems', '必交材料', 'teacherTaskAction', 'canDecideReview', '查看审阅记录']) {
  if (!teacherSource.includes(token)) {
    console.error(`Missing MP2 teacher-loop token: ${token}`)
    process.exit(1)
  }
}

const subscribeSource = [
  readFileSync(resolve(root, 'src/pages/submit/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/submit/success.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/utils/subscribe.ts'), 'utf8'),
].join('\n')
for (const token of ['requestSubscribeAuthorization', 'parseSubscribeTemplateIds', 'reportsFromSubscribeResult', 'reportSubscribe']) {
  if (!subscribeSource.includes(token)) {
    console.error(`Missing MP3 subscribe token: ${token}`)
    process.exit(1)
  }
}

const requestSource = [
  readFileSync(resolve(root, 'src/api/request.ts'), 'utf8'),
  readFileSync(resolve(root, 'src/utils/error-messages.ts'), 'utf8'),
].join('\n')
for (const token of ['CONTENT_SECURITY_REJECTED', 'CONTENT_SECURITY_UNAVAILABLE', 'describeApiError']) {
  if (!requestSource.includes(token)) {
    console.error(`Missing MP UGC security token: ${token}`)
    process.exit(1)
  }
}

const privacySource = [
  readFileSync(resolve(root, 'src/utils/privacy.ts'), 'utf8'),
  readFileSync(resolve(root, 'src/utils/tab-badge.ts'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/login/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/profile/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/profile/about.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/messages/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/tasks/index.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/submissions/detail.vue'), 'utf8'),
  readFileSync(resolve(root, 'src/pages/review/index.vue'), 'utf8'),
].join('\n')
for (const token of ['acceptPrivacy', 'isPrivacyAccepted', 'applySharePolicy', 'hideShareMenu', '关于与隐私政策', 'applyMessageTabBadge', 'setTabBarBadge']) {
  if (!privacySource.includes(token)) {
    console.error(`Missing MP privacy/share/badge token: ${token}`)
    process.exit(1)
  }
}

const uatSource = [
  readFileSync(resolve(root, 'src/utils/wechat-uat-preflight.ts'), 'utf8'),
  readFileSync(resolve(root, 'scripts/validate-build-env.mjs'), 'utf8'),
  readFileSync(resolve(root, 'scripts/check-wechat-uat.mjs'), 'utf8'),
].join('\n')
for (const token of ['isPublicHttpsApiBase', 'wechatUatBlockers', 'UAT-MP-DOMAIN', 'UAT-MP-DEVICE']) {
  if (!uatSource.includes(token)) {
    console.error(`Missing WeChat UAT preflight token: ${token}`)
    process.exit(1)
  }
}

console.log(`Mini-program MP1/MP2/MP3/UGC/UAT structure OK (${required.length} files, ${registered.length} pages)`)
