import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import '@/styles/apeui-theme.css'
import 'vue3-verify/dist/vue3-verify.css'

import App from './App.vue'
import router from './router'
import { permissionDirective } from './directives/permission'
import { initTheme } from './composables/useTheme'
import { useSettingsStore } from './stores/settings'
import { ElMessage } from 'element-plus'

const app = createApp(App)

// 应用已保存的主题（深色/浅色），需在挂载前执行以避免闪白
initTheme()

// 全局错误边界：捕获组件渲染/生命周期中未处理的异常，
// 避免整页白屏；同一错误 3 秒内只提示一次，防止刷屏。
let lastErrMsg = ''
let lastErrTs = 0
app.config.errorHandler = (err: unknown, _vm: unknown, info: string) => {
  console.error('[全局异常]', err, '|', info)
  const msg = err instanceof Error ? err.message : String(err)
  const now = Date.now()
  if (msg === lastErrMsg && now - lastErrTs < 3000) return
  lastErrMsg = msg
  lastErrTs = now
  ElMessage.error(`页面发生异常：${msg}（已记录到控制台）`)
}

// Register all Element Plus icons globally
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// Register global directives
app.directive('permission', permissionDirective)

// Fetch public settings and apply theme color before mount
const settingsStore = useSettingsStore()
settingsStore.fetchPublicSettings().finally(() => {
  app.mount('#app')
})
