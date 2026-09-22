// AI 视觉平台 - 实时监控台 API 封装
import request from '@/api/request'

// 增量拉取告警（id > since_id），供监控台时间线轮询
export function alarmsSince(params: { since_id: number; camera_id?: number; limit?: number }) {
  return request.get('/ai-vision/monitor/alarms-since', { params })
}

// 监控台指标（今日告警/未处理/活跃 worker）
export function monitorStatus() {
  return request.get('/ai-vision/monitor/status')
}

// 解析视频源播放地址（video 源 → media URL；RTSP 源 → play_url 为空走 MJPEG）
export function resolveSource(cameraId: number) {
  return request.get(`/ai-vision/monitor/sources/${cameraId}`)
}

// MJPEG 实时流地址（<img> 标签无法带 Authorization 头，token 走 query）
export function streamUrl(cameraId: number, fps = 5) {
  const token = localStorage.getItem('apeadmin_token') || ''
  return `/api/v1/ai-vision/monitor/stream/${cameraId}?token=${encodeURIComponent(token)}&fps=${fps}`
}

// 复用：任务启停（监控台"开始/停止监控"按钮）
export function startTask(id: number) {
  return request.post(`/ai-vision/tasks/${id}/start`)
}

export function stopTask(id: number) {
  return request.post(`/ai-vision/tasks/${id}/stop`)
}

export function createTask(body: { camera_id: number; event_id: number; analyze_fps: number }) {
  return request.post('/ai-vision/tasks', body)
}

export function listTasks(params: any) {
  return request.get('/ai-vision/tasks', { params })
}

export function listCameras(params: any = {}) {
  return request.get('/ai-vision/cameras', { params })
}

export function listEvents(params: any = {}) {
  return request.get('/ai-vision/events', { params })
}

// 告警处置（时间线内联按钮复用告警中心同款接口）
export function ackAlarm(id: number) {
  return request.post(`/ai-vision/alarms/${id}/ack`)
}

export function falsePositiveAlarm(id: number, note = '') {
  return request.post(`/ai-vision/alarms/${id}/false-positive`, null, { params: { note } })
}

export function alarmToSample(id: number) {
  return request.post(`/ai-vision/alarms/${id}/to-sample`)
}
