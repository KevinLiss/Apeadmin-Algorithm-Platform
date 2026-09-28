<!--
  AI 视觉平台 - 模型训练页（样本库 + 训练工作台聚合）
  Tab1 样本库：内嵌 samples 页（上传/转样本/AI 预标注/手工画框）
  Tab2 训练工作台：选基座（须有 .pt）→ 选类别 → 预览样本 → 发起训练
        → 列表看进度（轮询）→ 完成自动注册新版本模型（source=trained）
  依赖：api /ai-vision/train-jobs、/ai-vision/samples
-->
<template>
  <div class="train-page">
    <div class="page-header">
      <h2>模型训练</h2>
      <p class="text-muted">样本收集标注 → 一键续训 → 新版本自动入库，到识别事件换绑即生效</p>
    </div>

    <el-tabs v-model="activeTab">
      <!-- ═══ Tab 1：样本库 ═══ -->
      <el-tab-pane label="样本库" name="samples" lazy>
        <SamplesPage :embedded="true" />
      </el-tab-pane>

      <!-- ═══ Tab 2：训练工作台 ═══ -->
      <el-tab-pane label="训练工作台" name="train">
    <el-card shadow="never" class="form-card">
      <template #header>发起训练</template>
      <el-form :inline="false" label-width="110px">
        <el-form-item label="基座模型">
          <el-select v-model="form.base_model_id" placeholder="选择可续训的模型（须已登记 .pt）" style="width: 340px" @change="onBaseChange">
            <el-option
              v-for="m in trainableModels"
              :key="m.id"
              :label="`${m.name} v${m.version}${m.pt_path ? '' : '（无 .pt）'}`"
              :value="m.id"
              :disabled="!m.pt_path"
            />
          </el-select>
          <el-text size="small" type="info" style="margin-left: 8px">
            {{ baseModel ? `类别表：${baseNames.join(' / ') || '未登记'}` : '' }}
          </el-text>
        </el-form-item>
        <el-form-item label="训练类别">
          <el-select v-model="form.category_codes" multiple style="width: 340px" placeholder="须与基座类别表一致" @change="loadPreview">
            <el-option v-for="c in baseNames" :key="c" :label="categoryName(c)" :value="c" />
          </el-select>
        </el-form-item>
        <el-form-item label="样本范围">
          <el-radio-group v-model="scopeMode" @change="loadPreview">
            <el-radio-button value="all">全库</el-radio-button>
            <el-radio-button value="folder">按分组</el-radio-button>
            <el-radio-button value="manual">手动勾选</el-radio-button>
          </el-radio-group>
          <template v-if="scopeMode === 'folder'">
            <el-select v-model="form.folder" placeholder="选择分组" style="width: 200px; margin-left: 10px" @change="loadPreview">
              <el-option label="未分组" value="__none__" />
              <el-option v-for="f in folders" :key="f.folder" :label="`${f.folder}（${f.labeled}/${f.total} 已标注）`" :value="f.folder" />
            </el-select>
          </template>
          <template v-if="scopeMode === 'manual'">
            <el-button size="small" style="margin-left: 10px" @click="openPicker">
              勾选样本（已选 {{ pickedIds.length }} 张）
            </el-button>
          </template>
          <el-text size="small" type="info" style="margin-left: 10px">训练只取范围内"已标注且框类别命中"的样本</el-text>
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="6">
            <el-form-item label="轮数 epochs">
              <el-input-number v-model="form.epochs" :min="1" :max="300" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="图像尺寸">
              <el-select v-model="form.imgsz" style="width: 100%">
                <el-option :value="320" label="320（快）" />
                <el-option :value="416" label="416（推荐）" />
                <el-option :value="640" label="640（慢但细）" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="batch">
              <el-input-number v-model="form.batch" :min="1" :max="16" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="学习率">
              <el-input-number v-model="form.lr0" :min="0.0001" :max="0.1" :step="0.001" :precision="4" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="产物名称">
          <el-input v-model="form.output_name" placeholder="训练完成入库的模型名（留空=自动 基座名-ftN）" style="width: 340px" maxlength="60" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" placeholder="本次训练目的（可选）" style="width: 340px" />
        </el-form-item>
        <el-form-item>
          <el-alert v-if="preview" :closable="false" type="info" style="margin-bottom: 10px">
            将参与训练：{{ preview.samples }} 张已标注样本
            （{{ Object.entries(preview.per_class).map(([k, v]) => `${categoryName(k)} ${v} 框`).join('，') }}）
            <el-button v-if="preview.sample_ids?.length" link type="primary" size="small" @click="viewTrainSamples">
              查看这些图片
            </el-button>
          </el-alert>
          <el-button type="primary" :loading="creating" :disabled="!canCreate" @click="handleCreate" v-permission="'ai_vision:train:create'">
            开始训练
          </el-button>
          <el-text size="small" type="warning" style="margin-left: 10px">
            CPU 训练较慢（30 轮约数小时），训练期间监控任务会掉帧
          </el-text>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 训练任务列表 -->
    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; justify-content: space-between">
          <span>训练任务</span>
          <el-button size="small" @click="fetchJobs" :loading="loading">刷新</el-button>
        </div>
      </template>
      <el-table :data="jobs" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="基座模型" min-width="140">
          <template #default="{ row }">{{ row.base_model_name || `#${row.base_model_id}` }}</template>
        </el-table-column>
        <el-table-column label="参数" min-width="200">
          <template #default="{ row }">
            <el-text size="small">{{ row.params?.epochs }}轮 / {{ row.params?.imgsz }}px / batch{{ row.params?.batch }}</el-text>
            <div>
              <el-text size="small" type="info">
                样本：{{ row.sample_ids?.length ?? 0 }} 张{{ scopeText(row) }}
              </el-text>
            </div>
            <div v-if="row.params?.note">
              <el-text size="small" style="color: #b8860b">备注：{{ row.params.note }}</el-text>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="jobStatusType(row.status)" size="small">{{ jobStatusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" min-width="180">
          <template #default="{ row }">
            <el-progress v-if="row.status === 'running'" :percentage="row.progress" :stroke-width="10" />
            <template v-else-if="row.status === 'completed'">
              <el-text size="small" type="success">mAP50 {{ row.metrics?.mAP50 ?? '—' }}</el-text>
            </template>
            <el-text v-else-if="row.status === 'failed'" size="small" type="danger">{{ (row.metrics?.error || '').slice(0, 40) }}</el-text>
            <el-text v-else size="small" type="info">—</el-text>
          </template>
        </el-table-column>
        <el-table-column label="产物模型" min-width="140">
          <template #default="{ row }">
            <el-tag v-if="row.output_model_name" size="small" type="success">{{ row.output_model_name }}</el-tag>
            <el-text v-else size="small" type="info">—</el-text>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openLog(row)">日志</el-button>
            <el-button link type="danger" size="small" @click="handleCancel(row)" v-if="row.status === 'running'" v-permission="'ai_vision:train:control'">取消</el-button>
            <el-button link type="danger" size="small" @click="handleDeleteJob(row)" v-else v-permission="'ai_vision:train:control'">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 基座档案：登记 .pt 与类别表（一次性准备，之后新模型自动继承） -->
    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; justify-content: space-between">
          <span>训练基座档案（.pt 权重 + 类别顺序表）</span>
          <el-button size="small" :loading="modelsLoading" @click="refreshModels">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
      </template>
      <el-table :data="models" size="small" stripe>
        <el-table-column prop="name" label="模型" min-width="140">
          <template #default="{ row }">
            {{ row.name }}
            <el-tooltip v-if="row.source === 'trained' && row.license_note" :content="row.license_note" placement="top">
              <el-icon size="12" color="#909399" style="vertical-align: middle"><InfoFilled /></el-icon>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="80">
          <template #default="{ row }">{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="类别顺序表" min-width="200">
          <template #default="{ row }">
            <el-text size="small">{{ modelNames(row).join(' → ') || '未登记' }}</el-text>
          </template>
        </el-table-column>
        <el-table-column label=".pt 基座" min-width="180">
          <template #default="{ row }">
            <el-tag v-if="row.pt_path" size="small" type="success">已登记</el-tag>
            <el-text v-else size="small" type="info">未登记</el-text>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" align="center">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="openPtDialog(row)">登记 .pt</el-button>
            <el-button size="small" text type="primary" @click="openNamesDialog(row)">类别表</el-button>
            <el-button size="small" text type="danger" @click="handleDeleteModel(row)" v-permission="'ai_vision:model:manage'">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- .pt 登记弹窗 -->
    <el-dialog v-model="ptVisible" title="登记训练基座权重（.pt）" width="520px">
      <el-form label-width="90px">
        <el-form-item label="模型">{{ ptTarget?.name }}</el-form-item>
        <el-form-item label=".pt 路径">
          <el-input v-model="ptPath" placeholder="服务器上的绝对路径，如 D:/.../best.pt" />
          <el-text size="small" type="info">文件须在本机存在；相对路径按 backend 根或 assets/models 解析</el-text>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ptVisible = false">取消</el-button>
        <el-button type="primary" :loading="ptSaving" @click="savePt">登记</el-button>
      </template>
    </el-dialog>

    <!-- 类别表登记弹窗 -->
    <el-dialog v-model="namesVisible" title="登记类别顺序表（顺序即 YOLO 索引）" width="520px">
      <el-form label-width="90px">
        <el-form-item label="模型">{{ namesTarget?.name }}</el-form-item>
        <el-form-item label="类别顺序">
          <el-input v-model="namesText" type="textarea" :rows="3" placeholder="每行一个类别 code，顺序即索引，如：&#10;swimming&#10;tread_water&#10;drowning" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="namesVisible = false">取消</el-button>
        <el-button type="primary" :loading="namesSaving" @click="saveNames">登记</el-button>
      </template>
    </el-dialog>

    <!-- 日志弹窗 -->
    <el-dialog v-model="logVisible" title="训练日志" width="800px">
      <pre class="log-pre">{{ logLines.join('\n') || '（暂无日志）' }}</pre>
    </el-dialog>

    <!-- 手动勾选样本弹窗（仅列已标注样本，卡片带标注框预览） -->
    <el-dialog v-model="pickerVisible" title="勾选训练样本（仅显示已标注）" width="920px" top="5vh">
      <div class="picker-tip">
        <el-text size="small" type="info">
          共 {{ pickerAll.length }} 张已标注样本，已勾选 {{ pickedIds.length }} 张；勾选后训练只使用这些样本
        </el-text>
        <el-button size="small" @click="pickAllInPreview">全选符合类别的</el-button>
      </div>
      <div class="picker-grid">
        <div
          v-for="s in pickerAll"
          :key="s.id"
          class="picker-card"
          :class="{ on: pickedIds.includes(s.id) }"
          @click="togglePick(s.id)"
        >
          <div class="picker-img" :style="{ aspectRatio: (s.width && s.height ? s.width / s.height : 16 / 9) + '' }">
            <el-image :src="s.image_url" fit="fill" lazy />
            <div v-for="(b, bi) in s._boxes" :key="bi" class="ov-box" :style="{
              left: `${(b.x - b.w / 2) * 100}%`, top: `${(b.y - b.h / 2) * 100}%`,
              width: `${b.w * 100}%`, height: `${b.h * 100}%`,
            }" />
          </div>
          <div class="picker-meta">
            <el-text size="small">#{{ s.id }} · {{ s._boxes.length }} 框</el-text>
            <el-icon v-if="pickedIds.includes(s.id)" color="#67c23a"><Select /></el-icon>
          </div>
        </div>
        <el-empty v-if="!pickerAll.length" description="样本库还没有已标注样本" :image-size="80" />
      </div>
      <template #footer>
        <el-button @click="pickerVisible = false">关闭</el-button>
        <el-button type="primary" @click="confirmPick">确定（{{ pickedIds.length }} 张）</el-button>
      </template>
    </el-dialog>

    <!-- 将参与训练的图片预览弹窗 -->
    <el-dialog v-model="viewVisible" title="将参与训练的样本" width="920px" top="5vh">
      <div class="picker-grid">
        <div v-for="s in viewSamples" :key="s.id" class="picker-card">
          <div class="picker-img" :style="{ aspectRatio: (s.width && s.height ? s.width / s.height : 16 / 9) + '' }">
            <el-image :src="s.image_url" fit="fill" lazy />
            <div v-for="(b, bi) in s._boxes" :key="bi" class="ov-box" :style="{
              left: `${(b.x - b.w / 2) * 100}%`, top: `${(b.y - b.h / 2) * 100}%`,
              width: `${b.w * 100}%`, height: `${b.h * 100}%`,
            }" />
          </div>
          <div class="picker-meta"><el-text size="small">#{{ s.id }} · {{ s._boxes.length }} 框</el-text></div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Select, Refresh, InfoFilled } from '@element-plus/icons-vue'
