<template>
	<v-container fluid class="report-center pa-6">
		<div class="d-flex align-center flex-wrap ga-3 mb-4">
			<div>
				<h1 class="text-h5 font-weight-bold">报告与诊断</h1>
				<p class="text-body-2 text-medium-emphasis mt-1">
					查看指标口径，核查诊断证据，跟踪周期报告与改进行动。
				</p>
			</div>
			<v-spacer /><v-chip color="primary" variant="tonal">{{
				agentId ? `当前智能体 ${agentId}` : '尚未选择智能体'
			}}</v-chip>
		</div>
		<v-alert type="info" variant="tonal" class="mb-4"
			>诊断结果最高为“观测关联”。模型解释、时间先后和目标达成均不能独立确认因果根因；现场或干预验证仍须提供独立证据。</v-alert
		>
		<v-row dense class="mb-2"
			><v-col cols="12" md="7"
				><v-text-field
					v-model="apiKey"
					label="智能体 API 密钥（已启用密钥保护时填写）"
					type="password"
					autocomplete="off"
					variant="outlined"
					density="compact"
					hide-details="auto"
					hint="只保留在页面内存；切换智能体或离开页面时清除。"
					persistent-hint /></v-col
			><v-col cols="12" md="5" class="d-flex align-center"
				><v-btn
					prepend-icon="mdi-refresh"
					:disabled="!agentId"
					:loading="busy.refresh"
					@click="refreshLists"
					>刷新报告与行动列表</v-btn
				></v-col
			></v-row
		>
		<v-alert
			v-if="error"
			type="error"
			closable
			class="my-4"
			@click:close="error = ''"
			>{{ error }}</v-alert
		>
		<v-alert
			v-if="notice"
			type="success"
			closable
			class="my-4"
			@click:close="notice = ''"
			>{{ notice }}</v-alert
		>
		<v-alert v-if="!agentId" type="warning" variant="tonal" class="my-4"
			>请先在左侧导航栏选择智能体；指标字典仍可查看。</v-alert
		>
		<v-tabs v-model="tab" color="primary" class="mt-4 border-b"
			><v-tab value="metrics">指标字典</v-tab
			><v-tab value="diagnosis">受控诊断</v-tab
			><v-tab value="schedules">周期报告</v-tab
			><v-tab value="actions">改进行动</v-tab></v-tabs
		>
		<v-tabs-window v-model="tab" class="pt-5">
			<v-tabs-window-item value="metrics">
				<v-select
					v-model="dataset"
					:items="datasetOptions"
					item-title="title"
					item-value="value"
					label="数据集"
					variant="outlined"
					density="compact"
					class="dataset-select"
				/>
				<v-progress-linear
					v-if="busy[`metrics-${dataset}`]"
					indeterminate
					color="primary"
				/>
				<v-alert v-else-if="!metrics.length" type="info" variant="tonal"
					>尚未读取到指标定义；请确认数据集配置或重新选择。</v-alert
				>
				<v-row
					><v-col
						v-for="metric in metrics"
						:key="metric.metric_id"
						cols="12"
						lg="6"
						><v-card variant="outlined" class="h-100 pa-4"
							><div class="d-flex align-center ga-2 mb-2">
								<h2 class="text-subtitle-1 font-weight-bold">
									{{ metric.name }}
								</h2>
								<v-chip size="small">v{{ metric.version }}</v-chip
								><v-chip size="small" variant="tonal">{{ metric.unit }}</v-chip>
							</div>
							<p class="text-body-2 mb-3">{{ metric.description }}</p>
							<dl class="definition">
								<dt>指标 ID</dt>
								<dd>{{ metric.metric_id }}</dd>
								<dt>观察粒度</dt>
								<dd>{{ metric.grain }}</dd>
								<dt>分子 / 数值口径</dt>
								<dd>{{ metric.numerator }}</dd>
								<dt>分母</dt>
								<dd>{{ metric.denominator || '不适用（非比率指标）' }}</dd>
								<dt>时间规则</dt>
								<dd>{{ metric.time_policy }}</dd>
								<dt>缺失值规则</dt>
								<dd>{{ metric.null_policy }}</dd>
								<dt>排除条件</dt>
								<dd>{{ metric.exclusions?.join('；') || '无额外注册条件' }}</dd>
								<dt>口径负责人</dt>
								<dd>{{ metric.owner }}</dd>
								<dt>定义指纹</dt>
								<dd class="evidence-id">{{ metric.semantic_hash }}</dd>
							</dl></v-card
						></v-col
					></v-row
				>
			</v-tabs-window-item>
			<v-tabs-window-item value="diagnosis">
				<v-card variant="outlined" class="pa-4 mb-4"
					><h2 class="text-subtitle-1 font-weight-bold mb-2">
						使用已成功执行的查询证据
					</h2>
					<p class="text-body-2 text-medium-emphasis mb-4">
						从分析证据页或报告来源附录复制运行 ID 与 query_result 证据
						ID。结局与暴露必须已有明确 0/1 定义；不会自动把缺失值补为
						0，也不执行额外 SQL。
					</p>
					<v-row dense
						><v-col cols="12" md="6"
							><v-text-field
								v-model="diagnostic.runId"
								label="分析运行 ID"
								variant="outlined" /></v-col
						><v-col cols="12" md="6"
							><v-text-field
								v-model="diagnostic.artifactId"
								label="query_result 证据 ID"
								variant="outlined" /></v-col
						><v-col cols="12" md="4"
							><v-text-field
								v-model="diagnostic.outcome"
								label="结局字段（0/1）"
								variant="outlined" /></v-col
						><v-col cols="12" md="4"
							><v-text-field
								v-model="diagnostic.exposure"
								label="暴露字段（0/1）"
								variant="outlined" /></v-col
						><v-col cols="12" md="4"
							><v-text-field
								v-model="diagnostic.entity"
								label="设备 / 实体 ID 字段"
								variant="outlined" /></v-col
						><v-col cols="12" md="6"
							><v-text-field
								v-model="diagnostic.keys"
								label="唯一观察单元键（多个用逗号分隔）"
								hint="例如 device_id, observed_at；重复键会阻断风险分母计算。"
								persistent-hint
								variant="outlined" /></v-col
						><v-col cols="12" md="6"
							><v-text-field
								v-model="diagnostic.context"
								label="混杂候选字段（可选，逗号分隔）"
								hint="本次只检查缺失，不执行混杂调整。"
								persistent-hint
								variant="outlined" /></v-col
					></v-row>
					<v-expansion-panels variant="accordion" class="my-3"
						><v-expansion-panel
							title="事件先后与分析窗口（可选，但推断方向需这些证据）"
							><v-expansion-panel-text
								><v-row dense
									><v-col cols="12" md="4"
										><v-text-field
											v-model="diagnostic.observedAt"
											label="观察时间字段"
											variant="outlined" /></v-col
									><v-col cols="12" md="4"
										><v-text-field
											v-model="diagnostic.exposureAt"
											label="暴露起点时间字段"
											variant="outlined" /></v-col
									><v-col cols="12" md="4"
										><v-text-field
											v-model="diagnostic.outcomeAt"
											label="结局事件时间字段"
											variant="outlined" /></v-col
									><v-col cols="12" md="6"
										><v-text-field
											v-model="diagnostic.windowStart"
											label="窗口开始（含，带时区）"
											placeholder="2026-10-01T00:00:00+08:00"
											variant="outlined" /></v-col
									><v-col cols="12" md="6"
										><v-text-field
											v-model="diagnostic.windowEnd"
											label="窗口结束（不含，带时区）"
											placeholder="2026-10-08T00:00:00+08:00"
											variant="outlined" /></v-col></v-row></v-expansion-panel-text></v-expansion-panel
					></v-expansion-panels>
					<v-btn
						color="primary"
						variant="flat"
						:disabled="!agentId"
						:loading="busy.diagnose"
						@click="submitDiagnosis"
						>核查证据并诊断</v-btn
					>
				</v-card>
				<v-card v-if="diagnosis" variant="outlined" class="pa-4"
					><div class="d-flex align-center ga-2 mb-3">
						<h2 class="text-subtitle-1 font-weight-bold">诊断结果</h2>
						<v-chip
							:color="
								diagnosis.content.qualityGate.status === 'passed'
									? 'success'
									: 'warning'
							"
							>{{
								diagnosis.content.qualityGate.status === 'passed'
									? '数据检查通过'
									: '数据检查阻断计算'
							}}</v-chip
						><v-chip variant="tonal">{{
							diagnosis.content.evidenceLevel === 'association'
								? '观测关联'
								: '观察事实'
						}}</v-chip>
					</div>
					<p class="text-body-2 mb-2">
						已保存
						{{ diagnosis.content.qualityGate.rowCount }} 条观察，重复观察键
						{{ diagnosis.content.qualityGate.duplicateKeys }} 条。
					</p>
					<p class="text-caption evidence-id mb-4">
						诊断证据 ID：{{ diagnosis.artifactId }}
					</p>
					<v-alert
						v-for="(issue, index) in diagnosis.content.qualityGate.issues"
						:key="index"
						:type="issue.severity === 'blocking' ? 'error' : 'warning'"
						variant="tonal"
						class="mb-2"
						>{{ issue.message }}</v-alert
					>
					<div v-if="diagnosis.content.association" class="mt-4">
						<v-table density="compact"
							><thead>
								<tr>
									<th>组别</th>
									<th>结局观察数</th>
									<th>观察分母</th>
									<th>观测比例</th>
								</tr>
							</thead>
							<tbody>
								<tr
									v-for="group in diagnosis.content.association.groups"
									:key="group.exposure"
								>
									<td>{{ group.exposure === 1 ? '暴露组' : '未暴露组' }}</td>
									<td>{{ group.events }}</td>
									<td>{{ group.observations }}</td>
									<td>{{ percent(group.risk) }}</td>
								</tr>
							</tbody></v-table
						>
						<p class="text-body-2 mt-3">
							比例差：{{
								(diagnosis.content.association.riskDifference * 100).toFixed(2)
							}}
							个百分点；相对比例：{{
								diagnosis.content.association.relativeRisk?.toFixed(3) ??
								'不可计算（未暴露组结局为 0）'
							}}
						</p>
						<p class="text-caption mt-1">
							{{
								diagnosis.content.association.riskDifference95Interval
									? `95% 观测比例差区间：${diagnosis.content.association.riskDifference95Interval.map(percent).join(' 至 ')}，基于独立观察单元假设。`
									: '缺少独立观察单元支持，不提供独立行置信区间。'
							}}
						</p>
					</div>
					<v-alert type="info" variant="tonal" class="mt-4">{{
						diagnosis.content.causalReadiness.reason
					}}</v-alert>
					<h3 class="text-subtitle-2 mt-4 mb-2">继续验证需要的输入</h3>
					<ul class="missing-inputs">
						<li v-for="item in diagnosis.content.missingInputs" :key="item">
							{{ item }}
						</li>
					</ul> </v-card
				><v-alert v-else type="info" variant="tonal"
					>尚无诊断结果。仅接受当前智能体、当前运行内带成功执行记录的查询证据。</v-alert
				>
			</v-tabs-window-item>
			<v-tabs-window-item value="schedules">
				<v-alert
					v-if="workerStatus"
					:type="workerStatus.running ? 'success' : 'warning'"
					variant="tonal"
					class="mb-3"
					>{{
						workerStatus.running
							? '后台周期调度正在运行；列表刷新只读取状态。'
							: workerStatus.enabled
								? '后台调度配置已启用，但当前没有运行；请检查后端服务。'
								: '后台周期调度当前未启用；创建定义不会自动定时运行。可手动触发并显式推进。'
					}}</v-alert
				>
				<v-alert type="info" variant="tonal" class="mb-4"
					>周期按上海时区已完成的自然日、自然周或自然月取数；当前仅支持工业
					PdM。自动到期执行需后台调度已启用；可以显式手动触发。源数据水位未覆盖该周期时会拒绝出报告。</v-alert
				>
				<v-card variant="outlined" class="pa-4 mb-4"
					><h2 class="text-subtitle-1 font-weight-bold mb-3">新建周期报告</h2>
					<v-row dense
						><v-col cols="12" md="6"
							><v-text-field
								v-model="schedule.name"
								label="报告名称"
								variant="outlined" /></v-col
						><v-col cols="12" md="3"
							><v-select
								v-model="schedule.frequency"
								:items="frequencies"
								label="报告周期"
								variant="outlined" /></v-col
						><v-col cols="12" md="3"
							><v-text-field
								v-model="schedule.time"
								type="time"
								label="执行时间（上海）"
								variant="outlined" /></v-col
						><v-col cols="12"
							><v-text-field
								v-model="schedule.question"
								label="分析问题"
								variant="outlined" /></v-col
						><v-col cols="12"
							><v-select
								v-model="schedule.metrics"
								:items="pdmMetrics"
								item-title="name"
								item-value="metric_id"
								multiple
								chips
								label="概览指标（1–4 个）"
								variant="outlined" /></v-col></v-row
					><v-btn
						color="primary"
						variant="flat"
						:disabled="!agentId"
						:loading="busy.createSchedule"
						@click="createSchedule"
						>创建周期报告</v-btn
					></v-card
				>
				<v-text-field
					v-model="periodEnd"
					type="date"
					label="手动历史周期结束边界（可选，不含该日）"
					hint="周报须选星期一；月报须选每月 1 日。留空使用最近已完成周期。"
					persistent-hint
					variant="outlined"
					class="mb-3"
				/>
				<v-alert v-if="!schedules.length" type="info" variant="tonal"
					>当前智能体还没有周期报告。</v-alert
				><v-card
					v-for="item in schedules"
					:key="item.schedule_id"
					variant="outlined"
					class="pa-4 mb-3"
					><div class="d-flex align-center flex-wrap ga-3">
						<div>
							<h3 class="text-subtitle-1">{{ item.spec.name }}</h3>
							<p class="text-caption">
								{{ frequencyLabel(item.spec.frequency) }} ·
								{{ String(item.spec.hour).padStart(2, '0') }}:{{
									String(item.spec.minute).padStart(2, '0')
								}}
								· {{ item.spec.timezone }}
							</p>
						</div>
						<v-chip>{{ reportStatus(item.status) }}</v-chip
						><v-spacer /><v-btn
							size="small"
							:loading="busy[`pause-${item.schedule_id}`]"
							@click="pauseSchedule(item)"
							>{{ item.status === 'ACTIVE' ? '暂停' : '启用' }}</v-btn
						><v-btn
							size="small"
							color="primary"
							:loading="busy[`trigger-${item.schedule_id}`]"
							@click="triggerSchedule(item)"
							>手动触发</v-btn
						>
					</div>
					<p class="text-caption evidence-id mt-2">
						版本指纹：{{ item.definition_hash }}
					</p></v-card
				>
				<h2 class="text-subtitle-1 font-weight-bold mt-5 mb-3">执行记录</h2>
				<v-alert v-if="!runs.length" type="info" variant="tonal"
					>还没有执行记录。重复触发相同周期与定义版本不会创建重复报告。</v-alert
				><v-card
					v-for="run in runs"
					:key="run.run_id"
					variant="outlined"
					class="pa-4 mb-3"
					><div class="d-flex align-center flex-wrap ga-2">
						<v-chip
							:color="
								run.status === 'COMPLETED'
									? 'success'
									: run.status === 'FAILED'
										? 'error'
										: 'primary'
							"
							>{{ reportStatus(run.status) }}</v-chip
						><span class="text-body-2"
							>{{ run.spec.period.start_date }} 至
							{{ run.spec.period.end_date }} · 已尝试
							{{ run.attempts }} 次</span
						><v-spacer /><v-btn
							v-if="!reportRunTerminal(run.status)"
							size="small"
							:loading="busy[`reconcile-${run.run_id}`]"
							@click="reconcileRun(run)"
							>核查并推进执行</v-btn
						>
					</div>
					<p class="text-caption evidence-id mt-2">
						报告运行：{{ run.run_id }}<br />分析运行：{{
							run.task_id || '尚未派发'
						}}
					</p>
					<v-alert
						v-if="run.error"
						type="warning"
						variant="tonal"
						class="mt-3"
						>{{ run.error }}</v-alert
					><v-btn
						v-if="run.task_id"
						class="mt-3"
						size="small"
						@click="openRun(run)"
						>查看运行证据 / 报告</v-btn
					></v-card
				>
				<v-dialog v-model="runDialog" max-width="960"
					><v-card
						><v-card-title>运行证据与报告</v-card-title
						><v-card-text
							><div
								v-if="runAnswer"
								class="report-content"
								v-html="reportHtml"
							/>
							<v-alert v-else type="info" variant="tonal" class="mb-3"
								>该运行尚无可发布报告，请查看运行状态与证据明细。</v-alert
							>
							<v-expansion-panels class="mt-4"
								><v-expansion-panel
									title="查看运行证据明细（纯文本，不执行脚本）"
									><v-expansion-panel-text>
										<pre class="run-detail">{{ runDetail }}</pre>
									</v-expansion-panel-text></v-expansion-panel
								></v-expansion-panels
							></v-card-text
						><v-card-actions
							><v-spacer /><v-btn @click="runDialog = false"
								>关闭</v-btn
							></v-card-actions
						></v-card
					></v-dialog
				>
			</v-tabs-window-item>
			<v-tabs-window-item value="actions">
				<v-alert type="info" variant="tonal" class="mb-4"
					>行动基线与实测值都要绑定已完成分析的注册指标单元格。系统按目标规则验收，但不把目标达成解释为行动的因果收益；负责人姓名也不是独立身份认证。</v-alert
				>
				<v-card variant="outlined" class="pa-4 mb-4"
					><h2 class="text-subtitle-1 font-weight-bold mb-3">新建改进行动</h2>
					<v-row dense
						><v-col cols="12" md="6"
							><v-text-field
								v-model="action.title"
								label="行动标题"
								variant="outlined" /></v-col
						><v-col cols="12" md="3"
							><v-text-field
								v-model="action.owner"
								label="负责人"
								variant="outlined" /></v-col
						><v-col cols="12" md="3"
							><v-text-field
								v-model="action.dueDate"
								type="date"
								label="验收日期"
								variant="outlined" /></v-col
						><v-col cols="12" md="6"
							><v-text-field
								v-model="action.baseline.runId"
								label="基线分析运行 ID"
								variant="outlined" /></v-col
						><v-col cols="12" md="6"
							><v-text-field
								v-model="action.baseline.artifactId"
								label="基线 query_result 证据 ID"
								variant="outlined" /></v-col
						><v-col cols="12" md="4"
							><v-text-field
								v-model="action.baseline.metricId"
								label="基线注册指标 ID"
								variant="outlined" /></v-col
						><v-col cols="12" md="2"
							><v-text-field
								v-model="action.baseline.rowIndex"
								type="number"
								min="0"
								label="结果行号（从 0 起）"
								variant="outlined" /></v-col
						><v-col cols="12" md="3"
							><v-select
								v-model="action.acceptance"
								:items="acceptanceOptions"
								label="验收规则"
								variant="outlined" /></v-col
						><v-col cols="12" md="3"
							><v-text-field
								v-model="action.target"
								label="目标值（按指标单位）"
								variant="outlined" /></v-col
						><v-col cols="12"
							><v-textarea
								v-model="action.note"
								label="验收说明（负荷、观察期、取数范围等可比性）"
								rows="2"
								variant="outlined" /></v-col></v-row
					><v-btn
						color="primary"
						variant="flat"
						:disabled="!agentId"
						:loading="busy.createAction"
						@click="createAction"
						>保存行动与基线证据</v-btn
					></v-card
				>
				<v-alert v-if="!actions.length" type="info" variant="tonal"
					>当前智能体还没有改进行动。</v-alert
				><v-card
					v-for="item in actions"
					:key="item.action_id"
					variant="outlined"
					class="pa-4 mb-3"
					><div class="d-flex align-center flex-wrap ga-3">
						<h3 class="text-subtitle-1">{{ item.spec.title }}</h3>
						<v-chip>{{ reportStatus(item.status) }}</v-chip
						><v-spacer /><v-btn
							v-if="item.status !== 'CLOSED'"
							size="small"
							@click="selectReview(item)"
							>填写实测与复核</v-btn
						>
					</div>
					<p class="text-body-2 mt-2">
						负责人：{{ item.owner }} · 验收日期：{{ item.due_date }}
					</p>
					<p class="text-body-2 mt-1">
						{{ item.baseline.point.metric_id }}：基线
						{{ item.baseline.value }} →
						{{ acceptanceLabel(item.spec.acceptance) }} {{ item.spec.target }}
					</p>
					<p v-if="item.review.actual" class="text-body-2 mt-1">
						实测 {{ item.review.actual.value }} ·
						{{ item.review.target_met ? '目标达到' : '目标未达到' }} · 基线差值
						{{ item.review.difference_from_baseline }}
					</p>
					<p class="text-caption mt-2">
						{{ item.spec.acceptance_note }}
					</p></v-card
				>
				<v-dialog v-model="reviewDialog" max-width="760"
					><v-card
						><v-card-title
							>行动复核：{{ reviewAction?.spec.title }}</v-card-title
						><v-card-text
							><v-alert type="info" variant="tonal" class="mb-3"
								>实测证据须与基线指标版本、数据集及分组维度一致。必须使用新的单元格，不能复用基线。</v-alert
							><v-row dense
								><v-col cols="12" md="6"
									><v-text-field
										v-model="review.actual.runId"
										label="实测分析运行 ID"
										variant="outlined" /></v-col
								><v-col cols="12" md="6"
									><v-text-field
										v-model="review.actual.artifactId"
										label="实测 query_result 证据 ID"
										variant="outlined" /></v-col
								><v-col cols="12" md="8"
									><v-text-field
										v-model="review.actual.metricId"
										label="注册指标 ID"
										variant="outlined" /></v-col
								><v-col cols="12" md="4"
									><v-text-field
										v-model="review.actual.rowIndex"
										type="number"
										min="0"
										label="结果行号（从 0 起）"
										variant="outlined" /></v-col
								><v-col cols="12"
									><v-text-field
										v-model="review.owner"
										label="复核负责人（须与行动负责人一致）"
										variant="outlined" /></v-col
								><v-col cols="12"
									><v-textarea
										v-model="review.note"
										label="复核说明（检查观察范围与负荷是否可比）"
										rows="3"
										variant="outlined" /></v-col
								><v-col cols="12"
									><v-checkbox
										v-model="review.approved"
										label="负责人已核查证据并同意验收（仅在达到目标时关闭行动）" /></v-col></v-row></v-card-text
						><v-card-actions
							><v-spacer /><v-btn @click="reviewDialog = false">取消</v-btn
							><v-btn
								color="primary"
								:loading="busy.reviewAction"
								@click="submitReview"
								>保存复核</v-btn
							></v-card-actions
						></v-card
					></v-dialog
				>
			</v-tabs-window-item>
		</v-tabs-window>
	</v-container>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import axios from 'axios';
