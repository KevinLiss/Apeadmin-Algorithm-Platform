<template>
  <div class="sample-page">
    <div class="page-header" v-if="!embedded">
      <h2>样本库</h2>
      <p class="text-muted">收集模型训练样本：支持批量上传与告警转样本，用于后续微调训练</p>
    </div>

    <!-- 筛选栏 -->
    <el-form :inline="true" class="filter-bar">
      <el-form-item label="类别">
        <el-select v-model="filters.category_code" placeholder="全部" clearable filterable style="width: 150px" @change="handleFilterChange">
          <el-option v-for="c in categories" :key="c.code" :label="`${c.name} (${c.code})`" :value="c.code" />
        </el-select>
      </el-form-item>
      <el-form-item label="来源">
        <el-select v-model="filters.source" placeholder="全部" clearable style="width: 120px" @change="handleFilterChange">
          <el-option label="上传" value="upload" />
          <el-option label="告警" value="alarm" />
        </el-select>
      </el-form-item>
      <el-form-item label="分组">
        <el-select v-model="filters.folder" placeholder="全部" clearable style="width: 160px" @change="handleFilterChange">
          <el-option label="未分组" value="__none__" />
          <el-option v-for="f in folders" :key="f.folder" :label="`${f.folder}（${f.labeled}/${f.total} 已标注）`" :value="f.folder" />
        </el-select>
      </el-form-item>
      <el-form-item label="标注状态">
        <el-select v-model="filters.label_status" placeholder="全部" clearable style="width: 130px" @change="handleFilterChange">
          <el-option label="未标注" value="unlabeled" />
          <el-option label="已标注" value="labeled" />
          <el-option label="跳过（负样本）" value="skipped" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="showBoxes">显示标注框</el-checkbox>
      </el-form-item>
      <el-form-item>
        <el-button @click="fetchList" :loading="loading">
          <el-icon><Refresh /></el-icon> 刷新
        </el-button>
        <el-button v-if="tableData.length" @click="toggleSelectAll">
          {{ selectedIds.size === tableData.length ? '取消全选' : '全选本页' }}
        </el-button>
      </el-form-item>
    </el-form>

    <!-- ═══ 智能建议（主动学习）：误报→负样本 / 已确认→正样本 ═══ -->
    <el-card v-if="sug.negative.length || sug.positive.length" shadow="never" class="sug-card">
      <div class="sug-head">
        <el-icon color="#e6a23c"><MagicStick /></el-icon>
        <span class="sug-title">智能建议</span>
        <span class="sug-tip">误报图转"负样本"、已确认真实告警转"正样本"——下次训练带上它们，模型会越来越准</span>
        <el-button link size="small" style="margin-left: auto" :loading="sugLoading" @click="fetchSuggestions">刷新</el-button>
      </div>
      <div class="sug-body">
        <div v-for="a in sug.negative" :key="'n' + a.alarm_id" class="sug-item">
          <el-image :src="imageUrl(a.snapshot)" fit="cover" class="sug-thumb" :preview-src-list="[imageUrl(a.snapshot)]" preview-teleported />
          <div class="sug-meta">
            <el-tag size="small" type="danger">误报</el-tag>
            <el-text size="small">{{ categoryName(a.category_code) }} {{ (a.confidence * 100).toFixed(0) }}%</el-text>
            <el-text size="small" type="info">#{{ a.alarm_id }}</el-text>
          </div>
          <el-button size="small" type="warning" plain @click="toSample(a, 1)" v-permission="'ai_vision:alarm:edit'">转负样本</el-button>
        </div>
        <div v-for="a in sug.positive" :key="'p' + a.alarm_id" class="sug-item">
          <el-image :src="imageUrl(a.snapshot)" fit="cover" class="sug-thumb" :preview-src-list="[imageUrl(a.snapshot)]" preview-teleported />
          <div class="sug-meta">
            <el-tag size="small" type="success">已确认</el-tag>
            <el-text size="small">{{ categoryName(a.category_code) }} {{ (a.confidence * 100).toFixed(0) }}%</el-text>
            <el-text size="small" type="info">#{{ a.alarm_id }}</el-text>
          </div>
          <el-button size="small" type="primary" plain @click="toSample(a, 0)" v-permission="'ai_vision:alarm:edit'">转正样本</el-button>
        </div>
      </div>
    </el-card>

    <!-- 上传栏 -->
    <div class="upload-bar">
      <el-upload
        :auto-upload="false"
        :on-change="handleFileChange"
        :on-remove="handleFileRemove"
        :file-list="fileList"
        multiple
        accept=".jpg,.jpeg,.png,.bmp,.webp"
        drag
        style="flex: 1"
      >
        <el-icon style="font-size: 32px; color: #909399"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽图片到此处，或 <em>点击选择</em>（jpg/png/webp，单次最多 50 张）</div>
      </el-upload>
      <div class="upload-options">
        <el-select v-model="uploadCategory" placeholder="关联类别（可选）" clearable filterable style="width: 180px">
          <el-option v-for="c in categories" :key="c.code" :label="`${c.name} (${c.code})`" :value="c.code" />
        </el-select>
        <el-select v-model="uploadFolder" placeholder="存入分组（可选）" filterable allow-create default-first-option clearable style="width: 180px">
          <el-option v-for="f in folders" :key="f.folder" :label="f.folder" :value="f.folder" />
        </el-select>
        <el-button type="primary" :loading="uploading" :disabled="fileList.length === 0" @click="handleUpload">
          上传 {{ fileList.length ? `${fileList.length} 张` : '' }}
        </el-button>
      </div>
    </div>

    <!-- 批量操作条（勾选后出现） -->
    <div class="batch-bar" v-if="selectedIds.size > 0">
      <el-text size="small">已选 {{ selectedIds.size }} 张</el-text>
      <el-select v-model="batchModelId" placeholder="预标注模型" size="small" style="width: 180px">
        <el-option v-for="m in detectModels" :key="m.id" :label="`${m.name} v${m.version}`" :value="m.id" />
      </el-select>
      <el-button size="small" type="primary" :loading="batchActing" :disabled="!batchModelId" @click="handleBatchPrelabel" v-permission="'ai_vision:sample:edit'">批量 AI 标注</el-button>
      <el-button size="small" :loading="batchActing" @click="handleBatchMove" v-permission="'ai_vision:sample:edit'">📁 移入分组</el-button>
      <el-button size="small" type="danger" :loading="batchActing" @click="handleBatchDelete" v-permission="'ai_vision:sample:delete'">批量删除</el-button>
      <el-button size="small" text @click="selectedIds = new Set()">取消选择</el-button>
    </div>

    <!-- 网格列表 -->
    <div v-loading="loading" class="grid-wrap">
      <div class="sample-grid" v-if="tableData.length">
        <div class="sample-card" :class="{ checked: selectedIds.has(s.id) }" v-for="s in tableData" :key="s.id">
          <el-checkbox
            class="card-check"
            :model-value="selectedIds.has(s.id)"
            @change="toggleSelect(s.id)"
            @click.stop
          />
          <div class="sample-img-box" :style="{ aspectRatio: (s.width && s.height ? s.width / s.height : 16 / 9) + '' }">
            <el-image
              :src="s.image_url"
              :preview-src-list="[s.image_url]"
              preview-teleported
              fit="fill"
              class="sample-img"
            />
            <!-- 标注框叠加层：网格内直接预览标注结果（绿=人工/AI确认，橙=AI未确认） -->
            <div v-if="showBoxes && boxesOf(s).length" class="box-overlay">
              <div
                v-for="(b, bi) in boxesOf(s)"
                :key="bi"
                class="ov-box"
                :class="{ ai: b.conf != null }"
                :style="{
                  left: `${(b.x - b.w / 2) * 100}%`,
                  top: `${(b.y - b.h / 2) * 100}%`,
                  width: `${b.w * 100}%`,
                  height: `${b.h * 100}%`,
                }"
              />
            </div>
            <el-tag v-if="boxesOf(s).length" class="box-count" size="small" type="success">{{ boxesOf(s).length }} 框</el-tag>
          </div>
          <div class="sample-info">
            <div class="sample-row">
              <el-tag size="small" type="warning">{{ categoryName(s.category_code) }}</el-tag>
              <el-tag size="small" :type="s.source === 'upload' ? 'primary' : 'success'">
                {{ s.source === 'upload' ? '上传' : '告警' }}
              </el-tag>
              <el-tag size="small" :type="s.label_status === 'labeled' ? 'success' : s.label_status === 'skipped' ? 'warning' : 'info'">
                {{ s.label_status === 'labeled' ? '已标注' : s.label_status === 'skipped' ? '跳过(负样本)' : '未标注' }}
              </el-tag>
              <el-tag v-if="s.folder" size="small" type="info" effect="plain">📁{{ s.folder }}</el-tag>
            </div>
            <div class="sample-row meta">
              <el-text size="small" type="info">{{ s.width }}×{{ s.height }}</el-text>
              <el-text size="small" type="info">#{{ s.id }}</el-text>
            </div>
            <div class="sample-row">
              <el-button link type="primary" size="small" @click="openLabel(s)" v-permission="'ai_vision:sample:edit'">标注</el-button>
              <el-button
                v-if="s.label_status !== 'skipped'"
                link
                type="warning"
                size="small"
                @click="markSkipped(s)"
                v-permission="'ai_vision:sample:edit'"
              >跳过</el-button>
              <el-button
                v-else
                link
                type="info"
                size="small"
                @click="unmarkSkipped(s)"
                v-permission="'ai_vision:sample:edit'"
              >取消跳过</el-button>
              <el-button link type="danger" size="small" @click="handleDelete(s)" v-permission="'ai_vision:sample:delete'">删除</el-button>
            </div>
          </div>
        </div>
      </div>
      <el-empty v-else-if="!loading" description="还没有样本" :image-size="120">
        <p class="empty-tip">第四步：样本用于模型训练与优化。可上传图片，或在告警中心把抓拍图"转样本"</p>
        <el-button type="primary" @click="goAlarms">
          <el-icon><ArrowRight /></el-icon> 前往告警中心转样本
        </el-button>
      </el-empty>
    </div>

    <!-- 分页 -->
    <div class="pagination" v-if="total > 0">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[12, 24, 48]"
        layout="total, sizes, prev, pager, next"
        @size-change="fetchList"
        @current-change="fetchList"
      />
    </div>

    <!-- 标注弹窗：图上拖框画 bbox。禁用 Esc/遮罩关闭——Esc 已用作"取消选中框"，
         点遮罩也会静默丢掉未保存标注（自查 P1-1 / 二轮 P2-1） -->
    <el-dialog v-model="labelVisible" :title="`标注样本 #${labelSample?.id ?? ''}`" width="900px" top="4vh" :close-on-press-escape="false" :close-on-click-modal="false" @closed="onLabelClosed">
      <div class="label-wrap">
        <div class="label-canvas-box" ref="canvasBoxRef">
          <canvas
            ref="labelCanvasRef"
            @mousedown="onCanvasDown"
            @mousemove="onCanvasMove"
            @mouseup="onCanvasUp"
            @mouseleave="onCanvasUp"
            style="cursor: crosshair; display: block"
          ></canvas>
        </div>
        <div class="label-side">
          <!-- ① 画新框 -->
          <div class="ls-group">
            <div class="ls-group-title">画新框</div>
            <el-select v-model="labelClass" placeholder="新框类别" style="width: 100%">
              <el-option v-for="c in categories" :key="c.code" :label="`${c.name} (${c.code})`" :value="c.code" />
            </el-select>
            <el-text size="small" type="info" class="ls-help">空白处拖拽画框 · 框内拖动=移动 · 拖四角=缩放 · Delete 删除 · Ctrl+Z 撤销</el-text>
          </div>
          <!-- ② AI 辅助 -->
          <div class="ls-group">
            <div class="ls-group-title">AI 辅助</div>
            <div class="label-ai-row">
              <el-select v-model="prelabelModelId" placeholder="预标注模型" size="small" style="flex: 1">
                <el-option
                  v-for="m in detectModels"
                  :key="m.id"
                  :label="`${m.name} v${m.version}`"
                  :value="m.id"
                />
              </el-select>
              <el-button size="small" type="primary" plain :loading="prelabeling" :disabled="!prelabelModelId" @click="runPrelabel" v-permission="'ai_vision:sample:edit'">
                预标注
              </el-button>
            </div>
            <el-button
              v-if="labelBoxes.some((b) => b.conf != null)"
              size="small"
              type="success"
              plain
              style="width: 100%"
              @click="acceptAllAiBoxes"
            >
              一键接受全部 AI 框（{{ labelBoxes.filter((b) => b.conf != null).length }} 个）
            </el-button>
          </div>
          <!-- ③ 框列表（逐框改类别） -->
          <div class="ls-group ls-grow">
            <div class="ls-group-title">标注框（{{ labelBoxes.length }}）</div>
            <div class="label-box-list">
              <div
                v-for="(b, i) in labelBoxes"
                :key="i"
                class="label-box-item"
                :class="{ active: selectedBox === i }"
                @click="selectedBox = i"
                @mouseenter="onListHover(i)"
                @mouseleave="onListHover(-1)"
              >
                <!-- 每个框可独立改类别（改的是这个框自己，与"新框类别"下拉无关） -->
                <el-select
                  v-model="b.class_name"
                  size="small"
                  style="width: 106px"
                  @click.stop
                  @change="onBoxClassChange(b)"
                >
                  <el-option v-for="c in categories" :key="c.code" :label="c.name" :value="c.code" />
                </el-select>
                <el-tag v-if="b.conf != null" size="small" type="info">AI {{ (b.conf * 100).toFixed(0) }}%</el-tag>
                <el-button link type="danger" size="small" @click.stop="snapshot(); labelBoxes.splice(i, 1); selectedBox = -1; drawCanvas()">删</el-button>
              </div>
              <el-text v-if="!labelBoxes.length" size="small" type="info">暂无框，去图上拖一个</el-text>
            </div>
          </div>
          <!-- ④ 操作 -->
          <div class="ls-group ls-actions">
            <el-button size="small" text :disabled="!undoStack.length" @click="undo">撤销{{ undoStack.length ? `(${undoStack.length})` : '' }}</el-button>
            <el-button size="small" text type="warning" @click="snapshot(); labelBoxes = []; selectedBox = -1; drawCanvas()">清空</el-button>
            <el-button size="small" type="primary" :loading="labelSaving" @click="saveLabel" style="flex: 1">保存标注</el-button>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
