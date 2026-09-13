/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  /** 微信订阅消息模板 ID, 逗号分隔(审核结果/截止提醒); 未配置时端内引导置灰 */
  readonly VITE_SUBSCRIBE_TEMPLATE_IDS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
