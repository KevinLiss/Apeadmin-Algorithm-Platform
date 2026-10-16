<template>
  <div class="models-page">
    <div class="page-head">
      <div>
        <h3>模型版本管理</h3>
        <span class="breadcrumb">AI 视觉平台 / 模型注册 · 版本切换 · 完整性校验</span>
      </div>
      <div class="head-actions">
        <el-button :loading="loading" @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
        <el-button :loading="validatingAll" @click="handleValidateAll" v-permission="'ai_vision:model_version:validate'">
          <el-icon><CircleCheck /></el-icon>校验全部
        </el-button>
      </div>
    </div>

    <el-card shadow="never" class="models-card">
      <template #header><div class="card-title"><el-icon><Files /></el-icon><span>已注册模型</span></div></template>
      <el-table :data="models" stripe size="small" empty-text="暂无已注册模型" v-loading="loading">
        <el-table-column prop="name" label="模型名称" min-width="140" />
        <el-table-column prop="category" label="类别" width="120" />
        <el-table-column label="当前版本" width="120">
          <template #default="{ row }">
            <el-tag type="primary" size="small" v-if="row.is_default">{{ row.version }}</el-tag>
            <span v-else>{{ row.version }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="input_size" label="输入尺寸" width="100" />
        <el-table-column label="文件大小" width="120">
          <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="file_path" label="文件路径" show-overflow-tooltip />
        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
            <el-tag v-if="row.is_active" type="info" size="small" style="margin-left:4px">已加载</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openVersions(row)" v-permission="'ai_vision:model_version:switch'">版本</el-button>
            <el-button size="small" link type="warning" @click="handleValidate(row)" v-permission="'ai_vision:model_version:validate'">校验</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 版本管理弹窗 -->
    <el-dialog v-model="showVersions" :title="`版本管理 - ${currentModel}`" width="560px">
      <el-table :data="versions" stripe size="small" v-loading="loadingVersions">
        <el-table-column prop="version" label="版本" width="140" />
        <el-table-column prop="description" label="说明" show-overflow-tooltip />
        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
            <el-tag v-if="row.is_active" type="info" size="small">已加载</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button
              size="small" link type="primary"
              :disabled="row.is_default"
              @click="handleSwitch(row.version)"
              v-permission="'ai_vision:model_version:switch'"
            >设为默认</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, CircleCheck, Files } from '@element-plus/icons-vue'
import { listModels, getModelVersions, switchModelVersion, validateModel, validateAllModels } from '@/api'

const loading = ref(false)
const validatingAll = ref(false)
const loadingVersions = ref(false)
const models = ref<any[]>([])
const showVersions = ref(false)
const currentModel = ref('')
const versions = ref<any[]>([])

function formatSize(bytes: number) {
  if (!bytes) return '-'
  const mb = bytes / 1024 / 1024
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`
}

async function load() {
  loading.value = true
  try {
    const data: any = await listModels()
    models.value = Array.isArray(data) ? data : (data?.data ?? [])
  } catch {
    ElMessage.warning('加载模型列表失败')
  } finally {
    loading.value = false
  }
}

async function openVersions(row: any) {
  currentModel.value = row.name
  showVersions.value = true
  loadingVersions.value = true
  try {
    const res: any = await getModelVersions(row.name)
    versions.value = res.versions || []
  } catch {
    ElMessage.warning('加载版本失败')
  } finally {
    loadingVersions.value = false
  }
}

async function handleSwitch(version: string) {
  try {
    await switchModelVersion(currentModel.value, version)
    ElMessage.success(`已切换 ${currentModel.value} 默认版本为 ${version}`)
    await openVersions({ name: currentModel.value })
    await load()
  } catch { /* interceptor */ }
}

async function handleValidate(row: any) {
  try {
    const res: any = await validateModel(row.name)
    if (res.is_valid) ElMessage.success(`${row.name} 校验通过`)
    else ElMessage.error(`${row.name} 校验失败：${(res.errors || []).join('；')}`)
  } catch { /* interceptor */ }
}

async function handleValidateAll() {
  validatingAll.value = true
  try {
    const res: any = await validateAllModels()
    ElMessage.success(`校验完成：共 ${res.total} 个，有效 ${res.valid}，无效 ${res.invalid}`)
  } catch { /* interceptor */ } finally { validatingAll.value = false }
}

onMounted(load)
</script>

<style scoped>
.models-page { padding: 0; }
.page-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid #e9edf3;
}
.page-head h3 { margin: 0 0 4px; font-size: 22px; font-weight: 700; color: #2b2b2b; }
.breadcrumb { font-size: 13px; color: #909399; }
.head-actions { display: flex; gap: 10px; }
.models-card { border-radius: 16px; }
.card-title { display: flex; align-items: center; gap: 8px; font-weight: 600; }
.card-title .el-icon { color: var(--theme-default, #5a67f5); }
</style>
