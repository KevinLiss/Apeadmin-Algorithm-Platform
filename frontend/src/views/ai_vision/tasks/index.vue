<template>
  <div class="task-page">
    <div class="page-header" v-if="!embedded">
      <h2>任务编排</h2>
      <p class="text-muted">把"视频源 × 识别事件"绑定为分析任务；同摄像头多任务共享一路视频流，单机上限 4 路</p>
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:task:create'">
        <el-icon><Plus /></el-icon> 新建任务
      </el-button>
      <el-button @click="fetchList" :loading="loading">
        <el-icon><Refresh /></el-icon> 刷新
      </el-button>
    </div>

    <!-- 列表 -->
    <el-table v-if="total > 0 || loading" :data="tableData" v-loading="loading" stripe style="width: 100%; margin-top: 16px">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column label="摄像头" min-width="130">
        <template #default="{ row }">{{ row.camera_name || `#${row.camera_id}` }}</template>
      </el-table-column>
      <el-table-column label="识别事件" min-width="140">
        <template #default="{ row }">{{ row.event_name || `#${row.event_id}` }}</template>
      </el-table-column>
      <el-table-column label="分析帧率" width="90">
        <template #default="{ row }">{{ row.analyze_fps }} fps</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="实时指标" min-width="170">
        <template #default="{ row }">
          <template v-if="row.status === 'running' && row.last_stats && row.last_stats.fps_actual != null">
            <el-text size="small" type="success">实际 {{ Number(row.last_stats.fps_actual).toFixed(1) }} fps</el-text>
            <el-text size="small" type="info" style="margin-left: 6px">帧 {{ row.last_stats.frames_processed ?? 0 }}</el-text>
          </template>
          <el-text v-else size="small" type="info">—</el-text>
        </template>
      </el-table-column>
      <el-table-column prop="started_at" label="启动时间" width="170">
        <template #default="{ row }">{{ row.started_at || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openStats(row)" v-permission="'ai_vision:task:list'">详情</el-button>
          <el-button link type="warning" size="small" @click="handleStop(row)" v-if="row.status === 'running'" :loading="actingId === row.id" v-permission="'ai_vision:task:control'">停止</el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row)" v-permission="'ai_vision:task:delete'">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态引导 -->
    <el-empty v-if="!loading && total === 0" description="还没有分析任务" :image-size="120">
      <p class="empty-tip">第三步：把视频源和识别事件绑定为任务，然后到「实时监控」页开始监控</p>
      <div class="empty-actions">
        <el-button @click="goPage('/ai-vision/cameras')">先建视频源</el-button>
        <el-button v-if="events.length === 0" @click="goPage('/ai-vision/rule-config')">先建识别事件</el-button>
        <el-button type="primary" @click="openCreate" v-permission="'ai_vision:task:create'">
          <el-icon><Plus /></el-icon> 新建任务
        </el-button>
      </div>
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

    <!-- 新建任务弹窗 -->
    <el-dialog v-model="dialogVisible" title="新建分析任务" width="560px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="视频源" required>
          <el-select v-model="form.camera_id" placeholder="选择摄像头或视频文件" style="width: 100%" filterable>
            <el-option v-for="c in cameras" :key="c.id" :label="`${c.name}（${c.source_type === 'video' ? '视频文件' : 'RTSP'}）`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="识别事件" required>
          <el-select v-model="form.event_id" placeholder="选择已发布事件" style="width: 100%" filterable>
            <el-option
              v-for="e in events"
              :key="e.id"
              :label="eventLabel(e)"
              :value="e.id"
              :disabled="e.status !== 'ready'"
            >
              <el-tooltip
                :content="eventDisabledReason(e)"
                :disabled="e.status === 'ready'"
                placement="right"
              >
                <span>{{ eventLabel(e) }}</span>
              </el-tooltip>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="分析帧率">
          <el-input-number v-model="form.analyze_fps" :min="1" :max="10" /> <el-text size="small" type="info">fps（建议 1-4）</el-text>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">创建</el-button>
      </template>
    </el-dialog>

    <!-- 详情弹窗 -->
    <el-dialog v-model="statsVisible" title="任务运行详情" width="640px">
      <template v-if="currentStats">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="任务 ID">{{ currentStats.task_id }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="currentStats.status === 'running' ? 'success' : 'info'" size="small">{{ statusText(currentStats.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="实时帧率">{{ currentStats.live?.fps_actual?.toFixed(1) ?? '—' }} fps</el-descriptions-item>
          <el-descriptions-item label="源状态">{{ stateText(currentStats.live?.state) }}</el-descriptions-item>
          <el-descriptions-item label="已读取帧">{{ currentStats.live?.frames_read ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="已处理帧">{{ currentStats.live?.frames_processed ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="丢帧率">{{ currentStats.live?.drop_rate != null ? (currentStats.live.drop_rate * 100).toFixed(1) + '%' : '—' }}</el-descriptions-item>
          <el-descriptions-item label="该路任务数">{{ currentStats.live?.tasks_on_camera ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="最近告警时间" :span="2">{{ currentStats.live?.last_alarm_at || '—' }}</el-descriptions-item>
        </el-descriptions>
        <div class="stats-footer">
          <el-button type="primary" size="small" @click="goPage('/ai-vision/monitor?tab=records')">查看告警记录</el-button>
          <el-text size="small" type="info">丢帧率高 = CPU 跟不上，可降低分析帧率</el-text>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
defineProps<{ embedded?: boolean }>()
import { ref, reactive, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const saving = ref(false)
const actingId = ref<number | null>(null)
const tableData = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const dialogVisible = ref(false)
const statsVisible = ref(false)
const currentStats = ref<any>(null)
const cameras = ref<any[]>([])
const events = ref<any[]>([])

const form = reactive({
  camera_id: null as number | null,
  event_id: null as number | null,
  analyze_fps: 2,
})

function statusType(s: string) {
  return { pending: 'info', running: 'success', stopped: 'info', error: 'danger' }[s] || 'info'
}

function statusText(s: string) {
  return { pending: '待启动', running: '运行中', stopped: '已停止', error: '异常' }[s] || s
}

function stateText(s?: string) {
  return { online: '在线', offline: '离线', unknown: '检测中' }[s || ''] || s || '—'
}

function goPage(path: string) {
  router.push(path)
}

/** 事件下拉选项文案：附带状态提示 */
function eventLabel(e: any) {
  const tags: Record<string, string> = { draft: '（未发布）', paused: '（已退役）', pending_train: '（待训练）', running: '' }
  return e.name + (tags[e.status] || '')
}

/** 事件不可选原因（tooltip 展示） */
function eventDisabledReason(e: any) {
  if (e.status === 'draft') return '草稿状态：请先在「识别事件」页发布'
  if (e.status === 'paused') return '已退役：请编辑后重新发布'
  if (e.status === 'pending_train') return '待训练：模型尚不可用'
  if (!e.model_ids?.length) return '未绑定模型：请编辑事件并选择检测模型'
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
    const res: any = await request.get('/ai-vision/tasks', {
      params: { page: page.value, page_size: pageSize.value }
    })
    tableData.value = res.items || []
    total.value = res.total || 0
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

function openCreate() {
  form.camera_id = null
  form.event_id = null
  form.analyze_fps = 2
  // 支持从摄像头/事件页带参跳入并预选
  const q = route.query as Record<string, string>
  if (q.camera_id) form.camera_id = Number(q.camera_id)
  if (q.event_id) form.event_id = Number(q.event_id)
  dialogVisible.value = true
}

async function handleSave() {
  if (!form.camera_id || !form.event_id) {
    ElMessage.warning('请选择摄像头和识别事件')
    return
  }
  saving.value = true
  try {
    await request.post('/ai-vision/tasks', {
      camera_id: form.camera_id,
      event_id: form.event_id,
      analyze_fps: form.analyze_fps,
    })
    ElMessage.success('任务创建成功，可在「实时监控」页开始监控')
    dialogVisible.value = false
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    saving.value = false
  }
}

async function openStats(row: any) {
  statsVisible.value = true
  currentStats.value = null
  try {
    const res: any = await request.get(`/ai-vision/tasks/${row.id}/stats`)
    currentStats.value = res
  } catch {
    statsVisible.value = false
  }
}

async function handleStop(row: any) {
  try {
    await ElMessageBox.confirm(`确定停止任务「#${row.id} ${row.camera_name || ''} × ${row.event_name || ''}」吗？`, '提示', { type: 'warning' })
  } catch {
    return // 取消
  }
  actingId.value = row.id
  try {
    await request.post(`/ai-vision/tasks/${row.id}/stop`)
    ElMessage.success('任务已停止')
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    actingId.value = null
  }
}

async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除任务「#${row.id} ${row.camera_name || ''} × ${row.event_name || ''}」吗？`, '提示', { type: 'warning' })
  } catch {
    return // 取消
  }
  await request.delete(`/ai-vision/tasks/${row.id}`)
  ElMessage.success('删除成功')
  await fetchList()
}

onMounted(() => {
  fetchList()
  fetchCameras()
  fetchEvents().then(() => {
    // URL 带参进入时直接弹出新建弹窗（预选完成）
    if (route.query.camera_id || route.query.event_id) {
      openCreate()
    }
  })
})
</script>

<style scoped>
.task-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.toolbar { display: flex; gap: 8px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
.stats-footer { margin-top: 16px; display: flex; align-items: center; justify-content: space-between; }
.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }
.empty-actions { display: flex; gap: 8px; justify-content: center; }
</style>