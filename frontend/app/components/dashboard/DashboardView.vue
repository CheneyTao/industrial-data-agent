<template>
	<div class="dashboard-page">
		<section class="dashboard-hero">
			<div>
				<div class="dashboard-hero__eyebrow">
					<span class="live-dot" /> INDUSTRIAL OPERATIONS INTELLIGENCE
				</div>
				<h1>工业设备运行与故障分析</h1>
				<p>固定 BI 监控、设备联动与 DataAgent 深度分析，共用同一份来源数据与筛选口径。</p>
			</div>
			<div class="dashboard-hero__meta">
				<v-chip size="small" variant="tonal" color="primary" prepend-icon="mdi-database-outline">
					来源数据 · PdM
				</v-chip>
				<v-chip v-if="options" size="small" variant="outlined" prepend-icon="mdi-calendar-range">
					{{ options.dataRange.startDate }} — {{ options.dataRange.endDate }}
				</v-chip>
				<v-btn
					icon="mdi-refresh"
					size="small"
					variant="outlined"
					:loading="loading"
					title="刷新数据"
					@click="refresh"
				/>
			</div>
		</section>

		<DashboardFilters
			v-model="filters"
			:options="options"
			:loading="loading || optionsLoading"
			@apply="applyFilters"
		/>

		<div v-if="activeFilterChips.length" class="active-filters">
			<span>当前联动：</span>
			<v-chip
				v-for="chip in activeFilterChips"
				:key="chip.key"
				size="small"
				closable
				color="primary"
				variant="tonal"
				@click:close="clearFilter(chip.key)"
			>
				{{ chip.label }}
			</v-chip>
		</div>

		<v-alert
			v-if="pageError"
			type="error"
			variant="tonal"
			closable
			class="dashboard-alert"
			@update:model-value="pageError = ''"
		>
			{{ pageError }}
		</v-alert>

		<KpiCards :data="overview" :loading="loading" />

		<div class="dashboard-grid">
			<div class="span-8">
				<FailureTrendChart
					:items="failureTrend"
					:granularity="granularity"
					:loading="loading"
					:error="errors.trend"
					@granularity="changeGranularity"
				/>
			</div>
			<div class="span-4">
				<FailureModelChart
					:items="failureModels"
					:loading="loading"
					:error="errors.models"
					@select-model="selectModel"
				/>
			</div>

			<div class="span-4">
				<FailureTypeChart
					:items="failureTypes"
					:loading="loading"
					:error="errors.types"
					@select-failure="selectFailure"
				/>
			</div>
			<div class="span-4">
				<ErrorRankingChart
					:items="errorRanking"
					:loading="loading"
					:error="errors.errors"
				/>
			</div>
			<div class="span-4">
				<MachineRanking
					:items="machineRanking"
					:selected-machine-id="selectedTelemetryMachineId"
					:loading="loading"
					:error="errors.machines"
					@select-machine="selectMachine"
				/>
			</div>

			<div class="span-7">
				<TelemetryChart
					:data="telemetry"
					:machine-id="selectedTelemetryMachineId"
					:loading="telemetryLoading"
					:error="errors.telemetry"
				/>
			</div>
			<div class="span-5">
				<MaintenanceChart
					:data="maintenance"
					:loading="loading"
					:error="errors.maintenance"
				/>
			</div>

			<div class="span-12">
				<AiAnalysisPanel
					:loading="analysisLoading"
					:result="analysisResult"
					:error="analysisError"
					:progress="analysisProgress"
					:evidence="analysisEvidence"
					:agent-id="analysisAgentId || activeAgentId"
					@analyze="runAnalysis"
					@cancel="cancelAnalysis"
				/>
			</div>
		</div>

		<BiWorkbench dataset-id="industrial_pdm" :filters="biFilters" />
		<footer class="dashboard-footer">
			<span>指标时间以来源数据为准；“最近”预设相对最新数据日计算。</span>
			<span v-if="lastUpdated">最近刷新：{{ lastUpdated }}</span>
		</footer>
	</div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import AiAnalysisPanel from '~/components/dashboard/AiAnalysisPanel.vue';
