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
      <p class="text-muted">左侧任务轨：拨开关启停检测、点卡片切换画面；告警实时推送并支持内联处置</p>
    </div>

    <el-tabs v-model="activeTab">
      <!-- ═══ Tab 1：实时监控 ═══ -->
      <el-tab-pane label="实时监控" name="live">
        <!-- 指标卡 -->
        <el-row :gutter="16" class="stat-cards">
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-icon si-danger"><el-icon :size="20"><Bell /></el-icon></div>
                <div class="stat-text">
                  <div class="stat-label">今日告警</div>
                  <div class="stat-value danger">{{ status.today_alarms ?? 0 }}</div>
                  <div class="stat-trend" v-if="status.yesterday_alarms != null">
                    <template v-if="(status.today_alarms ?? 0) > (status.yesterday_alarms ?? 0)">
                      <span class="trend-up">↑ 较昨日 +{{ (status.today_alarms ?? 0) - (status.yesterday_alarms ?? 0) }}</span>
                    </template>
                    <template v-else-if="(status.today_alarms ?? 0) < (status.yesterday_alarms ?? 0)">
                      <span class="trend-down">↓ 较昨日 -{{ (status.yesterday_alarms ?? 0) - (status.today_alarms ?? 0) }}</span>
                    </template>
                    <template v-else><span class="trend-flat">与昨日持平</span></template>
                  </div>
                </div>
              </div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-icon si-warning"><el-icon :size="20"><Clock /></el-icon></div>
                <div class="stat-text">
                  <div class="stat-label">待处理</div>
                  <div class="stat-value warning">{{ status.pending_alarms ?? 0 }}</div>
                  <div class="stat-trend"><span class="trend-flat">{{ (status.pending_alarms ?? 0) > 0 ? '需人工处置' : '已清空' }}</span></div>
                </div>
              </div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-icon si-primary"><el-icon :size="20"><VideoCamera /></el-icon></div>
                <div class="stat-text">
                  <div class="stat-label">运行中视频源</div>
                  <div class="stat-value">{{ status.active_workers ?? 0 }}<span class="unit"> 路</span></div>
                  <div class="stat-trend"><span class="trend-flat">上限 4 路（CPU）</span></div>
                </div>
              </div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="never">
              <div class="stat-card">
                <div class="stat-icon si-success"><el-icon :size="20"><Odometer /></el-icon></div>
                <div class="stat-text">
                  <div class="stat-label">当前源实际帧率</div>
                  <div class="stat-value">{{ currentFps != null ? currentFps.toFixed(1) : '—' }}<span class="unit"> fps</span></div>
                  <div class="stat-trend"><span class="trend-flat" :class="{ 'trend-down': currentFps != null && currentFps < 1 }">{{ currentFps != null && currentFps < 1 ? '偏低，建议降 fps 或缩 ROI' : '正常' }}</span></div>
                </div>
              </div>
            </el-card>
          </el-col>
        </el-row>

        <!-- 主区：左监控轨(与画面等高) / 中视频窗口 / 右信息 + 告警时间线 -->
        <div class="main-flex">
          <!-- ── 左侧监控轨：限高与视频卡等高，内部滚动不撑页面 ── -->
          <div class="task-rail" ref="taskRailRef">
            <div class="rail-header">
              <span class="rail-title">监控任务 <el-text size="small" type="info">({{ taskCards.length }})</el-text></span>
              <div class="rail-actions">
                <el-button link type="success" size="small" :disabled="railActing" @click="railBatch('start')" v-permission="'ai_vision:task:control'">全启</el-button>
                <el-button link type="warning" size="small" :disabled="railActing" @click="railBatch('stop')" v-permission="'ai_vision:task:control'">全停</el-button>
                <el-button link size="small" :loading="railLoading" @click="fetchTaskCards()">刷新</el-button>
              </div>
            </div>
            <div class="rail-list" v-loading="railLoading">
              <el-empty v-if="!taskCards.length && !railLoading" description="暂无任务，去「任务管理」新建" :image-size="50" />
              <div
                v-for="t in taskCards"
                :key="t.id"
                class="task-card"
                :class="{ active: isCardActive(t), stopped: t.status !== 'running' }"
                @click="selectCard(t)"
              >
                <div class="tc-row1">
                  <span class="tc-dot" :class="t.status === 'running' ? 'dot-run' : t.status === 'degraded' ? 'dot-warn' : 'dot-idle'"></span>
                  <span class="tc-name" :title="`${t.camera_name} · ${t.event_name}`">{{ t.camera_name }}</span>
                  <el-switch
                    :model-value="t.status === 'running'"
                    size="small"
                    :loading="t._toggling"
                    @click.stop
                    @change="(v: boolean) => toggleCard(t, v)"
                  />
                </div>
                <div class="tc-row2">
                  <span class="tc-event">{{ t.event_name }}</span>
                </div>
                <div class="tc-row3">
                  <template v-if="t.status === 'running'">
                    <span>{{ t.last_stats?.fps_actual != null ? Number(t.last_stats.fps_actual).toFixed(1) : '—' }} fps</span>
                    <span class="tc-sep">·</span>
                    <span>今日 <b :class="{ 'tc-alarm-hot': (t.today_alarms ?? 0) > 0 }">{{ t.today_alarms ?? 0 }}</b> 告警</span>
                  </template>
                  <template v-else-if="t.last_alarm_at">
                    <span>累计 {{ t.total_alarms ?? 0 }} 告警</span>
                    <span class="tc-sep">·</span>
                    <span>最后 {{ shortTime(t.last_alarm_at) }}</span>
                  </template>
                  <template v-else>
                    <span>已停止 · 暂无告警记录</span>
                  </template>
                </div>
              </div>
            </div>
          </div>

          <!-- ── 中+右：原监控布局不动 ── -->
          <el-row :gutter="16" class="main-row" style="flex: 1; min-width: 0">
          <el-col :span="16">
            <el-card shadow="never" class="video-card" ref="videoCardRef">
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
                <!-- 监控模式：MJPEG 推流（检测框由后端画在分析帧上）。
                     img 常驻 DOM 用 v-show 切换——若随 v-if 卸载，Chrome 会挂起
                     而不关闭进行中的 multipart 流，切源即泄漏一条连接，6 条占满
                     同源连接池后新视频/接口全部排队卡死（2026-09-23 修复）。
                     src 反应式切换为空白 data: 即强制中止旧流。 -->
                <img
                  v-show="!showPreview && !!mjpegSrc"
                  ref="streamImgRef"
                  :src="mjpegSrc || BLANK_SRC"
                  class="video-el"
                  alt="实时画面"
                  @error="onStreamImgError"
                />
                <div v-if="!showPreview && !mjpegSrc" class="stream-idle">
                  {{ isVideoSource
                    ? '未开始监控；点左侧任务卡片的开关启动检测，或选择视频源+事件后自动开始（该视频文件不在媒体目录时无法原片回看，不影响分析告警）'
                    : '未开始监控，点左侧任务卡片的开关启动检测' }}
                </div>
                <div v-if="streamError && !showPreview && !!mjpegSrc" class="stream-tip">
                  实时流不可用（运行环境未安装或后端未启动）；开始监控后自动恢复
                </div>
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
                            <!-- 缩略图：仅大图预览；回看视频用下方"回看"链接（避免点击冲突） -->
                            <el-image
                              v-if="a.snapshot_url"
                              :src="a.snapshot_url"
                              :preview-src-list="[a.snapshot_url]"
                              preview-teleported
                              fit="cover"
                              class="alarm-thumb"
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
        </div><!-- /main-flex -->
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
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
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
// 时间水位（UTC naive ISO）：批量删除后新告警会复用更小的自增 id，
// 纯 id 增量会永久漏推，故轮询优先带 since_ts（见后端 alarms-since）
let sinceTs = ''
let alarmTimer: number | null = null
let statusTimer: number | null = null
let clockTimer: number | null = null
const clockText = ref('')
const streamImgRef = ref<HTMLImageElement | null>(null)
// 空白图源：把 <img>.src 切到它即可强制浏览器中止进行中的 MJPEG 流
const BLANK_SRC = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=='

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
  // 仅"监控中且非原片回看"时才建立 MJPEG 流连接——未监控时 RTSP 源只会推
  // NO SIGNAL 占位帧，却白占一条长连接（Chrome 同源 6 连接上限）。
  // URL 含 _t 时间戳，切源即新连接、旧连接由 src 变更触发浏览器 abort。
  if (!cameraId.value || !monitoring.value || showPreview.value) return ''
  return streamUrl(cameraId.value, 5)
})

