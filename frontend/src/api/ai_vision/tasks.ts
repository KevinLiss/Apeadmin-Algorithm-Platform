// AI 视觉平台 - 任务编排 API 封装
import request from '@/api/request'

export function listTasks(params: any) {
  return request.get('/ai-vision/tasks', { params })
}

export function createTask(data: any) {
  return request.post('/ai-vision/tasks', data)
}

export function deleteTask(id: number) {
  return request.delete(`/ai-vision/tasks/${id}`)
}

export function startTask(id: number) {
  return request.post(`/ai-vision/tasks/${id}/start`)
}

export function stopTask(id: number) {
  return request.post(`/ai-vision/tasks/${id}/stop`)
}

export function getTaskStats(id: number) {
  return request.get(`/ai-vision/tasks/${id}/stats`)
}