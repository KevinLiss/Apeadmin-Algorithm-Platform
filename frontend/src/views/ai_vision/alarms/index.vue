<template>
  <div class="alarm-page">
    <div class="page-header" v-if="!embedded">
      <h2>告警中心</h2>
      <p class="text-muted">查看识别告警与抓拍图，支持确认、标记误报、转样本和批量处理</p>
    </div>

    <!-- 筛选栏 -->
    <el-form :inline="true" class="filter-bar">
      <el-form-item label="状态">
        <el-select v-model="filters.status" placeholder="全部" clearable style="width: 130px" @change="handleFilterChange">
          <el-option label="待处理" value="pending" />
          <el-option label="已确认" value="acknowledged" />
          <el-option label="误报" value="false_positive" />
        </el-select>
      </el-form-item>
      <el-form-item label="摄像头">
        <el-select v-model="filters.camera_id" placeholder="全部" clearable filterable style="width: 160px" @change="handleFilterChange">
          <el-option v-for="c in cameras" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="事件">
        <el-select v-model="filters.event_id" placeholder="全部" clearable filterable style="width: 160px" @change="handleFilterChange">
          <el-option v-for="e in events" :key="e.id" :label="e.name" :value="e.id" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button @click="fetchList" :loading="loading">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
      </el-form-item>
    </el-form>

    <!-- 批量操作 -->
    <div class="batch-bar" v-if="selected.length > 0">
      <el-text size="small">已选 {{ selected.length }} 条</el-text>
      <el-button size="small" type="primary" @click="handleBatchAck" v-permission="'ai_vision:alarm:edit'">批量确认</el-button>
    </div>

    <!-- 列表 -->
    <el-table v-if="total > 0 || loading" :data="tableData" v-loading="loading" stripe style="width: 100%; margin-top: 8px" @selection-change="(rows: any[]) => (selected = rows)">
      <el-table-column type="selection" width="45" />
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column label="抓拍图" width="90">
        <template #default="{ row }">
          <el-image
            v-if="row.snapshot_url"
            :src="row.snapshot_url"
            :preview-src-list="[row.snapshot_url]"
            preview-teleported
            fit="cover"
            style="width: 64px; height: 40px; border-radius: 4px"
          />
          <el-text v-else size="small" type="info">—</el-text>
        </template>
      </el-table-column>
      <el-table-column label="摄像头" min-width="110">
        <template #default="{ row }">{{ row.camera_name || `#${row.camera_id}` }}</template>
      </el-table-column>
      <el-table-column label="事件" min-width="120">
        <template #default="{ row }">{{ row.event_name || `#${row.event_id}` }}</template>
      </el-table-column>
      <el-table-column label="类别" width="100">
        <template #default="{ row }">
          <el-tag size="small" type="warning">{{ row.category_code }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="90">
        <template #default="{ row }">{{ (row.confidence * 100).toFixed(1) }}%</template>
      </el-table-column>
      <el-table-column label="级别" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="row.level === 'critical' ? 'danger' : 'warning'">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="告警时间" width="170" />
      <el-table-column label="操作" width="250" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="handleAck(row)" v-if="row.status === 'pending'" v-permission="'ai_vision:alarm:edit'">确认</el-button>
          <el-button link type="warning" size="small" @click="handleFalsePositive(row)" v-if="['pending', 'acknowledged'].includes(row.status)" v-permission="'ai_vision:alarm:edit'">误报</el-button>
          <el-button link type="success" size="small" @click="handleToSample(row)" v-if="row.status === 'false_positive' || row.status === 'acknowledged'" v-permission="'ai_vision:alarm:edit'">转样本</el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row)" v-permission="'ai_vision:alarm:edit'">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态引导 -->
    <el-empty v-if="!loading && total === 0" description="还没有告警" :image-size="120">
      <p class="empty-tip">告警会在识别任务运行后自动产生，快去检查任务是否在运行</p>
      <el-button v-if="!embedded" type="primary" @click="goTasks">
        <el-icon><ArrowRight /></el-icon> 前往实时监控台
      </el-button>
    </el-empty>

    <!-- 分页 -->
    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @size-change="fetchList"
        @current-change="fetchList"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ embedded?: boolean }>()
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, ArrowRight } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import request from '@/api/request'