function onStreamImgError() {
  streamError.value = true
}
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

/** 停止卡"最后告警"短格式：今天只显示时分，跨天显示月日 */
function shortTime(iso: string) {
  if (!iso) return '—'
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  if (isNaN(d.getTime())) return '—'
  const now = new Date()
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
  return `${d.getMonth() + 1}-${d.getDate()} ${d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })}`
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

// ── 启停监控（保留给深链自动开始：?camera_id&event_id 进入时调用）──
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
        // 隐式创建任务（camera × event）：帧率跟随事件规则（跳水事件 ≥5fps），
        // 其余默认 2fps，可去任务详情调整
        const evt = events.value.find((e) => e.id === eid)
        const fps = Math.max(1, Math.min(10, Number(evt?.rule?.fps) || 2))
        const created: any = await createTask({ camera_id: cameraId.value, event_id: eid, analyze_fps: fps })
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
    await fetchTaskCards()
  } catch (e: any) {
    ElMessage.error(e?.message || '启动失败，请检查事件状态与运行环境')
  } finally {
    acting.value = false
  }
}

// ── 告警时间线（轮询增量）───────────────────────────
// ═══ 左侧监控轨：任务卡片（开关=启停检测，点卡片=切主画面）═══
const taskCards = ref<any[]>([])
const railLoading = ref(false)
const railActing = ref(false)
const taskRailRef = ref<HTMLElement | null>(null)
const videoCardRef = ref<any>(null)
let railRO: ResizeObserver | null = null