import request from '@/api/request'
import { formatDateTime } from '@/utils/time'
import SamplesPage from '@/views/ai_vision/samples/index.vue'

const activeTab = ref('samples')

const loading = ref(false)
const creating = ref(false)
const jobs = ref<any[]>([])
const models = ref<any[]>([])
const modelsLoading = ref(false)
const categories = ref<any[]>([])
const preview = ref<any>(null)
const logVisible = ref(false)
const logLines = ref<string[]>([])
let pollTimer: number | null = null
let logTimer: number | null = null

const form = reactive({
  base_model_id: null as number | null,
  category_codes: [] as string[],
  folder: '',
  output_name: '',
  epochs: 30,
  imgsz: 416,
  batch: 4,
  lr0: 0.01,
  note: '',
})

// ── 样本范围（全库 / 按分组 / 手动勾选）──────────────
const scopeMode = ref<'all' | 'folder' | 'manual'>('all')
const folders = ref<any[]>([])
const pickedIds = ref<number[]>([])
const pickerVisible = ref(false)
const pickerAll = ref<any[]>([])
const viewVisible = ref(false)
const viewSamples = ref<any[]>([])

function imageUrl(path: string) {
  if (!path) return ''
  const idx = path.lastIndexOf('ai_vision')
  if (idx >= 0) return '/api/v1/ai-vision/media/' + path.slice(idx + 'ai_vision/'.length).replace(/\\/g, '/')
  return ''
}