import DOMPurify from 'dompurify';
import { renderMarkdownContent } from '~/utils/markdown';
import reportService, {
	reportErrorMessage,
	type Diagnosis,
	type MetricContract,
	type ReportAction,
	type ReportRun,
	type ReportSchedule,
	type SchedulerStatus,
} from '~/services/reportOperations';
import {
	actionPayload,
	CoalescedRefreshQueue,
	diagnosisPayload,
	evidencePoint,
	percent,
	reportAnswer,
	reportRunTerminal,
	reportStatus,
	ReportRequestScope,
	schedulePayload,
	type DiagnosisInput,
	type EvidenceInput,
} from '~/utils/reportOperations';

const route = useRoute();
const agentId = computed(() => {
	const id = Number(route.query.agentId);
	return Number.isSafeInteger(id) && id > 0 ? id : 0;
});
const apiKey = ref('');
const tab = ref('metrics');
const dataset = ref('industrial_pdm');
const datasetOptions = [
	{ title: '工业设备 PdM', value: 'industrial_pdm' },
	{ title: '匿名 SCANIA Component X', value: 'scania_component_x' },
];
const metrics = ref<MetricContract[]>([]),
	pdmMetrics = ref<MetricContract[]>([]);
const schedules = ref<ReportSchedule[]>([]),
	runs = ref<ReportRun[]>([]),
	actions = ref<ReportAction[]>([]);
