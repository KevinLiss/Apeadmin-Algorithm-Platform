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
              <el-tag size="small" :type="s.label_status === 'labeled' ? 'success' : 'info'">
                {{ s.label_status === 'labeled' ? '已标注' : '未标注' }}
              </el-tag>
              <el-tag v-if="s.folder" size="small" type="info" effect="plain">📁{{ s.folder }}</el-tag>
            </div>
            <div class="sample-row meta">
              <el-text size="small" type="info">{{ s.width }}×{{ s.height }}</el-text>
              <el-text size="small" type="info">#{{ s.id }}</el-text>
            </div>
            <div class="sample-row">
              <el-button link type="primary" size="small" @click="openLabel(s)" v-permission="'ai_vision:sample:edit'">标注</el-button>
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

    <!-- ═══ 标注弹窗：图上拖框画 bbox ═══ -->
    <el-dialog v-model="labelVisible" :title="`标注样本 #${labelSample?.id ?? ''}`" width="900px" top="4vh" @closed="onLabelClosed">
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
          <el-text size="small" type="info">拖拽画框；点击框可选中，Delete 删除选中框；改已有框类别用下方列表里的下拉</el-text>
          <el-select v-model="labelClass" placeholder="新框类别（画新框用）" style="width: 100%; margin: 8px 0">
            <el-option v-for="c in categories" :key="c.code" :label="`${c.name} (${c.code})`" :value="c.code" />
          </el-select>
          <!-- AI 预标注：模型选择 + 一键预填（结果需人工确认后保存） -->
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
              AI 预标注
            </el-button>
          </div>
          <div class="label-box-list">
            <div
              v-for="(b, i) in labelBoxes"
              :key="i"
              class="label-box-item"
              :class="{ active: selectedBox === i }"
              @click="selectedBox = i"
            >
              <!-- 每个框可独立改类别（改的是这个框自己，与"新框类别"下拉无关） -->
              <el-select
                v-model="b.class_name"
                size="small"
                style="width: 118px"
                @click.stop
                @change="onBoxClassChange(b)"
              >
                <el-option v-for="c in categories" :key="c.code" :label="c.name" :value="c.code" />
              </el-select>
              <el-tag v-if="b.conf != null" size="small" type="info">AI {{ (b.conf * 100).toFixed(0) }}%</el-tag>
              <el-text size="small" type="info">({{ b.x.toFixed(2) }},{{ b.y.toFixed(2) }})</el-text>
              <el-button link type="danger" size="small" @click.stop="labelBoxes.splice(i, 1); selectedBox = -1">删</el-button>
            </div>
            <el-text v-if="!labelBoxes.length" size="small" type="info">暂无框</el-text>
          </div>
          <div class="label-actions">
            <el-button size="small" @click="labelBoxes = []; selectedBox = -1">清空</el-button>
            <el-button size="small" type="primary" :loading="labelSaving" @click="saveLabel">保存标注</el-button>
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
import { Refresh, UploadFilled, ArrowRight } from '@element-plus/icons-vue'
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
    const color = sel ? '#ff4d4f' : (b.conf != null ? '#e6a23c' : '#22c55e')
    ctx.setLineDash(!sel && b.conf != null ? [6, 3] : [])
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.85)'
    ctx.lineWidth = sel ? 5 : 4
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0)
    ctx.strokeStyle = color
    ctx.lineWidth = sel ? 3 : 2
    ctx.strokeRect(x0, y0, x1 - x0, y1 - y0)
    ctx.setLineDash([])
    // 标签底色块：黑底白字，不再用彩字直接叠在画面上
    const text = categoryName(b.class_name) + (b.conf != null ? ` AI${(b.conf * 100).toFixed(0)}%` : '')
    ctx.font = 'bold 12px sans-serif'
    const tw = ctx.measureText(text).width
    const ty = Math.max(14, y0) - 14
    ctx.fillStyle = 'rgba(0, 0, 0, 0.75)'
    ctx.fillRect(x0, ty, tw + 8, 15)
    ctx.fillStyle = color
    ctx.fillText(text, x0 + 4, ty + 11)
  })
  // 拖拽中的框
  if (dragStart && dragCur) {
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

function onCanvasDown(e: MouseEvent) {
  const p = canvasPos(e)
  // 命中已有框 → 选中（从后往前，后画的在上层）
  for (let i = labelBoxes.value.length - 1; i >= 0; i--) {
    const [x0, y0, x1, y1] = yoloToPx(labelBoxes.value[i])
    if (p.x >= x0 && p.x <= x1 && p.y >= y0 && p.y <= y1) {
      selectedBox.value = i
      drawCanvas()
      return
    }
  }
  selectedBox.value = -1
  dragStart = p
  dragCur = p
}

function onCanvasMove(e: MouseEvent) {
  if (!dragStart) return
  dragCur = canvasPos(e)
  drawCanvas()
}

function onCanvasUp() {
  if (!dragStart || !dragCur) { dragStart = null; dragCur = null; return }
  const cv = labelCanvasRef.value!
  const x0 = Math.min(dragStart.x, dragCur.x) / cv.width
  const x1 = Math.max(dragStart.x, dragCur.x) / cv.width
  const y0 = Math.min(dragStart.y, dragCur.y) / cv.height
  const y1 = Math.max(dragStart.y, dragCur.y) / cv.height
  dragStart = null
  dragCur = null
  // 过小的拖拽视为误触
  if (x1 - x0 < 0.01 || y1 - y0 < 0.01) { drawCanvas(); return }
  if (!labelClass.value) {
    ElMessage.warning('请先选择新框类别')
    drawCanvas()
    return
  }
  labelBoxes.value.push({
    class_name: labelClass.value,
    x: +((x0 + x1) / 2).toFixed(4),
    y: +((y0 + y1) / 2).toFixed(4),
    w: +(x1 - x0).toFixed(4),
    h: +(y1 - y0).toFixed(4),
  })
  selectedBox.value = labelBoxes.value.length - 1
  drawCanvas()
}

function onLabelKey(e: KeyboardEvent) {
  if (!labelVisible.value) return
  if ((e.key === 'Delete' || e.key === 'Backspace') && selectedBox.value >= 0) {
    labelBoxes.value.splice(selectedBox.value, 1)
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
.label-side { width: 240px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; }
.label-ai-row { display: flex; gap: 6px; margin-bottom: 4px; }
.label-box-list {
  flex: 1; max-height: 300px; overflow-y: auto;
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 4px; padding: 6px;
}
.label-box-item {
  display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 4px; cursor: pointer;
}
.label-box-item:hover { background: var(--el-fill-color-light, #f5f7fa); }
.label-box-item.active { background: var(--el-color-danger-light-9, #fef0f0); }
.label-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>