function parseBoxes(s: any): any[] {
  try {
    return JSON.parse(s.label_data || '{}').boxes || []
  } catch {
    return []
  }
}

async function fetchFolders() {
  try {
    const res: any = await request.get('/ai-vision/samples/folders')
    folders.value = (res.items || []).filter((f: any) => f.folder && f.folder !== '未分组')
  } catch {
    // handled
  }
}

/** 分页拉全样本（后端 page_size 上限 100，循环取完，最多 10 页防失控） */
async function fetchAllSamples(extraParams: any = {}): Promise<any[]> {
  const all: any[] = []
  for (let p = 1; p <= 10; p++) {
    const res: any = await request.get('/ai-vision/samples', { params: { page: p, page_size: 100, ...extraParams } })
    const items = res.items || []
    all.push(...items)
    if (all.length >= (res.total || 0) || items.length === 0) break
  }
  return all
}

async function openPicker() {
  try {
    const items = await fetchAllSamples({ label_status: 'labeled' })
    pickerAll.value = items.map((s: any) => ({ ...s, image_url: imageUrl(s.file_path), _boxes: parseBoxes(s) }))
  } catch {
    pickerAll.value = []
  }
  pickerVisible.value = true
}

function togglePick(id: number) {
  const i = pickedIds.value.indexOf(id)
  if (i >= 0) pickedIds.value.splice(i, 1)
  else pickedIds.value.push(id)
}

