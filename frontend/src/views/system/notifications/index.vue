<template>
  <div class="notif-page">
    <!-- 页面标题 -->
    <div class="page-head">
      <div>
        <h3>报警外发配置</h3>
        <span class="breadcrumb">系统管理 / 报警外发（MQTT / Webhook）</span>
      </div>
      <div class="head-actions">
        <el-button :loading="testing" @click="handleTest('mqtt')" v-permission="'system:notification:test'">测试 MQTT</el-button>
        <el-button :loading="testing" @click="handleTest('webhook')" v-permission="'system:notification:test'">测试 Webhook</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave" v-permission="'system:notification:edit'">
          <el-icon><Check /></el-icon>保存配置
        </el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="报警外发说明"
      description="AI 视觉平台产生告警时，除在平台内记录与展示外，还可通过 MQTT 推送到物联网平台，或通过 Webhook 推送到企业微信 / 钉钉 / 自研系统。配置保存后立即生效（无需重启）。"
      style="margin-bottom: 20px"
    />

    <el-row :gutter="20">
      <!-- MQTT -->
      <el-col :xs="24" :lg="12">
        <el-card shadow="never" class="notif-card">
          <template #header>
            <div class="card-title">
              <el-icon><Connection /></el-icon>
              <span>MQTT 推送</span>
              <el-switch
                v-model="form.mqtt.enabled"
                active-value="true"
                inactive-value="false"
                style="margin-left: auto"
              />
            </div>
          </template>
          <el-form label-width="110px" label-position="right">
            <el-form-item label="Broker 地址">
              <el-input v-model="form.mqtt.broker" placeholder="如 192.168.1.100" :disabled="!onMqtt" />
            </el-form-item>
            <el-form-item label="端口">
              <el-input v-model="form.mqtt.port" placeholder="1883" :disabled="!onMqtt" />
            </el-form-item>
            <el-form-item label="用户名">
              <el-input v-model="form.mqtt.username" placeholder="可选，留空匿名" :disabled="!onMqtt" />
            </el-form-item>
            <el-form-item label="密码">
              <el-input
                v-model="form.mqtt.password"
                type="password"
                show-password
                placeholder="留空表示未设置或不修改"
                :disabled="!onMqtt"
              />
            </el-form-item>
            <el-form-item label="Topic">
              <el-input v-model="form.mqtt.topic" placeholder="apeadmin/alarm" :disabled="!onMqtt" />
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <!-- Webhook -->
      <el-col :xs="24" :lg="12">
        <el-card shadow="never" class="notif-card">
          <template #header>
            <div class="card-title">
              <el-icon><Link /></el-icon>
              <span>Webhook 推送</span>
              <el-switch
                v-model="form.webhook.enabled"
                active-value="true"
                inactive-value="false"
                style="margin-left: auto"
              />
            </div>
          </template>
          <el-form label-width="110px" label-position="right">
            <el-form-item label="回调 URL">
              <el-input v-model="form.webhook.url" placeholder="https://..." :disabled="!onWebhook" />
            </el-form-item>
            <el-form-item label="签名密钥">
              <el-input
                v-model="form.webhook.secret"
                type="password"
                show-password
                placeholder="留空不签名；发送时带 X-ApeAdmin-Signature 头"
                :disabled="!onWebhook"
              />
            </el-form-item>
            <el-form-item label="超时(秒)">
              <el-input v-model="form.webhook.timeout" placeholder="5" :disabled="!onWebhook" />
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Connection, Link } from '@element-plus/icons-vue'
import { getNotificationConfig, saveNotificationConfig, testNotification } from '@/api'

const saving = ref(false)
const testing = ref(false)

const form = reactive({
  mqtt: { enabled: 'false', broker: '', port: '1883', username: '', password: '', topic: 'apeadmin/alarm' },
  webhook: { enabled: 'false', url: '', secret: '', timeout: '5' },
})

const onMqtt = computed(() => form.mqtt.enabled === 'true')
const onWebhook = computed(() => form.webhook.enabled === 'true')

async function load() {
  try {
    const data: any = await getNotificationConfig()
    if (data?.mqtt) Object.assign(form.mqtt, data.mqtt)
    if (data?.webhook) Object.assign(form.webhook, data.webhook)
  } catch {
    ElMessage.warning('加载通知配置失败，使用默认值')
  }
}

async function handleSave() {
  saving.value = true
  try {
    await saveNotificationConfig({ mqtt: form.mqtt, webhook: form.webhook })
    ElMessage.success('配置已保存并立即生效')
  } catch {
    // error message shown by interceptor
  } finally {
    saving.value = false
  }
}

async function handleTest(channel: 'mqtt' | 'webhook') {
  testing.value = true
  try {
    const res: any = await testNotification(channel)
    if (res?.ok) ElMessage.success(`${channel.toUpperCase()} 连通性测试成功`)
    else ElMessage.error(`${channel.toUpperCase()} 测试失败：${res?.message || ''}`)
  } catch {
    // error message shown by interceptor
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.notif-page {
  padding: 0;
}
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e9edf3;
}
.page-head h3 {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 700;
  color: #2b2b2b;
}
.breadcrumb {
  font-size: 13px;
  color: #909399;
}
.head-actions {
  display: flex;
  gap: 10px;
}
.notif-card {
  border-radius: 16px;
}
.card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
.card-title .el-icon {
  color: var(--theme-default, #5a67f5);
}
</style>