import DashboardFilters from '~/components/dashboard/DashboardFilters.vue';
import ErrorRankingChart from '~/components/dashboard/ErrorRankingChart.vue';
import FailureModelChart from '~/components/dashboard/FailureModelChart.vue';
import FailureTrendChart from '~/components/dashboard/FailureTrendChart.vue';
import FailureTypeChart from '~/components/dashboard/FailureTypeChart.vue';
import KpiCards from '~/components/dashboard/KpiCards.vue';
import MachineRanking from '~/components/dashboard/MachineRanking.vue';
import MaintenanceChart from '~/components/dashboard/MaintenanceChart.vue';
import TelemetryChart from '~/components/dashboard/TelemetryChart.vue';
import agentService from '~/services/agent';
import chatService from '~/services/chat';
import dashboardService, {
	dashboardErrorMessage,
	type DashboardFilters as DashboardFilterValues,
	type DashboardOptions,
	type DashboardOverview,
	type ErrorRankingItem,
	type FailureModelItem,
	type FailureTrendItem,
	type FailureTypeItem,
	type MachineRankingItem,
	type MaintenanceResponse,
	type TelemetryResponse,
} from '~/services/dashboard';
import graphService, {
	GraphEventType,
	TextType,
	type GraphNodeResponse,
} from '~/services/graph';

type PanelErrorKey =
	| 'overview'
	| 'trend'
	| 'models'
	| 'types'
	| 'errors'
	| 'machines'
	| 'telemetry'
	| 'maintenance';

const route = useRoute();
const options = ref<DashboardOptions | null>(null);
const filters = ref<DashboardFilterValues>({
	startDate: '',
	endDate: '',
	machineId: null,
	model: null,
	failureType: null,
});
const appliedFilters = ref<DashboardFilterValues>({ ...filters.value });
const biFilters = computed(() => ({
	start_date: appliedFilters.value.startDate || null,
	end_date: appliedFilters.value.endDate || null,
	machine_id: appliedFilters.value.machineId,
	model: appliedFilters.value.model,
	failure_type: appliedFilters.value.failureType,
}));
const granularity = ref<'day' | 'week' | 'month'>('day');
const overview = ref<DashboardOverview | null>(null);
const failureTrend = ref<FailureTrendItem[]>([]);
const failureModels = ref<FailureModelItem[]>([]);
const failureTypes = ref<FailureTypeItem[]>([]);
const errorRanking = ref<ErrorRankingItem[]>([]);
const machineRanking = ref<MachineRankingItem[]>([]);
const telemetry = ref<TelemetryResponse | null>(null);
const maintenance = ref<MaintenanceResponse | null>(null);
const selectedTelemetryMachineId = ref<number | null>(null);
const loading = ref(false);
const optionsLoading = ref(false);
const telemetryLoading = ref(false);
const pageError = ref('');
const lastUpdated = ref('');
const errors = reactive<Record<PanelErrorKey, string>>({
	overview: '',
	trend: '',
	models: '',
	types: '',
	errors: '',
	machines: '',
	telemetry: '',
	maintenance: '',
});
let dashboardRequestId = 0;
let trendRequestId = 0;

const activeFilterChips = computed(() => {
	const values: Array<{ key: 'model' | 'machineId' | 'failureType'; label: string }> = [];
	if (appliedFilters.value.model) {
		const model = options.value?.models.find((item) => item.value === appliedFilters.value.model);
		values.push({ key: 'model', label: `型号：${model?.label || appliedFilters.value.model}` });
	}
	if (appliedFilters.value.machineId !== null) {
		values.push({ key: 'machineId', label: `设备：#${appliedFilters.value.machineId}` });
	}
	if (appliedFilters.value.failureType) {
		values.push({ key: 'failureType', label: `故障：${appliedFilters.value.failureType}` });
	}
	return values;
});

const activeAgentId = computed(() => {
	const raw = Array.isArray(route.query.agentId) ? route.query.agentId[0] : route.query.agentId;
	const value = Number(raw);
	return Number.isFinite(value) && value > 0 ? value : null;
});

function resetPanelErrors() {
	(Object.keys(errors) as PanelErrorKey[]).forEach((key) => (errors[key] = ''));
}

