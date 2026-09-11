// AI 视觉平台 - 告警中心 API 封装
import request from '@/api/request'

export function listAlarms(params: any) {
  return request.get('/ai-vision/alarms', { params })
}

export function ackAlarm(id: number) {
  return request.post(`/ai-vision/alarms/${id}/ack`)
}

export function falsePositiveAlarm(id: number, note = '') {
  return request.post(`/ai-vision/alarms/${id}/false-positive`, null, { params: { note } })
}

export function alarmToSample(id: number) {
  return request.post(`/ai-vision/alarms/${id}/to-sample`)
}

export function batchAckAlarms(ids: number[]) {
  return request.post('/ai-vision/alarms/batch-ack', null, { params: { alarm_ids: ids } })
}