const diagnosis = ref<Diagnosis | null>(null);
const workerStatus = ref<SchedulerStatus | null>(null);
const busy = ref<Record<string, boolean>>({}),
	error = ref(''),
	notice = ref('');
const scope = new ReportRequestScope();
const emptyEvidence = (): EvidenceInput => ({
	runId: '',
	artifactId: '',
	metricId: '',
	rowIndex: 0,
});
const emptyDiagnosis = (): DiagnosisInput => ({
	runId: '',
	artifactId: '',
	outcome: '',
	exposure: '',
	entity: '',
	keys: '',
	observedAt: '',
	exposureAt: '',
	outcomeAt: '',
	context: '',
	windowStart: '',
	windowEnd: '',
});
const diagnostic = ref(emptyDiagnosis());
const frequencies = [
	{ title: '日报', value: 'daily' },
	{ title: '周报', value: 'weekly' },
	{ title: '月报', value: 'monthly' },
];
const frequencyLabel = (value: string) =>
	frequencies.find((item) => item.value === value)?.title || value;
const acceptanceOptions = [
	{ title: '不高于', value: 'at_most' },
	{ title: '不低于', value: 'at_least' },
	{ title: '等于', value: 'equal' },
];
const acceptanceLabel = (value: string) =>
	acceptanceOptions.find((item) => item.value === value)?.title || value;