// 全选"框类别命中当前训练类别"的已标注样本
function pickAllInPreview() {
  const cs = new Set(form.category_codes)
  const hit = pickerAll.value.filter((s) => s._boxes.some((b: any) => cs.has(b.class_name)))
  pickedIds.value = hit.map((s) => s.id)
  ElMessage.success(`已勾选 ${hit.length} 张（框类别命中）`)
}

function confirmPick() {
  pickerVisible.value = false
  loadPreview()
}

// 预览"将参与训练"的图片（用 preview.sample_ids 拉取）
async function viewTrainSamples() {
  const ids: number[] = preview.value?.sample_ids || []
  if (!ids.length) return
  try {
    const items = await fetchAllSamples({ label_status: 'labeled' })
    const set = new Set(ids)
    viewSamples.value = items
      .filter((s: any) => set.has(s.id))
      .map((s: any) => ({ ...s, image_url: imageUrl(s.file_path), _boxes: parseBoxes(s) }))
  } catch {
    viewSamples.value = []
  }
  viewVisible.value = true
}

const baseModel = computed(() => models.value.find((m) => m.id === form.base_model_id) || null)
const baseNames = computed<string[]>(() => {
  if (!baseModel.value) return []
  try {
    const m = JSON.parse(baseModel.value.category_map || '{}')
    return Object.keys(m).sort((a, b) => Number(a) - Number(b)).map((k) => m[k])
  } catch {
    return []
  }
})
const trainableModels = computed(() => models.value.filter((m) => m.pt_path || m.source === 'trained'))
// 与后端 create_train_job 门槛一致：≥5 张（8:2 划分后训练集 ≥4）
const canCreate = computed(() => !!form.base_model_id && form.category_codes.length > 0 && (preview.value?.samples ?? 0) >= 5)

