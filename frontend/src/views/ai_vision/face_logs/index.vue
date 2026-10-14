<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2>识别记录</h2>
        <p class="text-muted">
          人脸识别事件（record 模式）的流水：每次识别到的人脸都会记录，已知人员带姓名，未登记人员记为「陌生人」。
        </p>
      </div>
      <div class="head-actions">
        <el-select v-model="filters.camera_id" placeholder="摄像头" clearable filterable style="width: 160px" @change="onFilterChange">
          <el-option v-for="c in cameras" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
        <el-select v-model="filters.group" placeholder="分组" clearable style="width: 110px" @change="onFilterChange">
          <el-option label="白名单" value="whitelist" />
          <el-option label="黑名单" value="blacklist" />
        </el-select>
        <el-input
          v-model="filters.keyword"
          placeholder="姓名"
          clearable
          style="width: 130px"
          @keyup.enter="onFilterChange"
          @clear="onFilterChange"
        />
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="~"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          style="width: 240px"
          @change="onFilterChange"
        />
        <el-button :loading="loading" @click="onFilterChange">查询</el-button>
      </div>
    </div>

    <el-table :data="tableData" v-loading="loading" stripe style="width: 100%">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="摄像头" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">{{ row.camera_name || `#${row.camera_id}` }}</template>
      </el-table-column>
      <el-table-column label="人员" min-width="130">
        <template #default="{ row }">
          <el-tag v-if="row.person_id === 0" size="small" type="info">陌生人</el-tag>
          <template v-else>
            <el-tag size="small" :type="row.group === 'blacklist' ? 'danger' : 'success'" style="margin-right: 6px">
              {{ row.group === 'blacklist' ? '黑名单' : '白名单' }}
            </el-tag>
            {{ row.person_name }}
          </template>
        </template>
      </el-table-column>
      <el-table-column label="相似度" width="100">
        <template #default="{ row }">
          <el-text size="small" :type="row.similarity >= 0.363 ? 'success' : 'info'">{{ row.similarity?.toFixed(3) ?? '—' }}</el-text>
        </template>
      </el-table-column>
      <el-table-column label="特写" width="90">
        <template #default="{ row }">
          <el-image
            v-if="row.image_url"
            :src="row.image_url"
            :preview-src-list="[row.image_url]"
            preview-teleported
            fit="cover"
            class="face-thumb"
          />
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && tableData.length === 0 && total === 0" description="还没有识别记录" :image-size="120">
      <p class="empty-tip">识别记录来自「识别模式 = 识别记录」的人脸识别事件任务运行后的流水（考勤 / 轨迹查询用）。</p>
    </el-empty>

    <div class="pagination" v-if="total > 0">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        @size-change="fetchList"
        @current-change="fetchList"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import request from '@/api/request'
import { formatDateTime } from '@/utils/time'

const loading = ref(false)
const tableData = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const cameras = ref<any[]>([])
const dateRange = ref<[string, string] | null>(null)

const filters = reactive({ camera_id: '' as number | '', group: '', keyword: '' })

function imageUrl(path: string) {
  if (!path) return ''
  const idx = path.lastIndexOf('ai_vision')
  if (idx >= 0) return '/api/v1/ai-vision/media/' + path.slice(idx + 'ai_vision/'.length).replace(/\\/g, '/')
  return ''
}

async function fetchCameras() {
  try {
    const res: any = await request.get('/ai-vision/cameras', { params: { page: 1, page_size: 100 } })
    cameras.value = (res.items || []).filter((c: any) => !c.is_deleted)
  } catch {
    // handled by interceptor
  }
}

async function fetchList() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize.value }
    if (filters.camera_id !== '') params.camera_id = filters.camera_id
    if (filters.group) params.group = filters.group
    if (filters.keyword) params.keyword = filters.keyword
    if (dateRange.value?.[0]) params.date_from = dateRange.value[0]
    if (dateRange.value?.[1]) params.date_to = dateRange.value[1]
    const res: any = await request.get('/ai-vision/face-logs', { params })
    tableData.value = (res.items || []).map((l: any) => ({ ...l, image_url: imageUrl(l.snapshot_path) }))
    total.value = res.total || 0
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

function onFilterChange() {
  page.value = 1
  fetchList()
}

onMounted(() => {
  fetchCameras()
  fetchList()
})
</script>

<style scoped>
.page { padding: 16px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 12px; flex-wrap: wrap; }
.page-head h2 { margin: 0 0 4px; font-size: 18px; }
.text-muted { color: var(--el-text-color-secondary); font-size: 13px; margin: 0; }
.head-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.face-thumb { width: 48px; height: 48px; border-radius: 6px; display: block; }
.empty-tip { color: var(--el-text-color-secondary); font-size: 13px; max-width: 420px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
</style>