defineProps<{ embedded?: boolean }>()
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, UploadFilled, ArrowRight, MagicStick } from '@element-plus/icons-vue'
import type { UploadFile, UploadFiles } from 'element-plus'
import { useRouter } from 'vue-router'
import request from '@/api/request'

const router = useRouter()

const loading = ref(false)
const uploading = ref(false)
const tableData = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(12)
const categories = ref<any[]>([])
const fileList = ref<UploadFile[]>([])
const uploadCategory = ref('')

const filters = reactive({
  category_code: '',
  source: '',
  label_status: '',
  folder: '',
})

const showBoxes = ref(true)
const folders = ref<any[]>([])
const uploadFolder = ref('')

/** 解析样本标注框（label_data JSON → boxes 数组），失败返回空 */
function boxesOf(s: any): any[] {
  if (s.label_status !== 'labeled') return []
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

// 相对路径 → 静态 URL
function imageUrl(path: string) {
  if (!path) return ''
  const idx = path.lastIndexOf('ai_vision')
  if (idx >= 0) return '/api/v1/ai-vision/media/' + path.slice(idx + 'ai_vision/'.length).replace(/\\/g, '/')
  return ''
}

/** 类别 code → 中文名（未匹配回退 code 本身） */
function categoryName(code: string) {
  if (!code) return '未分类'
  return categories.value.find((c) => c.code === code)?.name || code
}

async function fetchCategories() {
  try {
    const res: any = await request.get('/ai-vision/categories', { params: { page: 1, page_size: 100 } })
    categories.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

async function fetchList() {
  loading.value = true
  try {
    const params: any = { page: page.value, page_size: pageSize.value }
    if (filters.category_code) params.category_code = filters.category_code
    if (filters.source) params.source = filters.source
    if (filters.label_status) params.label_status = filters.label_status
    if (filters.folder) params.folder = filters.folder
    const res: any = await request.get('/ai-vision/samples', { params })
    const items = res.items || []
    tableData.value = items.map((s: any) => ({ ...s, image_url: imageUrl(s.file_path) }))
    total.value = res.total || 0
    // 翻页/刷新后清空勾选（避免选中项不在当前页造成误操作）
    selectedIds.value = new Set()
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

// ── 主动学习：智能建议 + 跳过标记 ───────────────────
const sug = reactive<{ negative: any[]; positive: any[] }>({ negative: [], positive: [] })
const sugLoading = ref(false)

async function fetchSuggestions() {
  sugLoading.value = true
  try {
    const res: any = await request.get('/ai-vision/samples/suggestions', { params: { limit: 8 } })
    sug.negative = res.negative || []
    sug.positive = res.positive || []
  } catch {
    sug.negative = []
    sug.positive = []
  } finally {
    sugLoading.value = false
  }
}

/** 告警一键转样本：asNegative=1 转负样本（skipped 背景图），0 转正样本 */
async function toSample(a: any, asNegative: number) {
  try {
    const res: any = await request.post(`/ai-vision/alarms/${a.alarm_id}/to-sample?as_negative=${asNegative}`)
    ElMessage.success(res?.msg || (asNegative ? '已转负样本' : '已转样本'))
    sug.negative = sug.negative.filter((x) => x.alarm_id !== a.alarm_id)
    sug.positive = sug.positive.filter((x) => x.alarm_id !== a.alarm_id)
    fetchList()
  } catch {
    // handled
  }
}

/** 样本卡"跳过"= 标记为负样本（背景图） */
async function markSkipped(s: any) {
  try {
    await ElMessageBox.confirm(
      '标记为"跳过"表示这张图里没有需要检测的目标（或不该报警）。开启背景负样本训练后，它会作为背景图参与训练，帮助模型压误报。',
      '标记为负样本',
      { type: 'info', confirmButtonText: '跳过', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await request.put(`/ai-vision/samples/${s.id}/status`, { label_status: 'skipped' })
    ElMessage.success('已标记为负样本')
    fetchList()
  } catch {
    // handled
  }
}

/** 取消跳过：按"有无有效标注框"恢复原状态——直接写 unlabeled 会让
 *  原本已标注的样本带着框退出训练筛选（labeled-only），永远不再参训（自查 P1-2） */
async function unmarkSkipped(s: any) {
  let boxes: any[] = []
  try {
    boxes = (JSON.parse(s.label_data || '{}').boxes || []).filter((b: any) => b && b.class_name)
  } catch {
    boxes = []
  }
  try {
    await request.put(`/ai-vision/samples/${s.id}/status`, { label_status: boxes.length ? 'labeled' : 'unlabeled' })
    ElMessage.success(boxes.length ? '已恢复为已标注' : '已恢复为未标注')
    fetchList()
  } catch {
    // handled
  }
}

// ── 批量选择 / 批量操作 ─────────────────────────────
const selectedIds = ref<Set<number>>(new Set())
const batchActing = ref(false)
const batchModelId = ref<number | null>(null)

function toggleSelect(id: number) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function toggleSelectAll() {
  if (selectedIds.value.size === tableData.value.length) {
    selectedIds.value = new Set()
  } else {
    selectedIds.value = new Set(tableData.value.map((s: any) => s.id))
  }
}

async function handleBatchPrelabel() {
  const ids = [...selectedIds.value]
  if (!ids.length || !batchModelId.value) return
  try {
    await ElMessageBox.confirm(
      `将用所选模型对 ${ids.length} 张样本自动画框并直接保存为已标注。已有"人工框"的样本会自动跳过不覆盖；仅含 AI 框的样本会被本次结果替换。`,
      '批量 AI 标注',
      { type: 'info', confirmButtonText: '开始标注', cancelButtonText: '取消' },
    )
  } catch {
    return // 取消
  }
  batchActing.value = true
  try {
    const res: any = await request.post(
      '/ai-vision/samples/batch-prelabel',
      { sample_ids: ids, model_id: batchModelId.value },
      { timeout: 300000 },
    )
    ElMessage.success(res.msg || '批量预标注完成')
    selectedIds.value = new Set()
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    batchActing.value = false
  }
}

async function handleBatchMove() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  let folder: string
  try {
    const { value } = await ElMessageBox.prompt(
      `将选中的 ${ids.length} 张样本移入分组（输入新名称即可新建分组；留空=移回未分组）`,
      '移入分组',
      { confirmButtonText: '移动', cancelButtonText: '取消', inputPlaceholder: '如：泳池白天', inputValue: '' },
    )
    folder = (value || '').trim()
  } catch {
    return // 取消
  }
  batchActing.value = true
  try {
    const res: any = await request.post('/ai-vision/samples/batch-move', { sample_ids: ids, folder })
    ElMessage.success(res.msg || '移动完成')
    selectedIds.value = new Set()
    await Promise.all([fetchList(), fetchFolders()])
  } catch {
    // handled
  } finally {
    batchActing.value = false
  }
}

async function handleBatchDelete() {
  const ids = [...selectedIds.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${ids.length} 个样本？图片文件将一并删除，且不可恢复。`,
      '批量删除样本',
      { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
    )
  } catch {
    return // 取消
  }
  batchActing.value = true
  try {
    const res: any = await request.post('/ai-vision/samples/batch-delete', { sample_ids: ids })
    ElMessage.success(res.msg || `已删除 ${ids.length} 个样本`)
    selectedIds.value = new Set()
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    batchActing.value = false
  }
}

function goAlarms() {
  router.push('/ai-vision/monitor?tab=records')
}

function handleFileChange(file: UploadFile, files: UploadFiles) {
  fileList.value = files
}

function handleFileRemove() {
  // el-upload 移除时自动更新 fileList，无需额外处理
}

async function handleUpload() {
  if (fileList.value.length === 0) return
  uploading.value = true
  try {
    const form = new FormData()
    for (const f of fileList.value) {
      if (f.raw) form.append('files', f.raw)
    }
    const params: any = {}
    if (uploadCategory.value) params.category_code = uploadCategory.value
    if (uploadFolder.value) params.folder = uploadFolder.value
    const res: any = await request.post('/ai-vision/samples/upload', form, {
      params,
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000,
    })
    const errs = res.errors || []
    if (res.saved?.length) {
      ElMessage.success(`上传成功 ${res.saved.length} 张${errs.length ? `，失败 ${errs.length} 张` : ''}`)
    } else if (errs.length) {
      ElMessage.error(`上传失败：${errs[0]}`)
    }
    fileList.value = []
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    uploading.value = false
  }
}

async function handleDelete(s: any) {
  try {
    await ElMessageBox.confirm(`确定删除样本 #${s.id} 吗？文件将一并删除。`, '提示', { type: 'warning' })
  } catch {
    return // 取消
  }
  await request.delete(`/ai-vision/samples/${s.id}`)
  ElMessage.success('已删除')
  await fetchList()
}

// ═══ 标注（canvas 拖框）═══════════════════════════
const labelVisible = ref(false)
const labelSaving = ref(false)
const labelSample = ref<any>(null)
const labelBoxes = ref<any[]>([])
const labelClass = ref('')
const selectedBox = ref(-1)
const hoverBox = ref(-1)
const labelCanvasRef = ref<HTMLCanvasElement | null>(null)
const canvasBoxRef = ref<HTMLElement | null>(null)
let labelImg: HTMLImageElement | null = null
let drawScale = 1 // canvas 显示像素 / 原图像素
let dragStart: { x: number; y: number } | null = null
let dragCur: { x: number; y: number } | null = null

function openLabel(s: any) {
  labelSample.value = s
  labelClass.value = s.category_code || categories.value[0]?.code || ''
  try {
    labelBoxes.value = (JSON.parse(s.label_data || '{}').boxes || []).map((b: any) => ({ ...b }))
  } catch {
    labelBoxes.value = []
  }
  selectedBox.value = -1
  hoverBox.value = -1
  undoStack.value = []
  labelVisible.value = true
  nextTick(() => loadLabelImage(s.image_url))
}

function onLabelClosed() {
  labelSample.value = null
  labelBoxes.value = []
  labelImg = null
}

/** 列表里改某个框的类别 → 画布同步重绘（v-model 已改 b.class_name） */
function onBoxClassChange(b: any) {
  snapshot()
  // 人工改过类别即视为人工确认框：去掉 AI 角标（保存后也不再被批量AI标注覆盖）
  delete b.conf
  drawCanvas()
}

function loadLabelImage(url: string) {
  const img = new Image()
  img.onload = () => {
    labelImg = img
    fitCanvas()
    drawCanvas()
  }
  img.src = url
}

function fitCanvas() {
  const cv = labelCanvasRef.value
  const box = canvasBoxRef.value
  if (!cv || !labelImg || !box) return
  const maxW = box.clientWidth - 4
  const maxH = window.innerHeight * 0.62
  drawScale = Math.min(maxW / labelImg.width, maxH / labelImg.height, 1)
  cv.width = Math.round(labelImg.width * drawScale)
  cv.height = Math.round(labelImg.height * drawScale)
}

function drawCanvas() {
  const cv = labelCanvasRef.value
  if (!cv || !labelImg) return
  const ctx = cv.getContext('2d')!
  ctx.clearRect(0, 0, cv.width, cv.height)
  ctx.drawImage(labelImg, 0, 0, cv.width, cv.height)
  // 已存框：人工框=绿实线，AI 未确认框=橙虚线（与列表叠加层一致）；
  // 一律先描 4px 黑边再画 2px 彩色线，水面/天空等亮背景下也清晰
  labelBoxes.value.forEach((b, i) => {
    const [x0, y0, x1, y1] = yoloToPx(b)
    const sel = i === selectedBox.value
    const hov = i === hoverBox.value
    const color = sel ? '#ff4d4f' : (b.conf != null ? '#e6a23c' : '#22c55e')
    ctx.setLineDash(!sel && b.conf != null ? [6, 3] : [])
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.85)'
    ctx.lineWidth = sel ? 5 : hov ? 4.5 : 4
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0)
    ctx.strokeStyle = color
    ctx.lineWidth = sel ? 3 : hov ? 2.5 : 2
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0)
    ctx.setLineDash([])
    // hover（未选中）加半透明填充，列表↔画布联动更醒目
    if (hov && !sel) {
      ctx.fillStyle = 'rgba(255, 77, 79, 0.12)'
      ctx.fillRect(x0, y0, x1 - x0, y1 - y0)
    }
    // 标签底色块：黑底白字，不再用彩字直接叠在画面上
    const text = categoryName(b.class_name) + (b.conf != null ? ` AI${(b.conf * 100).toFixed(0)}%` : '')
    ctx.font = 'bold 12px sans-serif'
    const tw = ctx.measureText(text).width
    const ty = Math.max(14, y0) - 14
    ctx.fillStyle = 'rgba(0, 0, 0, 0.75)'
    ctx.fillRect(x0, ty, tw + 8, 15)
    ctx.fillStyle = color
    ctx.fillText(text, x0 + 4, ty + 11)
    // 选中框：四角白色拉伸把手
    if (sel) {
      ctx.fillStyle = '#fff'
      ctx.strokeStyle = '#ff4d4f'
      ctx.lineWidth = 1.5
      for (const [hx, hy] of [[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) {
        ctx.fillRect(hx - 4, hy - 4, 8, 8)
        ctx.strokeRect(hx - 4, hy - 4, 8, 8)
      }
    }
  })
  // 拖拽中的新框
  if (dragMode === 'new' && dragStart && dragCur) {
    ctx.setLineDash([5, 3])
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.85)'
    ctx.lineWidth = 4
    ctx.strokeRect(dragStart.x, dragStart.y, dragCur.x - dragStart.x, dragCur.y - dragStart.y)
    ctx.strokeStyle = '#67c23a'
    ctx.lineWidth = 2
    ctx.strokeRect(dragStart.x, dragStart.y, dragCur.x - dragStart.x, dragCur.y - dragStart.y)
    ctx.setLineDash([])
  }
}

/** YOLO 归一化(中心+wh) → canvas 像素(x0,y0,x1,y1) */
function yoloToPx(b: any): number[] {
  const cv = labelCanvasRef.value!
  const x0 = (b.x - b.w / 2) * cv.width
  const y0 = (b.y - b.h / 2) * cv.height
  return [x0, y0, x0 + b.w * cv.width, y0 + b.h * cv.height]
}

function canvasPos(e: MouseEvent) {
  const cv = labelCanvasRef.value!
  const r = cv.getBoundingClientRect()
  return { x: e.clientX - r.left, y: e.clientY - r.top }
}

// ── 撤销栈：几何改动/删除/新增/清空/接受AI 前压快照（上限 50 步）──
const undoStack = ref<string[]>([])
function snapshot() {
  undoStack.value.push(JSON.stringify(labelBoxes.value))
  if (undoStack.value.length > 50) undoStack.value.shift()
}
function undo() {
  const s = undoStack.value.pop()
  if (s == null) {
    ElMessage.info('没有可撤销的操作')
    return
  }
  labelBoxes.value = JSON.parse(s)
  if (selectedBox.value >= labelBoxes.value.length) selectedBox.value = -1
  drawCanvas()
}

/** 像素框 → 归一化写回（clamp 到图内，最小 0.005） */
function pxToBox(b: any, x0: number, y0: number, x1: number, y1: number) {
  const cv = labelCanvasRef.value!
  let nx0 = Math.max(0, Math.min(x0, x1)) / cv.width
  let nx1 = Math.min(1, Math.max(x0, x1)) / cv.width
  let ny0 = Math.max(0, Math.min(y0, y1)) / cv.height
  let ny1 = Math.min(1, Math.max(y0, y1)) / cv.height
  if (nx1 - nx0 < 0.005) { const c = (nx0 + nx1) / 2; nx0 = Math.max(0, c - 0.0025); nx1 = Math.min(1, c + 0.0025) }
  if (ny1 - ny0 < 0.005) { const c = (ny0 + ny1) / 2; ny0 = Math.max(0, c - 0.0025); ny1 = Math.min(1, c + 0.0025) }
  b.x = +((nx0 + nx1) / 2).toFixed(4)
  b.y = +((ny0 + ny1) / 2).toFixed(4)
  b.w = +(nx1 - nx0).toFixed(4)
  b.h = +(ny1 - ny0).toFixed(4)
}

/** 命中检测：优先选中框的四角把手（±6px），再框体（从上层往下） */
function hitTest(p: { x: number; y: number }): { mode: 'resize' | 'move' | 'new'; idx: number; corner: number; ox: number; oy: number } {
  if (selectedBox.value >= 0) {
    const [x0, y0, x1, y1] = yoloToPx(labelBoxes.value[selectedBox.value])
    const corners = [[x0, y0], [x1, y0], [x0, y1], [x1, y1]]
    for (let c = 0; c < 4; c++) {
      if (Math.abs(p.x - corners[c][0]) <= 6 && Math.abs(p.y - corners[c][1]) <= 6) {
        return { mode: 'resize', idx: selectedBox.value, corner: c, ox: 0, oy: 0 }
      }
    }
  }
  for (let i = labelBoxes.value.length - 1; i >= 0; i--) {
    const [x0, y0, x1, y1] = yoloToPx(labelBoxes.value[i])
    if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1) {
      return { mode: 'move', idx: i, corner: -1, ox: p.x - x0, oy: p.y - y0 }
    }
  }
  return { mode: 'new', idx: -1, corner: -1, ox: 0, oy: 0 }
}

let dragMode: 'new' | 'move' | 'resize' = 'new'
let dragIdx = -1
let dragCorner = -1
let dragOX = 0
let dragOY = 0
let dragOrigBox: any = null // move/resize 起始快照（相对位移用）

function onCanvasDown(e: MouseEvent) {
  const p = canvasPos(e)
  const hit = hitTest(p)
  dragMode = hit.mode
  if (hit.mode === 'new') {
    selectedBox.value = -1
    dragStart = p
    dragCur = p
    drawCanvas()
    return
  }
  // 操作已有框：先选中 + 压撤销快照 + 记起始几何
  selectedBox.value = hit.idx
  snapshot()
  dragIdx = hit.idx
  dragCorner = hit.corner
  dragOX = hit.ox
  dragOY = hit.oy
  const b = labelBoxes.value[hit.idx]
  const [x0, y0, x1, y1] = yoloToPx(b)
  dragOrigBox = { x0, y0, x1, y1 }
  dragStart = p
  dragCur = p
  updateCursor(p)
}

function onCanvasMove(e: MouseEvent) {
  const p = canvasPos(e)
  if (!dragStart) {
    hoverBox.value = hitTest(p).idx >= 0 ? hitTest(p).idx : -1
    updateCursor(p)
    drawCanvas()
    return
  }
  dragCur = p
  if (dragMode === 'move' && dragIdx >= 0) {
    const b = labelBoxes.value[dragIdx]
    const cv = labelCanvasRef.value!
    const dx = p.x - dragStart.x
    const dy = p.y - dragStart.y
    pxToBox(b, dragOrigBox.x0 + dx, dragOrigBox.y0 + dy, dragOrigBox.x1 + dx, dragOrigBox.y1 + dy)
    drawCanvas()
  } else if (dragMode === 'resize' && dragIdx >= 0) {
    const b = labelBoxes.value[dragIdx]
    const [x0, y0, x1, y1] = dragOrigBox
    const fixed = [[x1, y1], [x0, y1], [x1, y0], [x0, y0]][dragCorner] // 对角为锚点
    pxToBox(b, fixed[0], fixed[1], p.x, p.y)
    drawCanvas()
  } else {
    drawCanvas()
  }
}

function onCanvasUp() {
  if (!dragStart) return
  if (dragMode === 'new') {
    const cv = labelCanvasRef.value!
    const x0 = Math.min(dragStart.x, dragCur!.x) / cv.width
    const x1 = Math.max(dragStart.x, dragCur!.x) / cv.width
    const y0 = Math.min(dragStart.y, dragCur!.y) / cv.height
    const y1 = Math.max(dragStart.y, dragCur!.y) / cv.height
    dragStart = null
    dragCur = null
    // 过小的拖拽视为误触
    if (x1 - x0 < 0.01 || y1 - y0 < 0.01) { drawCanvas(); return }
    if (!labelClass.value) {
      ElMessage.warning('请先选择新框类别')
      drawCanvas()
      return
    }
    snapshot()
    labelBoxes.value.push({
      class_name: labelClass.value,
      x: +((x0 + x1) / 2).toFixed(4),
      y: +((y0 + y1) / 2).toFixed(4),
      w: +(x1 - x0).toFixed(4),
      h: +(y1 - y0).toFixed(4),
    })
    selectedBox.value = labelBoxes.value.length - 1
  }
  dragStart = null
  dragCur = null
  dragIdx = -1
  dragCorner = -1
  dragOrigBox = null
  drawCanvas()
}

function updateCursor(p: { x: number; y: number }) {
  const cv = labelCanvasRef.value
  if (!cv) return
  const hit = hitTest(p)
  if (hit.mode === 'resize') cv.style.cursor = hit.corner < 2 ? 'nwse-resize' : 'nesw-resize'
  else if (hit.mode === 'move') cv.style.cursor = 'move'
  else cv.style.cursor = 'crosshair'
}

/** 列表 → 画布 hover 联动 */
function onListHover(i: number) {
  hoverBox.value = i
  drawCanvas()
}

/** 一键接受全部 AI 框（去 conf 标记 → 转人工框，受批量AI标注保护） */
function acceptAllAiBoxes() {
  const n = labelBoxes.value.filter((b) => b.conf != null).length
  if (!n) {
    ElMessage.info('没有待确认的 AI 框')
    return
  }
  snapshot()
  labelBoxes.value.forEach((b) => delete b.conf)
  drawCanvas()
  ElMessage.success(`已接受 ${n} 个 AI 框为人工标注`)
}

function onLabelKey(e: KeyboardEvent) {
  if (!labelVisible.value) return
  // 输入框内打字不拦截
  const tag = (e.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA') return
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
    e.preventDefault()
    undo()
    return
  }
  if ((e.key === 'Delete' || e.key === 'Backspace') && selectedBox.value >= 0) {
    snapshot()
    labelBoxes.value.splice(selectedBox.value, 1)
    selectedBox.value = -1
    drawCanvas()
  }
  // Esc 取消选中
  if (e.key === 'Escape' && selectedBox.value >= 0) {
    selectedBox.value = -1
    drawCanvas()
  }
}

async function saveLabel() {
  if (!labelSample.value) return
  labelSaving.value = true
  try {
    await request.put(`/ai-vision/samples/${labelSample.value.id}/label`, { boxes: labelBoxes.value })
    ElMessage.success(labelBoxes.value.length ? `已保存 ${labelBoxes.value.length} 个框` : '已取消标注')
    labelVisible.value = false
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    labelSaving.value = false
  }
}

// ═══ AI 预标注 ═══
const models = ref<any[]>([])
const prelabelModelId = ref<number | null>(null)
const prelabeling = ref(false)
// 检测模型（排除 pose——pose 无检测框语义）
const detectModels = computed(() => models.value.filter((m) => !((m.name || '').toLowerCase().includes('pose'))))

async function fetchModels() {
  try {
    const res: any = await request.get('/ai-vision/models', { params: { page: 1, page_size: 100 } })
    models.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

async function runPrelabel() {
  if (!labelSample.value || !prelabelModelId.value) return
  prelabeling.value = true
  try {
    const res: any = await request.post(
      `/ai-vision/samples/${labelSample.value.id}/prelabel`,
      null,
      { params: { model_id: prelabelModelId.value }, timeout: 60000 },
    )
    const boxes: any[] = res.boxes || []
    if (!boxes.length) {
      ElMessage.info('AI 未检出目标，请手工标注')
      return
    }
    // 追加到现有框（不覆盖手工已画的），并提示确认
    labelBoxes.value.push(...boxes)
    selectedBox.value = -1
    drawCanvas()
    ElMessage.success(`AI 预标 ${boxes.length} 框（带 AI 角标），请检查删改后保存`)
  } catch {
    // handled by interceptor
  } finally {
    prelabeling.value = false
  }
}

onMounted(() => {
  fetchList()
  fetchCategories()
  fetchModels()
  fetchSuggestions()
  window.addEventListener('keydown', onLabelKey)
  window.addEventListener('resize', () => { if (labelVisible.value) { fitCanvas(); drawCanvas() } })
})
</script>

<style scoped>
.sample-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.filter-bar { margin-bottom: 12px; }
.sug-card { margin-bottom: 12px; border-color: var(--el-color-warning-light-5, #f3d19e); }
.sug-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.sug-title { font-size: 14px; font-weight: 600; }
.sug-tip { font-size: 12px; color: #909399; }
.sug-body { display: flex; flex-wrap: wrap; gap: 12px; }
.sug-item {
  display: flex; align-items: center; gap: 8px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 8px; padding: 6px 10px 6px 6px;
}
.sug-thumb { width: 72px; height: 44px; border-radius: 4px; flex-shrink: 0; }
.sug-meta { display: flex; flex-direction: column; gap: 2px; min-width: 110px; }
.batch-bar {
  margin-bottom: 12px; display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; background: var(--el-color-primary-light-9, #ecf5ff);
  border: 1px solid var(--el-color-primary-light-7, #c6e2ff); border-radius: 6px;
}
.upload-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  background: #f8f9fa;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.upload-options { display: flex; flex-direction: column; gap: 8px; align-items: stretch; }
.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }
.grid-wrap { min-height: 200px; }
.sample-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}
.sample-card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
  transition: box-shadow 0.2s;
  position: relative;
}
.sample-card:hover { box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08); }
.sample-card.checked { border-color: var(--el-color-primary, #409eff); box-shadow: 0 0 0 2px var(--el-color-primary-light-7, #c6e2ff); }
.card-check {
  position: absolute; top: 6px; left: 6px; z-index: 2;
  padding: 2px 4px; border-radius: 4px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
}
.card-check :deep(.el-checkbox__inner) { border-color: #909399; }
.sample-img { width: 100%; height: 100%; display: block; }
/* 图片框：与样本同宽高比，标注叠加层坐标才能与图片对齐 */
.sample-img-box { position: relative; width: 100%; background: #1e1e1e; overflow: hidden; }
.box-overlay { position: absolute; inset: 0; pointer-events: none; }
.ov-box {
  position: absolute; border: 2px solid #22c55e; box-sizing: border-box;
  /* 内外黑描边：亮色水面背景下小框也能看清 */
  outline: 1px solid rgba(0, 0, 0, 0.7); outline-offset: -1px;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.5);
}
.ov-box.ai { border-color: #e6a23c; border-style: dashed; }
.box-count {
  position: absolute; right: 4px; bottom: 4px; z-index: 1;
  background: rgba(0, 0, 0, 0.55); color: #fff; border: none;
}
.sample-info { padding: 8px; }
.sample-row { display: flex; justify-content: space-between; align-items: center; gap: 4px; margin-bottom: 6px; }
.sample-row.meta { margin-bottom: 2px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }

/* ── 标注弹窗 ── */
.label-wrap { display: flex; gap: 14px; }
.label-canvas-box {
  flex: 1; min-width: 0; background: #1e1e1e; border-radius: 6px;
  display: flex; align-items: center; justify-content: center; overflow: hidden;
}
.label-side { width: 250px; flex-shrink: 0; display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.ls-group {
  background: var(--el-fill-color-lighter, #fafafa);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.ls-group-title { font-size: 12px; font-weight: 600; color: #909399; letter-spacing: 0.5px; }
.ls-help { line-height: 1.5; }
.ls-grow { flex: 1; min-height: 120px; }
.ls-actions { flex-direction: row; align-items: center; background: transparent; border: none; padding: 0; }
.label-ai-row { display: flex; gap: 6px; }
.label-box-list {
  flex: 1; min-height: 0; overflow-y: auto;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 6px; padding: 4px;
}
.label-box-item {
  display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 4px; cursor: pointer;
}
.label-box-item:hover { background: var(--el-fill-color-light, #f5f7fa); }
.label-box-item.active { background: var(--el-color-danger-light-9, #fef0f0); }
</style>