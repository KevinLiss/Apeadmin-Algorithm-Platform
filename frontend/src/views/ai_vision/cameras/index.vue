<template>
  <div class="camera-page">
    <div class="page-header">
      <h2>视频源管理</h2>
      <p class="text-muted">接入 RTSP 摄像头或本地视频文件（测试/演示用），测试连通性后可创建识别任务</p>
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:camera:create'">
        <el-icon><Plus /></el-icon> 新增视频源
      </el-button>
      <el-button @click="fetchList" :loading="loading">
        <el-icon><Refresh /></el-icon> 刷新
      </el-button>
    </div>

    <!-- 列表 -->
    <el-table v-if="total > 0 || loading" :data="tableData" v-loading="loading" stripe style="width: 100%; margin-top: 16px">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column label="类型" width="110">
        <template #default="{ row }">
          <el-tag :type="row.source_type === 'video' ? 'warning' : 'primary'" size="small">
            {{ row.source_type === 'video' ? '视频文件' : 'RTSP 摄像头' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="rtsp_url" label="地址/文件" min-width="220" show-overflow-tooltip />
      <el-table-column prop="location" label="位置" min-width="100" show-overflow-tooltip />
      <el-table-column prop="vendor" label="厂商" width="90" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">
            {{ statusText(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_online_at" label="最近在线" width="170">
        <template #default="{ row }">{{ row.last_online_at || '—' }}</template>
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

    <!-- 空状态引导 -->
    <el-empty v-if="!loading && total === 0" description="还没有视频源" :image-size="120">
      <p class="empty-tip">第一步：先接入一个摄像头（RTSP）或上传一段测试视频</p>
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:camera:create'">
        <el-icon><Plus /></el-icon> 新增视频源
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

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑视频源' : '新增视频源'" width="580px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：东门岗亭 / 测试视频-行人" maxlength="200" />
        </el-form-item>
        <el-form-item label="接入类型" required>
          <el-radio-group v-model="form.source_type">
            <el-radio value="camera">RTSP 摄像头</el-radio>
            <el-radio value="video">视频文件（测试/演示）</el-radio>
          </el-radio-group>
        </el-form-item>

        <!-- camera：RTSP 地址输入 -->
        <el-form-item v-if="form.source_type === 'camera'" label="RTSP 地址" required>
          <el-input v-model="form.rtsp_url" placeholder="rtsp://user:pass@ip:554/stream1" maxlength="500" />
        </el-form-item>

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
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Upload } from '@element-plus/icons-vue'
import request from '@/api/request'

const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const uploading = ref(false)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const testingId = ref<number | null>(null)
const testVisible = ref(false)
const testResult = ref<any>(null)
const videoOptions = ref<any[]>([])

const form = reactive({
  name: '',
  source_type: 'camera',
  rtsp_url: '',
  location: '',
  vendor: '',
})

function statusType(s: string) {
  return s === 'online' ? 'success' : s === 'offline' ? 'danger' : 'info'
}

function statusText(s: string) {
  return s === 'online' ? '在线' : s === 'offline' ? '离线' : '未知'
}

/** 跳转监控台任务管理并预选当前视频源（任务页嵌入监控台 tab，共享 query） */
function createTask(row: any) {
  router.push({ path: '/ai-vision/monitor', query: { tab: 'tasks', camera_id: row.id, camera_name: row.name } })
}

async function fetchList() {
  loading.value = true
  try {
    const res: any = await request.get('/ai-vision/cameras', {
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

async function fetchVideos() {
  try {
    const res: any = await request.get('/ai-vision/videos/list')
    videoOptions.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

// 自定义上传：multipart 直传，成功后自动选中
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

function openCreate() {
  editingId.value = null
  form.name = ''
  form.source_type = 'camera'
  form.rtsp_url = ''
  form.location = ''
  form.vendor = ''
  dialogVisible.value = true
  fetchVideos()
}

function openEdit(row: any) {
  editingId.value = row.id
  form.name = row.name
  form.source_type = row.source_type || 'camera'
  form.rtsp_url = row.rtsp_url
  form.location = row.location
  form.vendor = row.vendor
  dialogVisible.value = true
  if (form.source_type === 'video') fetchVideos()
}

async function handleSave() {
  if (!form.name.trim() || !form.rtsp_url.trim()) {
    ElMessage.warning(form.source_type === 'video' ? '请填写名称并上传/选择视频文件' : '请填写名称和 RTSP 地址')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await request.put(`/ai-vision/cameras/${editingId.value}`, { ...form })
      ElMessage.success('更新成功')
    } else {
      await request.post('/ai-vision/cameras', { ...form })
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
  await ElMessageBox.confirm(`确定删除视频源「${row.name}」吗？`, '提示', { type: 'warning' })
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

onMounted(() => {
  fetchList()
})
</script>

<style scoped>
.camera-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.toolbar { display: flex; gap: 8px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
.test-meta { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.snapshot-img {
  width: 100%;
  max-height: 320px;
  object-fit: contain;
  border-radius: 6px;
  background: #000;
  margin-top: 12px;
}
.video-upload-area { width: 100%; }
.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }
</style>
