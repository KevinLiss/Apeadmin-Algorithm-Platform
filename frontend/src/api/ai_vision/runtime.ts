// AI 视觉平台 - 运行环境 API 封装
import request from '@/api/request'

export function getRuntimeStatus() {
  return request.get('/ai-vision/runtime/status')
}

export function installLayer(layer: 'L1' | 'L2') {
  return request.post(`/ai-vision/runtime/install?layer=${layer}`)
}

export function uninstallLayer(layer: 'L1' | 'L2') {
  return request.post(`/ai-vision/runtime/uninstall?layer=${layer}`)
}

export function testRuntime(layer?: 'L1' | 'L2' | '') {
  return request.post(`/ai-vision/runtime/test${layer ? `?layer=${layer}` : ''}`)
}

export function getInstallLog() {
  return request.get('/ai-vision/runtime/install-log')
}

// ── 环境文件管理 ─────────────────────────────────────
export function getEnvFiles() {
  return request.get('/ai-vision/runtime/env-files')
}

export function verifyEnvFiles() {
  return request.post('/ai-vision/runtime/env-files/verify')
}

export function uploadEnvFile(file: File) {
  const fd = new FormData()
  fd.append('file', file)
  return request.post('/ai-vision/runtime/env-files/upload', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function deleteEnvFile(modelId: number) {
  return request.delete(`/ai-vision/runtime/env-files/${modelId}`)
}

export function reloadEnvFile(modelId: number) {
  return request.post(`/ai-vision/runtime/env-files/${modelId}/reload`)
}

// ── AI 环境排查助手 ─────────────────────────────────────
export function aiAssistChat(data: { message: string; provider_id?: number; model?: string }) {
  return request.post('/ai-vision/runtime/ai/chat', data)
}