/** 左栏高度实时跟随视频卡（画面 16:9 随列宽变，写死高度必不齐；
 *  卡片再高也不撑页面——列表内部滚动） */
function syncRailHeight() {
  const cardEl = videoCardRef.value?.$el as HTMLElement | undefined
  if (cardEl && taskRailRef.value) {
    taskRailRef.value.style.height = `${cardEl.offsetHeight}px`
  }
}

async function fetchTaskCards(silent = false) {
  // 有开关正在等启停结果时跳过静默刷新——整组替换会提前抹掉 loading 态（自查 P2-3）
  if (silent && taskCards.value.some((t) => t._toggling)) return
  if (!silent) railLoading.value = true
  try {
    const norm = (items: any[]) => items.map((t: any) => ({ ...t, _toggling: false }))
    // 运行中单独拉（后端按 id 倒序，任务超 100 时"混合分页"会把 id 小的老任务
    // 截断丢失——恰好是最稳定长跑的那批，自查 P2-2）
    const runRes: any = await listTasks({ page: 1, page_size: 100, status: 'running' })
    const running = norm(runRes.items || [])
    let stopped: any[]
    let needFull = false
    if (silent) {
      // 静默轮询只更新运行中卡片（1 个请求）；停止卡指标不随时间变化，沿用旧数据。
      // 用"全库任务总数"轻量对账（page_size=1 只取 total）：数量变了说明
      // 有任务被新建/删除（任务管理 tab 操作），停止卡沿用旧数据会留幽灵卡/
      // 漏新卡（二轮自检 P3-3）——变了才补一次全量，平时仍是恒定开销。
      const countRes: any = await listTasks({ page: 1, page_size: 1 })
      if ((countRes.total ?? 0) !== taskCards.value.length) needFull = true
      const runIds = new Set(running.map((t: any) => t.id))
      stopped = norm(
        taskCards.value
          .filter((t: any) => !runIds.has(t.id))
          .map((t: any) => (t.status === 'running' ? { ...t, status: 'stopped' } : t)),
      )
    }
    if (!silent || needFull) {
      // 全量分页收集（≤5 页=500 条封顶），剔除运行中的避免重复
      const first: any = await listTasks({ page: 1, page_size: 100 })
      let all: any[] = [...(first.items || [])]
      const pages = Math.min(5, Math.ceil((first.total || 0) / 100))
      for (let p = 2; p <= pages; p++) {
        const r: any = await listTasks({ page: p, page_size: 100 })
        all = all.concat(r.items || [])
      }
      stopped = norm(all.filter((t: any) => t.status !== 'running'))
    }
    running.sort((a: any, b: any) => b.id - a.id)
    stopped.sort((a: any, b: any) => b.id - a.id)
    taskCards.value = [...running, ...stopped]
  } catch {
    // handled
  } finally {
    if (!silent) railLoading.value = false
  }
}

