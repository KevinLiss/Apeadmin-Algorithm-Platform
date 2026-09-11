// AI 视觉平台 - 识别事件 API 封装
import request from '@/api/request'

export function listEvents(params: any) {
  return request.get('/ai-vision/events', { params })
}

export function createEvent(data: any) {
  return request.post('/ai-vision/events', data)
}

export function updateEvent(id: number, data: any) {
  return request.put(`/ai-vision/events/${id}`, data)
}

export function deleteEvent(id: number) {
  return request.delete(`/ai-vision/events/${id}`)
}

export function publishEvent(id: number) {
  return request.post(`/ai-vision/events/${id}/publish`)
}

export function retireEvent(id: number) {
  return request.post(`/ai-vision/events/${id}/retire`)
}