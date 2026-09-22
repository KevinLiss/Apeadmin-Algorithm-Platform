<template>
  <div class="dashboard-page">
    <!-- 加载中 -->
    <div v-if="loading" class="page-loading" v-loading="true" element-loading-text="正在检测平台状态…" style="min-height: 300px" />

    <!-- ═══ 未就绪：分步引导 ═══ -->
    <template v-else-if="!ready">
      <div class="guide-hero">
        <img :src="guideImg" alt="AI 视觉平台" class="guide-img" />
        <div class="guide-text">
          <h2>欢迎使用 AI 视觉平台</h2>
          <p>三步开启智能识别：安装运行环境 → 创建识别任务 → 查看告警</p>
        </div>
      </div>

      <el-row :gutter="16" class="steps-row">
        <!-- 第 1 步：运行环境 -->
        <el-col :span="8">
          <el-card shadow="hover" class="step-card" :class="{ done: envInstalled }">
            <div class="step-head">
              <el-tag v-if="envInstalled" type="success" effect="dark" circle class="step-num">✓</el-tag>
              <el-tag v-else type="primary" effect="dark" circle class="step-num">1</el-tag>
              <span class="step-title">安装运行环境</span>
            </div>
            <p class="step-desc">AI 推理引擎（onnxruntime + opencv），约需几分钟，仅需安装一次</p>
            <el-button
              v-if="!envInstalled"
              type="primary"
              @click="go('/ai-vision/runtime-env')"
            >去安装</el-button>
            <el-text v-else type="success" size="small">已完成</el-text>
          </el-card>
        </el-col>

        <!-- 第 2 步：创建任务 -->
        <el-col :span="8">
          <el-card shadow="hover" class="step-card" :class="{ done: hasTask, locked: !envInstalled }">
            <div class="step-head">
              <el-tag v-if="hasTask" type="success" effect="dark" circle class="step-num">✓</el-tag>
              <el-tag v-else type="primary" effect="dark" circle class="step-num">2</el-tag>
              <span class="step-title">创建识别任务</span>
            </div>
            <p class="step-desc">把「摄像头 + 识别事件」绑定为任务，即可开始智能识别</p>
            <el-button
              v-if="!hasTask"
              type="primary"
              :disabled="!envInstalled"
              @click="go('/ai-vision/monitor?tab=tasks')"
            >{{ envInstalled ? '去创建' : '先完成第 1 步' }}</el-button>
            <el-text v-else type="success" size="small">已完成</el-text>
          </el-card>
        </el-col>

        <!-- 第 3 步：查看告警 -->
        <el-col :span="8">
          <el-card shadow="hover" class="step-card" :class="{ done: hasAlarm, locked: !hasTask }">
            <div class="step-head">
              <el-tag v-if="hasAlarm" type="success" effect="dark" circle class="step-num">✓</el-tag>
              <el-tag v-else type="primary" effect="dark" circle class="step-num">3</el-tag>
              <span class="step-title">接收识别告警</span>
            </div>
            <p class="step-desc">识别到目标（人员/车辆/明火…）即产生告警，附抓拍图可回溯</p>
            <el-button
              v-if="!hasAlarm"
              type="primary"
              plain
              :disabled="!hasTask"
              @click="go('/ai-vision/monitor?tab=live')"
            >{{ hasTask ? '去查看' : '先完成第 2 步' }}</el-button>
            <el-text v-else type="success" size="small">已收到告警</el-text>
          </el-card>
        </el-col>
      </el-row>

      <!-- 已有数据但未就绪时的提示条 -->
      <el-alert
        v-if="envInstalled && hasTask && !hasAlarm"
        title="任务运行中，暂无告警"
        description="识别到目标后告警会出现在告警中心；可先用「视频文件」源 + 演示视频快速验证整条链路。"
        type="info"
        show-icon
        :closable="false"
        class="tip-bar"
      />
    </template>

    <!-- ═══ 已就绪：原统计看板 ═══ -->
    <template v-else>
      <div class="page-header">
        <h2>统计看板</h2>
        <p class="text-muted">AI 视觉平台运行概览与告警统计分析，每 30 秒自动刷新</p>
      </div>

      <!-- 状态卡片 -->
      <el-row :gutter="16" class="stat-cards">
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-card">
              <div class="stat-label">今日告警</div>
              <div class="stat-value danger">{{ overview.today_alarms ?? 0 }}</div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-card">
              <div class="stat-label">待处理告警</div>
              <div class="stat-value warning">{{ overview.pending_alarms ?? 0 }}</div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-card">
              <div class="stat-label">运行任务</div>
              <div class="stat-value">{{ runtime.tasks?.running ?? 0 }}<span class="unit"> / {{ runtime.tasks?.total ?? 0 }}</span></div>
            </div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-card">
              <div class="stat-label">在线摄像头</div>
              <div class="stat-value">{{ runtime.cameras?.online ?? 0 }}<span class="unit"> / {{ runtime.cameras?.total ?? 0 }}</span></div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 系统资源与运行 worker -->
      <el-row :gutter="16" class="sys-row">
        <el-col :span="8">
          <el-card shadow="never">
            <template #header>系统资源</template>
            <div class="sys-item">
              <span>CPU 使用率</span>
              <el-progress :percentage="runtimeCpu" :stroke-width="8" />
            </div>
            <div class="sys-item">
              <span>内存使用率</span>
              <el-progress :percentage="runtimeMem" :stroke-width="8" :color="memColor" />
            </div>
            <div class="sys-item" v-if="runtime.system?.memory_used_mb != null">
              <span>内存占用</span>
              <el-text size="small">{{ runtime.system.memory_used_mb }} MB / {{ runtime.system.memory_total_mb }} MB</el-text>
            </div>
          </el-card>
        </el-col>
        <el-col :span="16">
          <el-card shadow="never">
            <template #header>运行中的推理 worker（{{ runtime.worker_count ?? 0 }} 路）</template>
            <el-table :data="runtime.workers || []" size="small" stripe>
              <el-table-column label="摄像头" width="100">
                <template #default="{ row }">#{{ row.camera_id }}</template>
              </el-table-column>
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.running ? 'success' : 'danger'">{{ stateText(row.state) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="实际帧率" width="100">
                <template #default="{ row }">{{ row.fps_actual?.toFixed(1) ?? '—' }} fps</template>
              </el-table-column>
              <el-table-column label="已处理帧" width="100">
                <template #default="{ row }">{{ row.frames_processed ?? 0 }}</template>
              </el-table-column>
              <el-table-column label="任务数" width="80">
                <template #default="{ row }">{{ row.tasks?.length ?? 0 }}</template>
              </el-table-column>
              <el-table-column label="最近告警">
                <template #default="{ row }">{{ row.last_alarm_at || '—' }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <!-- 图表区 -->
      <el-row :gutter="16" class="chart-row">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>近 7 日告警趋势</template>
            <div ref="trendRef" class="chart-box"></div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>告警类别分布</template>
            <div ref="distRef" class="chart-box"></div>
          </el-card>
        </el-col>
      </el-row>
      <el-row :gutter="16" class="chart-row">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>摄像头告警排行 TOP10</template>
            <div ref="rankRef" class="chart-box"></div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>事件运行状态</template>
            <div ref="statusRef" class="chart-box"></div>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import request from '@/api/request'
