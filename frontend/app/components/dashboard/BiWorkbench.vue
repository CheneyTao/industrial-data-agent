<template>
	<DashboardPanel title="智能 BI · 连续分析" subtitle="选择指标、维度与图表，结果保留数据证据" icon="mdi-brain">
		<div class="bi-controls">
			<v-select v-model="agentId" :items="agents" item-title="name" item-value="id" label="使用现有 DataAgent" density="compact" hide-details :disabled="busy" />
			<v-text-field v-if="selectedAgent?.apiKeyEnabled" v-model="apiKey" label="Agent API Key" type="password" density="compact" hide-details />
			<v-checkbox v-model="report" label="生成完整报告" density="compact" hide-details :disabled="busy" />
		</div>
		<v-textarea v-model="question" label="例如：按规格比较车辆数量；哪些车辆带有临近事件标签？" rows="2" auto-grow hide-details />
		<div class="bi-actions">
			<v-btn color="primary" variant="flat" :loading="starting" :disabled="busy || !agentId || !question.trim()" @click="start">开始分析</v-btn>
			<v-btn v-if="busy || task?.status === 'WAITING'" color="error" :disabled="task?.status === 'CANCELLING'" @click="cancel">{{ task?.status === 'CANCELLING' ? '正在安全停止' : '停止' }}</v-btn>
			<v-btn :disabled="busy" @click="reset">新分析上下文</v-btn>
			<v-chip v-if="task" size="small" :color="task.status === 'FAILED' ? 'error' : 'primary'">{{ statusLabel }}</v-chip>
		</div>
		<v-progress-linear v-if="busy && !disconnected" :model-value="task?.progress || 0" color="primary" class="mb-3" />
		<v-btn v-if="disconnected" @click="reconnect">重新连接任务</v-btn>
		<v-alert v-if="error" type="error" variant="tonal" class="mb-3">{{ error }}</v-alert>
		<v-alert v-if="task?.result.message" type="info" variant="tonal">{{ task.result.message }}</v-alert>
		<v-alert v-if="task?.result.plan && filtersChanged" type="info" variant="tonal" class="mb-3">筛选已变更。下方保留的是上一次分析，请重新分析以使用当前筛选。</v-alert>
		<div v-if="task?.status === 'WAITING'" class="bi-review">
			<pre>{{ JSON.stringify(task.result.plan, null, 2) }}</pre>
			<v-text-field v-model="feedback" label="审核意见（可选）" density="compact" />
			<v-btn color="primary" :disabled="starting" @click="review(true)">批准执行</v-btn>
			<v-btn :disabled="starting" @click="review(false)">退回修改</v-btn>
		</div>
		<NuxtLink v-if="dashboardRoute" :to="dashboardRoute">打开已筛选的 Dashboard</NuxtLink>
		<div v-if="task?.result.result" class="bi-data">
			<div v-if="singleMetricRow" class="bi-kpis"><article v-for="(value, name) in singleMetricRow" :key="name"><span>{{ name }}</span><strong>{{ finiteDisplay(value) }}</strong></article></div>
			<p>查询结果 · {{ task.result.result.data.length }} 行{{ task.result.result.truncated ? '（已限制返回量）' : '' }}</p>
			<v-table density="compact" fixed-header height="240">
				<thead><tr><th v-for="column in task.result.result.column" :key="column">{{ column }}</th></tr></thead>
				<tbody><tr v-for="(row, index) in task.result.result.data" :key="index"><td v-for="column in task.result.result.column" :key="column">{{ row[column] ?? '—' }}</td></tr></tbody>
			</v-table>
		</div>
		<DashboardEChart v-for="chart in task?.result.charts || []" :key="chart.artifact_id" :option="chart.option" :height="350" />
		<div v-if="task?.result.answer" class="bi-answer" v-html="answerHtml" />
		<v-expansion-panels v-if="task?.result.result" class="mt-3">
			<v-expansion-panel title="查看筛选、指标计划与查询证据"><v-expansion-panel-text>
				<pre>{{ JSON.stringify({ plan: task.result.plan, sql: task.result.result.sql, provenance: task.result.result.provenance }, null, 2) }}</pre>
				<p v-for="limitation in task.result.limitations || []" :key="limitation">{{ limitation }}</p>
			</v-expansion-panel-text></v-expansion-panel>
		</v-expansion-panels>
	</DashboardPanel>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import DOMPurify from 'dompurify';