function settledValue<T>(
	result: PromiseSettledResult<T>,
	key: PanelErrorKey,
	fallback: T,
): T {
	if (result.status === 'fulfilled') return result.value;
	errors[key] = dashboardErrorMessage(result.reason);
	return fallback;
}

async function loadTelemetry(machineId: number | null, requestId: number) {
	selectedTelemetryMachineId.value = machineId;
	telemetry.value = null;
	errors.telemetry = '';
	if (machineId === null) return;
	telemetryLoading.value = true;
	try {
		const result = await dashboardService.telemetry(appliedFilters.value, machineId);
		if (requestId === dashboardRequestId) telemetry.value = result;
	} catch (error) {
		if (requestId === dashboardRequestId) errors.telemetry = dashboardErrorMessage(error);
	} finally {
		if (requestId === dashboardRequestId) telemetryLoading.value = false;
	}
}

async function loadDashboard(nextFilters: DashboardFilterValues) {
	const requestId = ++dashboardRequestId;
	appliedFilters.value = { ...nextFilters };
	filters.value = { ...nextFilters };
	loading.value = true;
	telemetryLoading.value = true;
	pageError.value = '';
	resetPanelErrors();
	if (analysisLoading.value) await cancelAnalysis();

	const results = await Promise.allSettled([
		dashboardService.overview(nextFilters),
		dashboardService.failureTrend(nextFilters, granularity.value),
		dashboardService.failureByModel(nextFilters),
		dashboardService.failureTypes(nextFilters),
		dashboardService.errorRanking(nextFilters),
		dashboardService.machineRanking(nextFilters),
		dashboardService.maintenance(nextFilters),
	]);
	if (requestId !== dashboardRequestId) return;

	overview.value = settledValue(results[0], 'overview', null);
	failureTrend.value = settledValue(results[1], 'trend', []);
	failureModels.value = settledValue(results[2], 'models', []);
	failureTypes.value = settledValue(results[3], 'types', []);
	errorRanking.value = settledValue(results[4], 'errors', []);
	machineRanking.value = settledValue(results[5], 'machines', []);
	maintenance.value = settledValue(results[6], 'maintenance', null);

	const requestedMachine = nextFilters.machineId;
	const fallbackMachine = machineRanking.value[0]?.machineId ?? null;
	await loadTelemetry(requestedMachine ?? fallbackMachine, requestId);
	if (requestId !== dashboardRequestId) return;
	loading.value = false;
	telemetryLoading.value = false;
	lastUpdated.value = new Intl.DateTimeFormat('zh-CN', {
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit',
	}).format(new Date());
}

async function initialize() {
	optionsLoading.value = true;
	try {
		options.value = await dashboardService.options();
		filters.value = {
			...options.value.defaultRange,
			machineId: null,
			model: null,
			failureType: null,
		};
		if (typeof route.query.filters === 'string') {
			const incoming = JSON.parse(route.query.filters);
			filters.value = {
				startDate: incoming.start_date || filters.value.startDate,
				endDate: incoming.end_date || filters.value.endDate,
				machineId: incoming.machine_id ?? null,
				model: incoming.model ?? null,
				failureType: incoming.failure_type ?? null,
			};
		}
		await loadDashboard(filters.value);
	} catch (error) {
		pageError.value = dashboardErrorMessage(error);
	} finally {
		optionsLoading.value = false;
		loading.value = false;
		telemetryLoading.value = false;
	}
}

function applyFilters(value: DashboardFilterValues) {
	void loadDashboard(value);
}

function refresh() {
	void loadDashboard(appliedFilters.value);
}

function clearFilter(key: 'model' | 'machineId' | 'failureType') {
	const next = { ...appliedFilters.value, [key]: null };
	if (key === 'model') next.machineId = null;
	void loadDashboard(next);
}

function selectModel(model: string) {
	const selectedMachine = options.value?.machines.find(
		(item) => item.id === appliedFilters.value.machineId,
	);
	const next = {
		...appliedFilters.value,
		model,
		machineId: selectedMachine?.model === model ? appliedFilters.value.machineId : null,
	};
	void loadDashboard(next);
}

