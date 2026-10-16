<template>
  <div class="camera-page">
    <div class="page-header">
      <h2>视频源管理</h2>
      <p class="text-muted">统一接入与管理 RTSP / GB28181 国标 / ONVIF 摄像头，以及本地视频文件（测试/演示）</p>
    </div>

    <el-tabs v-model="activeTab" class="cam-tabs">
      <!-- 接入列表（原摄像头管理） -->
      <el-tab-pane label="接入列表" name="list">
        <div class="toolbar">
          <el-button type="primary" @click="openCreate" v-permission="'ai_vision:camera:create'">
            <el-icon><Plus /></el-icon> 新增视频源
          </el-button>
          <el-button @click="fetchList" :loading="listLoading">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>

        <el-table v-if="total > 0 || listLoading" :data="tableData" v-loading="listLoading" stripe style="width: 100%; margin-top: 16px" @sort-change="onSortChange">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="name" label="名称" min-width="120" sortable="custom" />
          <el-table-column label="类型" width="110">
            <template #default="{ row }">
              <el-tag :type="row.source_type === 'video' ? 'warning' : 'primary'" size="small">
                {{ row.source_type === 'video' ? '视频文件' : '摄像头' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="协议" width="110">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ protocolLabel(row.protocol) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="rtsp_url" label="地址/文件" min-width="200" show-overflow-tooltip />
          <el-table-column prop="location" label="位置" min-width="100" show-overflow-tooltip />
          <el-table-column prop="vendor" label="厂商" width="90" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="last_online_at" label="最近在线" width="170">
            <template #default="{ row }">{{ row.status === 'unknown' ? '—' : formatDateTime(row.last_online_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="300" fixed="right">
            <template #default="{ row }">
              <el-button link type="success" size="small" @click="handleTest(row)" v-permission="'ai_vision:camera:test'" :loading="testingId === row.id">测试</el-button>
              <el-button link type="primary" size="small" @click="createTask(row)" v-permission="'ai_vision:task:create'">创建任务</el-button>
              <el-button link type="primary" size="small" @click="openEdit(row)" v-permission="'ai_vision:camera:edit'">编辑</el-button>
              <el-button link type="danger" size="small" @click="handleDelete(row)" v-permission="'ai_vision:camera:delete'">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-empty v-if="!listLoading && total === 0" description="还没有视频源" :image-size="120">
          <p class="empty-tip">第一步：先接入一个摄像头（RTSP / GB28181 / ONVIF）或上传一段测试视频</p>
          <el-button type="primary" @click="openCreate" v-permission="'ai_vision:camera:create'">
            <el-icon><Plus /></el-icon> 新增视频源
          </el-button>
        </el-empty>

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
      </el-tab-pane>

      <!-- GB28181 国标 -->
      <el-tab-pane label="GB28181 国标" name="gb">
        <el-card shadow="never" class="src-card">
          <template #header>
            <div class="card-title">
              <el-icon><Connection /></el-icon><span>GB28181 服务器</span>
              <el-tag :type="gbStatus.running ? 'success' : 'info'" size="small" style="margin-left: auto">
                {{ gbStatus.running ? '运行中' : '未运行' }}
              </el-tag>
            </div>
          </template>
          <div v-if="gbStatus.running" class="gb-config">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="SIP ID">{{ gbStatus.config?.sip_id }}</el-descriptions-item>
              <el-descriptions-item label="SIP 端口">{{ gbStatus.config?.sip_port }}</el-descriptions-item>
              <el-descriptions-item label="已注册设备">{{ gbStatus.device_count }}</el-descriptions-item>
            </el-descriptions>
            <div class="row-actions">
              <el-button type="danger" plain @click="handleStopGb" :loading="gbBusy">停止服务</el-button>
            </div>
          </div>
          <el-empty v-else description="GB28181 服务未启动">
            <el-button type="primary" @click="handleStartGb" :loading="gbBusy">启动服务</el-button>
          </el-empty>
        </el-card>

        <el-card shadow="never" class="src-card">
          <template #header><div class="card-title"><el-icon><Monitor /></el-icon><span>已注册设备</span></div></template>
          <el-table :data="gbDevices" stripe size="small" empty-text="暂无设备注册（设备需指向本机 5060 主动注册）">
            <el-table-column prop="device_id" label="设备 ID" />
            <el-table-column prop="name" label="名称" />
            <el-table-column prop="ip" label="IP" />
            <el-table-column prop="port" label="端口" width="80" />
            <el-table-column prop="channel" label="通道" width="80" />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="row.status === 'online' ? 'success' : 'warning'" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ONVIF 设备 -->
      <el-tab-pane label="ONVIF 设备" name="onvif">
        <el-card shadow="never" class="src-card">
          <template #header>
            <div class="card-title">
              <el-icon><Search /></el-icon><span>设备发现</span>
              <el-button size="small" type="primary" style="margin-left:auto" @click="handleDiscover" :loading="discovering" v-permission="'ai_vision:video:discover'">开始发现</el-button>
            </div>
          </template>
          <el-table :data="onvifDevices" stripe size="small" empty-text="点击「开始发现」搜索局域网 ONVIF 设备">
            <el-table-column prop="ip" label="IP" />
            <el-table-column prop="port" label="端口" width="80" />
            <el-table-column prop="url" label="服务地址" />
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button size="small" link type="primary" @click="fillTest(row)">测试</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="src-card">
          <template #header><div class="card-title"><el-icon><Link /></el-icon><span>连通性测试</span></div></template>
          <el-form :inline="true" :model="onvifTestForm" class="inline-form">
            <el-form-item label="设备地址">
              <el-input v-model="onvifTestForm.onvif_url" placeholder="http://192.168.1.64" style="width: 220px" />
            </el-form-item>
            <el-form-item label="用户名"><el-input v-model="onvifTestForm.username" style="width: 120px" /></el-form-item>
            <el-form-item label="密码"><el-input v-model="onvifTestForm.password" type="password" show-password style="width: 120px" /></el-form-item>
            <el-form-item>
              <el-button type="primary" @click="handleOnvifTest" :loading="testing">测试</el-button>
            </el-form-item>
          </el-form>
          <el-alert v-if="onvifTestResult" :type="onvifTestResult.ok ? 'success' : 'error'" :closable="false" :title="onvifTestResult.message" style="margin-top: 8px">
            <div v-if="onvifTestResult.stream_url">推流地址：{{ onvifTestResult.stream_url }}</div>
          </el-alert>
        </el-card>

        <el-card shadow="never" class="src-card">
          <template #header><div class="card-title"><el-icon><VideoCamera /></el-icon><span>云台控制 (PTZ)</span></div></template>
          <el-form :inline="true" :model="ptzForm" class="inline-form">
            <el-form-item label="摄像头 ID">
              <el-input v-model="ptzForm.camera_id" type="number" placeholder="已建 ONVIF 源的 ID" style="width: 160px" />
            </el-form-item>
            <el-form-item>
              <el-button-group>
                <el-button @click="handlePtz('up')" v-permission="'ai_vision:video:ptz'"><el-icon><Top /></el-icon></el-button>
                <el-button @click="handlePtz('down')" v-permission="'ai_vision:video:ptz'"><el-icon><Bottom /></el-icon></el-button>
                <el-button @click="handlePtz('left')" v-permission="'ai_vision:video:ptz'"><el-icon><Back /></el-icon></el-button>
                <el-button @click="handlePtz('right')" v-permission="'ai_vision:video:ptz'"><el-icon><Right /></el-icon></el-button>
                <el-button @click="handlePtz('zoom_in')" v-permission="'ai_vision:video:ptz'">+</el-button>
                <el-button @click="handlePtz('zoom_out')" v-permission="'ai_vision:video:ptz'">-</el-button>
                <el-button @click="handlePtz('stop')" v-permission="'ai_vision:video:ptz'">停</el-button>
              </el-button-group>
            </el-form-item>
          </el-form>
        </el-card>
      </el-tab-pane>

      <!-- 活跃视频源 -->
      <el-tab-pane label="活跃视频源" name="sources">
        <div class="toolbar">
          <el-button type="primary" @click="showCreate = true" v-permission="'ai_vision:video:list'">
            <el-icon><Plus /></el-icon> 创建视频源
          </el-button>
          <el-button @click="refreshVideoSources" :loading="vsLoading">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
        <el-card shadow="never" class="src-card">
          <template #header><div class="card-title"><el-icon><VideoPlay /></el-icon><span>视频源列表（运行态，重启后清空）</span></div></template>
          <el-table :data="sources" stripe size="small" empty-text="暂无活跃视频源">
            <el-table-column prop="config.camera_id" label="摄像头 ID" width="110" />
            <el-table-column prop="config.name" label="名称" />
            <el-table-column label="协议" width="100">
              <template #default="{ row }"><el-tag size="small">{{ row.config.protocol }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="stream_url" label="流地址" show-overflow-tooltip />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" link type="danger" @click="handleClose(row.config.camera_id)">关闭</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑视频源' : '新增视频源'" width="600px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：东门岗亭 / 测试视频-行人" maxlength="200" />
        </el-form-item>
        <el-form-item label="接入类型" required>
          <el-radio-group v-model="form.source_type">
            <el-radio value="camera">网络摄像头</el-radio>
            <el-radio value="video">视频文件（测试/演示）</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- camera：协议选择 + 对应字段 -->
        <template v-if="form.source_type === 'camera'">
          <el-form-item label="协议" required>
            <el-select v-model="form.protocol" style="width: 100%">
              <el-option label="RTSP/RTP" value="rtsp" />
              <el-option label="GB28181 国标" value="gb28181" />
              <el-option label="ONVIF" value="onvif" />
            </el-select>
          </el-form-item>

          <el-form-item v-if="form.protocol === 'rtsp'" label="RTSP 地址" required>
            <el-input v-model="form.rtsp_url" placeholder="rtsp://user:pass@ip:554/stream1" maxlength="500" />
          </el-form-item>

          <template v-else-if="form.protocol === 'gb28181'">
            <el-form-item label="设备 ID" required><el-input v-model="form.gb_device_id" maxlength="50" placeholder="如 31010000001320000001" /></el-form-item>
            <el-form-item label="设备 IP"><el-input v-model="form.gb_ip" maxlength="50" /></el-form-item>
            <el-form-item label="端口"><el-input v-model="form.gb_port" maxlength="6" placeholder="5060" /></el-form-item>
            <el-form-item label="通道"><el-input v-model="form.gb_channel" maxlength="20" placeholder="如 1" /></el-form-item>
          </template>

          <template v-else-if="form.protocol === 'onvif'">
            <el-form-item label="服务地址" required><el-input v-model="form.onvif_url" maxlength="200" placeholder="http://192.168.1.64" /></el-form-item>
            <el-form-item label="用户名"><el-input v-model="form.onvif_username" maxlength="50" /></el-form-item>
            <el-form-item label="密码"><el-input v-model="form.onvif_password" type="password" show-password maxlength="50" /></el-form-item>
          </template>
        </template>

        <!-- video：上传 + 已上传列表选择 -->
        <template v-else>
          <el-form-item label="视频文件" required>
            <div class="video-upload-area">
              <el-upload
                :show-file-list="false"
                :http-request="handleUploadVideo"
                accept=".mp4,.avi,.mkv,.mov,.flv,.wmv,.webm"
              >
                <el-button :loading="uploading" type="primary" plain>
                  <el-icon><Upload /></el-icon> 上传视频
                </el-button>
              </el-upload>
              <el-select
                v-if="videoOptions.length"
                v-model="form.rtsp_url"
                placeholder="或从已上传视频中选择"
                style="width: 100%; margin-top: 8px"
                filterable
              >
                <el-option
                  v-for="v in videoOptions"
                  :key="v.file_path"
                  :label="`${v.file_name}（${v.size_mb}MB）`"
                  :value="v.file_path"
                />
              </el-select>
              <el-input
                v-if="form.rtsp_url"
                v-model="form.rtsp_url"
                disabled
                size="small"
                style="margin-top: 8px"
              />
            </div>
          </el-form-item>
          <el-form-item label="循环播放">
            <el-text size="small" type="info">视频播放结束后自动从头循环（便于持续测试）</el-text>
          </el-form-item>
        </template>

        <el-form-item label="位置">
          <el-input v-model="form.location" placeholder="如：园区东门" maxlength="200" />
        </el-form-item>
        <el-form-item label="厂商/型号">
          <el-input v-model="form.vendor" placeholder="如：海康威视（视频文件源可留空）" maxlength="100" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 测试结果弹窗 -->
    <el-dialog v-model="testVisible" title="视频源测试" width="520px">
      <template v-if="testResult">
        <div class="test-meta">
          <el-tag :type="testResult.ok ? 'success' : 'danger'" size="small">
            {{ testResult.ok ? '连接成功' : '连接失败' }}
          </el-tag>
          <el-text size="small" type="info">延迟：{{ testResult.latency_ms }} ms</el-text>
          <el-text v-if="testResult.ok" size="small" type="info">
            分辨率：{{ testResult.width }} × {{ testResult.height }}
          </el-text>
        </div>
        <el-text v-if="testResult.error" type="danger" size="small">{{ testResult.error }}</el-text>
        <img v-if="testResult.snapshot_base64" :src="'data:image/jpeg;base64,' + testResult.snapshot_base64" class="snapshot-img" alt="快照" />
      </template>
    </el-dialog>

    <!-- 创建视频源（运行态）弹窗 -->
    <el-dialog v-model="showCreate" title="创建视频源" width="520px">
      <el-form :model="createForm" label-width="110px">
        <el-form-item label="摄像头 ID"><el-input v-model="createForm.camera_id" type="number" /></el-form-item>
        <el-form-item label="名称"><el-input v-model="createForm.name" /></el-form-item>
        <el-form-item label="协议">
          <el-select v-model="createForm.protocol" style="width: 100%">
            <el-option label="RTSP/RTP" value="rtsp" />
            <el-option label="GB28181 国标" value="gb28181" />
            <el-option label="ONVIF" value="onvif" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="createForm.protocol === 'rtsp'" label="RTSP 地址">
          <el-input v-model="createForm.rtsp_url" placeholder="rtsp://..." />
        </el-form-item>
        <template v-else-if="createForm.protocol === 'gb28181'">
          <el-form-item label="设备 ID"><el-input v-model="createForm.gb_device_id" /></el-form-item>
          <el-form-item label="IP"><el-input v-model="createForm.gb_ip" /></el-form-item>
          <el-form-item label="端口"><el-input v-model="createForm.gb_port" type="number" /></el-form-item>
          <el-form-item label="通道"><el-input v-model="createForm.gb_channel" /></el-form-item>
        </template>
        <template v-else-if="createForm.protocol === 'onvif'">
          <el-form-item label="服务地址"><el-input v-model="createForm.onvif_url" placeholder="http://..." /></el-form-item>
          <el-form-item label="用户名"><el-input v-model="createForm.onvif_username" /></el-form-item>
          <el-form-item label="密码"><el-input v-model="createForm.onvif_password" type="password" show-password /></el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Refresh, Upload, Connection, Monitor, Search, Link, VideoCamera, VideoPlay,
  Top, Bottom, Back, Right,
} from '@element-plus/icons-vue'
import request from '@/api/request'
import { formatDateTime } from '@/utils/time'
import {
  getGb28181Status, startGb28181, stopGb28181, getGb28181Devices,
  discoverOnvif, testOnvif, ptzOnvif, createVideoSource, listVideoSources, deleteVideoSource,
} from '@/api'

const router = useRouter()
const activeTab = ref('list')

// ---- 接入列表（原摄像头管理） ----
const listLoading = ref(false)
const saving = ref(false)
const uploading = ref(false)
const tableData = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const testingId = ref<number | null>(null)
const testVisible = ref(false)
const testResult = ref<any>(null)
const videoOptions = ref<any[]>([])
const sort = ref('')

const form = reactive({
  name: '',
  source_type: 'camera',
  protocol: 'rtsp',
  rtsp_url: '',
  gb_device_id: '',
  gb_ip: '',
  gb_port: '',
  gb_channel: '',
  onvif_url: '',
  onvif_username: '',
  onvif_password: '',
  location: '',
  vendor: '',
})

// ---- 视频源接入标签页（GB28181 / ONVIF / 活跃视频源） ----
const vsLoading = ref(false)
const gbBusy = ref(false)
const discovering = ref(false)
const testing = ref(false)
const creating = ref(false)
const showCreate = ref(false)

const gbStatus = reactive<any>({ running: false, config: {}, device_count: 0 })
const gbDevices = ref<any[]>([])
const onvifDevices = ref<any[]>([])
const onvifTestResult = ref<any>(null)
const sources = ref<any[]>([])

const onvifTestForm = reactive({ onvif_url: '', username: 'admin', password: 'admin' })
const ptzForm = reactive({ camera_id: '' })
const createForm = reactive({
  camera_id: '', name: '', protocol: 'rtsp',
  rtsp_url: '', gb_device_id: '', gb_ip: '', gb_port: '', gb_channel: '',
  onvif_url: '', onvif_username: '', onvif_password: '',
})

// ---- 公共 ----
function statusType(s: string) {
  return s === 'online' ? 'success' : s === 'offline' ? 'danger' : 'info'
}
function statusText(s: string) {
  return s === 'online' ? '在线' : s === 'offline' ? '离线' : '未知'
}
function protocolLabel(p?: string) {
  return p === 'gb28181' ? 'GB28181' : p === 'onvif' ? 'ONVIF' : 'RTSP'
}

// ---- 接入列表 ----
function createTask(row: any) {
  router.push({ path: '/ai-vision/monitor', query: { tab: 'tasks', camera_id: row.id, camera_name: row.name } })
}
function onSortChange({ prop, order }: { prop: string; order: string | null }) {
  if (prop === 'name' && order) {
    sort.value = order === 'ascending' ? 'name' : '-name'
  } else {
    sort.value = ''
  }
  page.value = 1
  fetchList()
}
async function fetchList() {
  listLoading.value = true
  try {
    const res: any = await request.get('/ai-vision/cameras', {
      params: { page: page.value, page_size: pageSize.value, sort: sort.value }
    })
    tableData.value = res.items || []
    total.value = res.total || 0
  } catch {
    // handled by interceptor
  } finally {
    listLoading.value = false
  }
}
async function fetchVideos() {
  try {
    const res: any = await request.get('/ai-vision/videos/list')
    videoOptions.value = res.items || []
  } catch {
    // handled by interceptor
  }
}
async function handleUploadVideo(options: any) {
  const file = options.file as File
  if (file.size > 500 * 1024 * 1024) {
    ElMessage.warning('视频过大（>500MB），请压缩后重试')
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res: any = await request.post('/ai-vision/videos/upload', fd, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    form.rtsp_url = res.file_path
    ElMessage.success(`上传成功（${res.size_mb}MB），已自动选用`)
    await fetchVideos()
  } catch {
    // handled by interceptor
  } finally {
    uploading.value = false
  }
}
function resetForm() {
  form.name = ''
  form.source_type = 'camera'
  form.protocol = 'rtsp'
  form.rtsp_url = ''
  form.gb_device_id = ''
  form.gb_ip = ''
  form.gb_port = ''
  form.gb_channel = ''
  form.onvif_url = ''
  form.onvif_username = ''
  form.onvif_password = ''
  form.location = ''
  form.vendor = ''
}
function openCreate() {
  editingId.value = null
  resetForm()
  dialogVisible.value = true
  fetchVideos()
}
function openEdit(row: any) {
  editingId.value = row.id
  form.name = row.name
  form.source_type = row.source_type || 'camera'
  form.protocol = row.protocol || 'rtsp'
  form.rtsp_url = row.rtsp_url || ''
  form.gb_device_id = row.gb_device_id || ''
  form.gb_ip = row.gb_ip || ''
  form.gb_port = row.gb_port != null ? String(row.gb_port) : ''
  form.gb_channel = row.gb_channel || ''
  form.onvif_url = row.onvif_url || ''
  form.onvif_username = row.onvif_username || ''
  form.onvif_password = row.onvif_password || ''
  form.location = row.location || ''
  form.vendor = row.vendor || ''
  dialogVisible.value = true
  if (form.source_type === 'video') fetchVideos()
}
function buildPayload() {
  const p = {
    name: form.name,
    source_type: form.source_type,
    protocol: form.protocol,
    rtsp_url: form.rtsp_url || undefined,
    gb_device_id: form.gb_device_id || undefined,
    gb_ip: form.gb_ip || undefined,
    gb_port: form.gb_port ? Number(form.gb_port) : undefined,
    gb_channel: form.gb_channel || undefined,
    onvif_url: form.onvif_url || undefined,
    onvif_username: form.onvif_username || undefined,
    onvif_password: form.onvif_password || undefined,
    location: form.location || undefined,
    vendor: form.vendor || undefined,
  }
  return p
}
async function handleSave() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  if (form.source_type === 'video') {
    if (!form.rtsp_url.trim()) {
      ElMessage.warning('请上传或选择视频文件')
      return
    }
  } else if (form.protocol === 'rtsp' && !form.rtsp_url.trim()) {
    ElMessage.warning('请填写 RTSP 地址')
    return
  } else if (form.protocol === 'gb28181' && !form.gb_device_id.trim()) {
    ElMessage.warning('请填写 GB28181 设备 ID')
    return
  } else if (form.protocol === 'onvif' && !form.onvif_url.trim()) {
    ElMessage.warning('请填写 ONVIF 服务地址')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await request.put(`/ai-vision/cameras/${editingId.value}`, buildPayload())
      ElMessage.success('更新成功')
    } else {
      await request.post('/ai-vision/cameras', buildPayload())
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
async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除视频源「${row.name}」吗？`, '提示', { type: 'warning' })
  } catch {
    return // 取消
  }
  await request.delete(`/ai-vision/cameras/${row.id}`)
  ElMessage.success('删除成功')
  await fetchList()
}
async function handleTest(row: any) {
  testingId.value = row.id
  testResult.value = null
  testVisible.value = true
  try {
    const res: any = await request.post(`/ai-vision/cameras/${row.id}/test`)
    testResult.value = res
    if (!res.ok) {
      ElMessage.warning('测试失败：' + (res.error || '未知错误'))
    } else {
      ElMessage.success(`测试成功，延迟 ${res.latency_ms}ms`)
    }
    await fetchList()
  } catch {
    testVisible.value = false
  } finally {
    testingId.value = null
  }
}

// ---- 视频源接入（GB28181 / ONVIF / 活跃视频源） ----
async function refreshVideoSources() {
  vsLoading.value = true
  try {
    const [st, dev, src] = await Promise.all([
      getGb28181Status(), getGb28181Devices(), listVideoSources(),
    ])
    Object.assign(gbStatus, st)
    gbDevices.value = (dev as any).items || []
    sources.value = (src as any).items || []
  } catch {
    ElMessage.warning('刷新失败')
  } finally {
    vsLoading.value = false
  }
}
async function handleStartGb() {
  gbBusy.value = true
  try {
    await startGb28181({})
    ElMessage.success('GB28181 服务已启动')
    await refreshVideoSources()
  } catch { /* interceptor */ } finally { gbBusy.value = false }
}
async function handleStopGb() {
  gbBusy.value = true
  try {
    await stopGb28181()
    ElMessage.success('已停止')
    await refreshVideoSources()
  } catch { /* interceptor */ } finally { gbBusy.value = false }
}
async function handleDiscover() {
  discovering.value = true
  try {
    const res: any = await discoverOnvif(3)
    onvifDevices.value = res.items || []
    ElMessage.success(`发现 ${onvifDevices.value.length} 个设备`)
  } catch { /* interceptor */ } finally { discovering.value = false }
}
function fillTest(row: any) {
  onvifTestForm.onvif_url = row.url || `http://${row.ip}:${row.port}`
  activeTab.value = 'onvif'
}
async function handleOnvifTest() {
  testing.value = true
  onvifTestResult.value = null
  try {
    const res: any = await testOnvif({ ...onvifTestForm })
    onvifTestResult.value = res
  } catch { /* interceptor */ } finally { testing.value = false }
}
async function handlePtz(action: string) {
  const id = Number(ptzForm.camera_id)
  if (!id) { ElMessage.warning('请输入摄像头 ID'); return }
  try {
    await ptzOnvif({ camera_id: id, action })
  } catch { /* interceptor */ }
}
async function handleCreate() {
  creating.value = true
  try {
    await createVideoSource({
      camera_id: Number(createForm.camera_id),
      name: createForm.name,
      protocol: createForm.protocol,
      rtsp_url: createForm.rtsp_url || undefined,
      gb_device_id: createForm.gb_device_id || undefined,
      gb_ip: createForm.gb_ip || undefined,
      gb_port: createForm.gb_port ? Number(createForm.gb_port) : undefined,
      gb_channel: createForm.gb_channel || undefined,
      onvif_url: createForm.onvif_url || undefined,
      onvif_username: createForm.onvif_username || undefined,
      onvif_password: createForm.onvif_password || undefined,
    })
    ElMessage.success('视频源创建成功')
    showCreate.value = false
    await refreshVideoSources()
  } catch { /* interceptor */ } finally { creating.value = false }
}
async function handleClose(cameraId: number) {
  try {
    await deleteVideoSource(cameraId)
    ElMessage.success('已关闭')
    await refreshVideoSources()
  } catch { /* interceptor */ }
}

onMounted(() => {
  fetchList()
  refreshVideoSources()
})
</script>

<style scoped>
.camera-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
.test-meta { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.snapshot-img { width: 100%; max-height: 320px; object-fit: contain; border-radius: 6px; background: #000; margin-top: 12px; }
.video-upload-area { width: 100%; }
.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }
.cam-tabs { margin-top: 8px; }
.src-card { border-radius: 16px; margin-bottom: 16px; }
.card-title { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.card-title .el-icon { color: var(--theme-default, #5a67f5); }
.gb-config { margin-bottom: 12px; }
.row-actions { margin-top: 12px; }
.inline-form { display: flex; flex-wrap: wrap; gap: 8px 16px; }
</style>
