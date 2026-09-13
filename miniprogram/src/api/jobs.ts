import { request } from './request'

export interface JobView {
  id: string
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string
  [key: string]: unknown
}

const TERMINAL_STATES = new Set(['succeeded', 'failed', 'cancelled'])

/**
 * 轮询持久任务(后端约定: 耗时操作返回 202 + job_id, 见设计方案 §6/§7)。
 * 调用方在页面 onHide 时应停止轮询、onShow 时重建。
 */
export async function pollJob<T = JobView>(
  jobId: string,
  options: { intervalMs?: number; maxAttempts?: number } = {},
): Promise<T> {
  const intervalMs = options.intervalMs ?? 3_000
  const maxAttempts = options.maxAttempts ?? 60
  let job: JobView | null = null
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    job = await request<JobView>(`/api/jobs/${jobId}`)
    if (TERMINAL_STATES.has(job.state)) return job as T
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
  throw new Error('任务处理超时，请稍后在电脑端查看进度')
}
