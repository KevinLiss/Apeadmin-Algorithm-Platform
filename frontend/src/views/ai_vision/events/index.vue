<template>
  <div class="event-page">
    <div class="page-header">
      <h2>识别事件</h2>
      <p class="text-muted">定义"看什么 + 怎么算告警"的规则包（类别 + 置信度阈值 + 绑定模型），发布后可被任务引用</p>
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:event:create'">
        <el-icon><Plus /></el-icon> 新建事件
      </el-button>
      <el-button @click="fetchList" :loading="loading">
        <el-icon><Refresh /></el-icon> 刷新
      </el-button>
    </div>

    <!-- 列表 -->
    <el-table v-if="total > 0 || loading" :data="tableData" v-loading="loading" stripe style="width: 100%; margin-top: 16px">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="事件名称" min-width="140" />
      <el-table-column label="类别" min-width="120">
        <template #default="{ row }">
          <el-tag v-for="code in row.category_codes" :key="code" size="small" class="cat-tag">{{ code }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="模型" width="90">
        <template #default="{ row }">
          <el-text size="small" :type="row.model_ids?.length ? '' : 'danger'">{{ row.model_ids?.length || 0 }} 个</el-text>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="操作" width="310" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" size="small" @click="handlePublish(row)" v-if="['draft', 'ready'].includes(row.status)" v-permission="'ai_vision:event:edit'">发布</el-button>
          <el-button link type="warning" size="small" @click="handleRetire(row)" v-if="['ready', 'paused'].includes(row.status)" v-permission="'ai_vision:event:edit'">退役</el-button>
          <el-button link type="primary" size="small" @click="createTask(row)" v-if="row.status === 'ready'" v-permission="'ai_vision:task:create'">创建任务</el-button>
          <el-button link type="primary" size="small" @click="openEdit(row)" v-permission="'ai_vision:event:edit'">编辑</el-button>
          <el-button link type="danger" size="small" @click="handleDelete(row)" v-permission="'ai_vision:event:delete'">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态引导 -->
    <el-empty v-if="!loading && total === 0" description="还没有识别事件" :image-size="120">
      <p class="empty-tip">第二步：定义一个识别事件（如"检测到人员出现即告警"）</p>
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:event:create'">
        <el-icon><Plus /></el-icon> 新建事件
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

    <!-- 新建/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑事件' : '新建识别事件'" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="事件名称" required>
          <el-input v-model="form.name" placeholder="如：吸烟识别" maxlength="200" />
        </el-form-item>
        <el-form-item label="目标类别" required>
          <el-select v-model="form.category_codes" multiple placeholder="选择要识别的目标类别" style="width: 100%">
            <el-option v-for="c in categories" :key="c.code" :label="`${c.name} (${c.code})`" :value="c.code" :disabled="c.status !== 1" />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定模型" :required="form.status === 'ready' || form.status === 'running'">
          <el-select v-model="form.model_ids" multiple placeholder="选择检测模型（发布后不可为空）" style="width: 100%">
            <el-option v-for="m in models" :key="m.id" :label="`${m.name} v${m.version}${m.quantized ? ' (INT8)' : ''}`" :value="m.id" />
          </el-select>
          <el-text size="small" type="info">需选择与目标类别匹配的模型</el-text>
        </el-form-item>
        <el-form-item label="置信度阈值">
          <el-slider v-model="form.rule.threshold" :min="0.1" :max="0.95" :step="0.05" show-input />
        </el-form-item>
        <el-form-item label="持续时长(s)">
          <el-input-number v-model="form.rule.duration" :min="0" :max="300" /> <el-text size="small" type="info">0 = 单帧即告警</el-text>
        </el-form-item>
        <el-form-item label="冷却(s)">
          <el-input-number v-model="form.rule.cooldown" :min="0" :max="3600" />
        </el-form-item>
        <el-form-item label="分析帧率">
          <el-input-number v-model="form.rule.fps" :min="1" :max="10" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'

const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const categories = ref<any[]>([])
const models = ref<any[]>([])

const form = reactive({
  name: '',
  description: '',
  category_codes: [] as string[],
  model_ids: [] as number[],
  rule: {
    threshold: 0.45,
    duration: 0,
    cooldown: 60,
    fps: 2,
  },
})

function statusType(s: string) {
  return { draft: 'info', ready: 'success', running: 'warning', paused: 'info', pending_train: 'danger' }[s] || 'info'
}

function statusText(s: string) {
  return { draft: '草稿', ready: '已发布', running: '运行中', paused: '已暂停', pending_train: '待训练' }[s] || s
}

async function fetchCategories() {
  try {
    const res: any = await request.get('/ai-vision/categories', { params: { page: 1, page_size: 100 } })
    categories.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

async function fetchModels() {
  try {
    const res: any = await request.get('/ai-vision/models', { params: { page: 1, page_size: 100 } })
    models.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

async function fetchList() {
  loading.value = true
  try {
    const res: any = await request.get('/ai-vision/events', {
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
  editingId.value = null
  form.name = ''
  form.description = ''
  form.category_codes = []
  form.model_ids = []
  form.rule = { threshold: 0.45, duration: 0, cooldown: 60, fps: 2 }
  dialogVisible.value = true
}

function openEdit(row: any) {
  editingId.value = row.id
  form.name = row.name
  form.description = row.description
  form.category_codes = [...(row.category_codes || [])]
  form.model_ids = [...(row.model_ids || [])]
  form.rule = {
    threshold: row.rule?.threshold ?? 0.45,
    duration: row.rule?.duration ?? 0,
    cooldown: row.rule?.cooldown ?? 60,
    fps: row.rule?.fps ?? 2,
  }
  dialogVisible.value = true
}

async function handleSave() {
  if (!form.name.trim() || form.category_codes.length === 0) {
    ElMessage.warning('请填写事件名称并选择至少一个类别')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await request.put(`/ai-vision/events/${editingId.value}`, {
        name: form.name,
        description: form.description,
        category_codes: form.category_codes,
        model_ids: form.model_ids,
        rule: form.rule,
      })
      ElMessage.success('更新成功')
    } else {
      await request.post('/ai-vision/events', {
        name: form.name,
        description: form.description,
        category_codes: form.category_codes,
        model_ids: form.model_ids,
        rule: form.rule,
      })
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    saving.value = false
  }
}

async function handlePublish(row: any) {
  // 前端预校验：未绑模型直接阻止，避免启动任务时才报错
  if (!row.model_ids?.length) {
    ElMessage.warning(`事件「${row.name}」还未绑定检测模型，请先编辑并选择模型`)
    return
  }
  await request.post(`/ai-vision/events/${row.id}/publish`)
  ElMessage.success('已发布，可在任务编排中引用')
  await fetchList()
}

/** 跳转任务编排并预选当前事件 */
function createTask(row: any) {
  router.push({ path: '/ai-vision/tasks', query: { event_id: row.id, event_name: row.name } })
}

async function handleRetire(row: any) {
  await request.post(`/ai-vision/events/${row.id}/retire`)
  ElMessage.success('已退役')
  await fetchList()
}

async function handleDelete(row: any) {
  await ElMessageBox.confirm(`确定删除事件「${row.name}」吗？`, '提示', { type: 'warning' })
  await request.delete(`/ai-vision/events/${row.id}`)
  ElMessage.success('删除成功')
  await fetchList()
}

onMounted(() => {
  fetchList()
  fetchCategories()
  fetchModels()
})
</script>

<style scoped>
.event-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.toolbar { display: flex; gap: 8px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
.cat-tag { margin-right: 4px; }
.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }
</style>