function categoryName(code: string) {
  return categories.value.find((c) => c.code === code)?.name || code
}
/** 训练任务的样本范围描述（参数列副标题用） */
function scopeText(row: any): string {
  const p = row.params || {}
  if (p.sample_ids?.length) return '（手动勾选）'
  if (p.folder) return `（分组「${p.folder === '__none__' ? '未分组' : p.folder}」）`
  return '（全库）'
}
function jobStatusType(s: string) {
  return { queued: 'info', running: 'warning', completed: 'success', failed: 'danger' }[s] || 'info'
}
function jobStatusText(s: string) {
  return { queued: '排队中', running: '训练中', completed: '已完成', failed: '失败/取消' }[s] || s
}

function onBaseChange() {
  form.category_codes = [...baseNames.value]
  loadPreview()
}

async function loadPreview() {
  preview.value = null
  if (!form.category_codes.length) return
  if (scopeMode.value === 'manual' && !pickedIds.value.length) return
  try {
    const params: any = { category_codes: form.category_codes.join(',') }
    if (scopeMode.value === 'folder' && form.folder) params.folder = form.folder
    if (scopeMode.value === 'manual') params.sample_ids = pickedIds.value.join(',')
    preview.value = await request.get('/ai-vision/train-jobs/preview', { params })
  } catch {
    // handled
  }
}