import guideImg from '@/assets/ai_vision/guide.jpg'

const router = useRouter()
const loading = ref(true)
const ready = ref(false)
const envInstalled = ref(false)
const hasTask = ref(false)
const hasAlarm = ref(false)

const runtime = ref<any>({})
const overview = ref<any>({})

const trendRef = ref<HTMLElement | null>(null)
const distRef = ref<HTMLElement | null>(null)
const rankRef = ref<HTMLElement | null>(null)
const statusRef = ref<HTMLElement | null>(null)

let charts: echarts.ECharts[] = []
let pollTimer: number | null = null

const runtimeCpu = computed(() => Math.min(100, Math.round(runtime.value.system?.cpu_percent ?? 0)))
const runtimeMem = computed(() => Math.min(100, Math.round(runtime.value.system?.memory_percent ?? 0)))
const memColor = computed(() => (runtimeMem.value > 85 ? '#f56c6c' : runtimeMem.value > 60 ? '#e6a23c' : '#409eff'))

const statusTextMap: Record<string, string> = {
  draft: '草稿', ready: '已发布', running: '运行中', paused: '已暂停', pending_train: '待训练',
}

function stateText(s: string) {
  return { online: '在线', offline: '离线', unknown: '检测中' }[s] || s || '检测中'
}

function go(path: string) {
  router.push(path)
}

/** 首次进入：检测三步状态，决定显示引导还是看板 */
async function detect() {
  loading.value = true
  try {
    const [rt, ov]: any[] = await Promise.all([
      request.get('/ai-vision/dashboard/runtime'),
      request.get('/ai-vision/dashboard/overview'),
    ])
    runtime.value = rt
    overview.value = ov
    // L1 推理层已装 = 环境就绪（返回 {"L1": {...}, "L2": {...}}）
    const l1 = (rt.layers || {})['L1']
    envInstalled.value = !!l1?.installed
    hasTask.value = (rt.tasks?.total ?? 0) > 0
    hasAlarm.value = (ov.total_alarms ?? 0) > 0
    ready.value = envInstalled.value && hasTask.value
  } catch {
    // 接口异常时降级显示看板
    ready.value = true
  } finally {
    loading.value = false
  }
  if (ready.value) {
    await nextTick()
    renderCharts()
  }
}

