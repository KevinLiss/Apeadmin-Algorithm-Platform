<!--
  AI 视觉平台 - 实时监控台页面（一期页面归并产物）
  整合原「任务管理-启动 + 告警中心-处置 + 预览流」三处操作：
  - Tab1 实时监控：选择视频源/事件一键启停任务，MJPEG 预览流（带检测框/骨架叠加），
    指标卡（帧率/告警数/运行时长）
  - Tab2 实时告警：alarm_bus SSE 推送的新告警内联确认/误报处置
  依赖：api/ai_vision/monitor.ts（start/stop/stats）、后端 /ai-vision/monitor/* 接口
-->
<template>
  <div class="monitor-page">
    <div class="page-header">
      <h2>实时监控台</h2>
      <p class="text-muted">选择视频源与识别事件，一键开始监控；告警实时推送并支持内联处置</p>
    </div>

    <el-tabs v-model="activeTab">
      <!-- ═══ Tab 1：实时监控 ═══ -->
      <el-tab-pane label="实时监控" name="live">
        <!-- 指标卡 -->
        <el-row :gutter="16" class="stat-cards">
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-label">今日告警</div>
                <div class="stat-value danger">{{ status.today_alarms ?? 0 }}</div>
              </div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-label">待处理</div>
                <div class="stat-value warning">{{ status.pending_alarms ?? 0 }}</div>
              </div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-label">运行中视频源</div>
                <div class="stat-value">{{ status.active_workers ?? 0 }}<span class="unit"> 路</span></div>
              </div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-label">当前源实际帧率</div>
                <div class="stat-value">{{ currentFps != null ? currentFps.toFixed(1) : '—' }}<span class="unit"> fps</span></div>
              </div>
            </el-card>
          </el-col>
        </el-row>

        <!-- 控制栏：源 × 事件 × 启停 -->
        <el-card shadow="never" class="control-bar">
          <div class="control-row">
            <div class="control-item">
              <span class="control-label">视频源</span>
              <el-select
                v-model="cameraId"
                placeholder="选择摄像头 / 视频文件"
                filterable
                style="width: 220px"
                :disabled="monitoring || acting"
                @change="handleCameraChange"
              >
                <el-option
                  v-for="c in cameras"
                  :key="c.id"
                  :label="`${c.name}（${c.source_type === 'video' ? '视频文件' : 'RTSP'}）`"
                  :value="c.id"
                />
              </el-select>
            </div>
            <div class="control-item">
              <span class="control-label">识别事件</span>
              <el-select
                v-model="eventIds"
                multiple
                collapse-tags
                collapse-tags-tooltip
                placeholder="选择事件（可多选）"
                style="width: 280px"
                :disabled="monitoring || acting"
              >
                <el-option
                  v-for="e in events"
                  :key="e.id"
                  :label="eventLabel(e)"
                  :value="e.id"
                  :disabled="e.status !== 'ready' && e.status !== 'running'"
                >
                  <el-tooltip :content="eventDisabledReason(e)" :disabled="e.status === 'ready' || e.status === 'running'" placement="right">
                    <span>{{ eventLabel(e) }}</span>
                  </el-tooltip>
                </el-option>
              </el-select>
            </div>
            <div class="control-item">
              <el-button v-if="!monitoring" type="primary" :loading="acting" :disabled="!cameraId || eventIds.length === 0" @click="startMonitor">
                <el-icon><VideoPlay /></el-icon> 开始监控
              </el-button>
              <el-button v-else type="danger" plain :loading="acting" @click="stopMonitor">
                <el-icon><VideoPause /></el-icon> 停止监控
              </el-button>
              <el-tag v-if="monitoring" type="success" effect="dark" class="live-tag">
                <span class="live-dot"></span> LIVE
              </el-tag>
            </div>
          </div>
        </el-card>

        <!-- 主区：左视频窗口 / 右信息 + 告警时间线 -->
        <el-row :gutter="16" class="main-row">
          <el-col :span="16">
            <el-card shadow="never" class="video-card">
              <template #header>
                <div class="video-header">
                  <span>{{ selectedCamera?.name || '实时画面' }}</span>
                  <el-tag v-if="selectedCamera" size="small" :type="selectedCamera.source_type === 'video' ? 'info' : 'warning'">
                    {{ selectedCamera.source_type === 'video' ? '视频文件' : 'RTSP 实时流' }}
                  </el-tag>
                  <el-button
                    v-if="isVideoSource && videoPlayUrl && monitoring"
                    link
                    size="small"
                    class="view-toggle"
                    @click="previewVideo = !previewVideo"
                  >
                    {{ previewVideo ? '返回监控画面' : '原视频回看' }}
                  </el-button>
                </div>
              </template>

              <div class="video-box" v-if="cameraId">
                <!-- 视频文件源预览模式：HTML5 播放器（StaticFiles 原生 Range，可拖进度条回看） -->
                <template v-if="showPreview">
                  <video
                    ref="videoRef"
                    :src="videoPlayUrl"
                    class="video-el"
                    controls
                    loop
                    muted
                    autoplay
                  ></video>
                  <div class="video-watermark">{{ clockText }}</div>
                </template>
                <!-- 监控模式（含视频文件源）：MJPEG 推流，检测框由后端直接画在分析帧上 -->
                <template v-else-if="mjpegSrc">
                  <img :src="mjpegSrc" class="video-el" alt="实时画面" @error="streamError = true" />
                  <div v-if="streamError" class="stream-tip">
                    实时流不可用（运行环境未安装或后端未启动）；开始监控后自动恢复
                  </div>
                </template>
                <!-- video 源但文件不在 media 目录下 -->
                <template v-else>
                  <el-empty description="该视频文件不在平台媒体目录内，无法在线播放；开始监控后仍可正常分析与告警" :image-size="90" />
                </template>
              </div>
              <div class="video-box" v-else>
                <el-empty description="请先选择视频源" :image-size="90" />
              </div>
            </el-card>
          </el-col>

          <el-col :span="8">
            <!-- 源信息卡 -->
            <el-card shadow="never" class="info-card">
              <template #header>源信息</template>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="名称">{{ selectedCamera?.name || '—' }}</el-descriptions-item>
                <el-descriptions-item label="类型">{{ selectedCamera ? (selectedCamera.source_type === 'video' ? '视频文件' : 'RTSP 摄像头') : '—' }}</el-descriptions-item>
                <el-descriptions-item label="位置">{{ selectedCamera?.location || '—' }}</el-descriptions-item>
                <el-descriptions-item label="监控状态">
                  <el-tag size="small" :type="monitoring ? 'success' : 'info'">{{ monitoring ? '监控中' : '未启动' }}</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="布防事件">
                  <template v-if="monitorEvents.length">
                    <el-tag v-for="e in monitorEvents" :key="e.id" size="small" class="event-tag">{{ e.name }}</el-tag>
                  </template>
                  <span v-else class="muted">—</span>
                </el-descriptions-item>
              </el-descriptions>
            </el-card>

            <!-- 实时告警时间线 -->
            <el-card shadow="never" class="timeline-card">
              <template #header>
                <div class="timeline-header">
                  <span>实时告警</span>
                  <el-badge v-if="pendingInTimeline" :value="pendingInTimeline" :max="99" type="danger" />
                  <el-button link size="small" @click="clearTimeline" style="margin-left: auto">清空</el-button>
                </div>
              </template>
              <div class="timeline-box" ref="timelineRef">
                <el-empty v-if="timeline.length === 0" description="暂无告警，监控运行后自动推送" :image-size="60" />
                <template v-for="group in groupedTimeline" :key="group.date">
                  <!-- 日期分组头 -->
                  <div class="tl-group">
                    <span class="tl-group-dot"></span>
                    <span class="tl-group-date">{{ group.date }}</span>
                  </div>
                  <!-- 该日告警节点 -->
                  <div class="tl-items">
                    <div v-for="a in group.items" :key="a.id" class="tl-item" :class="{ fresh: a._fresh }">
                      <span class="tl-node" :class="a.status === 'pending' ? 'node-danger' : 'node-info'"></span>
                      <div class="tl-content">
                        <div class="tl-time" :class="{ 'time-highlight': a._fresh }">{{ formatTime(a.created_at) }}</div>
                        <div class="tl-body">
                          <div class="alarm-title">
                            <el-image
                              v-if="a.snapshot_url"
                              :src="a.snapshot_url"
                              :preview-src-list="[a.snapshot_url]"
                              preview-teleported
                              fit="cover"
                              class="alarm-thumb"
                              @click="seekAlarm(a)"
                            />
                            <el-tag size="small" :type="a.level === 'critical' ? 'danger' : 'warning'" effect="dark">{{ a.event_name || a.category_code }}</el-tag>
                            <span class="alarm-conf">{{ (a.confidence * 100).toFixed(0) }}%</span>
                            <el-tag size="small" :type="alarmStatusType(a.status)">{{ alarmStatusText(a.status) }}</el-tag>
                          </div>
                          <div class="alarm-meta">
                            {{ a.camera_name }}
                            <el-link v-if="a.source_type === 'video' && a.video_ts > 0" type="primary" :underline="false" class="seek-link" @click="seekAlarm(a)">
                              回看 {{ formatTs(a.video_ts) }}
                            </el-link>
                          </div>
                          <div class="alarm-actions" v-if="a.status === 'pending' || a.status === 'acknowledged'">
                            <el-button v-if="a.status === 'pending'" link type="primary" size="small" @click="handleAck(a)" v-permission="'ai_vision:alarm:edit'">确认</el-button>
                            <el-button link type="warning" size="small" @click="handleFalsePositive(a)" v-permission="'ai_vision:alarm:edit'">误报</el-button>
                            <el-button v-if="a.status !== 'pending'" link type="success" size="small" @click="handleToSample(a)" v-permission="'ai_vision:alarm:edit'">转样本</el-button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ═══ Tab 2：任务管理（嵌入原任务编排） ═══ -->
      <el-tab-pane label="任务管理" name="tasks" lazy>
        <TasksPage :embedded="true" />
      </el-tab-pane>

      <!-- ═══ Tab 3：告警记录（嵌入原告警中心） ═══ -->
      <el-tab-pane label="告警记录" name="records" lazy>
        <AlarmsPage :embedded="true" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VideoPlay, VideoPause } from '@element-plus/icons-vue'
import request from '@/api/request'
import {
  alarmsSince,
  monitorStatus,
  resolveSource,
  streamUrl,
  startTask,
  stopTask,
  createTask,
  listTasks,
  listCameras,
  listEvents,
  ackAlarm,
  falsePositiveAlarm,
  alarmToSample,
} from '@/api/ai_vision/monitor'
import AlarmsPage from '@/views/ai_vision/alarms/index.vue'
import TasksPage from '@/views/ai_vision/tasks/index.vue'

const route = useRoute()

// ── 基础数据 ─────────────────────────────────────────
const activeTab = ref('live')
const cameras = ref<any[]>([])
const events = ref<any[]>([])
const cameraId = ref<number | null>(null)
const eventIds = ref<number[]>([])
const monitoring = ref(false)
const acting = ref(false)
const status = ref<any>({})
const videoPlayUrl = ref('')
const streamError = ref(false)
const videoRef = ref<HTMLVideoElement | null>(null)
const timelineRef = ref<HTMLElement | null>(null)

const timeline = ref<any[]>([])
let sinceId = 0
let alarmTimer: number | null = null
let statusTimer: number | null = null
let clockTimer: number | null = null
const clockText = ref('')

// 视频文件源：监控中默认显示带检测框的 MJPEG 监控画面，可切换回原视频播放器回看
const previewVideo = ref(false)
const isVideoSource = computed(() => selectedCamera.value?.source_type === 'video')
const showPreview = computed(() => isVideoSource.value && !!videoPlayUrl.value && (!monitoring.value || previewVideo.value))

const selectedCamera = computed(() => cameras.value.find((c) => c.id === cameraId.value) || null)
const monitorEvents = computed(() => events.value.filter((e) => eventIds.value.includes(e.id)))
const currentFps = computed(() => {
  const workers = status.value.workers || []
  const w = workers.find((x: any) => x.camera_id === cameraId.value)
  return w?.fps_actual ?? null
})
const mjpegSrc = computed(() => {
  // 监控画面统一走 MJPEG：检测框由后端画在分析帧上，画面与框严格对齐
  // （video 源未开始监控时走原视频播放器，不请求此流）
  if (!cameraId.value) return ''
  if (showPreview.value) return ''
  return streamUrl(cameraId.value, 5)
})
// 角标 = 当前时间线列表内未处理条数（清空/处置后即时归零；全局待处理数见顶部指标卡）
const pendingInTimeline = computed(() => timeline.value.filter((a) => a.status === 'pending').length)

// 按日期分组（时间线展示：日期节点 + 当日告警节点，最新在前）
function dayLabel(iso?: string) {
  if (!iso) return '未知日期'
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  if (isNaN(d.getTime())) return '未知日期'
  const now = new Date()
  const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  const yesterday = new Date(now.getTime() - 86400000)
  if (sameDay(d, now)) return '今天'
  if (sameDay(d, yesterday)) return '昨天'
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

const groupedTimeline = computed(() => {
  const groups: { date: string; items: any[] }[] = []
  for (const a of timeline.value) {
    const label = dayLabel(a.created_at)
    const last = groups[groups.length - 1]
    if (last && last.date === label) last.items.push(a)
    else groups.push({ date: label, items: [a] })
  }
  return groups
})

// ── 文案工具 ─────────────────────────────────────────
function eventLabel(e: any) {
  const tags: Record<string, string> = { draft: '（未发布）', paused: '（已退役）', pending_train: '（待训练）', running: '', ready: '' }
  return e.name + (tags[e.status] || '')
}

function eventDisabledReason(e: any) {
  if (e.status === 'draft') return '草稿状态：请先在「规则配置」页发布'
  if (e.status === 'paused') return '已退役：请编辑后重新发布'
  if (e.status === 'pending_train') return '待训练：模型尚不可用'
  if (!e.model_ids?.length) return '未绑定模型：请编辑事件并选择检测模型'
  return ''
}

function alarmStatusType(s: string) {
  return ({ pending: 'danger', acknowledged: 'success', false_positive: 'info' } as any)[s] || 'info'
}

function alarmStatusText(s: string) {
  return ({ pending: '待处理', acknowledged: '已确认', false_positive: '误报' } as any)[s] || s
}

function formatTime(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z') // 后端存 UTC
  if (isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

function formatTs(sec: number) {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// ── 数据加载 ─────────────────────────────────────────
async function fetchBase() {
  try {
    const [camRes, evtRes]: any = await Promise.all([
      listCameras({ page: 1, page_size: 100 }),
      listEvents({ page: 1, page_size: 100 }),
    ])
    cameras.value = camRes.items || []
    events.value = evtRes.items || []
  } catch {
    // handled by interceptor
  }
}

async function handleCameraChange() {
  videoPlayUrl.value = ''
  streamError.value = false
  monitoring.value = false
  previewVideo.value = false
  eventIds.value = []
  timeline.value = []
  sinceId = 0
  if (!cameraId.value) return

  // 解析视频文件播放地址（video 源）
  const cam = cameras.value.find((c) => c.id === cameraId.value)
  if (cam?.source_type === 'video') {
    try {
      const res: any = await resolveSource(cameraId.value)
      videoPlayUrl.value = res.play_url || ''
    } catch {
      videoPlayUrl.value = ''
    }
  }

  // 恢复现场：该源已有运行中任务 → 直接呈监控中状态
  try {
    const res: any = await listTasks({ camera_id: cameraId.value, status: 'running', page: 1, page_size: 100 })
    const running = res.items || []
    if (running.length) {
      monitoring.value = true
      eventIds.value = running.map((t: any) => t.event_id)
    }
  } catch {
    // ignore
  }
  await seedTimeline()
}

// ── 启停监控（隐式任务编排）─────────────────────────
async function startMonitor() {
  if (!cameraId.value || eventIds.value.length === 0) return
  acting.value = true
  try {
    const res: any = await listTasks({ camera_id: cameraId.value, page: 1, page_size: 100 })
    const tasks: any[] = res.items || []
    let started = 0
    for (const eid of eventIds.value) {
      let t = tasks.find((x) => x.event_id === eid)
      if (!t) {
        // 隐式创建任务（camera × event，默认 2fps，可去任务详情调整）
        const created: any = await createTask({ camera_id: cameraId.value, event_id: eid, analyze_fps: 2 })
        t = created
      }
      if (t && t.status !== 'running') {
        await startTask(t.id)
        started++
      }
    }
    monitoring.value = true
    previewVideo.value = false // 监控画面（带检测框叠加）优先
    ElMessage.success(started > 0 ? `监控已启动（${started} 路事件分析）` : '监控已在运行')
  } catch (e: any) {
    ElMessage.error(e?.message || '启动失败，请检查事件状态与运行环境')
  } finally {
    acting.value = false
  }
}

async function stopMonitor() {
  if (!cameraId.value) return
  acting.value = true
  try {
    const res: any = await listTasks({ camera_id: cameraId.value, status: 'running', page: 1, page_size: 100 })
    for (const t of res.items || []) {
      await stopTask(t.id)
    }
    monitoring.value = false
    ElMessage.success('监控已停止')
  } catch (e: any) {
    ElMessage.error(e?.message || '停止失败')
  } finally {
    acting.value = false
  }
}

// ── 告警时间线（轮询增量）───────────────────────────
async function seedTimeline() {
  // 以现有告警列表（倒序）初始化时间线与 since_id 水位
  try {
    const params: any = { page: 1, page_size: 20 }
    if (cameraId.value) params.camera_id = cameraId.value
    const res: any = await request.get('/ai-vision/alarms', { params })
    const items: any[] = res.items || []
    sinceId = items.reduce((m, a) => Math.max(m, a.id), 0)
    timeline.value = items
      .map((a) => ({
        ...a,
        snapshot_url: snapshotUrl(a.snapshot_path),
        camera_name: a.camera_name || cameras.value.find((c) => c.id === a.camera_id)?.name || `#${a.camera_id}`,
        event_name: a.event_name || events.value.find((e) => e.id === a.event_id)?.name || '',
        source_type: cameras.value.find((c) => c.id === a.camera_id)?.source_type || 'camera',
        _fresh: false,
      }))
    // 列表接口按 id 倒序返回，正好 = 时间线"最新在前"的展示顺序
  } catch {
    // ignore
  }
}

function snapshotUrl(path: string) {
  if (!path) return ''
  const idx = path.indexOf('ai_vision')
  if (idx >= 0) return '/api/v1/ai-vision/media/' + path.slice(idx + 'ai_vision/'.length).replace(/\\/g, '/')
  return ''
}

async function pollAlarms() {
  try {
    const params: any = { since_id: sinceId, limit: 50 }
    if (cameraId.value) params.camera_id = cameraId.value
    const res: any = await alarmsSince(params)
    const items: any[] = res.items || []
    if (res.next_since_id) sinceId = res.next_since_id
    if (items.length) {
      // 新告警插到时间线顶部（倒序展示），高亮 3 秒
      const fresh = items
        .slice()
        .reverse()
        .map((a) => ({ ...a, _fresh: true }))
      timeline.value = [...fresh, ...timeline.value].slice(0, 60)
      setTimeout(() => fresh.forEach((f) => (f._fresh = false)), 3000)
    }
  } catch {
    // ignore（避免轮询失败刷屏）
  }
}

async function pollStatus() {
  try {
    status.value = await monitorStatus()
  } catch {
    // ignore
  }
}

function clearTimeline() {
  timeline.value = []
}

// 视频源告警 → 点击回看：切到原视频播放器并把进度条跳到告警时刻
async function seekAlarm(a: any) {
  if (selectedCamera.value?.source_type !== 'video' || !a.video_ts) return
  previewVideo.value = true
  await nextTick()
  if (!videoRef.value) return
  videoRef.value.currentTime = a.video_ts
  videoRef.value.play()
  ElMessage.info(`已跳转到告警时刻 ${formatTs(a.video_ts)}`)
}

// ── 内联处置 ─────────────────────────────────────────
async function handleAck(a: any) {
  try {
    await ackAlarm(a.id)
    a.status = 'acknowledged'
    ElMessage.success('已确认')
    pollStatus()
  } catch {
    // handled
  }
}

async function handleFalsePositive(a: any) {
  try {
    const { value } = await ElMessageBox.prompt('可填写误报原因（可选）', '标记误报', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputPlaceholder: '如：灯光反光误触发',
    })
    await falsePositiveAlarm(a.id, value || '')
    a.status = 'false_positive'
    ElMessage.success('已标记为误报')
    pollStatus()
  } catch {
    // cancel
  }
}

async function handleToSample(a: any) {
  try {
    await ElMessageBox.confirm('将该告警抓拍图转入样本库？', '提示', { type: 'info' })
    await alarmToSample(a.id)
    ElMessage.success('已转样本')
  } catch {
    // cancel
  }
}

// ── 生命周期 ─────────────────────────────────────────
function tickClock() {
  clockText.value = new Date().toLocaleString('zh-CN', { hour12: false })
}

onMounted(async () => {
  tickClock()
  clockTimer = window.setInterval(tickClock, 1000)
  await fetchBase()
  await pollStatus()

  // 支持其它页面带参跳入：?tab=live|tasks|records & camera_id & event_id
  const q = route.query as Record<string, string>
  if (q.tab && ['live', 'tasks', 'records'].includes(q.tab)) activeTab.value = q.tab
  if (q.camera_id) {
    cameraId.value = Number(q.camera_id)
    await handleCameraChange()
    if (q.event_id) {
      eventIds.value = [Number(q.event_id)]
      // 从"识别事件-新建任务"深链进入：选好后直接开始监控
      if (activeTab.value === 'live') await startMonitor()
    }
  } else if (q.event_id && activeTab.value === 'live') {
    // 只带 event_id（未选源）：预选事件，等用户选视频源后一键开始
    eventIds.value = [Number(q.event_id)]
  }

  alarmTimer = window.setInterval(pollAlarms, 3000)
  statusTimer = window.setInterval(pollStatus, 5000)
})

onBeforeUnmount(() => {
  if (alarmTimer) window.clearInterval(alarmTimer)
  if (statusTimer) window.clearInterval(statusTimer)
  if (clockTimer) window.clearInterval(clockTimer)
})
</script>

<style scoped>
.monitor-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }

/* 指标卡（与统计看板同风格） */
.stat-cards { margin-bottom: 16px; }
.stat-card { text-align: center; padding: 6px 0; }
.stat-label { color: #909399; font-size: 13px; margin-bottom: 8px; }
.stat-value { font-size: 28px; font-weight: 600; color: #303133; }
.stat-value.danger { color: #f56c6c; }
.stat-value.warning { color: #e6a23c; }
.stat-value .unit { font-size: 13px; font-weight: 400; color: #909399; }

/* 控制栏 */
.control-bar { margin-bottom: 16px; }
.control-row { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.control-item { display: flex; align-items: center; gap: 8px; }
.control-label { font-size: 13px; color: #606266; white-space: nowrap; }
.live-tag { margin-left: 10px; }
.live-dot {
  display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: #fff; margin-right: 5px; animation: blink 1.2s infinite;
}
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

/* 视频窗口 */
.main-row { margin-bottom: 16px; }
.video-header { display: flex; align-items: center; gap: 10px; }
.video-box {
  position: relative; width: 100%; aspect-ratio: 16 / 9;
  background: #1a1a1a; border-radius: 6px; overflow: hidden;
  display: flex; align-items: center; justify-content: center;
}
.video-el { width: 100%; height: 100%; object-fit: contain; display: block; }
.video-watermark {
  position: absolute; top: 8px; right: 12px; color: rgba(255, 255, 255, 0.85);
  font-size: 12px; font-family: monospace; text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
  pointer-events: none;
}
.stream-tip {
  position: absolute; bottom: 10px; left: 0; right: 0; text-align: center;
  color: #ffd04b; font-size: 12px; text-shadow: 0 1px 2px #000; pointer-events: none;
}
.video-box :deep(.el-empty) { --el-empty-padding: 12px; }
.video-box :deep(.el-empty__description p) { color: #aaa; }

/* 源信息卡 */
.info-card { margin-bottom: 16px; }
.event-tag { margin: 2px 4px 2px 0; }
.muted { color: #c0c4cc; }

/* 告警时间线（竖向节点式：日期分组 + 逐条节点） */
.timeline-header { display: flex; align-items: center; gap: 10px; }
.view-toggle { margin-left: auto; }
.timeline-box { max-height: 480px; overflow-y: auto; padding: 4px 6px 4px 2px; }

/* 日期分组头 */
.tl-group { display: flex; align-items: center; gap: 8px; padding: 8px 0 6px 4px; }
.tl-group-dot {
  width: 10px; height: 10px; border-radius: 50%; background: #fff;
  border: 2px solid #e6a23c; flex-shrink: 0;
}
.tl-group-date { font-size: 13px; font-weight: 600; color: #303133; }

/* 节点列（左侧竖线 + 圆点） */
.tl-items { position: relative; margin-left: 8px; padding-left: 20px; }
.tl-items::before {
  content: ''; position: absolute; left: 0; top: 2px; bottom: 2px;
  width: 2px; background: #e4e7ed; border-radius: 1px;
}
.tl-item { position: relative; padding: 6px 0 10px; transition: background 0.4s; }
.tl-node {
  position: absolute; left: -24px; top: 12px; width: 10px; height: 10px;
  border-radius: 50%; border: 2px solid #fff; box-shadow: 0 0 0 1px #dcdfe6;
  background: #909399; z-index: 1;
}
.tl-node.node-danger { background: #f56c6c; box-shadow: 0 0 0 1px #f56c6c; }
.tl-node.node-info { background: #67c23a; box-shadow: 0 0 0 1px #67c23a; }
.tl-item.fresh .tl-node { animation: pulse 1.2s infinite; }
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 1px #f56c6c; }
  50% { box-shadow: 0 0 0 4px rgba(245, 108, 108, 0.35); }
}
.tl-content { display: flex; flex-direction: column; gap: 4px; }
.tl-time {
  font-size: 13px; font-weight: 600; color: #606266; font-family: monospace;
  display: inline-block; padding: 0 6px; line-height: 20px; border-radius: 3px;
  width: fit-content;
}
.tl-time.time-highlight { color: #e6a23c; background: #fdf6ec; border: 1px solid #f3d19e; }
.tl-body { display: flex; flex-direction: column; gap: 4px; }
.alarm-thumb {
  width: 84px; height: 56px; border-radius: 4px; flex-shrink: 0; cursor: pointer;
}
.alarm-title { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.alarm-conf { font-size: 12px; color: #909399; }
.alarm-meta { font-size: 12px; color: #909399; }
.seek-link { font-size: 12px; margin-left: 6px; }
.alarm-actions { display: flex; gap: 2px; }
</style>
