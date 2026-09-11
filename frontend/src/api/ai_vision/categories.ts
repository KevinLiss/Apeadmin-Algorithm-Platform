// AI 视觉平台 - 类别库 API 封装
import request from '@/api/request'

export function listCategories(params: any) {
  return request.get('/ai-vision/categories', { params })
}

export function createCategory(data: any) {
  return request.post('/ai-vision/categories', data)
}

export function updateCategory(id: number, data: any) {
  return request.put(`/ai-vision/categories/${id}`, data)
}

export function deleteCategory(id: number) {
  return request.delete(`/ai-vision/categories/${id}`)
}