async function fetchRuntime() {
  try {
    runtime.value = (await request.get('/ai-vision/dashboard/runtime')) as any
  } catch {
    // handled by interceptor
  }
}

async function fetchOverview() {
  try {
    overview.value = (await request.get('/ai-vision/dashboard/overview')) as any
    renderCharts()
  } catch {
    // handled by interceptor
  }
}

function renderCharts() {
  const trendEl = trendRef.value
  const distEl = distRef.value
  const rankEl = rankRef.value
  const statusEl = statusRef.value
  if (!trendEl || !distEl || !rankEl || !statusEl) return

  // 清空旧实例
  charts.forEach((c) => c.dispose())
  charts = []

  // 近 7 日告警趋势
  const trendKeys = Object.keys(overview.value.trend || {})
  const trendVals = trendKeys.map((k) => overview.value.trend[k])
  const c1 = echarts.init(trendEl)
  c1.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 16, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: trendKeys, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'line',
      smooth: true,
      data: trendVals,
      areaStyle: { opacity: 0.15 },
      itemStyle: { color: '#409eff' },
    }],
  })
  charts.push(c1)

  // 告警类别分布
  const distData = (overview.value.event_distribution || []).map((d: any) => ({
    name: d.category || 'unknown',
    value: d.count,
  }))
  const c2 = echarts.init(distEl)
  c2.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{
      type: 'pie',
      radius: ['35%', '62%'],
      center: ['50%', '45%'],
      data: distData,
      label: { fontSize: 11 },
    }],
  })
  charts.push(c2)

  // 摄像头告警排行
  const rankItems = overview.value.camera_ranking || []
  const c3 = echarts.init(rankEl)
  c3.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 90, right: 24, top: 16, bottom: 30 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: rankItems.map((r: any) => r.camera_name || `#${r.camera_id}`),
      axisLabel: { fontSize: 11 },
    },
    series: [{
      type: 'bar',
      data: rankItems.map((r: any) => r.count),
      itemStyle: { color: '#67c23a', borderRadius: [0, 3, 3, 0] },
    }],
  })
  charts.push(c3)

  // 事件运行状态
  const statusData = (overview.value.event_status || []).map((d: any) => ({
    name: statusTextMap[d.status] || d.status,
    value: d.count,
  }))
  const c4 = echarts.init(statusEl)
  c4.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['30%', '58%'],
      center: ['50%', '45%'],
      data: statusData,
      label: { fontSize: 11 },
    }],
  })
  charts.push(c4)
}

function tick() {
  fetchRuntime()
  fetchOverview()
}

function startPoll() {
  pollTimer = window.setInterval(tick, 30000)
}

onMounted(async () => {
  await detect()
  if (ready.value) startPoll()
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  charts.forEach((c) => c.dispose())
  charts = []
})
</script>

<style scoped>
.dashboard-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }

/* ── 引导页 ── */
.guide-hero {
  display: flex; align-items: center; gap: 28px;
  background: linear-gradient(135deg, #fff7ed 0%, #fef3c7 100%);
  border-radius: 16px; padding: 28px 36px; margin-bottom: 20px;
}
.guide-img { width: 180px; height: 180px; object-fit: contain; border-radius: 16px; flex-shrink: 0; }
.guide-text h2 { margin: 0 0 10px; font-size: 24px; color: #303133; }
.guide-text p { margin: 0; color: #6b7280; font-size: 14px; }
.steps-row { margin-bottom: 16px; }
.step-card { height: 100%; text-align: center; padding-top: 8px; }
.step-card.locked { opacity: 0.65; }
.step-head { display: flex; align-items: center; justify-content: center; gap: 8px; margin-bottom: 12px; }
.step-num { width: 28px; height: 28px; font-size: 16px; display: inline-flex; align-items: center; justify-content: center; }
.step-title { font-size: 16px; font-weight: 600; color: #303133; }
.step-desc { color: #909399; font-size: 13px; margin: 0 0 16px; min-height: 38px; }
.tip-bar { margin-top: 4px; }

/* ── 看板 ── */
.stat-cards { margin-bottom: 16px; }
.stat-card { text-align: center; padding: 6px 0; }
.stat-label { color: #909399; font-size: 13px; margin-bottom: 8px; }
.stat-value { font-size: 30px; font-weight: 600; color: #303133; }
.stat-value.warning { color: #e6a23c; }
.stat-value .unit { font-size: 14px; font-weight: 400; color: #909399; }
.sys-row { margin-bottom: 16px; }
.sys-item { margin-bottom: 12px; }
.sys-item:last-child { margin-bottom: 0; }
.sys-item span { display: block; margin-bottom: 4px; font-size: 13px; color: #606266; }
.chart-row { margin-bottom: 16px; }
.chart-box { height: 300px; }
</style>