const router = useRouter()

const loading = ref(false)
const tableData = ref<any[]>([])
const selected = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const cameras = ref<any[]>([])
const events = ref<any[]>([])

const filters = reactive({
  status: '',
  camera_id: null as number | null,
  event_id: null as number | null,
})

function statusType(s: string) {
  return { pending: 'danger', acknowledged: 'success', false_positive: 'info' }[s] || 'info'
}

function statusText(s: string) {
  return { pending: '待处理', acknowledged: '已确认', false_positive: '误报' }[s] || s
}

// 抓拍图相对路径 → 可访问 URL（静态挂载 /api/v1/ai-vision/media）
function snapshotUrl(path: string) {
  if (!path) return ''
  const idx = path.indexOf('ai_vision')
  if (idx >= 0) return '/api/v1/ai-vision/media/' + path.slice(idx + 'ai_vision/'.length).replace(/\\/g, '/')
  return ''
}

async function fetchCameras() {
  try {
    const res: any = await request.get('/ai-vision/cameras', { params: { page: 1, page_size: 100 } })
    cameras.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

async function fetchEvents() {
  try {
    const res: any = await request.get('/ai-vision/events', { params: { page: 1, page_size: 100 } })
    events.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

async function fetchList() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize.value }
    if (filters.status) params.status = filters.status
    if (filters.camera_id) params.camera_id = filters.camera_id
    if (filters.event_id) params.event_id = filters.event_id
    const res: any = await request.get('/ai-vision/alarms', { params })
    const items = res.items || []
    tableData.value = items.map((a: any) => ({ ...a, snapshot_url: snapshotUrl(a.snapshot_path) }))
    total.value = res.total || 0
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

function handleFilterChange() {
  page.value = 1
  fetchList()
}

function goTasks() {
  router.push('/ai-vision/monitor?tab=live')
}

async function handleAck(row: any) {
  await request.post(`/ai-vision/alarms/${row.id}/ack`)
  ElMessage.success('已确认')
  await fetchList()
}

async function handleFalsePositive(row: any) {
  try {
    const { value } = await ElMessageBox.prompt('可填写误报原因（可选）', '标记误报', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputPlaceholder: '如：灯光反光误触发',
    })
    await request.post(`/ai-vision/alarms/${row.id}/false-positive`, null, { params: { note: value || '' } })
    ElMessage.success('已标记为误报')
    await fetchList()
  } catch {
    // cancel
  }
}

async function handleToSample(row: any) {
  await ElMessageBox.confirm('将该告警抓拍图转入样本库？', '提示', { type: 'info' })
  await request.post(`/ai-vision/alarms/${row.id}/to-sample`)
  ElMessage.success('已转样本')
  await fetchList()
}

async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除告警 #${row.id}？抓拍图将一并删除，且不可恢复。`, '删除告警', {
      type: 'warning',
      confirmButtonText: '删除',
      confirmButtonClass: 'el-button--danger',
    })
  } catch {
    return // cancel
  }
  await request.delete(`/ai-vision/alarms/${row.id}`)
  ElMessage.success('已删除')
  await fetchList()
}

async function handleBatchAck() {
  const ids = selected.value.map((r) => r.id)
  await request.post('/ai-vision/alarms/batch-ack', null, { params: { alarm_ids: ids } })
  ElMessage.success('批量确认完成')
  selected.value = []
  await fetchList()
}

onMounted(() => {
  fetchList()
  fetchCameras()
  fetchEvents()
})
</script>

<style scoped>
.alarm-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.filter-bar { margin-bottom: 4px; }
.batch-bar { display: flex; align-items: center; gap: 12px; margin: 8px 0; }
.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>