function selectFailure(failureType: string) {
	void loadDashboard({ ...appliedFilters.value, failureType });
}

function selectMachine(machineId: number) {
	void loadDashboard({ ...appliedFilters.value, machineId });
}

async function changeGranularity(value: 'day' | 'week' | 'month') {
	granularity.value = value;
	const requestId = ++trendRequestId;
	errors.trend = '';
	try {
		const items = await dashboardService.failureTrend(appliedFilters.value, value);
		if (requestId === trendRequestId) failureTrend.value = items;
	} catch (error) {
		if (requestId === trendRequestId) errors.trend = dashboardErrorMessage(error);
	}
}

const analysisLoading = ref(false);
const analysisResult = ref('');
const analysisError = ref('');
const analysisProgress = ref<string[]>([]);
const analysisEvidence = ref<Array<{ type: string; text: string }>>([]);
const analysisAgentId = ref<number | null>(null);
let closeAnalysisStream: ((cancelRun?: boolean) => Promise<void>) | null = null;
let analysisTimeout: ReturnType<typeof setTimeout> | null = null;

function recordProgress(response: GraphNodeResponse) {
	const name = response.nodeName || 'DataAgent';
	if (name && analysisProgress.value.at(-1) !== name) {
		analysisProgress.value = [...analysisProgress.value, name].slice(-12);
	}
	if ([TextType.SQL, TextType.RESULT_SET, TextType.PYTHON].includes(response.textType)) {
		analysisEvidence.value = [
			...analysisEvidence.value,
			{ type: response.textType, text: response.text.slice(0, 8000) },
		].slice(-12);
	}
}

async function resolveAgentId(): Promise<number> {
	if (activeAgentId.value) return activeAgentId.value;
	let agents = await agentService.list('published');
	if (!agents.length) agents = await agentService.list();
	const id = agents.find((item) => Number(item.id) > 0)?.id;
	if (!id) throw new Error('当前没有可用 DataAgent，请先创建并绑定工业数据源。');
	return id;
}

function clearAnalysisTimer() {
	if (analysisTimeout) clearTimeout(analysisTimeout);
	analysisTimeout = null;
}

async function runAnalysis() {
	if (analysisLoading.value) return;
	analysisLoading.value = true;
	analysisResult.value = '';
	analysisError.value = '';
	analysisProgress.value = [];
	analysisEvidence.value = [];

	try {
		const agentId = await resolveAgentId();
		analysisAgentId.value = agentId;
		const context = await dashboardService.analysisContext(appliedFilters.value);
		const session = await chatService.createSession(agentId, '工业设备 Dashboard 深度分析');
		await chatService.saveMessage(session.id, {
			sessionId: session.id,
			role: 'user',
			content: context.question,
			messageType: 'text',
			titleNeeded: true,
		});

		let finalReply = '';
		let reportBuffer = '';
		let humanFeedbackRequired = false;
		const closeStream = await graphService.streamSearch(
			{
				agentId: String(agentId),
				conversationId: session.id,
				query: context.question,
				humanFeedback: false,
				rejectedPlan: false,
				nl2sqlOnly: false,
			},
			async (response) => {
				recordProgress(response);
				if (response.eventType === GraphEventType.FINAL_ANSWER) {
					finalReply = response.text?.trim() || finalReply;
				} else if (
					response.nodeName === 'ReportGeneratorNode' &&
					response.textType === TextType.MARK_DOWN
				) {
					reportBuffer += response.text;
				} else if (response.eventType === GraphEventType.HUMAN_FEEDBACK_REQUIRED) {
					humanFeedbackRequired = true;
				}
			},
			async (error) => {
				clearAnalysisTimer();
				analysisError.value = error.message || 'DataAgent 分析失败，请稍后重试。';
				analysisLoading.value = false;
				closeAnalysisStream = null;
			},
			async () => {
				clearAnalysisTimer();
				const result = finalReply || reportBuffer.trim();
				if (result) {
					analysisResult.value = result;
					await chatService
						.saveMessage(session.id, {
							sessionId: session.id,
							role: 'assistant',
							content: result,
							messageType: 'text',
						})
						.catch(() => undefined);
				} else if (humanFeedbackRequired) {
					analysisError.value = '当前 Agent 要求人工确认计划，请在“数据问答”中继续该会话。';
				} else {
					analysisError.value = 'DataAgent 已结束，但没有返回可展示的分析结论。';
				}
				analysisLoading.value = false;
				closeAnalysisStream = null;
			},
		);
		if (!analysisLoading.value) {
			await closeStream(false).catch(() => undefined);
			return;
		}
		closeAnalysisStream = closeStream;

		analysisTimeout = setTimeout(() => {
			analysisError.value = 'AI 深度分析超过 180 秒，已停止本次运行。';
			void cancelAnalysis(false);
		}, 180_000);
	} catch (error) {
		clearAnalysisTimer();
		analysisError.value = dashboardErrorMessage(error);
		analysisLoading.value = false;
		closeAnalysisStream = null;
	}
}