function isCardActive(t: any) {
  return t.camera_id === cameraId.value
}

/** 点卡片主体 = 切换主画面到该路（不自动开检测——开关才控制启停） */
async function selectCard(t: any) {
  if (isCardActive(t) && eventIds.value.includes(t.event_id)) return
  cameraId.value = t.camera_id
  await handleCameraChange()
  // 该摄像头若无运行中任务，事件预选为被点卡片的事件（画面保持预览态）
  if (!monitoring.value) eventIds.value = [t.event_id]
}

/** 卡片开关 = 启停该任务检测 */
async function toggleCard(t: any, wantRunning: boolean) {
  t._toggling = true
  try {
    if (wantRunning) {
      await startTask(t.id)
      ElMessage.success(`「${t.camera_name}·${t.event_name}」检测已启动`)
    } else {
      await stopTask(t.id)
      ElMessage.info(`「${t.camera_name}·${t.event_name}」检测已停止`)
    }
    await fetchTaskCards()
    // 若操作的是当前画面这路：同步 monitoring 状态（决定是否出 MJPEG 流）
    if (t.camera_id === cameraId.value) {
      const mine = taskCards.value.filter((x) => x.camera_id === cameraId.value && x.status === 'running')
      monitoring.value = mine.length > 0
      if (monitoring.value) {
        eventIds.value = mine.map((x) => x.event_id)
        previewVideo.value = false
      }
    }
    pollStatus()
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
    await fetchTaskCards()
  } finally {
    t._toggling = false
  }
}