let hadRunning = false
async function fetchJobs() {
  loading.value = true
  try {
    const res: any = await request.get('/ai-vision/train-jobs', { params: { page: 1, page_size: 50 } })
    jobs.value = res.items || []
    // 训练从运行中→结束的边沿：自动刷新模型档案（产物已入库）
    const running = jobs.value.some((j) => j.status === 'running')
    if (hadRunning && !running) fetchModels()
    hadRunning = running
  } catch {
    // handled
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  try {
    await ElMessageBox.confirm(
      `用 ${preview.value?.samples ?? 0} 张已标注样本，基于「${baseModel.value?.name}」训练 ${form.epochs} 轮？CPU 训练耗时较长。`,
      '发起训练',
      { type: 'warning' },
    )
  } catch {
    return
  }
  creating.value = true
  try {
    const payload: any = { ...form }
    // 按范围模式清理互斥字段：手动勾选只发 sample_ids，分组只发 folder
    if (scopeMode.value !== 'folder') payload.folder = ''
    payload.sample_ids = scopeMode.value === 'manual' ? pickedIds.value : []
    const res: any = await request.post('/ai-vision/train-jobs', payload)
    ElMessage.success(`训练已启动（训练集 ${res.dataset?.train} 张 / 验证集 ${res.dataset?.val} 张）`)
    await fetchJobs()
  } catch {
    // handled
  } finally {
    creating.value = false
  }
}

async function handleCancel(row: any) {
  try {
    await ElMessageBox.confirm(`确定取消训练任务 #${row.id} 吗？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  await request.post(`/ai-vision/train-jobs/${row.id}/cancel`)
  ElMessage.success('已发送取消指令')
  await fetchJobs()
}

async function handleDeleteJob(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除训练任务 #${row.id} 的记录？将连带清理其数据集副本与训练产物目录（日志/权重）。已注册的产物模型不会被删除（在基座档案里单独管理）。`,
      '删除训练任务',
      { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
    )
  } catch {
    return // 取消
  }
  try {
    const res: any = await request.delete(`/ai-vision/train-jobs/${row.id}`)
    ElMessage.success(res?.msg || '已删除')
    await fetchJobs()
  } catch {
    // handled by interceptor
  }
}

async function openLog(row: any) {
  logVisible.value = true
  logLines.value = []
  const load = async () => {
    try {
      const res: any = await request.get(`/ai-vision/train-jobs/${row.id}/log`, { params: { tail: 200 } })
      logLines.value = res.lines || []
    } catch {
      // ignore
    }
  }
  await load()
  if (logTimer) window.clearInterval(logTimer)
  logTimer = window.setInterval(load, 3000)
}

// ═══ 基座档案（.pt + 类别表登记）═══
const ptVisible = ref(false)
const ptSaving = ref(false)
const ptTarget = ref<any>(null)
const ptPath = ref('')
const namesVisible = ref(false)
const namesSaving = ref(false)
const namesTarget = ref<any>(null)
const namesText = ref('')

function modelNames(m: any): string[] {
  try {
    const map = JSON.parse(m.category_map || '{}')
    return Object.keys(map).sort((a, b) => Number(a) - Number(b)).map((k) => map[k])
  } catch {
    return []
  }
}

function openPtDialog(row: any) {
  ptTarget.value = row
  ptPath.value = row.pt_path || ''
  ptVisible.value = true
}

async function savePt() {
  if (!ptTarget.value || !ptPath.value.trim()) return
  ptSaving.value = true
  try {
    await request.put(`/ai-vision/runtime/env-files/${ptTarget.value.id}/pt`, { path: ptPath.value.trim() })
    ElMessage.success('基座权重已登记')
    ptVisible.value = false
    await fetchModels()
  } catch {
    // handled
  } finally {
    ptSaving.value = false
  }
}

function openNamesDialog(row: any) {
  namesTarget.value = row
  namesText.value = modelNames(row).join('\n')
  namesVisible.value = true
}

async function saveNames() {
  const names = namesText.value.split('\n').map((s) => s.trim()).filter(Boolean)
  if (!namesTarget.value || !names.length) return
  namesSaving.value = true
  try {
    await request.put(`/ai-vision/runtime/env-files/${namesTarget.value.id}/category-map`, { names })
    ElMessage.success('类别表已登记')
    namesVisible.value = false
    await fetchModels()
  } catch {
    // handled
  } finally {
    namesSaving.value = false
  }
}

async function fetchModels() {
  modelsLoading.value = true
  try {
    const m: any = await request.get('/ai-vision/models', { params: { page: 1, page_size: 100 } })
    models.value = m.items || []
  } catch {
    // handled
  } finally {
    modelsLoading.value = false
  }
}

/** 刷新基座档案（训练产物入库后手动拉新） */
async function refreshModels() {
  await fetchModels()
  ElMessage.success('模型列表已刷新')
}

async function handleDeleteModel(row: any) {
  try {
    await ElMessageBox.confirm(
      `确定删除模型「${row.name} v${row.version}」？ONNX 与 .pt 文件将一并删除，不可恢复。被识别事件绑定的模型无法删除。`,
      '删除模型',
      { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
    )
  } catch {
    return // 取消
  }
  try {
    const res: any = await request.delete(`/ai-vision/models/${row.id}`)
    ElMessage.success(res.msg || '模型已删除')
    await Promise.all([fetchModels(), fetchJobs()])
  } catch {
    // handled by interceptor（被绑定时后端返回具体事件名提示）
  }
}

async function fetchCategories() {
  try {
    const c: any = await request.get('/ai-vision/categories', { params: { page: 1, page_size: 100 } })
    categories.value = c.items || []
  } catch {
    // handled
  }
}

onMounted(async () => {
  await Promise.all([fetchModels(), fetchCategories(), fetchFolders()])
  await fetchJobs()
  pollTimer = window.setInterval(fetchJobs, 5000)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  if (logTimer) window.clearInterval(logTimer)
})
</script>

<style scoped>
.train-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
/* 嵌入页自带 padding，抵消避免双重留白 */
.train-page :deep(.sample-page) { padding: 0; }
.log-pre {
  background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px;
  max-height: 420px; overflow: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap;
}
.picker-tip { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.picker-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px; max-height: 60vh; overflow-y: auto;
}
.picker-card {
  border: 2px solid #ebeef5; border-radius: 6px; overflow: hidden; cursor: pointer; background: #fff;
}
.picker-card.on { border-color: var(--el-color-success, #67c23a); box-shadow: 0 0 0 2px var(--el-color-success-light-7, #c2e7b0); }
.picker-img { position: relative; width: 100%; background: #1e1e1e; }
.picker-img :deep(.el-image), .picker-img img { width: 100%; height: 100%; display: block; }
.picker-meta { display: flex; align-items: center; justify-content: space-between; padding: 4px 6px; }
.ov-box {
  position: absolute; border: 2px solid #22c55e; box-sizing: border-box; pointer-events: none;
  outline: 1px solid rgba(0, 0, 0, 0.7); outline-offset: -1px;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.5);
}
</style>
