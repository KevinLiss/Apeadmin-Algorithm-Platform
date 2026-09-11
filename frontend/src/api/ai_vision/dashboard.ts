// AI 视觉平台 - 统计看板 API 封装
import request from '@/api/request'

export function getDashboardRuntime() {
  return request.get('/ai-vision/dashboard/runtime')
}

export function getDashboardOverview() {
  return request.get('/ai-vision/dashboard/overview')
}