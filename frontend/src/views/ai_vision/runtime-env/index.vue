<template>
  <div class="runtime-env-page">
    <!-- ═══ 顶部：运行环境总览 ═══ -->
    <div class="page-header">
      <div>
        <h2>运行环境</h2>
        <p class="text-muted">AI 推理引擎（L1）与训练引擎（L2）的依赖管理、健康检测与环境排查助手</p>
      </div>
      <div class="header-actions">
        <el-button :loading="testing" @click="handleTest">
          <el-icon><Monitor /></el-icon>&nbsp;环境自检
        </el-button>
        <el-button type="primary" @click="refreshAll">
          <el-icon><Refresh /></el-icon>&nbsp;刷新状态
        </el-button>
      </div>
    </div>

    <!-- 安装进行中提示条 -->
    <el-alert
      v-if="installing"
      type="warning"
      :closable="false"
      show-icon
      class="installing-bar"
    >
      <template #title>
        <span>正在{{ installTypeText }} {{ installLayerName }}层依赖…（后台任务，可实时查看进度）</span>
      </template>
    </el-alert>

    <!-- 自检结果展示 -->
    <el-card v-if="testResult" shadow="never" class="test-result-card">
      <template #header>
        <div class="card-header">
          <span>环境自检结果</span>
          <el-tag :type="testResult.ok ? 'success' : 'danger'" size="small">
            {{ testResult.ok ? '全部通过' : '存在异常' }}
          </el-tag>
        </div>
      </template>
      <div v-for="(layer, key) in testResult.layers" :key="key" class="test-layer">
        <div class="test-layer-head">
          <span class="test-layer-label">{{ layer.label }}</span>
          <el-tag :type="layer.ok ? 'success' : 'danger'" size="small">{{ layer.ok ? '通过' : '失败' }}</el-tag>
          <span class="test-layer-time">{{ layer.elapsed_ms }}ms</span>
        </div>
        <div class="test-pkg-grid">
          <div v-for="pkg in layer.packages" :key="pkg.name" class="test-pkg" :class="{ bad: pkg.importable === false }">
            <span class="pkg-name">{{ pkg.name }}</span>
            <el-icon v-if="pkg.importable === false" color="#f56c6c" :size="14"><WarningFilled /></el-icon>
            <el-icon v-else color="#67c23a" :size="14"><CircleCheck /></el-icon>
            <span class="pkg-ver">{{ pkg.version || '未安装' }}</span>
          </div>
        </div>
        <div v-if="layer.model_probe" class="test-model-probe" :class="{ bad: !layer.model_probe.ok }">
          {{ layer.model_probe.detail }}
        </div>
      </div>
      <el-button class="test-close" text @click="testResult = null">关闭</el-button>
    </el-card>

    <!-- ═══ L1/L2 依赖卡片 ═══ -->
    <el-row :gutter="16" class="layer-row">
      <el-col :span="12">
        <el-card shadow="never" class="layer-card">
          <template #header>
            <div class="card-header">
              <div class="layer-title">
                <span class="layer-badge">L1</span>
                <span>推理层（必装）</span>
              </div>
              <div class="layer-status">
                <el-icon v-if="l1.installed" color="#67c23a" :size="16"><CircleCheck /></el-icon>
                <el-icon v-else color="#e6a23c" :size="16"><WarningFilled /></el-icon>
                <span :class="l1.installed ? 'ok' : 'warn'">{{ l1.installed ? '已就绪' : '未安装' }}</span>
              </div>
            </div>
          </template>
          <p class="layer-desc">onnxruntime + opencv + numpy + pillow，推理引擎核心，约 {{ l1Size }}，必需</p>
          <el-table :data="l1.packages || []" size="small" class="pkg-table">
            <el-table-column label="依赖包" prop="name" />
            <el-table-column label="版本" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.version" size="small" type="success">{{ row.version }}</el-tag>
                <el-text v-else type="danger" size="small">缺失</el-text>
              </template>
            </el-table-column>
          </el-table>
          <div class="layer-actions">
            <el-button v-if="!l1.installed" type="primary" :loading="isInstalling('L1')" @click="handleInstall('L1')">
              <el-icon><Download /></el-icon>&nbsp;安装 L1 依赖
            </el-button>
            <el-button v-else type="danger" plain :loading="isInstalling('L1')" @click="handleUninstall('L1')">
              <el-icon><Delete /></el-icon>&nbsp;卸载 L1 依赖
            </el-button>
          </div>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card shadow="never" class="layer-card">
          <template #header>
            <div class="card-header">
              <div class="layer-title">
                <span class="layer-badge layer-badge-l2">L2</span>
                <span>训练层（可选）</span>
              </div>
              <div class="layer-status">
                <el-icon v-if="l2.installed" color="#67c23a" :size="18"><CircleCheck /></el-icon>
                <el-icon v-else color="#909399" :size="18"><InfoFilled /></el-icon>
                <span :class="l2.installed ? 'ok' : 'muted'">{{ l2.installed ? '已就绪' : '未安装' }}</span>
              </div>
            </div>
          </template>
          <p class="layer-desc">torch + torchvision + ultralytics，用于样本训练闭环，按需安装</p>
          <el-table :data="l2.packages || []" size="small" class="pkg-table">
            <el-table-column prop="name" label="依赖包" />
            <el-table-column label="版本" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.version" size="small" type="success">{{ row.version }}</el-tag>
                <el-text v-else type="info" size="small">未安装</el-text>
              </template>
            </el-table-column>
          </el-table>
          <div class="layer-actions">
            <el-button v-if="!l2.installed" type="primary" plain :loading="isInstalling('L2')" @click="handleInstall('L2')">
              <el-icon><Download /></el-icon>&nbsp;安装 L2 依赖
            </el-button>
            <el-button v-else type="danger" plain :loading="isInstalling('L2')" @click="handleUninstall('L2')">
              <el-icon><Delete /></el-icon>&nbsp;卸载 L2 依赖
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ═══ 模型文件管理 ═══ -->
    <el-card shadow="never" class="files-card">
      <template #header>
        <div class="card-header">
          <span>模型文件管理</span>
          <div class="card-header-actions">
            <el-button size="small" :loading="verifying" @click="handleVerify">
              <el-icon><Checked /></el-icon>&nbsp;完整性校验
            </el-button>
            <el-upload
              :show-file-list="false"
              :http-request="handleUpload"
              accept=".onnx"
            >
              <el-button size="small" type="primary">
                <el-icon><Upload /></el-icon>&nbsp;上传 ONNX
              </el-button>
            </el-upload>
          </div>
        </div>
      </template>

      <el-table :data="envFiles" size="small" stripe>
        <el-table-column label="类型" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="kindTagType(row.kind)">{{ row.kind === 'model' ? '模型' : row.kind === 'manifest' ? '清单' : '孤儿' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="名称" prop="name" min-width="140" show-overflow-tooltip />
        <el-table-column label="文件" prop="file" min-width="120" show-overflow-tooltip />
        <el-table-column label="大小" width="90">
          <template #default="{ row }">{{ row.size_fmt || '-' }}</template>
        </el-table-column>
        <el-table-column label="完整性" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.hash_ok" size="small" type="success">完好</el-tag>
            <el-tag v-else size="small" :type="row.kind === 'orphan' ? 'info' : 'danger'">未校验</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="被引用" width="100">
          <template #default="{ row }">
            <el-text v-if="row.referenced_by?.length" size="small">{{ row.referenced_by.length }} 个事件</el-text>
            <el-text v-else size="small" type="info">—</el-text>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <template v-if="row.kind === 'model'">
              <el-button size="small" text type="primary" @click="handleReload(row)">重载</el-button>
              <el-button size="small" text type="danger" :disabled="row.referenced_by?.length" @click="handleDelete(row)">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ═══ AI 环境排查助手 ═══ -->
    <el-card shadow="never" class="assist-card">
      <template #header>
        <div class="card-header">
          <div class="assist-title">
            <el-icon class="assist-icon"><MagicStick /></el-icon>
            <span>AI 环境排查助手</span>
          </div>
          <el-tag size="small" type="info">可查状态 · 可测环境 · 可装依赖 · 不可卸载</el-tag>
        </div>
      </template>

      <div ref="chatRef" class="chat-box">
        <div v-if="messages.length === 0" class="chat-welcome">
          <el-icon :size="26" class="assist-logo"><MagicStick /></el-icon>
          <p>遇到环境问题？直接问我，例如「帮我查一下 L1 装了什么」「自检一下推理环境」「安装 L2 训练依赖」</p>
        </div>
        <div v-for="(msg, i) in messages" :key="i" class="msg-row" :class="msg.role">
          <div class="msg-avatar" :class="msg.role">
            <el-icon v-if="msg.role === 'user'" :size="15" color="#fff"><User /></el-icon>
            <el-icon v-else :size="15" color="#fff"><MagicStick /></el-icon>
          </div>
          <div class="msg-body">
            <div class="msg-tool" v-if="msg.toolEvents?.length">
              <div v-for="(te, j) in msg.toolEvents" :key="j" class="tool-event">
                <el-icon :size="13" :color="te.type === 'tool_call' ? '#5a67f5' : '#67c23a'">
                  <Loading v-if="te.type === 'tool_call'" class="is-loading" />
                  <CircleCheck v-else />
                </el-icon>
                <span class="tool-name">{{ te.name }}</span>
                <span class="tool-args">{{ formatToolArgs(te.arguments) }}</span>
              </div>
            </div>
            <div v-if="msg.content" class="msg-content markdown-body" v-html="renderMarkdown(msg.content)"></div>
            <div v-if="msg.error" class="msg-error">{{ msg.error }}</div>
            <div v-if="msg.streaming && !msg.content" class="typing"><span></span><span></span><span></span></div>
          </div>
        </div>
      </div>

      <div class="chat-input">
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="2"
          :autosize="{ minRows: 2, maxRows: 4 }"
          placeholder="描述你的环境问题，Enter 发送，Shift+Enter 换行"
          resize="none"
          @keydown.enter.exact.prevent="handleSend"
        />
        <div class="chat-input-actions">
          <el-button :loading="streaming" type="primary" :disabled="!inputText.trim()" @click="handleSend">
            <el-icon><Promotion /></el-icon>&nbsp;发送
          </el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getRuntimeStatus,
  installLayer as apiInstallLayer,
  uninstallLayer as apiUninstallLayer,
  testRuntime as apiTestRuntime,
  getInstallLog,
  getEnvFiles,
  verifyEnvFiles,
  uploadEnvFile,
  deleteEnvFile,
  reloadEnvFile,
} from '@/api/ai_vision/runtime'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import Prism from 'prismjs'
import 'prismjs/components/prism-markup.min.js'
import 'prismjs/components/prism-json.min.js'
import 'prismjs/components/prism-bash.min.js'
import 'prismjs/components/prism-python.min.js'
import {
  CircleCheck,
  WarningFilled,
  InfoFilled,
  Monitor,
  Refresh,
  Download,
  Delete,
  Checked,
  Upload,
  MagicStick,
  Loading,
  User,
  Promotion,
} from '@element-plus/icons-vue'

interface ToolEvent {
  type: 'tool_call' | 'tool_result'
  name: string
  arguments?: any
  result?: any
}

interface ChatMsg {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  error?: string
  toolEvents?: ToolEvent[]
}

// ── 状态 ──
const status = ref<any>({})
const l1 = computed(() => status.value.L1 || { packages: [], installed: false, label: '推理层（必装）' })
const l2 = computed(() => status.value.L2 || { packages: [], installed: false, label: '训练层（可选）' })
const installingLayer = ref<'L1' | 'L2' | null>(null)
const installLog = ref<any>({})

const l1Size = computed(() => {
  const pkgs = l1.value.packages || []
  let total = 0
  pkgs.forEach((p: any) => { if (p.size) total += p.size })
  return total > 0 ? fmtSize(total) : '75MB'
})

function fmtSize(n: number): string {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`
  return `${(n / 1024 / 1024).toFixed(0)}MB`
}

const isInstalling = (layer: string) => installingLayer.value === layer && !!installLog.value.running
const installing = computed(() => !!installLog.value.running)
const installTypeText = computed(() => (installLog.value.cmd?.includes('uninstall') ? '卸载' : '安装'))
const installLayerName = computed(() => (installLog.value.layer === 'L1' ? '推理' : installLog.value.layer === 'L2' ? '训练' : ''))

// ── 自检 ──
const testing = ref(false)
const testResult = ref<any>(null)

// ── 环境文件 ──
const envFiles = ref<any[]>([])
const verifying = ref(false)

// ── AI 助手 ──
const chatRef = ref<HTMLElement>()
const inputText = ref('')
const streaming = ref(false)
const messages = ref<ChatMsg[]>([])
let abortController: AbortController | null = null

// ── Markdown ──
marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(text: string): string {
  try {
    return DOMPurify.sanitize(marked.parse(text) as string, { ADD_ATTR: ['target'] })
  } catch {
    return DOMPurify.sanitize(text)
  }
}

function highlightCode() {
  nextTick(() => {
    if (!chatRef.value) return
    chatRef.value.querySelectorAll('pre code').forEach((el) => {
      if (!el.classList.contains('language-marked')) {
        el.classList.add('language-marked')
        try { Prism.highlightElement(el as HTMLElement) } catch { /* ignore */ }
      }
    })
  })
}

watch(() => messages.value.map((m) => m.content).join(''), highlightCode)

function formatToolArgs(args: any): string {
  if (!args || typeof args !== 'object') return ''
  const entries = Object.entries(args)
  if (entries.length === 0) return ''
  return entries.map(([k, v]) => `${k}=${v}`).join(', ')
}

function kindTagType(kind: string): string {
  if (kind === 'model') return 'success'
  if (kind === 'manifest') return 'info'
  return 'warning'
}

// ── 数据加载 ──
async function fetchStatus() {
  try {
    const data: any = await getRuntimeStatus()
    status.value = data
  } catch { /* interceptor handles */ }
}

async function fetchEnvFiles() {
  try {
    const data: any = await getEnvFiles()
    envFiles.value = data?.items || []
  } catch { envFiles.value = [] }
}

function refreshAll() {
  fetchStatus()
  fetchEnvFiles()
}

// ── 安装 / 卸载 ──
async function handleInstall(layer: 'L1' | 'L2') {
  try {
    const res: any = await apiInstallLayer(layer)
    ElMessage.success(res?.msg || `${layer} 依赖安装已启动`)
    installingLayer.value = layer
    startLogPoll()
  } catch (e: any) {
    ElMessage.error(e?.msg || `安装启动失败：${e?.message || e}`)
  }
}

async function handleUninstall(layer: 'L1' | 'L2') {
  try {
    await ElMessageBox.confirm(
      `卸载 ${layer} 依赖将移除该层全部依赖包，运行中的推理任务会被自动停止。确定卸载？`,
      '卸载确认',
      { confirmButtonText: '卸载', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }
  try {
    const res: any = await apiUninstallLayer(layer)
    ElMessage.success(res?.msg || `${layer} 依赖卸载已启动`)
    installingLayer.value = layer
    startLogPoll()
  } catch (e: any) {
    ElMessage.error(e?.msg || `卸载失败：${e?.message || e}`)
  }
}

// ── 日志轮询 ──
let logTimer: number | null = null
function startLogPoll() {
  stopLogPoll()
  logTimer = window.setInterval(async () => {
    try {
      const data: any = await getInstallLog()
      installLog.value = data
      if (!data.running) {
        stopLogPoll()
        installingLayer.value = null
        ElMessage.success(`依赖${data.returncode === 0 ? '操作完成' : '操作失败'}`)
        await fetchStatus()
      }
    } catch { /* ignore */ }
  }, 1500)
}

function stopLogPoll() {
  if (logTimer) { window.clearInterval(logTimer); logTimer = null }
}

// ── 环境自检 ──
async function handleTest() {
  testing.value = true
  try {
    const data: any = await apiTestRuntime()
    testResult.value = data
  } catch (e: any) {
    ElMessage.error(e?.msg || '自检失败')
  } finally {
    testing.value = false
  }
}

// ── 环境文件操作 ──
async function handleVerify() {
  verifying.value = true
  try {
    const data: any = await verifyEnvFiles()
    const bad = data?.items?.filter((i: any) => i.status !== 'ok') || []
    if (bad.length) {
      ElMessage.warning(`发现 ${bad.length} 个文件异常：${bad.map((b: any) => b.name).join('、')}`)
    } else {
      ElMessage.success('全部模型文件校验通过')
    }
    await fetchEnvFiles()
  } catch (e: any) {
    ElMessage.error(e?.msg || '校验失败')
  } finally {
    verifying.value = false
  }
}

async function handleUpload(options: any) {
  try {
    await uploadEnvFile(options.file)
    ElMessage.success('模型文件上传成功')
    await fetchEnvFiles()
  } catch (e: any) {
    ElMessage.error(e?.msg || '上传失败')
  }
}

async function handleReload(row: any) {
  try {
    await reloadEnvFile(row.model_id)
    ElMessage.success('引擎缓存已重置，模型将在下次推理时按新文件加载')
  } catch (e: any) {
    ElMessage.error(e?.msg || '重载失败')
  }
}

async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(`删除模型「${row.name}」及其文件？删除后不可恢复。`, '删除确认', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  try {
    await deleteEnvFile(row.model_id)
    ElMessage.success('模型文件已删除')
    await fetchEnvFiles()
  } catch (e: any) {
    ElMessage.error(e?.msg || '删除失败')
  }
}

// ── AI 助手：流式对话 ──
function scrollToBottom(force = false) {
  nextTick(() => {
    if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
  })
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || streaming.value) return
  messages.value.push({ role: 'user', content: text })
  inputText.value = ''

  const assistantMsg: ChatMsg = { role: 'assistant', content: '', streaming: true, toolEvents: [] }
  messages.value.push(assistantMsg)
  streaming.value = true
  scrollToBottom(true)

  const controller = new AbortController()
  abortController = controller
  const token = localStorage.getItem('apeadmin_token')

  try {
    const resp = await fetch('/api/v1/ai-vision/runtime/ai/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: token ? `Bearer ${token}` : '',
      },
      body: JSON.stringify({ message: text }),
      signal: controller.signal,
    })
    if (!resp.ok || !resp.body) {
      const errBody = await resp.json().catch(() => ({}))
      throw new Error(errBody?.msg || `HTTP ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const chunk = line.slice(6).trim()
        if (!chunk) continue
        let evt: any
        try { evt = JSON.parse(chunk) } catch { continue }
        if (evt.type === 'content') {
          assistantMsg.content += evt.content || ''
          scrollToBottom()
        } else if (evt.type === 'tool_call') {
          assistantMsg.toolEvents!.push({ type: 'tool_call', name: evt.name, arguments: evt.arguments })
          scrollToBottom()
        } else if (evt.type === 'tool_result') {
          assistantMsg.toolEvents!.push({ type: 'tool_result', name: evt.name, result: evt.result })
          scrollToBottom()
        } else if (evt.type === 'done') {
          assistantMsg.streaming = false
          streaming.value = false
          scrollToBottom()
        } else if (evt.type === 'error') {
          assistantMsg.streaming = false
          assistantMsg.error = evt.message || '对话异常'
          streaming.value = false
        }
      }
    }
  } catch (err: any) {
    if (err?.name !== 'AbortError') {
      assistantMsg.streaming = false
      assistantMsg.error = err?.message || '对话请求失败'
    } else {
      assistantMsg.streaming = false
      if (!assistantMsg.content) assistantMsg.content = '（已停止）'
    }
    streaming.value = false
  } finally {
    abortController = null
  }
}

