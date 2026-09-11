// AI 视觉平台 - 样本库 API 封装
import request from '@/api/request'

export function listSamples(params: any) {
  return request.get('/ai-vision/samples', { params })
}

export function uploadSamples(files: File[], categoryCode = '', eventId?: number) {
  const form = new FormData()
  files.forEach((f) => form.append('files', f))
  return request.post('/ai-vision/samples/upload', form, {
    params: { category_code: categoryCode, event_id: eventId },
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
  })
}

export function deleteSample(id: number) {
  return request.delete(`/ai-vision/samples/${id}`)
}