async function cancelAnalysis(showMessage = true) {
	clearAnalysisTimer();
	const close = closeAnalysisStream;
	closeAnalysisStream = null;
	if (close) {
		try {
			await close(true);
		} catch {
			// The local abort is already effective even if the stop request fails.
		}
	}
	analysisLoading.value = false;
	if (showMessage && !analysisResult.value) analysisError.value = '已停止本次 AI 深度分析。';
}

onMounted(() => void initialize());
onBeforeUnmount(() => {
	dashboardRequestId += 1;
	trendRequestId += 1;
	if (closeAnalysisStream) void closeAnalysisStream(true);
	clearAnalysisTimer();
});
</script>

<style scoped>
.dashboard-page {
	min-height: 100%;
	padding: 22px;
	background:
		radial-gradient(circle at 100% 0, rgba(37, 99, 235, 0.08), transparent 30%),
		#f4f7fb;
	display: flex;
	flex-direction: column;
	gap: 16px;
}

.dashboard-hero {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 22px;
	padding: 8px 2px 4px;
}

.dashboard-hero__eyebrow {
	display: flex;
	align-items: center;
	gap: 7px;
	font-size: 9px;
	font-weight: 800;
	letter-spacing: 0.12em;
	color: #2563eb;
}

.live-dot {
	width: 7px;
	height: 7px;
	border-radius: 50%;
	background: #10b981;
	box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.13);
}

.dashboard-hero h1 {
	font-size: clamp(23px, 2.2vw, 32px);
	line-height: 1.2;
	letter-spacing: -0.025em;
	color: #142033;
	margin: 5px 0 7px;
}

.dashboard-hero p {
	font-size: 12px;
	color: #64748b;
	margin: 0;
}

.dashboard-hero__meta {
	display: flex;
	align-items: center;
	justify-content: flex-end;
	flex-wrap: wrap;
	gap: 8px;
}

.active-filters {
	display: flex;
	align-items: center;
	flex-wrap: wrap;
	gap: 7px;
	padding: 0 2px;
}

.active-filters > span {
	font-size: 11px;
	color: #7b879b;
}

.dashboard-alert {
	font-size: 12px;
}

.dashboard-grid {
	display: grid;
	grid-template-columns: repeat(12, minmax(0, 1fr));
	gap: 14px;
}

.span-4 { grid-column: span 4; min-width: 0; }
.span-5 { grid-column: span 5; min-width: 0; }
.span-7 { grid-column: span 7; min-width: 0; }
.span-8 { grid-column: span 8; min-width: 0; }
.span-12 { grid-column: span 12; min-width: 0; }

.dashboard-footer {
	display: flex;
	justify-content: space-between;
	gap: 16px;
	padding: 2px 4px 8px;
	font-size: 10px;
	color: #94a3b8;
}

@media (max-width: 1180px) {
	.span-4,
	.span-5,
	.span-7,
	.span-8 {
		grid-column: span 6;
	}
}

@media (max-width: 760px) {
	.dashboard-page { padding: 14px; }
	.dashboard-hero { align-items: flex-start; flex-direction: column; }
	.dashboard-hero__meta { justify-content: flex-start; }
	.span-4,
	.span-5,
	.span-7,
	.span-8,
	.span-12 { grid-column: span 12; }
	.dashboard-footer { flex-direction: column; }
}
</style>
