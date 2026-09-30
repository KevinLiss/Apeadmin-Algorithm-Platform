<template>
  <div class="event-page">
    <div class="page-header" v-if="!embedded">
      <h2>识别事件</h2>
      <p class="text-muted">定义"看什么 + 怎么算告警"的规则包（类别 + 置信度阈值 + 绑定模型），发布后可被任务引用</p>
    </div>

    <!-- 操作栏 -->
    <div class="toolbar">
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:event:create'">
        <el-icon><Plus /></el-icon> 新建事件
      </el-button>
      <el-button @click="fetchList" :loading="loading">
        <el-icon><Refresh /></el-icon> 刷新
      </el-button>
    </div>

    <!-- 列表 -->
    <el-table v-if="total > 0 || loading" :data="tableData" v-loading="loading" stripe style="width: 100%; margin-top: 16px">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="name" label="事件名称" min-width="140">
        <template #default="{ row }">
          {{ row.name }}
          <el-tag v-if="row.rule?.detector === 'diving'" size="small" type="warning" style="margin-left: 6px">动作识别</el-tag>
          <el-tag v-else-if="row.rule?.detector === 'diving_top'" size="small" type="primary" style="margin-left: 6px">俯视跳水</el-tag>
          <el-tag v-else-if="row.rule?.detector === 'climbing'" size="small" type="danger" style="margin-left: 6px">攀爬翻越</el-tag>
          <el-tag v-else-if="row.rule?.detector === 'absence'" size="small" type="info" style="margin-left: 6px">离岗检测</el-tag>
          <el-tag v-else-if="row.rule?.detector === 'identity'" size="small" type="success" style="margin-left: 6px">人岗验证</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="类别" min-width="120">
        <template #default="{ row }">
          <el-tag v-for="code in row.category_codes" :key="code" size="small" class="cat-tag">{{ categoryName(code) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="模型" width="90">
        <template #default="{ row }">
          <el-text size="small" :type="row.model_ids?.length ? '' : 'danger'">{{ row.model_ids?.length || 0 }} 个</el-text>
        </template>
      </el-table-column>
      <el-table-column label="7天质量" width="120">
        <template #default="{ row }">
          <el-tooltip v-if="row.quality?.alarms_7d" :content="row.quality.tip || `近7天 ${row.quality.alarms_7d} 条告警，误报率 ${(row.quality.fp_rate * 100).toFixed(0)}%，状态良好`" placement="top">
            <el-tag size="small" :type="row.quality.tip ? (row.quality.fp_rate >= 0.5 ? 'danger' : 'warning') : 'success'">
              误报 {{ (row.quality.fp_rate * 100).toFixed(0) }}%
            </el-tag>
          </el-tooltip>
          <el-text v-else size="small" type="info">无告警</el-text>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="310" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" size="small" @click="handlePublish(row)" v-if="['draft', 'ready'].includes(row.status)" v-permission="'ai_vision:event:edit'">发布</el-button>
          <el-button link type="warning" size="small" @click="handleRetire(row)" v-if="['ready', 'paused'].includes(row.status)" v-permission="'ai_vision:event:edit'">退役</el-button>
          <el-button link type="primary" size="small" @click="createTask(row)" v-if="row.status === 'ready'" v-permission="'ai_vision:task:create'">创建任务</el-button>
          <el-tooltip content="运行中的事件不可编辑，请先停止引用它的任务" :disabled="row.status !== 'running'" placement="top">
            <span>
              <el-button link type="primary" size="small" @click="openEdit(row)" :disabled="row.status === 'running'" v-permission="'ai_vision:event:edit'">编辑</el-button>
            </span>
          </el-tooltip>
          <el-button link type="danger" size="small" @click="handleDelete(row)" v-permission="'ai_vision:event:delete'">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态引导 -->
    <el-empty v-if="!loading && total === 0" description="还没有识别事件" :image-size="120">
      <p class="empty-tip">第二步：定义一个识别事件（如"检测到人员出现即告警"）</p>
      <el-button type="primary" @click="openCreate" v-permission="'ai_vision:event:create'">
        <el-icon><Plus /></el-icon> 新建事件
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

    <!-- 新建/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑事件' : '新建识别事件'" width="640px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="事件名称" required>
          <el-input v-model="form.name" placeholder="如：吸烟识别" maxlength="200" />
        </el-form-item>
        <el-form-item label="判定方式">
          <el-radio-group v-model="form.rule.detector" @change="onDetectorChange">
            <el-radio-button value="object">目标检测</el-radio-button>
            <el-radio-button value="diving">动作识别（跳水）</el-radio-button>
            <el-radio-button value="diving_top">跳水-高空俯视</el-radio-button>
            <el-radio-button value="climbing">攀爬翻越</el-radio-button>
            <el-radio-button value="absence">离岗检测</el-radio-button>
            <el-radio-button value="identity">人岗验证</el-radio-button>
          </el-radio-group>
          <el-text v-if="form.rule.detector === 'diving'" size="small" type="warning" style="margin-top: 4px">
            需绑定姿态模型（yolo11n-pose），类别选择"人员"
          </el-text>
          <el-text v-else-if="form.rule.detector === 'diving_top'" size="small" type="warning" style="margin-top: 4px">
            无人机/高位俯视机位专用：需绑定目标检测模型（yolo11n-coco），类别选"人员"，并标定水池区域
          </el-text>
          <el-text v-else-if="form.rule.detector === 'climbing'" size="small" type="warning" style="margin-top: 4px">
            翻越围栏/桥边/湖岸：需绑定姿态模型（yolo11n-pose），类别选"人员"；可选配警戒线做越线确认
          </el-text>
          <el-text v-else-if="form.rule.detector === 'absence'" size="small" type="warning" style="margin-top: 4px">
            岗位区域（ROI）内持续无人超阈值即告警：需绑定含"人员"类的检测模型（yolo11n-coco），并务必标定岗位 ROI
          </el-text>
          <el-text v-else-if="form.rule.detector === 'identity'" size="small" type="warning" style="margin-top: 4px">
            在岗人员与登记员工体貌比对，不符持续超阈值告警：需绑定含"人员"类的检测模型 + 标定岗位 ROI + 登记员工档案
          </el-text>
        </el-form-item>
        <el-form-item label="目标类别" required>
          <el-select v-model="form.category_codes" multiple placeholder="选择要识别的目标类别" style="width: 100%" :disabled="['diving', 'diving_top', 'climbing', 'absence', 'identity'].includes(form.rule.detector)">
            <el-option v-for="c in categories" :key="c.code" :label="`${c.name} (${c.code})`" :value="c.code" :disabled="c.status !== 1" />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定模型" :required="form.status === 'ready' || form.status === 'running'">
          <el-select v-model="form.model_ids" multiple placeholder="选择检测模型（发布后不可为空）" style="width: 100%">
            <el-option v-for="m in models" :key="m.id" :label="`${m.name} v${m.version}${m.quantized ? ' (INT8)' : ''}`" :value="m.id" />
          </el-select>
          <el-text size="small" type="info">
            <template v-if="form.rule.detector === 'diving'">跳水识别必须选择 yolo11n-pose 姿态模型</template>
            <template v-else-if="form.rule.detector === 'diving_top'">高空俯视跳水必须选择 yolo11n-coco 目标检测模型</template>
            <template v-else-if="form.rule.detector === 'climbing'">攀爬翻越必须选择 yolo11n-pose 姿态模型</template>
            <template v-else-if="form.rule.detector === 'absence' || form.rule.detector === 'identity'">必须选择含"人员"类的检测模型（yolo11n-coco）</template>
            <template v-else>需选择与目标类别匹配的模型</template>
          </el-text>
        </el-form-item>
        <el-form-item label="置信度阈值">
          <el-slider v-model="form.rule.threshold" :min="0.1" :max="0.95" :step="0.05" show-input />
          <el-text v-if="form.rule.detector === 'diving_top'" size="small" type="info">俯视人体目标小，建议 ≤0.18（过高会漏掉整段轨迹）</el-text>
        </el-form-item>
        <template v-if="form.rule.detector === 'object'">
          <el-form-item label="持续时长(s)">
            <el-input-number v-model="form.rule.duration" :min="0" :max="300" /> <el-text size="small" type="info">0 = 单帧即告警</el-text>
          </el-form-item>
        </template>
        <template v-else-if="form.rule.detector === 'diving'">
          <el-form-item label="躯干倾角(°)">
            <el-input-number v-model="form.rule.angle_thr" :min="10" :max="90" :step="5" />
            <el-text size="small" type="info">≥该角度视为腾空姿态（站立≈0-20°，跳水翻转 55-180°）</el-text>
          </el-form-item>
          <el-form-item label="最短腾空(s)">
            <el-input-number v-model="form.rule.min_air_seconds" :min="0" :max="5" :step="0.2" :precision="1" />
            <el-text size="small" type="info">腾空姿态需持续的最短时间，防单帧误判</el-text>
          </el-form-item>
          <el-form-item label="消失确认(s)">
            <el-input-number v-model="form.rule.miss_seconds" :min="0" :max="10" :step="0.2" :precision="1" />
            <el-text size="small" type="info">腾空后目标从画面消失多久判定为入水</el-text>
          </el-form-item>
          <el-form-item label="头朝下阈值">
            <el-slider v-model="form.rule.head_down_frac" :min="0" :max="0.15" :step="0.01" show-input />
            <el-text size="small" type="info">鼻低于肩占框高比例阈值（头先入水时髋部被遮挡，用头/肩信号兜底；0.02 适合多数场景）</el-text>
          </el-form-item>
          <el-form-item label="下落确认">
            <el-slider v-model="form.rule.descent_frac" :min="0" :max="0.6" :step="0.05" show-input />
            <el-text size="small" type="info">目标需下落≥画面高×该比例才算入水（0=不校验，遮挡场景建议调低）</el-text>
          </el-form-item>
        </template>
        <template v-else-if="form.rule.detector === 'diving_top'">
          <el-form-item label="水池区域">
            <el-button size="small" type="primary" plain style="margin-bottom: 6px" @click="fetchDrawerCameras(); poolRoiDrawerVisible = true">在画面上圈选</el-button>
            <el-input
              v-model="poolRoiText"
              type="textarea"
              :rows="2"
              placeholder='[[0.22,0.39],[0.76,0.4],[0.76,0.63],[0.22,0.62]]'
              @blur="parsePoolRoi"
            />
            <el-text size="small" type="info">
              水面的归一化多边形（0~1，左上为原点），人消失时须位于该区域内才告警。
              可先留空（不校验位置），或参考监控台画面比例手动标定 3~6 个顶点
            </el-text>
            <el-text v-if="poolRoiError" size="small" type="danger">格式错误：需为 [[x,y],...] 且 x/y 在 0~1</el-text>
          </el-form-item>
          <el-form-item label="轨迹速度">
            <el-slider v-model="form.rule.move_speed_thr" :min="0.05" :max="0.5" :step="0.01" show-input />
            <el-text size="small" type="info">人每秒横穿画面的宽度占比阈值（跳水掠过水面 ≈0.2，游泳 ≈0.1 以下）</el-text>
          </el-form-item>
          <el-form-item label="消失确认(s)">
            <el-input-number v-model="form.rule.disappear_seconds" :min="0.3" :max="5" :step="0.1" :precision="1" />
            <el-text size="small" type="info">高速轨迹后目标消失多久判定为入水</el-text>
          </el-form-item>
        </template>
        <template v-else-if="form.rule.detector === 'climbing'">
          <el-form-item label="手过头阈值">
            <el-slider v-model="form.rule.hand_over_thr" :min="0.1" :max="1.0" :step="0.05" show-input />
            <el-text size="small" type="info">最高手腕举过肩的高度（躯干长倍数，尺度不变，航拍远近通用）。攀爬抓握通常 ≥0.3</el-text>
          </el-form-item>
          <el-form-item label="持续时长(s)">
            <el-input-number v-model="form.rule.hand_dur" :min="0.4" :max="5" :step="0.1" :precision="1" />
            <el-text size="small" type="info">手过头+身体竖直需持续该秒数才认定攀爬（防挥手/扶栏误判）</el-text>
          </el-form-item>
          <el-form-item label="硬时长(s)">
            <el-input-number v-model="form.rule.hard_dur" :min="2" :max="15" :step="0.5" :precision="1" />
            <el-text size="small" type="info">无抬腿/上攀等辅助特征时，仅凭持续攀爬姿态告警的最低时长</el-text>
          </el-form-item>
          <el-form-item label="警戒线（预留）">
            <el-button size="small" type="primary" plain style="margin-bottom: 6px" @click="fetchDrawerCameras(); alarmLineDrawerVisible = true">在画面上画线</el-button>
            <el-input
              v-model="alarmLineText"
              type="textarea"
              :rows="1"
              placeholder='[[0.3,0.4],[0.6,0.4]]'
              @blur="parseAlarmLine"
            />
            <el-text size="small" type="info">
              围栏/岸线的两个端点（归一化 0~1）。配置后仅当人从一侧越过到另一侧才告警（方案四越线确认）；留空=不启用
            </el-text>
            <el-text v-if="alarmLineError" size="small" type="danger">格式错误：需为 [[x1,y1],[x2,y2]] 且坐标在 0~1</el-text>
          </el-form-item>
        </template>
        <template v-else-if="form.rule.detector === 'absence'">
          <el-form-item label="岗位区域">
            <el-button size="small" type="primary" plain style="margin-bottom: 6px" @click="fetchDrawerCameras(); roiDrawerVisible = true">在画面上圈选</el-button>
            <el-input
              v-model="roiText"
              type="textarea"
              :rows="2"
              placeholder='[[0.3,0.4],[0.7,0.4],[0.7,0.8],[0.3,0.8]]'
              @blur="parseRoi"
            />
            <el-text size="small" type="info">
              岗位工作区域的归一化多边形（0~1，左上为原点，3~6 顶点）。只有该区域内的人才算"在岗"；
              留空 = 整个画面。建议用「在画面上圈选」直接在监控画面画，不用手填坐标
            </el-text>
            <el-text v-if="roiError" size="small" type="danger">格式错误：需为 [[x,y],...] 且 x/y 在 0~1</el-text>
          </el-form-item>
          <el-form-item label="在岗判定类">
            <el-select v-model="form.rule.presence_class" style="width: 200px">
              <el-option label="人员（通用，任何人算在岗）" value="person" />
              <el-option v-for="c in categories.filter((x) => x.code !== 'person' && x.status === 1)" :key="c.code" :label="`${c.name}（穿该制服/工装才算在岗）`" :value="c.code" />
            </el-select>
            <el-text size="small" type="info" style="display: block; margin-top: 4px">
              判定"在岗"依据的检测类别：选"人员"=画面里有人就不算离岗；选"救生员"等训练类别=必须有该着装的人在场（需绑定含该类别的模型，如自训 lifeguard 产物）
            </el-text>
          </el-form-item>
          <el-form-item label="离岗时长(s)">
            <el-input-number v-model="form.rule.absence_seconds" :min="1" :max="3600" :step="1" />
            <el-text size="small" type="info">岗位区域内持续无人超过该秒数即告警（人回来自动复位）</el-text>
          </el-form-item>
          <el-form-item label="重复提醒(s)">
            <el-input-number v-model="form.rule.absence_repeat" :min="0" :max="3600" :step="30" />
            <el-text size="small" type="info">0 = 每次离岗只报一条（人回岗再离开才算新告警，双模型/多模型不会重复刷屏）；&gt;0 = 持续离岗期间每隔该秒数再提醒一次</el-text>
          </el-form-item>
          <el-form-item label="布防时段">
            <div v-for="(seg, i) in form.rule.schedule" :key="i" style="display: flex; gap: 8px; margin-bottom: 6px">
              <el-time-select v-model="seg.start" start="00:00" step="00:30" end="23:30" placeholder="开始" style="width: 110px" />
              <el-time-select v-model="seg.end" :min-time="seg.start" start="00:00" step="00:30" end="23:59" placeholder="结束" style="width: 110px" />
              <el-button link type="danger" size="small" @click="form.rule.schedule.splice(i, 1)">删</el-button>
            </div>
            <el-button link type="primary" size="small" @click="form.rule.schedule.push({ start: '09:00', end: '18:00' })">+ 添加时段</el-button>
            <el-text size="small" type="info" style="display: block">留空 = 全天布防；时段外不判定离岗（如夜班岗位只布防夜班时段）</el-text>
          </el-form-item>
        </template>
        <template v-else-if="form.rule.detector === 'identity'">
          <el-form-item label="岗位区域">
            <el-button size="small" type="primary" plain style="margin-bottom: 6px" @click="fetchDrawerCameras(); roiDrawerVisible = true">在画面上圈选</el-button>
            <el-input
              v-model="roiText"
              type="textarea"
              :rows="2"
              placeholder='[[0.3,0.4],[0.7,0.4],[0.7,0.8],[0.3,0.8]]'
              @blur="parseRoi"
            />
            <el-text size="small" type="info">
              岗位工作区域的归一化多边形（0~1，左上为原点，3~6 顶点）。只有该区域内的人才参与身份比对；
              留空 = 整个画面（路过的人也会被比对，建议务必标定）
            </el-text>
            <el-text v-if="roiError" size="small" type="danger">格式错误：需为 [[x,y],...] 且 x/y 在 0~1</el-text>
          </el-form-item>
          <el-form-item label="相似度阈值">
            <el-slider v-model="form.rule.identity_threshold" :min="0.3" :max="0.9" :step="0.05" show-input />
            <el-text size="small" type="info">在岗人员与登记员工的体貌相似度低于该值视为"非登记人员"（同款工装多时调低）</el-text>
          </el-form-item>
          <el-form-item label="持续时长(s)">
            <el-input-number v-model="form.rule.identity_seconds" :min="5" :max="600" :step="5" />
            <el-text size="small" type="info">非登记人员持续在岗超过该秒数才告警（防路过/短暂顶替误报）</el-text>
          </el-form-item>
          <el-form-item label="员工档案">
            <el-text size="small" :type="staffCount > 0 ? 'success' : 'danger'">
              该事件已登记 {{ staffCount }} 名员工{{ staffCount === 0 ? '（未登记时人岗验证不生效，请到「员工档案」登记当班着装照片）' : '' }}
            </el-text>
            <el-button link type="primary" size="small" @click="staffDrawer = true; fetchStaffList()">管理员工档案</el-button>
          </el-form-item>
        </template>
        <el-form-item label="冷却(s)">
          <el-input-number v-model="form.rule.cooldown" :min="0" :max="3600" />
        </el-form-item>
        <el-form-item label="分析帧率">
          <el-input-number v-model="form.rule.fps" :min="1" :max="10" />
          <el-text v-if="form.rule.detector === 'diving'" size="small" type="info">跳水动作短暂，建议 ≥5fps（CPU 上 pose 推理约 180ms/帧）</el-text>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- ROI 可视化画框器（三处；确认后写回 rule 并同步文本框） -->
    <RoiDrawer :visible="roiDrawerVisible" :model-value="form.rule.roi" mode="polygon" :cameras="drawerCameras" @update:visible="roiDrawerVisible = $event" @update:model-value="onRoiDrawn" />
    <RoiDrawer :visible="poolRoiDrawerVisible" :model-value="form.rule.pool_roi" mode="polygon" :cameras="drawerCameras" @update:visible="poolRoiDrawerVisible = $event" @update:model-value="onPoolRoiDrawn" />
    <RoiDrawer :visible="alarmLineDrawerVisible" :model-value="form.rule.alarm_line" mode="line" :cameras="drawerCameras" @update:visible="alarmLineDrawerVisible = $event" @update:model-value="onAlarmLineDrawn" />

    <!-- 员工体貌档案抽屉（人岗验证用） -->
    <el-drawer v-model="staffDrawer" title="员工体貌档案（人岗验证）" size="420px">
      <el-alert v-if="!editingId" type="warning" :closable="false" style="margin-bottom: 12px">
        请先保存事件后再登记员工档案
      </el-alert>
      <template v-else>
        <el-card shadow="never" style="margin-bottom: 12px">
          <el-form label-width="70px" size="small">
            <el-form-item label="姓名">
              <el-input v-model="staffForm.name" placeholder="员工姓名或工号" maxlength="50" />
            </el-form-item>
            <el-form-item label="班次备注">
              <el-input v-model="staffForm.note" placeholder="如：早班 / 白班工装" maxlength="200" />
            </el-form-item>
            <el-form-item label="着装照片">
              <el-upload
                v-model:file-list="staffFiles"
                :auto-upload="false"
                accept=".jpg,.jpeg,.png,.bmp,.webp"
                :limit="5"
                list-type="picture"
              >
                <el-button size="small" type="primary">选择照片（1~5 张）</el-button>
              </el-upload>
              <el-text size="small" type="info" style="display: block; margin-top: 4px">
                拍当班实际着装（站姿正面/侧面各一张效果更好）；换班或换工装后重新登记覆盖
              </el-text>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="staffSaving" :disabled="!staffForm.name || !staffFiles.length" @click="registerStaff">
                登记 / 更新
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>
        <el-table :data="staffList" size="small" v-loading="staffLoading">
          <el-table-column prop="name" label="姓名" width="90" />
          <el-table-column prop="photo_count" label="照片" width="50" />
          <el-table-column prop="note" label="备注" show-overflow-tooltip />
          <el-table-column label="操作" width="60" align="center">
            <template #default="{ row }">
              <el-button link type="danger" size="small" @click="deleteStaff(row)">删</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
defineProps<{ embedded?: boolean }>()
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'
import { formatDateTime } from '@/utils/time'
import RoiDrawer from '@/components/RoiDrawer.vue'

const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const tableData = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const categories = ref<any[]>([])
const models = ref<any[]>([])

const form = reactive({
  name: '',
  description: '',
  status: '',
  category_codes: [] as string[],
  model_ids: [] as number[],
  rule: {
    threshold: 0.3,
    duration: 0,
    cooldown: 15,
    fps: 2,
    detector: 'object',
    angle_thr: 55,
    min_air_seconds: 0.4,
    miss_seconds: 0.8,
    descent_frac: 0.2,
    head_down_frac: 0.02,
    pool_roi: [] as number[][],
    move_speed_thr: 0.12,
    disappear_seconds: 1.5,
    hand_over_thr: 0.3,
    hand_dur: 1.2,
    hard_dur: 2.2,
    alarm_line: [] as number[][],
    roi: [] as number[][],
    presence_class: 'person',
    absence_seconds: 60,
    absence_repeat: 0,
    schedule: [] as { start: string; end: string }[],
    identity_threshold: 0.55,
    identity_seconds: 30,
    identity_interval: 5,
  },
})

/** 攀爬警戒线：文本 ↔ form.rule.alarm_line 双向同步 */
const alarmLineText = ref('')
const alarmLineError = ref(false)
function parseAlarmLine() {
  const s = alarmLineText.value.trim()
  if (!s) {
    form.rule.alarm_line = []
    alarmLineError.value = false
    return
  }
  try {
    const v = JSON.parse(s)
    const ok =
      Array.isArray(v) &&
      v.length === 2 &&
      v.every((p: any) => Array.isArray(p) && p.length >= 2 && p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1)
    if (!ok) throw new Error('bad')
    form.rule.alarm_line = v.map((p: any) => [Number(p[0]), Number(p[1])])
    alarmLineError.value = false
  } catch {
    alarmLineError.value = true
  }
}

/** 俯视水池多边形：文本 ↔ form.rule.pool_roi 双向同步 */
const poolRoiText = ref('')
const poolRoiError = ref(false)

/** 岗位区域 ROI：文本 ↔ form.rule.roi 双向同步（absence/identity 用） */
const roiText = ref('')
const roiError = ref(false)
function parseRoi() {
  const s = roiText.value.trim()
  if (!s) {
    form.rule.roi = []
    roiError.value = false
    return
  }
  try {
    const v = JSON.parse(s)
    const ok =
      Array.isArray(v) &&
      v.length >= 3 &&
      v.every((p: any) => Array.isArray(p) && p.length >= 2 && p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1)
    if (!ok) throw new Error('bad')
    form.rule.roi = v.map((p: any) => [Number(p[0]), Number(p[1])])
    roiError.value = false
  } catch {
    roiError.value = true
  }
}

// ═══ ROI 可视化画框器（三处复用：岗位区域/水池区域/警戒线）═══
const roiDrawerVisible = ref(false)
const poolRoiDrawerVisible = ref(false)
const alarmLineDrawerVisible = ref(false)
/** 摄像头列表统一拉取一次，三个画框器实例共用（避免各自重复请求） */
const drawerCameras = ref<any[]>([])
async function fetchDrawerCameras() {
  if (drawerCameras.value.length) return
  try {
    const res: any = await request.get('/ai-vision/monitor/cameras-brief')
    drawerCameras.value = res.items || []
  } catch {
    // handled
  }
}
/** 画框器确认后：写回 rule + 同步文本框显示 */
function onRoiDrawn(v: number[][]) {
  form.rule.roi = v
  roiText.value = v.length ? JSON.stringify(v) : ''
  roiError.value = false
}
function onPoolRoiDrawn(v: number[][]) {
  form.rule.pool_roi = v
  poolRoiText.value = v.length ? JSON.stringify(v) : ''
  poolRoiError.value = false
}
function onAlarmLineDrawn(v: number[][]) {
  form.rule.alarm_line = v
  alarmLineText.value = v.length ? JSON.stringify(v) : ''
  alarmLineError.value = false
}
function parsePoolRoi() {
  const s = poolRoiText.value.trim()
  if (!s) {
    form.rule.pool_roi = []
    poolRoiError.value = false
    return
  }
  try {
    const v = JSON.parse(s)
    const ok =
      Array.isArray(v) &&
      v.length >= 3 &&
      v.every((p: any) => Array.isArray(p) && p.length >= 2 && p[0] >= 0 && p[0] <= 1 && p[1] >= 0 && p[1] <= 1)
    if (!ok) throw new Error('bad')
    form.rule.pool_roi = v.map((p: any) => [Number(p[0]), Number(p[1])])
    poolRoiError.value = false
  } catch {
    poolRoiError.value = true
  }
}

/** 切换判定方式：diving/diving_top 自动锁定类别为 person 并调参 */
function onDetectorChange(v: string) {
  if (v === 'diving') {
    form.category_codes = ['person']
    if (form.rule.fps < 5) form.rule.fps = 5
    if (form.rule.cooldown < 10) form.rule.cooldown = 10
    // 自动选中 pose 模型（按名称匹配）
    const pose = models.value.find((m: any) => m.name?.includes('pose'))
    if (pose && !form.model_ids.includes(pose.id)) form.model_ids = [pose.id]
  } else if (v === 'diving_top') {
    form.category_codes = ['person']
    if (form.rule.fps < 5) form.rule.fps = 5
    if (form.rule.cooldown < 10) form.rule.cooldown = 10
    // 俯视人体小，检测阈值降到 0.15（过高漏掉整段轨迹）
    if (form.rule.threshold > 0.2) form.rule.threshold = 0.15
    // 自动选中 COCO 检测模型（名称含 coco 且非 pose）
    const coco = models.value.find((m: any) => m.name?.includes('coco') && !m.name?.includes('pose'))
    if (coco) form.model_ids = [coco.id]
  } else if (v === 'climbing') {
    form.category_codes = ['person']
    if (form.rule.fps < 5) form.rule.fps = 5
    if (form.rule.cooldown < 10) form.rule.cooldown = 10
    const pose = models.value.find((m: any) => m.name?.includes('pose'))
    if (pose) form.model_ids = [pose.id]
  } else if (v === 'absence') {
    // 告警语义类别=离岗；"在岗判定类"由 rule.presence_class 单独控制（检测过滤用）
    form.category_codes = ['absence']
    form.rule.presence_class = form.rule.presence_class || 'person'
    form.rule.fps = 1 // 离岗是分钟级事件，1fps 足够且省 CPU
    if (form.rule.cooldown < 60) form.rule.cooldown = 300
    const coco = models.value.find((m: any) => m.name?.includes('coco') && !m.name?.includes('pose'))
    if (coco) form.model_ids = [coco.id]
  } else if (v === 'identity') {
    form.category_codes = ['wrong_person']
    form.rule.presence_class = 'person' // 人岗验证固定按人体框提体貌特征
    form.rule.fps = 1
    if (form.rule.cooldown < 60) form.rule.cooldown = 300
    const coco = models.value.find((m: any) => m.name?.includes('coco') && !m.name?.includes('pose'))
    if (coco) form.model_ids = [coco.id]
    fetchStaffCount()
  } else {
    if (form.rule.fps > 2) form.rule.fps = 2
  }
}

function statusType(s: string) {
  return { draft: 'info', ready: 'success', running: 'warning', paused: 'info', pending_train: 'danger' }[s] || 'info'
}

function statusText(s: string) {
  return { draft: '草稿', ready: '已发布', running: '运行中', paused: '已暂停', pending_train: '待训练' }[s] || s
}

/** 类别编码 → 中文名（类别库未加载时回退显示 code） */
function categoryName(code: string) {
  return categories.value.find((c) => c.code === code)?.name || code
}

async function fetchCategories() {
  try {
    const res: any = await request.get('/ai-vision/categories', { params: { page: 1, page_size: 100 } })
    categories.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

async function fetchModels() {
  try {
    const res: any = await request.get('/ai-vision/models', { params: { page: 1, page_size: 100 } })
    models.value = res.items || []
  } catch {
    // handled by interceptor
  }
}

// ═══ 员工体貌档案（人岗验证）═══const staffDrawer = ref(false)
const staffLoading = ref(false)
const staffSaving = ref(false)
const staffList = ref<any[]>([])
const staffCount = ref(0)
const staffFiles = ref<any[]>([])
const staffForm = reactive({ name: '', note: '' })

async function fetchStaffList() {
  if (!editingId.value) return
  staffLoading.value = true
  try {
    const res: any = await request.get('/ai-vision/staff', { params: { event_id: editingId.value } })
    staffList.value = res.items || []
    staffCount.value = staffList.value.length
  } catch {
    // handled
  } finally {
    staffLoading.value = false
  }
}

async function fetchStaffCount() {
  const eid = editingId.value
  if (!eid) return
  try {
    const res: any = await request.get('/ai-vision/staff', { params: { event_id: eid } })
    staffCount.value = (res.items || []).length
  } catch {
    // handled
  }
}

async function registerStaff() {
  if (!editingId.value || !staffFiles.value.length) return
  staffSaving.value = true
  try {
    const fd = new FormData()
    fd.append('name', staffForm.name)
    fd.append('event_id', String(editingId.value))
    fd.append('note', staffForm.note)
    for (const f of staffFiles.value) {
      if (f.raw) fd.append('files', f.raw)
    }
    const res: any = await request.post('/ai-vision/staff/register', fd)
    ElMessage.success(res.msg || '登记成功')
    staffForm.name = ''
    staffForm.note = ''
    staffFiles.value = []
    await fetchStaffList()
  } catch (e: any) {
    ElMessage.error(e?.message || '登记失败')
  } finally {
    staffSaving.value = false
  }
}

async function deleteStaff(row: any) {
  try {
    await ElMessageBox.confirm(`删除 ${row.name} 的体貌档案？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  await request.delete(`/ai-vision/staff/${row.id}`)
  ElMessage.success('已删除')
  await fetchStaffList()
}

async function fetchList() {
  loading.value = true
  try {
    const res: any = await request.get('/ai-vision/events', {
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
  form.name = ''
  form.description = ''
  form.status = ''
  form.category_codes = []
  form.model_ids = []
  form.rule = {
    threshold: 0.3,
    duration: 0,
    cooldown: 15,
    fps: 2,
    detector: 'object',
    angle_thr: 55,
    min_air_seconds: 0.4,
    miss_seconds: 0.8,
    descent_frac: 0.2,
    head_down_frac: 0.02,
    pool_roi: [],
    move_speed_thr: 0.12,
    disappear_seconds: 1.5,
    hand_over_thr: 0.3,
    hand_dur: 1.2,
    hard_dur: 2.2,
    alarm_line: [],
    roi: [],
    presence_class: 'person',
    absence_seconds: 60,
    absence_repeat: 0,
    schedule: [],
    identity_threshold: 0.55,
    identity_seconds: 30,
    identity_interval: 5,
  }
  poolRoiText.value = ''
  poolRoiError.value = false
  alarmLineText.value = ''
  alarmLineError.value = false
  roiText.value = ''
  roiError.value = false
  dialogVisible.value = true
}

function openEdit(row: any) {
  editingId.value = row.id
  form.name = row.name
  form.description = row.description
  form.status = row.status || ''
  form.category_codes = [...(row.category_codes || [])]
  form.model_ids = [...(row.model_ids || [])]
  form.rule = {
    threshold: row.rule?.threshold ?? 0.45,
    duration: row.rule?.duration ?? 0,
    cooldown: row.rule?.cooldown ?? 60,
    fps: row.rule?.fps ?? 2,
    detector: row.rule?.detector ?? 'object',
    angle_thr: row.rule?.angle_thr ?? 55,
    min_air_seconds: row.rule?.min_air_seconds ?? 0.4,
    miss_seconds: row.rule?.miss_seconds ?? 0.8,
    descent_frac: row.rule?.descent_frac ?? 0.2,
    head_down_frac: row.rule?.head_down_frac ?? 0.02,
    pool_roi: row.rule?.pool_roi ?? [],
    move_speed_thr: row.rule?.move_speed_thr ?? 0.12,
    disappear_seconds: row.rule?.disappear_seconds ?? 1.5,
    hand_over_thr: row.rule?.hand_over_thr ?? 0.3,
    hand_dur: row.rule?.hand_dur ?? 1.2,
    hard_dur: row.rule?.hard_dur ?? 2.2,
    alarm_line: row.rule?.alarm_line ?? [],
    roi: row.rule?.roi ?? [],
    presence_class: row.rule?.presence_class ?? 'person',
    absence_seconds: row.rule?.absence_seconds ?? 60,
    absence_repeat: row.rule?.absence_repeat ?? 0,
    schedule: Array.isArray(row.rule?.schedule) ? row.rule.schedule.map((s: any) => ({ ...s })) : [],
    identity_threshold: row.rule?.identity_threshold ?? 0.55,
    identity_seconds: row.rule?.identity_seconds ?? 30,
    identity_interval: row.rule?.identity_interval ?? 5,
  }
  poolRoiText.value = row.rule?.pool_roi?.length ? JSON.stringify(row.rule.pool_roi) : ''
  poolRoiError.value = false
  alarmLineText.value = row.rule?.alarm_line?.length ? JSON.stringify(row.rule.alarm_line) : ''
  alarmLineError.value = false
  roiText.value = row.rule?.roi?.length ? JSON.stringify(row.rule.roi) : ''
  roiError.value = false
  if (form.rule.detector === 'identity') fetchStaffCount()
  dialogVisible.value = true
}

/** 提交前兜底：el-input-number 被清空时值为 null，后端 schema 按类型拒绝
 *  （422 且错误详情为数组、提示不友好）——null 一律回退默认值（自查 2026-09-30 #2） */
function ruleForSubmit() {
  const d: Record<string, number> = {
    threshold: 0.45, duration: 0, cooldown: 60, fps: 2,
    absence_seconds: 60, absence_repeat: 0,
    identity_threshold: 0.55, identity_seconds: 30, identity_interval: 5,
  }
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(form.rule)) {
    out[k] = v === null && k in d ? d[k] : v
  }
  return out
}

async function handleSave() {
  if (!form.name.trim() || form.category_codes.length === 0) {
    ElMessage.warning('请填写事件名称并选择至少一个类别')
    return
  }
  // diving 模式前置校验：必须绑定 pose 模型
  if (form.rule.detector === 'diving') {
    const bound = models.value.filter((m: any) => form.model_ids.includes(m.id))
    if (!bound.some((m: any) => m.name?.includes('pose'))) {
      ElMessage.warning('跳水动作识别需要绑定姿态模型（yolo11n-pose），请在"绑定模型"中选择')
      return
    }
  }
  // diving_top 模式前置校验：必须绑定 COCO 检测模型 + ROI 格式合法
  if (form.rule.detector === 'diving_top') {
    if (poolRoiError.value) {
      ElMessage.warning('水池区域格式错误，需为 [[x,y],...] 且坐标在 0~1')
      return
    }
    const bound = models.value.filter((m: any) => form.model_ids.includes(m.id))
    if (!bound.some((m: any) => !m.name?.includes('pose'))) {
      ElMessage.warning('高空俯视跳水需要绑定目标检测模型（yolo11n-coco），请在"绑定模型"中选择')
      return
    }
  }
  // climbing 模式前置校验：必须绑定 pose 模型 + 警戒线格式合法
  if (form.rule.detector === 'climbing') {
    if (alarmLineError.value) {
      ElMessage.warning('警戒线格式错误，需为 [[x1,y1],[x2,y2]] 且坐标在 0~1')
      return
    }
    const bound = models.value.filter((m: any) => form.model_ids.includes(m.id))
    if (!bound.some((m: any) => m.name?.includes('pose'))) {
      ElMessage.warning('攀爬翻越识别需要绑定姿态模型（yolo11n-pose），请在"绑定模型"中选择')
      return
    }
  }
  // absence / identity 模式前置校验：绑定模型类别表含在岗判定类 + ROI 格式
  if (form.rule.detector === 'absence' || form.rule.detector === 'identity') {
    if (roiError.value) {
      ElMessage.warning('岗位区域格式错误，需为 [[x,y],...] 且坐标在 0~1')
      return
    }
    const pc = form.rule.detector === 'identity' ? 'person' : form.rule.presence_class || 'person'
    const bound = models.value.filter((m: any) => form.model_ids.includes(m.id))
    const hasClass = bound.some((m: any) => {
      try {
        return Object.values(JSON.parse(m.category_map || '{}')).includes(pc)
      } catch {
        return false
      }
    })
    if (!hasClass) {
      const cn = categories.value.find((c) => c.code === pc)?.name || pc
      ElMessage.warning(`需绑定类别表含「${cn}」的检测模型${pc !== 'person' ? `（如自训的 ${pc} 产物模型）` : '（如 yolo11n-coco）'}`)
      return
    }
    if (form.rule.detector === 'identity' && editingId.value && staffCount.value === 0) {
      ElMessage.warning('人岗验证需要先登记至少一名员工档案（表单内"管理员工档案"）；未登记时该事件不会告警')
      return
    }
  }
  saving.value = true
  try {
    if (editingId.value) {
      await request.put(`/ai-vision/events/${editingId.value}`, {
        name: form.name,
        description: form.description,
        category_codes: form.category_codes,
        model_ids: form.model_ids,
        rule: ruleForSubmit(),
      })
      ElMessage.success('更新成功')
    } else {
      await request.post('/ai-vision/events', {
        name: form.name,
        description: form.description,
        category_codes: form.category_codes,
        model_ids: form.model_ids,
        rule: ruleForSubmit(),
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

async function handlePublish(row: any) {
  // 前端预校验：未绑模型直接阻止，避免启动任务时才报错
  if (!row.model_ids?.length) {
    ElMessage.warning(`事件「${row.name}」还未绑定检测模型，请先编辑并选择模型`)
    return
  }
  await request.post(`/ai-vision/events/${row.id}/publish`)
  ElMessage.success('已发布，可在任务编排中引用')
  await fetchList()
}

/** 跳转监控台任务管理并预选当前事件（任务页嵌入监控台 tab，共享 query） */
function createTask(row: any) {
  router.push({ path: '/ai-vision/monitor', query: { tab: 'tasks', event_id: row.id, event_name: row.name } })
}

async function handleRetire(row: any) {
  await request.post(`/ai-vision/events/${row.id}/retire`)
  ElMessage.success('已退役')
  await fetchList()
}

async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除事件「${row.name}」吗？`, '提示', { type: 'warning' })
  } catch {
    return // 取消
  }
  await request.delete(`/ai-vision/events/${row.id}`)
  ElMessage.success('删除成功')
  await fetchList()
}

onMounted(() => {
  fetchList()
  fetchCategories()
  fetchModels()
})
</script>

<style scoped>
.event-page { padding: 20px; }
.page-header { margin-bottom: 16px; }
.page-header h2 { margin: 0 0 4px; font-size: 20px; }
.page-header .text-muted { color: #999; font-size: 13px; margin: 0; }
.toolbar { display: flex; gap: 8px; }
.pagination { margin-top: 16px; display: flex; justify-content: flex-end; }
.cat-tag { margin-right: 4px; }
.empty-tip { color: #909399; font-size: 13px; margin: 0 0 12px; }
</style>