/** 全部启动 / 全部停止（串行，避免 worker 并发拉起抢 CPU） */
async function railBatch(op: 'start' | 'stop') {
  const targets = taskCards.value.filter((t) => (op === 'start' ? t.status !== 'running' : t.status === 'running'))
  if (!targets.length) {
    ElMessage.info(op === 'start' ? '没有可启动的任务' : '没有运行中的任务')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定${op === 'start' ? '启动' : '停止'}全部 ${targets.length} 个任务的检测吗？`,
      op === 'start' ? '全部启动' : '全部停止',
      { type: 'warning' },
    )
  } catch {
    return
  }
  railActing.value = true
  let ok = 0
  let fail = 0
  for (const t of targets) {
    try {
      if (op === 'start') await startTask(t.id)
      else await stopTask(t.id)
      ok++
    } catch {
      fail++
    }
  }
  railActing.value = false
  await fetchTaskCards()
  // 当前画面这路的运行状态跟随刷新
  const mineRunning = taskCards.value.some((x) => x.camera_id === cameraId.value && x.status === 'running')
  if (cameraId.value) monitoring.value = mineRunning
  pollStatus()
  const label = op === 'start' ? '启动' : '停止'
  if (fail === 0) ElMessage.success(`已批量${label} ${ok} 个任务`)
  else ElMessage.warning(`批量${label}：成功 ${ok}，失败 ${fail}`)
}

async function seedTimeline() {
  // 以现有告警列表（倒序）初始化时间线与 since_id 水位
  try {
    const params: any = { page: 1, page_size: 20 }
    if (cameraId.value) params.camera_id = cameraId.value
    const res: any = await request.get('/ai-vision/alarms', { params })
    const items: any[] = res.items || []
    sinceId = items.reduce((m, a) => Math.max(m, a.id), 0)
    sinceTs = items.reduce((m: string, a: any) => (a.created_at && a.created_at > m ? a.created_at : m), '')
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
    if (sinceTs) params.since_ts = sinceTs
    if (cameraId.value) params.camera_id = cameraId.value
    const res: any = await alarmsSince(params)
    const items: any[] = res.items || []
    if (res.next_since_id) sinceId = res.next_since_id
    if (res.next_since_ts) sinceTs = res.next_since_ts
    if (items.length) {
      // 时间水位按秒比较，同秒多条会重复命中 → 按 id 去重
      const seen = new Set(timeline.value.map((t) => t.id))
      const fresh = items
        .filter((a) => !seen.has(a.id))
        .slice()
        .reverse()
        .map((a) => ({ ...a, _fresh: true }))
      if (fresh.length) {
        timeline.value = [...fresh, ...timeline.value].slice(0, 60)
        setTimeout(() => fresh.forEach((f) => (f._fresh = false)), 3000)
      }
    }
  } catch {
    // ignore（避免轮询失败刷屏）
  }
}

async function pollStatus() {
  try {
    status.value = await monitorStatus()
    // 监控轨随状态轮询同步（fps/告警角标/运行态），免单独定时器
    fetchTaskCards(true)
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
  const neg = a.status === 'false_positive'
  try {
    await ElMessageBox.confirm(
      neg ? '该告警已标误报——将以「无标注负样本」转入（训练时作背景图压误报）。' : '将该告警抓拍图转入样本库？',
      neg ? '转负样本' : '转样本',
      { type: 'info' },
    )
  } catch {
    return // cancel
  }
  try {
    const res: any = await request.post(`/ai-vision/alarms/${a.id}/to-sample`, null, { params: { as_negative: neg ? 1 : 0 } })
    ElMessage.success(res?.msg || (neg ? '已转负样本' : '已转样本'))
  } catch {
    // handled
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
  fetchTaskCards()
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
  } else if (!q.camera_id && activeTab.value === 'live') {
    // 无参进入：自动定位到第一个运行中的任务（不再黑屏等选择）
    await fetchTaskCards()
    const first = taskCards.value.find((t) => t.status === 'running')
    if (first) await selectCard(first)
  }

  alarmTimer = window.setInterval(pollAlarms, 3000)
  statusTimer = window.setInterval(pollStatus, 5000)
  // 左栏与视频卡等高：监听视频卡尺寸（列宽变化/源切换都触发）
  ensureRailSync()
})

/** 挂左栏等高同步。live pane 是 v-if 渲染——深链进 tasks/records tab 时
 *  视频卡不存在，必须等切回 live 再挂一次（自查 P2-4） */
function ensureRailSync() {
  nextTick(() => {
    syncRailHeight()
    const cardEl = videoCardRef.value?.$el as HTMLElement | undefined
    if (!cardEl) return
    if (!railRO && 'ResizeObserver' in window) railRO = new ResizeObserver(syncRailHeight)
    railRO?.disconnect()
    railRO?.observe(cardEl)
  })
}

// 切回"实时监控"tab 时补挂同步
watch(activeTab, (v) => { if (v === 'live') ensureRailSync() })

onBeforeUnmount(() => {
  if (alarmTimer) window.clearInterval(alarmTimer)
  if (statusTimer) window.clearInterval(statusTimer)
  if (clockTimer) window.clearInterval(clockTimer)
  if (railRO) { railRO.disconnect(); railRO = null }
  // 离开页面前主动把流 img 指向空白源：仅销毁 DOM 的话 Chrome 会挂起
  // 而非关闭 multipart 流，僵尸连接会占满同源连接池（切源卡加载根因）
  if (streamImgRef.value) streamImgRef.value.src = BLANK_SRC
})
</script>

<style scoped>
.monitor-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }

/* 指标卡（与统计看板同风格） */
.stat-cards { margin-bottom: 16px; }
.stat-card { display: flex; align-items: center; gap: 14px; padding: 4px 0; text-align: left; }
.stat-icon {
  width: 44px; height: 44px; border-radius: 10px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.si-danger { background: var(--el-color-danger-light-9, #fef0f0); color: #f56c6c; }
.si-warning { background: var(--el-color-warning-light-9, #fdf6ec); color: #e6a23c; }
.si-primary { background: var(--el-color-primary-light-9, #ecf5ff); color: #409eff; }
.si-success { background: var(--el-color-success-light-9, #f0f9eb); color: #67c23a; }
.stat-text { min-width: 0; }
.stat-label { color: #909399; font-size: 13px; margin-bottom: 2px; }
.stat-value { font-size: 26px; font-weight: 600; color: #303133; line-height: 1.2; }
.stat-value.danger { color: #f56c6c; }
.stat-value.warning { color: #e6a23c; }
.stat-value .unit { font-size: 13px; font-weight: 400; color: #909399; }
.stat-trend { font-size: 12px; line-height: 1.4; margin-top: 2px; }
.trend-up { color: #f56c6c; }
.trend-down { color: #67c23a; }
.trend-flat { color: #a8abb2; }
.trend-flat.trend-down { color: #e6a23c; }

/* 控制栏（旧深链样式保留兼容） */
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

/* ── 左侧监控轨：JS 同步为与视频卡等高，内部滚动 ── */
.main-flex { display: flex; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
.task-rail {
  width: 216px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--el-bg-color, #fff);
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 8px;
  overflow: hidden;
}
.rail-header {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 10px; border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
  background: var(--el-fill-color-light, #f5f7fa);
}
.rail-title { font-size: 13px; font-weight: 600; color: #303133; }
.rail-actions { display: flex; align-items: center; gap: 2px; }
.rail-list { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
.task-card {
  border: 1px solid var(--el-border-color-lighter, #ebeef5); border-radius: 8px;
  padding: 8px 10px; cursor: pointer; background: var(--el-bg-color, #fff);
  transition: border-color 0.15s, background 0.15s;
}
.task-card:hover { border-color: var(--el-color-primary-light-5, #a0cfff); }
.task-card.active { border: 1.5px solid var(--el-color-primary, #409eff); background: var(--el-color-primary-light-9, #ecf5ff); }
.task-card.stopped { background: var(--el-fill-color-lighter, #fafafa); }
.tc-row1 { display: flex; align-items: center; gap: 6px; }
.tc-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot-run { background: #67c23a; animation: blink 1.6s infinite; }
.dot-warn { background: #e6a23c; }
.dot-idle { background: #c0c4cc; }
.tc-name {
  flex: 1; min-width: 0; font-size: 13px; font-weight: 600; color: #303133;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.task-card.stopped .tc-name { color: #909399; font-weight: 400; }
.tc-row2 { margin-top: 5px; }
.tc-event {
  display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 11px; color: #606266; background: var(--el-fill-color, #f0f2f5);
  border-radius: 3px; padding: 0 6px; line-height: 18px;
}
.task-card.active .tc-event { background: var(--el-color-primary-light-8, #d9ecff); color: #409eff; }
.tc-row3 { margin-top: 6px; font-size: 11px; color: #909399; display: flex; align-items: center; gap: 4px; }
.tc-sep { color: #dcdfe6; }
.tc-alarm-hot { color: #f56c6c; }

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
.stream-idle {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: #9aa0a6; font-size: 13px; text-align: center; padding: 0 24px; line-height: 1.7;
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