const schedule = ref({
	name: '',
	question: '分析工业设备在报告周期内的设备数、故障次数及故障设备占比。',
	frequency: 'daily',
	time: '08:00',
	metrics: ['device_count', 'failure_count', 'failure_rate_pct'],
});
const periodEnd = ref('');
const action = ref({
	title: '',
	owner: '',
	dueDate: '',
	target: '',
	acceptance: 'at_most',
	note: '',
	baseline: emptyEvidence(),
});
const reviewDialog = ref(false),
	reviewAction = ref<ReportAction | null>(null);
const review = ref({
	actual: emptyEvidence(),
	owner: '',
	note: '',
	approved: false,
});
const runDialog = ref(false),
	runDetail = ref('');
const runAnswer = ref('');
const reportHtml = computed(() =>
	DOMPurify.sanitize(renderMarkdownContent(runAnswer.value)),
);

async function perform<T>(
	key: string,
	work: (agent: number, credential: string, signal: AbortSignal) => Promise<T>,
	success: (result: T) => void,
	requireAgent = true,
) {
	if (busy.value[key]) return;
	if (requireAgent && !agentId.value) {
		error.value = '请先在左侧导航栏选择智能体。';
		return;
	}
	const captured = scope.capture(),
		agent = agentId.value,
		credential = apiKey.value;
	busy.value[key] = true;
	error.value = '';
	try {
		const result = await work(agent, credential, captured.signal);
		if (scope.isCurrent(captured.generation)) success(result);
	} catch (failure) {
		if (scope.isCurrent(captured.generation) && !axios.isCancel(failure))
			error.value = reportErrorMessage(failure);
	} finally {
		if (scope.isCurrent(captured.generation)) busy.value[key] = false;
	}
}
function loadMetrics() {
	const selected = dataset.value;
	void perform(
		`metrics-${selected}`,
		async (_agent, _key, signal) => ({
			selected: await reportService.metrics(selected, signal),
			pdm:
				selected === 'industrial_pdm'
					? null
					: await reportService.metrics('industrial_pdm', signal),
		}),
		(value) => {
			if (selected === dataset.value) metrics.value = value.selected;
			pdmMetrics.value = value.pdm || value.selected;
		},
		false,
	);
}
async function fetchLists() {
	await perform(
		'refresh',
		(agent, key, signal) =>
			Promise.all([
				reportService.schedules(agent, key, signal),
				reportService.runs(agent, key, signal),
				reportService.actions(agent, key, signal),
				reportService.status(agent, key, signal),
			]),
		(values) => {
			schedules.value = values[0];
			runs.value = values[1];
			actions.value = values[2];
			workerStatus.value = values[3];
		},
	);
}
const listRefresh = new CoalescedRefreshQueue(fetchLists);
function refreshLists() {
	return listRefresh.request();
}
function submitDiagnosis() {
	void perform(
		'diagnose',
		(agent, key, signal) =>
			reportService.diagnose(
				diagnosisPayload(agent, diagnostic.value),
				key,
				signal,
			),
		(value) => {
			diagnosis.value = value;
			notice.value = '诊断与数据检查证据已保存。';
		},
	);
}
function createSchedule() {
	void perform(
		'createSchedule',
		(agent, key, signal) =>
			reportService.createSchedule(
				schedulePayload(
					schedule.value.name,
					schedule.value.question,
					schedule.value.frequency,
					schedule.value.time,
					schedule.value.metrics,
				),
				agent,
				key,
				signal,
			),
		() => {
			notice.value = '周期报告定义已保存；执行时会校验指标版本与源数据水位。';
			void refreshLists();
		},
	);
}
function pauseSchedule(item: ReportSchedule) {
	void perform(
		`pause-${item.schedule_id}`,
		(agent, key, signal) =>
			reportService.pause(
				item.schedule_id,
				item.status === 'ACTIVE',
				agent,
				key,
				signal,
			),
		() => {
			void refreshLists();
		},
	);
}
function triggerSchedule(item: ReportSchedule) {
	void perform(
		`trigger-${item.schedule_id}`,
		(agent, key, signal) =>
			reportService.trigger(
				item.schedule_id,
				periodEnd.value,
				agent,
				key,
				signal,
			),
		() => {
			notice.value = '已提交该周期执行请求；请查看执行记录。';
			void refreshLists();
		},
	);
}
function reconcileRun(run: ReportRun) {
	void perform(
		`reconcile-${run.run_id}`,
		(agent, key, signal) =>
			reportService.refreshRun(run.run_id, agent, key, signal),
		() => {
			notice.value = '已显式核查并推进运行；重试仍受次数与超时规则限制。';
			void refreshLists();
		},
	);
}
function openRun(run: ReportRun) {
	void perform(
		'runDetail',
		(agent, key, signal) => reportService.run(run.run_id, agent, key, signal),
		(value) => {
			runDetail.value = JSON.stringify(value.result, null, 2);
			runAnswer.value = reportAnswer(value.result);
			runDialog.value = true;
		},
	);
}
function createAction() {
	void perform(
		'createAction',
		(agent, key, signal) =>
			reportService.createAction(
				actionPayload(action.value),
				agent,
				key,
				signal,
			),
		() => {
			notice.value = '行动及其已执行的基线证据已保存。';
			void refreshLists();
		},
	);
}
function selectReview(item: ReportAction) {
	reviewAction.value = item;
	review.value = {
		actual: { ...emptyEvidence(), metricId: item.baseline.point.metric_id },
		owner: item.owner,
		note: '',
		approved: false,
	};
	reviewDialog.value = true;
}
function submitReview() {
	const item = reviewAction.value;
	if (!item) return;
	void perform(
		'reviewAction',
		(agent, key, signal) => {
			if (!review.value.note.trim() || !review.value.owner.trim())
				throw new Error('请填写复核负责人及复核说明。');
			return reportService.review(
				item.action_id,
				{
					actual: evidencePoint(review.value.actual),
					reviewed_by: review.value.owner.trim(),
					review_note: review.value.note.trim(),
					approved: review.value.approved,
				},
				agent,
				key,
				signal,
			);
		},
		() => {
			reviewDialog.value = false;
			notice.value = '复核已保存，是否验收按目标与人工审核共同判断。';
			void refreshLists();
		},
	);
}
watch(
	agentId,
	() => {
		scope.reset();
		listRefresh.reset();
		apiKey.value = '';
		busy.value = {};
		error.value = '';
		notice.value = '';
		diagnosis.value = null;
		diagnostic.value = emptyDiagnosis();
		schedules.value = [];
		runs.value = [];
		actions.value = [];
		workerStatus.value = null;
		reviewDialog.value = false;
		reviewAction.value = null;
		runDialog.value = false;
		runDetail.value = '';
		runAnswer.value = '';
		action.value.baseline = emptyEvidence();
		action.value.owner = '';
		review.value = {
			actual: emptyEvidence(),
			owner: '',
			note: '',
			approved: false,
		};
		loadMetrics();
		if (agentId.value) void refreshLists();
	},
	{ immediate: true },
);
watch(dataset, () => {
	metrics.value = [];
	loadMetrics();
});
onBeforeUnmount(() => {
	scope.close();
	listRefresh.reset();
	apiKey.value = '';
});
</script>

<style scoped>
.report-center {
	max-width: 1500px;
}
.dataset-select {
	max-width: 380px;
}
.definition {
	display: grid;
	grid-template-columns: 112px minmax(0, 1fr);
	gap: 10px 14px;
	font-size: 13px;
}
.definition dt {
	color: #64748b;
}
.definition dd {
	margin: 0;
	overflow-wrap: anywhere;
}
.evidence-id {
	overflow-wrap: anywhere;
	font-family: ui-monospace, monospace;
}
.missing-inputs {
	padding-left: 22px;
	font-size: 14px;
	line-height: 1.9;
}
.run-detail {
	white-space: pre-wrap;
	overflow-wrap: anywhere;
	font-size: 12px;
	max-height: 65vh;
	overflow: auto;
}
</style>
