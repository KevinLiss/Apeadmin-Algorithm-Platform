<!--
  AI 视觉平台 - 规则配置页面（一期页面归并产物）
  以 Tab 内嵌方式聚合两个原有页面（embedded 模式，路由入口不再单列）：
  - Tab1 识别事件：复用 views/ai_vision/events（含跳水动作识别 detector 表单）
  - Tab2 类别库：复用 views/ai_vision/categories
  定位：监控台布防前的"规则/事件维护"入口，与实时监控台形成 配置→执行 两页闭环。
-->
<template>
  <div class="rule-config-page">
    <div class="page-header">
      <h2>规则配置</h2>
      <p class="text-muted">维护识别事件（类别 × 判定规则 × 模型）与目标类别库，是监控台的布防依据</p>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="识别事件" name="events" lazy>
        <EventsPage :embedded="true" />
      </el-tab-pane>
      <el-tab-pane label="类别库" name="categories" lazy>
        <CategoriesPage :embedded="true" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import EventsPage from '@/views/ai_vision/events/index.vue'
import CategoriesPage from '@/views/ai_vision/categories/index.vue'

const activeTab = ref('events')
</script>

<style scoped>
.rule-config-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
/* 嵌入页自带 padding，抵消避免双重留白 */
.rule-config-page :deep(.event-page),
.rule-config-page :deep(.category-page) { padding: 0; }
</style>
