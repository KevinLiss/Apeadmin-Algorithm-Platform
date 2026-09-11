// AI 视觉平台 - 摄像头 API 封装
import request from '@/api/request'

export function listCameras(params: any) {
  return request.get('/ai-vision/cameras', { params })
}

export function createCamera(data: any) {
  return request.post('/ai-vision/cameras', data)
}

export function updateCamera(id: number, data: any) {
  return request.put(`/ai-vision/cameras/${id}`, data)
}

export function deleteCamera(id: number) {
  return request.delete(`/ai-vision/cameras/${id}`)
}

export function testCamera(id: number, rtspUrl?: string) {
  return request.post(`/ai-vision/cameras/${id}/test`, null, {
    params: rtspUrl ? { rtsp_url: rtspUrl } : {},
    timeout: 20000,
  })
}