<!-- ROI 可视化画框器：在视频源当前帧上点选多边形/线段，回填归一化坐标。

用法：<RoiDrawer v-model:visible="show" v-model="roiArr" :mode="'polygon'|'line'" />
- polygon：点击落顶点（≥3），闭合区域；拖动顶点微调；右键撤销上一点
- line：固定两端点
背景帧来自 GET /monitor/snapshot/{cameraId}（worker 帧优先，否则开源抓一帧）
-->
<template>
  <el-dialog
    :model-value="visible"
    :title="mode === 'line' ? '在画面上圈定警戒线' : '在画面上圈定区域'"
    width="860px"
    top="5vh"
    append-to-body
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @open="onOpen"
  >
    <div class="rd-bar">
      <el-select v-model="cameraId" placeholder="选择视频源取画面" filterable style="width: 220px" @change="fetchFrame">
        <el-option v-for="c in cameras" :key="c.id" :label="`${c.name}（${c.source_type === 'video' ? '视频' : 'RTSP'}）`" :value="c.id" />
      </el-select>
      <el-button size="small" :loading="loading" :disabled="!cameraId" @click="fetchFrame">重新抓帧</el-button>
      <el-divider direction="vertical" />
      <el-button size="small" :disabled="!pts.length" @click="undoPoint">撤销上一点</el-button>
      <el-button size="small" :disabled="!pts.length" @click="pts = []">清空</el-button>
      <el-text size="small" type="info" style="margin-left: 8px">
        {{ mode === 'line' ? '点击两个端点画线' : '点击落顶点（至少 3 个），拖动顶点可调整' }}
      </el-text>
    </div>
    <div v-loading="loading" class="rd-canvas-wrap">
      <img v-if="frameSrc" ref="imgEl" :src="frameSrc" class="rd-img" draggable="false" @load="fitCanvas">
      <canvas
        v-if="frameSrc"
        ref="cvEl"
        class="rd-canvas"
        @pointerdown="onDown"
        @pointermove="onMove"
        @pointerup="onUp"
        @contextmenu.prevent="undoPoint"
      />
      <el-empty v-else :image-size="60" description="选择视频源抓取画面后开始圈定" />
    </div>
    <el-text size="small" type="info">提示：坐标按画面比例保存（0~1），换分辨率不影响；RTSP 源首次抓帧约需 1~3 秒。</el-text>
    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button type="primary" :disabled="!valid" @click="confirm">完成圈定</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import request from '@/api/request'
import { ElMessage } from 'element-plus'

const props = defineProps<{
  visible: boolean
  modelValue: number[][]
  mode?: 'polygon' | 'line'
  /** 摄像头列表由父组件统一拉取一次传入，避免多个实例重复请求 */
  cameras?: any[]
}>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'update:modelValue', v: number[][]): void
}>()

const mode = computed(() => props.mode || 'polygon')
const cameras = computed(() => props.cameras || [])
const cameraId = ref<number | null>(null)
const frameSrc = ref('')
const loading = ref(false)
const cvEl = ref<HTMLCanvasElement | null>(null)
const imgEl = ref<HTMLImageElement | null>(null)

/** 归一化点集（编辑中） */
const pts = ref<number[][]>([])
const dragging = ref(-1)
const view = { w: 0, h: 0 }

const valid = computed(() => (mode.value === 'line' ? pts.value.length === 2 : pts.value.length >= 3))

function onOpen() {
  // 回填已有坐标开始编辑
  pts.value = (props.modelValue || []).map((p) => [p[0], p[1]])
  // 摄像头列表由父组件传入；若尚未选中且有可用源，默认选第一个
  if (cameraId.value == null && cameras.value.length) {
    cameraId.value = cameras.value[0].id
    fetchFrame()
  }
}

async function fetchFrame() {
  if (!cameraId.value) return
  loading.value = true
  try {
    const res: any = await request.get(`/ai-vision/monitor/snapshot/${cameraId.value}`)
    frameSrc.value = res.image
  } catch (e: any) {
    frameSrc.value = ''
    ElMessage.error(e?.message || '抓帧失败，可稍后重试或手填坐标')
  } finally {
    loading.value = false
  }
}

function fitCanvas() {
  const cv = cvEl.value
  const img = imgEl.value
  if (!cv || !img) return
  // 用 img 实际显示尺寸（受 max-width 约束）精确对齐画布
  const w = img.clientWidth || img.naturalWidth
  const h = img.clientHeight || img.naturalHeight
  view.w = w
  view.h = h
  cv.width = w
  cv.height = h
  cv.style.width = w + 'px'
  cv.style.height = h + 'px'
  draw()
}

function toNorm(e: PointerEvent): number[] {
  const r = (e.target as HTMLCanvasElement).getBoundingClientRect()
  return [
    Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
    Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
  ]
}

function hitPoint(p: number[]): number {
  const tol = 12 / (view.w || 1)
  for (let i = pts.value.length - 1; i >= 0; i--) {
    const q = pts.value[i]
    if (Math.abs(q[0] - p[0]) < tol && Math.abs(q[1] - p[1]) < tol) return i
  }
  return -1
}

function onDown(e: PointerEvent) {
  const p = toNorm(e)
  const hit = hitPoint(p)
  if (hit >= 0) {
    dragging.value = hit
    ;(e.target as HTMLCanvasElement).setPointerCapture(e.pointerId)
    return
  }
  if (mode.value === 'line') {
    if (pts.value.length >= 2) pts.value = []
    pts.value.push(p)
  } else {
    pts.value.push(p)
  }
  draw()
}

function onMove(e: PointerEvent) {
  if (dragging.value < 0) return
  pts.value[dragging.value] = toNorm(e)
  draw()
}

function onUp() {
  dragging.value = -1
}

function undoPoint() {
  pts.value.pop()
  draw()
}

function draw() {
  const cv = cvEl.value
  if (!cv) return
  const ctx = cv.getContext('2d')!
  ctx.clearRect(0, 0, cv.width, cv.height)
  if (!pts.value.length) return
  const px = pts.value.map((p) => [p[0] * cv.width, p[1] * cv.height])
  ctx.strokeStyle = '#22c55e'
  ctx.fillStyle = 'rgba(34, 197, 94, 0.18)'
  ctx.lineWidth = 2
  if (mode.value === 'polygon' && px.length >= 3) {
    ctx.beginPath()
    px.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
    ctx.closePath()
    ctx.fill()
    ctx.stroke()
  } else {
    ctx.beginPath()
    px.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
    ctx.stroke()
  }
  ctx.fillStyle = '#fff'
  px.forEach((p) => {
    ctx.beginPath()
    ctx.arc(p[0], p[1], 5, 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()
  })
}

function confirm() {
  emit('update:modelValue', pts.value.map((p) => [+p[0].toFixed(4), +p[1].toFixed(4)]))
  emit('update:visible', false)
}
</script>

<style scoped>
.rd-bar { display: flex; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 4px; }
.rd-canvas-wrap { position: relative; min-height: 240px; background: #111; border-radius: 6px; display: flex; justify-content: center; }
.rd-img { display: block; max-width: 100%; }
.rd-canvas { position: absolute; left: 50%; transform: translateX(-50%); top: 0; cursor: crosshair; touch-action: none; }
</style>