// ── 生命周期 ──
onMounted(() => {
  refreshAll()
})

onBeforeUnmount(() => {
  stopLogPoll()
  if (abortController) abortController.abort()
})
</script>

<style scoped>
.runtime-env-page { padding: 20px; }

/* ── 顶部 ── */
.page-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 16px; gap: 12px;
}
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #909399; font-size: 13px; margin: 0; }
.header-actions { display: flex; gap: 8px; flex-shrink: 0; }

.installing-bar { margin-bottom: 16px; }

/* ── 自检结果 ── */
.test-result-card { margin-bottom: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.card-header-actions { display: flex; align-items: center; gap: 8px; }
.test-layer { margin-bottom: 12px; padding: 10px; background: #f9fafb; border-radius: 8px; }
.test-layer-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.test-layer-label { font-weight: 600; font-size: 13px; }
.test-layer-time { color: #909399; font-size: 12px; }
.test-pkg-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 6px; }
.test-pkg { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.test-pkg.bad { color: #f56c6c; }
.test-pkg .pkg-name { min-width: 90px; }
.test-pkg .pkg-ver { color: #909399; }
.test-model-probe { margin-top: 8px; font-size: 12px; color: #67c23a; }
.test-model-probe.bad { color: #f56c6c; }
.test-close { margin-top: 8px; }

/* ── L1/L2 卡片 ── */
.layer-row { margin-bottom: 16px; }
.layer-card { height: 100%; }
.layer-title { display: flex; align-items: center; gap: 8px; }
.layer-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border-radius: 6px;
  background: #5a67f5; color: #fff; font-size: 13px; font-weight: 600;
}
.layer-badge-l2 { background: #7c8a9a; }
.layer-status { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.layer-status .ok { color: #67c23a; }
.layer-status .warn { color: #e6a23c; }
.layer-status .muted { color: #909399; }
.layer-desc { color: #909399; font-size: 13px; margin: 0 0 12px; }
.pkg-table { margin-bottom: 12px; }
.layer-actions { display: flex; gap: 8px; }

/* ── 模型文件 ── */
.files-card { margin-bottom: 16px; }

/* ── AI 助手 ── */
.assist-card { margin-bottom: 8px; }
.assist-title { display: flex; align-items: center; gap: 8px; }
.assist-icon { color: #5a67f5; }

.chat-box {
  height: 320px; overflow-y: auto; padding: 12px;
  background: #f9fafb; border-radius: 8px; margin-bottom: 12px;
}
.chat-box::-webkit-scrollbar { width: 6px; }
.chat-box::-webkit-scrollbar-thumb { background: #d0d3d8; border-radius: 3px; }

.chat-welcome {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  height: 100%; text-align: center; color: #909399; font-size: 13px; gap: 12px;
}
.assist-logo { color: #5a67f5; }

.msg-row { display: flex; gap: 8px; margin-bottom: 14px; }
.msg-row.user { flex-direction: row-reverse; }
.msg-avatar {
  width: 30px; height: 30px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.msg-avatar.user { background: #5a67f5; }
.msg-avatar.assistant { background: linear-gradient(135deg, #5a67f5, #47d8ff); }
.msg-body { flex: 1; min-width: 0; max-width: 78%; }
.msg-row.user .msg-body { text-align: right; }

.msg-tool { margin-bottom: 4px; }
.tool-event { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #606266; padding: 3px 8px; background: #eef0ff; border-radius: 6px; margin-bottom: 2px; }
.tool-name { font-weight: 600; color: #5a67f5; }
.tool-args { color: #909399; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.msg-content {
  display: inline-block; padding: 9px 13px; border-radius: 10px;
  font-size: 13.5px; line-height: 1.6; text-align: left; word-break: break-word; max-width: 100%;
}
.msg-row.user .msg-content { background: #5a67f5; color: #fff; border-top-right-radius: 3px; }
.msg-row.assistant .msg-content { background: #fff; color: #303133; border: 1px solid #ebeef5; border-top-left-radius: 3px; }

.msg-error { display: inline-block; padding: 8px 12px; background: #fef0f0; border-radius: 8px; color: #f56c6c; font-size: 13px; }

.typing { display: inline-flex; gap: 4px; padding: 12px 16px; background: #fff; border: 1px solid #ebeef5; border-radius: 10px; }
.typing span { width: 6px; height: 6px; border-radius: 50%; background: #c0c4cc; animation: typing 1.4s infinite ease-in-out; }
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }
@keyframes typing { 0%, 60%, 100% { transform: translateY(0); opacity: 0.4; } 30% { transform: translateY(-5px); opacity: 1; } }

.chat-input-actions { display: flex; justify-content: flex-end; margin-top: 8px; }

/* markdown 样式 */
.msg-content :deep(h1), .msg-content :deep(h2), .msg-content :deep(h3) { margin: 8px 0 4px; font-weight: 600; }
.msg-content :deep(p) { margin: 4px 0; }
.msg-content :deep(ul), .msg-content :deep(ol) { padding-left: 20px; margin: 4px 0; }
.msg-content :deep(code) { background: #f0f2f5; padding: 2px 6px; border-radius: 4px; font-size: 13px; font-family: 'Consolas', monospace; }
.msg-content :deep(pre) { background: #1e1e2e; color: #d4d4d4; padding: 12px 14px; border-radius: 8px; overflow-x: auto; margin: 8px 0; font-size: 13px; }
.msg-content :deep(pre code) { background: transparent; padding: 0; color: inherit; }
.msg-content :deep(table) { border-collapse: collapse; margin: 8px 0; width: 100%; }
.msg-content :deep(th), .msg-content :deep(td) { border: 1px solid #ebeef5; padding: 6px 12px; text-align: left; }
.msg-content :deep(th) { background: #f5f7fa; font-weight: 600; }

@media (max-width: 991px) {
  .layer-row .el-col { margin-bottom: 16px; }
}
</style>