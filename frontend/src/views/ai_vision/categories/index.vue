<template>
  <div class="category-page">
    <!-- Hero 区：插画 + 引导文案（嵌入页签时隐藏） -->
    <div class="hero" v-if="!embedded">
      <div class="hero-text">
        <h2>类别库</h2>
        <p class="text-muted">定义 AI 视觉要识别的目标类别。内置类别（人员/车辆/明火/烟雾/溺水等）开箱即用，也可按业务新增自定义类别</p>
        <div class="hero-stats">
          <el-tag type="primary" effect="plain" round>内置 {{ builtinCount }} 类</el-tag>
          <el-tag type="success" effect="plain" round>自定义 {{ customCount }} 类</el-tag>
          <el-tag type="info" effect="plain" round>共 {{ total }} 类</el-tag>
        </div>
      </div>
      <img :src="categoriesImg" alt="类别库" class="hero-img" />
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:category:create'">
        <el-icon><Plus /></el-icon> 新增类别
      </el-button>
      <el-button @click="fetchList" :loading="loading">
        <el-icon><Refresh /></el-icon> 刷新
      </el-button>
    </div>

    <!-- 类别宫格 -->
    <div v-loading="loading" class="grid-wrap">
      <div class="cat-grid" v-if="tableData.length">
        <div
          class="cat-card"
          v-for="row in tableData"
          :key="row.id"
          :class="{ disabled: row.status !== 1 }"
        >
          <!-- 图标区 -->
          <div class="cat-icon" :class="{ disabled: row.status !== 1 }">
            <el-icon v-if="row.icon"><component :is="row.icon" /></el-icon>
            <span v-else class="cat-code">{{ row.code.slice(0, 2).toUpperCase() }}</span>
            <el-tag v-if="row.source === 'builtin'" size="small" type="primary" effect="dark" class="builtin-tag">内置</el-tag>
          </div>

          <!-- 信息区 -->
          <div class="cat-info">
            <div class="cat-name-row">
              <strong class="cat-name">{{ row.name }}</strong>
              <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small" effect="light" round>
                {{ row.status === 1 ? '启用' : '停用' }}
              </el-tag>
            </div>
            <span class="cat-code-text">{{ row.code }}</span>
            <p class="cat-desc" v-if="row.description">{{ row.description }}</p>
            <p class="cat-desc muted" v-else>暂无描述</p>
          </div>

          <!-- 操作区 -->
          <div class="cat-actions">
            <el-button link type="primary" size="small" @click="openEdit(row)" v-permission="'ai_vision:category:edit'">
              <el-icon><Edit /></el-icon> 编辑
            </el-button>
            <el-button
              link
              :type="row.source === 'builtin' ? 'warning' : 'danger'"
              size="small"
              @click="handleDelete(row)"
              v-permission="'ai_vision:category:delete'"
            >
              <el-icon><Delete /></el-icon> {{ row.source === 'builtin' ? '停用' : '删除' }}
            </el-button>
          </div>
        </div>
      </div>

      <!-- 空状态引导 -->
      <el-empty v-else-if="!loading" description="还没有类别" :image-size="120">
        <p class="empty-tip">内置类别（人员/车辆/明火/烟雾/溺水等）默认已就绪；如需新增自定义类别请点击下方按钮</p>
        <el-button type="primary" @click="openCreate" v-permission="'ai_vision:category:create'">
          <el-icon><Plus /></el-icon> 新增类别
        </el-button>
      </el-empty>
    </div>

    <!-- 分页 -->
    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[8, 12, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @size-change="fetchList"
        @current-change="fetchList"
      />
    </div>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑类别' : '新增类别'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="编码" required>
          <el-input v-model="form.code" placeholder="小写字母/下划线，如 helmet" :disabled="!!editingId" maxlength="50" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：安全帽" maxlength="100" />
        </el-form-item>
        <el-form-item label="图标">
          <el-input v-model="form.icon" placeholder="Element Plus 图标名，如 Hat" maxlength="50" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="类别用途说明" maxlength="500" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
defineProps<{ embedded?: boolean }>()
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Refresh, Edit, Delete,
} from '@element-plus/icons-vue'
import request from '@/api/request'
import categoriesImg from '@/assets/ai_vision/categories.jpg'

const loading = ref(false)
const saving = ref(false)
const tableData = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)

