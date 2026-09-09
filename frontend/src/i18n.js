import { ref } from 'vue'

const currentLang = ref(localStorage.getItem('paperguard_lang') || 'zh')

export function useI18n() {
  function setLang(lang) {
    currentLang.value = lang
    localStorage.setItem('paperguard_lang', lang)
  }

  function t(key) {
    const dict = translations[currentLang.value] || translations.zh
    return dict[key] || translations.zh[key] || key
  }

  return {
    lang: currentLang,
    setLang,
    t,
  }
}

export const translations = {
  zh: {
    // 导航
    brandTagline: '毕业论文写作工作台',
    navProjects: '我的论文',
    navWriting: '写作规范',
    navStandards: '模板规范',
    navDashboard: '格式检查',
    navTeacher: '👨‍🎓 指导学生',
    navUserAdmin: '👥 用户认证',
    navAdminAi: '⚙️ 默认模型',
    logout: '退出登录',
    pendingAuth: '待认证',

    // Hero
    heroOverline: '学术论文格式自动化分析引擎',
    heroHeadline: 'GB/T 7713 国标规范与高校模板体检',
    heroSubtext: '提供论文结构完整性、排版规范与样式约束秒级深度自检，支持从 Word 规范模板逆向解析与 AI 智能润色。',
    engineActive: '体检引擎就绪',
    rulesetText: '规范标准：GB/T 7713 / 高校定制',

    // Tabs
    tabUpload: '上传排版体检',
    tabProjects: '我的论文工作台',

    // 检查面板
    modePack: '标准规则包检查',
    modeTemplate: '高校模板对照检查',
    ruleSelectLabel: '检测规范：',
    benchmarkTemplate: '基准模板：',
    noTemplateSelected: '尚未选择高校 .docx 模板',
    changeTemplate: '更换基准模板',
    chooseTemplate: '选择高校官方规范模板 .docx',

    // 上传 Dropzone
    dropzoneTitle: '点击或拖入毕业论文 (.docx)',
    dropzoneTitleSelected: '已选论文：',
    dropzoneSubtitle: '支持 Word 2007+ (.docx) 格式，文档仅在服务器临时体检，不保存任何内容。',
    browseFiles: '选择本地文件',
    fileReady: '文件已就绪，可直接体检',
    startInspection: '开始格式体检',
    inspecting: '正在深度解析排版中…',
    loadingEnv: '正在加载规范规则库与论文环境…',

    // 指标卡
    totalIssues: '排版问题总数',
    criticalErrors: '严重不合规',
    formatWarnings: '建议调整警告',
    formattingTips: '格式提示项',

    // Findings
    detailedFindings: '排版体检详细报告',
    oneClickRepair: '一键自动修复并下载',
    repairing: '自动修复中…',
    perfectTitle: '排版完美合规！未发现格式问题',
    perfectSub: '当前论文完全符合目标规范要求，可以直接提交评审或答辩。',
    badgeCritical: '严重错误',
    badgeWarning: '格式警告',
    badgeInfo: '排版提示',
    paragraphLoc: '段落 #',
    actualLabel: '实际检测值 (ACTUAL)',
    expectedLabel: '规范要求值 (EXPECTED)',

    // 我的论文
    myThesesTitle: '我的论文项目',
    myThesesSub: '集中管理论文草稿、章节结构与写作依据，随时回到上次编辑位置。',
    templateLabel: '基于模板：',
    thesisTitlePlaceholder: '输入论文题目，例如：基于深度学习的高校排版引擎设计',
    newThesisBtn: '新建论文',
    creating: '创建中…',
    noThesesTitle: '暂无论文项目',
    noThesesSub: '在上方选择对应学校模板并输入论文题目，即可开启沉浸式写作。',
    continueWriting: '继续写作',
    exportDocx: '导出 docx',
    exporting: '导出中…',
    delete: '删除',
    deleteConfirm: '确定要删除此论文吗？',

    // 弹窗
    close: '关闭',
  },
  en: {
    // Nav
    brandTagline: 'Academic Thesis Writing Workspace',
    navProjects: 'My Theses',
    navWriting: 'Writing Standards',
    navStandards: 'Template Standards',
    navDashboard: 'Format Check',
    navTeacher: '👨‍🎓 Students',
    navUserAdmin: '👥 Users',
    navAdminAi: '⚙️ AI Model',
    logout: 'Sign Out',
    pendingAuth: 'Pending',

    // Hero
    heroOverline: 'MODERN ACADEMIC RIGOR',
    heroHeadline: 'GB/T 7713 Compliance Engine',
    heroSubtext: 'Automated structural and typographic analysis for your academic manuscript. Ensure perfect alignment with institutional formatting standards before submission.',
    engineActive: 'Engine Active',
    rulesetText: 'Ruleset: GB/T 7713 & Institutional',

    // Tabs
    tabUpload: 'Upload Inspection',
    tabProjects: 'My Theses',

    // Check panel
    modePack: 'Standard Ruleset',
    modeTemplate: 'Template Contrast',
    ruleSelectLabel: 'Standard: ',
    benchmarkTemplate: 'Base Template: ',
    noTemplateSelected: 'No institutional template selected yet',
    changeTemplate: 'Change Base Template',
    chooseTemplate: 'Select Institutional .docx Template',

    // Upload Dropzone
    dropzoneTitle: 'Drag & Drop your thesis (.docx)',
    dropzoneTitleSelected: 'Selected Thesis: ',
    dropzoneSubtitle: 'Supports .docx formats (max 50MB). Files are analyzed securely in memory.',
    browseFiles: 'Browse Files',
    fileReady: 'File ready, click to re-select',
    startInspection: 'Start Inspection',
    inspecting: 'Analyzing Manuscript…',
    loadingEnv: 'Loading rulesets and environment…',

    // Metric Cards
    totalIssues: 'Total Issues',
    criticalErrors: 'Critical Errors',
    formatWarnings: 'Format Warnings',
    formattingTips: 'Formatting Tips',

    // Findings
    detailedFindings: 'Detailed Findings',
    oneClickRepair: 'One-Click Repair & Download',
    repairing: 'Repairing…',
    perfectTitle: 'Perfect! No Formatting Issues Found',
    perfectSub: 'Your manuscript complies with all formatting standards and is ready for submission.',
    badgeCritical: 'Critical',
    badgeWarning: 'Warning',
    badgeInfo: 'Info',
    paragraphLoc: 'Paragraph #',
    actualLabel: 'ACTUAL VALUE',
    expectedLabel: 'EXPECTED REQUIREMENT',

    // My Theses
    myThesesTitle: 'My Theses Workstation',
    myThesesSub: 'Keep drafts, chapter structure, and writing guidance together so you can resume exactly where you left off.',
    templateLabel: 'Template: ',
    thesisTitlePlaceholder: 'Enter thesis title, e.g.: Design and Implementation of IoT System',
    newThesisBtn: 'New Thesis',
    creating: 'Creating…',
    noThesesTitle: 'No Thesis Projects Yet',
    noThesesSub: 'Select an institutional template above and enter your thesis title to get started.',
    continueWriting: 'Continue Writing',
    exportDocx: 'Export .docx',
    exporting: 'Exporting…',
    delete: 'Delete',
    deleteConfirm: 'Are you sure you want to delete this thesis?',

    // Modal
    close: 'Close',
  }
}