import agentService, { type Agent } from '~/services/agent';
import biService, { terminalTask, type BITask, type Filters } from '~/services/bi';
import { dashboardErrorMessage } from '~/services/dashboard';
import { safeDashboardRoute, finiteDisplay } from '~/utils/bi';
import { renderMarkdownContent } from '~/utils/markdown';
const props = defineProps<{ datasetId: string; filters: Filters }>();
const agents = ref<Agent[]>([]);
const agentId = ref<number | null>(null);
const selectedAgent = computed(() => agents.value.find(a => a.id === agentId.value));
const apiKey = ref('');
const question = ref('');
const report = ref(false);
const feedback = ref('');
const task = ref<BITask | null>(null);
const error = ref('');
const starting = ref(false);
const disconnected = ref(false);
const conversationId = ref<string | null>(null);
let timer: ReturnType<typeof setTimeout> | undefined;
let mounted = true;
let failures = 0;
let generation = 0;
const storageKey = computed(() => `bi-task:${props.datasetId}:${agentId.value}`);
const busy = computed(() => starting.value || !!task.value && !terminalTask(task.value.status));
const dashboardRoute = computed(() => safeDashboardRoute(task.value?.result));
const singleMetricRow = computed(() => {
	const result = task.value?.result.result;
	const plan = task.value?.result.plan as { dimension?: string | null } | undefined;
	return result?.data.length === 1 && !plan?.dimension ? result.data[0] : null;
});
const answerHtml = computed(() => DOMPurify.sanitize(renderMarkdownContent(task.value?.result.answer || '')));
const statusNames: Record<string, string> = { PENDING:'排队中', RUNNING:'分析中', CANCELLING:'正在停止', CANCELLED:'已停止', FAILED:'失败', TIMED_OUT:'超时', COMPLETED:'完成', PARTIAL:'部分完成', WAITING:'等待确认' };
const statusLabel = computed(() => statusNames[task.value?.status || ''] || task.value?.status);
const filtersChanged = computed(() => {
	const previous = (task.value?.result.plan as { filters?: Filters } | undefined)?.filters || {};
	const normalize = (value: Filters) => JSON.stringify(Object.fromEntries(Object.entries(value).filter(([, v]) => v !== null && v !== undefined && JSON.stringify(v) !== '{}').sort(([a], [b]) => a.localeCompare(b))));
	return normalize(previous) !== normalize(props.filters);
});
async function poll() {
	if (!mounted || !task.value || !agentId.value) return;
	const current = generation;
	const taskId = task.value.task_id;
	try {
		const snapshot = await biService.task(taskId, agentId.value, apiKey.value);
		if (!mounted || generation !== current || task.value?.task_id !== taskId) return;
		task.value = snapshot;
		disconnected.value = false;
		failures = 0;
		if (task.value.error) error.value = task.value.error;
		if (!terminalTask(task.value.status)) timer = setTimeout(poll, 1000);
	} catch (e) {
		if (!mounted || generation !== current) return;
		error.value = dashboardErrorMessage(e);
		if (++failures < 3) timer = setTimeout(poll, 2000);
		else { disconnected.value = true; error.value = '状态连接中断，服务端任务状态未知。请重新连接或请求停止。'; }
	}
}
async function start() {
	if (!agentId.value) return;
	generation++; clearTimeout(timer);
	starting.value = true; error.value = ''; failures = 0; disconnected.value = false;
	try {
		const created = await biService.analyze({ agent_id: agentId.value, question: question.value, dataset_id: props.datasetId, filters: props.filters, conversation_id: conversationId.value, report: report.value }, apiKey.value);
		conversationId.value = created.conversation_id;
		task.value = { ...created, current_node: '', progress: 0, result: {}, error: '' };
		sessionStorage.setItem(storageKey.value, JSON.stringify(created));
		void poll();
	} catch (e) { error.value = dashboardErrorMessage(e); }
	finally { starting.value = false; }
}
async function cancel() {
	if (!task.value || !agentId.value) return;
	try { task.value = await biService.cancel(task.value.task_id, agentId.value, apiKey.value); }
	catch (e) { error.value = dashboardErrorMessage(e); }
}
function reconnect() { failures = 0; disconnected.value = false; error.value = ''; void poll(); }
async function review(approved: boolean) {
	if (!task.value || !agentId.value) return;
	starting.value = true; error.value = '';
	try {
		await biService.resume(task.value.task_id, agentId.value, approved, feedback.value, apiKey.value);
		task.value.status = 'PENDING';
		void poll();
	} catch (e) { error.value = dashboardErrorMessage(e); }
	finally { starting.value = false; }
}
function reset() { generation++; disconnected.value = false; clearTimeout(timer); task.value = null; conversationId.value = null; error.value = ''; sessionStorage.removeItem(storageKey.value); }
watch(agentId, () => { generation++; apiKey.value = ''; task.value = null; conversationId.value = null; clearTimeout(timer); });
onMounted(async () => {
	try {
		agents.value = await agentService.list();
		agentId.value = agents.value[0]?.id || null;
		await nextTick();
		const saved = sessionStorage.getItem(storageKey.value);
		if (saved) { const value = JSON.parse(saved); conversationId.value = value.conversation_id; task.value = { ...value, result: {} }; void poll(); }
	} catch (e) { error.value = dashboardErrorMessage(e); }
});
onBeforeUnmount(() => { generation++; mounted = false; clearTimeout(timer); });
</script>
<style scoped>
.bi-controls, .bi-actions { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin-bottom:16px; }
.bi-controls > :deep(.v-input) { min-width:180px; max-width:300px; }
.bi-actions { margin-top:12px; }
.bi-data p { font-size:12px; color:#64748b; margin:10px 0; }
.bi-kpis { display:flex; flex-wrap:wrap; gap:12px; margin:16px 0; }
.bi-kpis article { display:flex; flex-direction:column; gap:8px; border:1px solid #e2e8f0; border-radius:10px; padding:16px; min-width:160px; }
.bi-kpis article span { font-size:12px; color:#64748b; }
.bi-kpis article strong { font-size:26px; color:#0f172a; }
.bi-answer { padding:18px; background:#f8fafc; border-radius:10px; line-height:1.8; font-size:13px; }
pre { white-space:pre-wrap; overflow:auto; font-size:11px; }
</style>