const form = reactive({
  code: '',
  name: '',
  icon: '',
  description: '',
  status: 1,
})

const totalItems = computed(() => tableData.value.length)
const builtinCount = computed(() => tableData.value.filter((r) => r.source === 'builtin').length)
const customCount = computed(() => tableData.value.filter((r) => r.source === 'custom').length)

async function fetchList() {
  loading.value = true
  try {
    const res: any = await request.get('/ai-vision/categories', {
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

function openCreate() {
  editingId.value = null
  form.code = ''
  form.name = ''
  form.icon = ''
  form.description = ''
  form.status = 1
  dialogVisible.value = true
}

function openEdit(row: any) {
  editingId.value = row.id
  form.code = row.code
  form.name = row.name
  form.icon = row.icon
  form.description = row.description
  form.status = row.status
  dialogVisible.value = true
}

async function handleSave() {
  if (!form.code.trim() || !form.name.trim()) {
    ElMessage.warning('请填写编码和名称')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await request.put(`/ai-vision/categories/${editingId.value}`, {
        name: form.name,
        icon: form.icon,
        description: form.description,
        status: form.status,
      })
      ElMessage.success('更新成功')
    } else {
      await request.post('/ai-vision/categories', {
        code: form.code,
        name: form.name,
        icon: form.icon,
        description: form.description,
        coco_map: {},
      })
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
  const msg = row.source === 'builtin'
    ? `确定停用内置类别「${row.name}」吗？停用后识别事件将无法引用。`
    : `确定删除「${row.name}」吗？`
  try {
    await ElMessageBox.confirm(msg, '提示', { type: 'warning' })
  } catch {
    return // 取消
  }
  await request.delete(`/ai-vision/categories/${row.id}`)
  ElMessage.success(row.source === 'builtin' ? '已停用' : '删除成功')
  await fetchList()
}

onMounted(() => {
  fetchList()
})
</script>

<style scoped>
.category-page { padding: 20px; }

/* ---- Hero 区 ---- */
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  background: linear-gradient(135deg, #eef4ff 0%, #f4fbf9 60%, #fdf6e8 100%);
  border: 1px solid #e3ebf7;
  border-radius: 14px;
  padding: 20px 28px;
  margin-bottom: 16px;
}
.hero-text h2 { margin: 0 0 6px; font-size: 22px; color: #1f2d3d; }
.hero-text .text-muted { color: #6b7a8c; font-size: 13px; margin: 0 0 14px; max-width: 520px; line-height: 1.6; }
.hero-img { width: 200px; height: 150px; object-fit: cover; border-radius: 10px; flex-shrink: 0; }
.hero-stats { display: flex; gap: 8px; }

/* ---- 操作栏 ---- */
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; }

/* ---- 类别宫格 ---- */
.grid-wrap { min-height: 200px; }
.cat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}
.cat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 12px;
  padding: 16px;
  transition: box-shadow 0.2s, border-color 0.2s;
}
.cat-card:hover { box-shadow: 0 4px 16px rgba(0, 0, 0, 0.07); border-color: #b3d8f7; }
.cat-card.disabled { opacity: 0.62; background: #fafbfc; }

.cat-icon {
  position: relative;
  width: 52px; height: 52px;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #e0f7f6, #eef4ff);
  border-radius: 12px;
  color: #13c2c2;
  font-size: 26px;
  flex-shrink: 0;
}
.cat-icon.disabled { background: #f0f2f5; color: #a8abb2; }
.cat-icon .cat-code { font-size: 16px; font-weight: 700; color: #13c2c2; }
.cat-icon.disabled .cat-code { color: #a8abb2; }
.builtin-tag { position: absolute; top: -6px; right: -6px; }

.cat-info { flex: 1; min-width: 0; }
.cat-name-row { display: flex; align-items: center; gap: 8px; }
.cat-name { font-size: 15px; color: #1f2d3d; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cat-code-text { font-size: 12px; color: #909399; font-family: Consolas, monospace; }
.cat-desc { font-size: 12px; color: #6b7a8c; margin: 4px 0 0; line-height: 1.4; }
.cat-desc.muted { color: #c0c4cc; }

.cat-actions { display: flex; flex-direction: column; gap: 2px; flex-shrink: 0; }

.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }

.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>