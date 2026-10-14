<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h2>人脸库</h2>
        <p class="text-muted">
          登记人员人脸（白名单 / 黑名单），供陌生人告警、黑名单告警、识别记录使用；
          每人 1~5 张正脸照，同名重复登记自动覆盖（换角度/换着装后重新登记即可）。
        </p>
      </div>
      <div class="head-actions">
        <el-select v-model="filters.group" placeholder="分组" clearable style="width: 120px" @change="onFilterChange">
          <el-option label="白名单" value="whitelist" />
          <el-option label="黑名单" value="blacklist" />
        </el-select>
        <el-input
          v-model="filters.keyword"
          placeholder="姓名 / 工号"
          clearable
          style="width: 160px"
          @keyup.enter="onFilterChange"
          @clear="onFilterChange"
        />
        <el-button :loading="loading" @click="fetchList">刷新</el-button>
        <el-button type="primary" @click="openRegister" v-permission="'ai_vision:face:create'">登记人脸</el-button>
      </div>
    </div>

    <el-table :data="tableData" v-loading="loading" stripe style="width: 100%">
      <el-table-column label="照片" width="90">
        <template #default="{ row }">
          <el-image
            v-if="row.image_url"
            :src="row.image_url"
            :preview-src-list="[row.image_url]"
            preview-teleported
            fit="cover"
            class="face-thumb"
          />
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="姓名" min-width="110" />
      <el-table-column prop="staff_no" label="工号" min-width="90">
        <template #default="{ row }">{{ row.staff_no || '—' }}</template>
      </el-table-column>
      <el-table-column label="分组" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.group === 'blacklist' ? 'danger' : 'success'">
            {{ row.group === 'blacklist' ? '黑名单' : '白名单' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="特征" width="85">
        <template #default="{ row }">
          <el-tag size="small" :type="row.has_embedding ? 'success' : 'info'">
            {{ row.has_embedding ? '已提取' : '缺失' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="photo_count" label="照片数" width="80" />
      <el-table-column prop="note" label="备注" min-width="110" show-overflow-tooltip>
        <template #default="{ row }">{{ row.note || '—' }}</template>
      </el-table-column>
      <el-table-column label="更新时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-button link type="danger" size="small" @click="handleDelete(row)" v-permission="'ai_vision:face:delete'">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && tableData.length === 0" description="人脸库还是空的" :image-size="120">
      <p class="empty-tip">
        登记后即可使用陌生人告警 / 黑名单告警 / 识别记录；
        白名单为空时，陌生人模式会把所有人都判为陌生人。
      </p>
      <el-button type="primary" @click="openRegister" v-permission="'ai_vision:face:create'">登记第一个人</el-button>
    </el-empty>

    <!-- 登记弹窗 -->
    <el-dialog v-model="registerVisible" title="登记人脸" width="540px">
      <el-form :model="registerForm" label-width="70px">
        <el-form-item label="姓名" required>
          <el-input v-model="registerForm.name" maxlength="50" placeholder="姓名或工号（同名重复登记将覆盖旧档案）" />
        </el-form-item>
        <el-form-item label="分组" required>
          <el-radio-group v-model="registerForm.group">
            <el-radio-button value="whitelist">白名单</el-radio-button>
            <el-radio-button value="blacklist">黑名单</el-radio-button>
          </el-radio-group>
          <el-text size="small" type="info" style="display: block">
            白名单 = 允许通行的人（陌生人模式据此判"陌生"）；黑名单 = 命中即告警
          </el-text>
        </el-form-item>
        <el-form-item label="工号">
          <el-input v-model="registerForm.staff_no" maxlength="50" placeholder="可选" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="registerForm.note" maxlength="200" placeholder="如：部门 / 班次" />
        </el-form-item>
        <el-form-item label="照片" required>
          <el-upload
            v-model:file-list="registerFiles"
            :auto-upload="false"
            accept=".jpg,.jpeg,.png,.bmp,.webp"
            :limit="5"
            list-type="picture-card"
          >
            <el-icon><Plus /></el-icon>
          </el-upload>
          <el-text size="small" type="info" style="display: block">
            1~5 张正脸清晰照（单人、光线均匀、人脸占画面 1/5~1/2）；
            系统逐张提取特征取均值，检测不到人脸的照片会被拒绝。
          </el-text>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!registerForm.name || registerFiles.length === 0"
          @click="handleRegister"
        >登记</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'
import request from '@/api/request'
import { formatDateTime } from '@/utils/time'

const loading = ref(false)
const saving = ref(false)
const tableData = ref<any[]>([])
const filters = reactive({ group: '', keyword: '' })

const registerVisible = ref(false)
const registerForm = reactive({ name: '', group: 'whitelist', staff_no: '', note: '' })
const registerFiles = ref<UploadFile[]>([])

/** 绝对路径 → 静态媒体 URL（与样本库同一约定：截取 ai_vision/ 之后拼 /media/） */
function imageUrl(path: string) {
  if (!path) return ''
  const idx = path.lastIndexOf('ai_vision')
  if (idx >= 0) return '/api/v1/ai-vision/media/' + path.slice(idx + 'ai_vision/'.length).replace(/\\/g, '/')
  return ''
}

async function fetchList() {
  loading.value = true
  try {
    const params: any = {}
    if (filters.group) params.group = filters.group
    if (filters.keyword) params.keyword = filters.keyword
    const res: any = await request.get('/ai-vision/faces', { params })
    tableData.value = (res.items || []).map((f: any) => ({ ...f, image_url: imageUrl(f.photo_path) }))
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

function onFilterChange() {
  fetchList()
}

function openRegister() {
  registerForm.name = ''
  registerForm.group = 'whitelist'
  registerForm.staff_no = ''
  registerForm.note = ''
  registerFiles.value = []
  registerVisible.value = true
}

async function handleRegister() {
  if (!registerForm.name) {
    ElMessage.warning('请填写姓名')
    return
  }
  if (registerFiles.value.length === 0) {
    ElMessage.warning('请至少上传 1 张照片')
    return
  }
  saving.value = true
  try {
    const form = new FormData()
    form.append('name', registerForm.name)
    form.append('group', registerForm.group)
    form.append('staff_no', registerForm.staff_no)
    form.append('note', registerForm.note)
    for (const f of registerFiles.value) {
      if (f.raw) form.append('files', f.raw)
    }
    const res: any = await request.post('/ai-vision/faces/register', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    ElMessage.success(res?.msg || '登记成功')
    registerVisible.value = false
    await fetchList()
  } catch {
    // handled by interceptor
  } finally {
    saving.value = false
  }
}

async function handleDelete(row: any) {
  await ElMessageBox.confirm(`删除「${row.name}」的人脸档案？登记照片将一并清理，进行中的人脸任务约 30 秒内生效。`, '删除人脸档案', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  try {
    const res: any = await request.delete(`/ai-vision/faces/${row.id}`)
    ElMessage.success(res?.msg || '已删除')
    await fetchList()
  } catch {
    // handled by interceptor
  }
}

onMounted(fetchList)
</script>

<style scoped>
.page { padding: 16px; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 12px; flex-wrap: wrap; }
.page-head h2 { margin: 0 0 4px; font-size: 18px; }
.text-muted { color: var(--el-text-color-secondary); font-size: 13px; margin: 0; }
.head-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.face-thumb { width: 56px; height: 56px; border-radius: 6px; display: block; }
.empty-tip { color: var(--el-text-color-secondary); font-size: 13px; max-width: 460px; }
</style>
