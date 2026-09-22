<template>
  <div class="sample-page">
    <div class="page-header">
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
      <el-form-item label="标注状态">
        <el-select v-model="filters.label_status" placeholder="全部" clearable style="width: 130px" @change="handleFilterChange">
          <el-option label="未标注" value="unlabeled" />
          <el-option label="已标注" value="labeled" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button @click="fetchList" :loading="loading">
          <el-icon><Refresh /></el-icon> 刷新
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
        <el-button type="primary" :loading="uploading" :disabled="fileList.length === 0" @click="handleUpload">
          上传 {{ fileList.length ? `${fileList.length} 张` : '' }}
        </el-button>
      </div>
    </div>

    <!-- 网格列表 -->
    <div v-loading="loading" class="grid-wrap">
      <div class="sample-grid" v-if="tableData.length">
        <div class="sample-card" v-for="s in tableData" :key="s.id">
          <el-image
            :src="s.image_url"
            :preview-src-list="[s.image_url]"
            preview-teleported
            fit="cover"
            class="sample-img"
          />
          <div class="sample-info">
            <div class="sample-row">
              <el-tag size="small" type="warning">{{ s.category_code || '未分类' }}</el-tag>
              <el-tag size="small" :type="s.source === 'upload' ? 'primary' : 'success'">
                {{ s.source === 'upload' ? '上传' : '告警' }}
              </el-tag>
              <el-tag size="small" :type="s.label_status === 'labeled' ? 'success' : 'info'">
                {{ s.label_status === 'labeled' ? '已标注' : '未标注' }}
              </el-tag>
            </div>
            <div class="sample-row meta">
              <el-text size="small" type="info">{{ s.width }}×{{ s.height }}</el-text>
              <el-text size="small" type="info">#{{ s.id }}</el-text>
            </div>
            <div class="sample-row">
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
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
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
})

// 相对路径 → 静态 URL
function imageUrl(path: string) {
  if (!path) return ''
  const idx = path.lastIndexOf('ai_vision')
  if (idx >= 0) return '/api/v1/ai-vision/media/' + path.slice(idx + 'ai_vision/'.length).replace(/\\/g, '/')
  return ''
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
    const res: any = await request.get('/ai-vision/samples', { params })
    const items = res.items || []
    tableData.value = items.map((s: any) => ({ ...s, image_url: imageUrl(s.file_path) }))
    total.value = res.total || 0
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
  await ElMessageBox.confirm(`确定删除样本 #${s.id} 吗？文件将一并删除。`, '提示', { type: 'warning' })
  await request.delete(`/ai-vision/samples/${s.id}`)
  ElMessage.success('已删除')
  await fetchList()
}

onMounted(() => {
  fetchList()
  fetchCategories()
})
</script>

<style scoped>
.sample-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.filter-bar { margin-bottom: 12px; }
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
}
.sample-card:hover { box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08); }
.sample-img { width: 100%; height: 120px; display: block; }
.sample-info { padding: 8px; }
.sample-row { display: flex; justify-content: space-between; align-items: center; gap: 4px; margin-bottom: 6px; }
.sample-row.meta { margin-bottom: 2px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>