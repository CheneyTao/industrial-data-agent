<template>
	<DashboardPanel
		title="AI 深度分析"
		subtitle="复用当前 DataAgent 的 Planner、SQL/Python、RAG、归因与报告链路"
		icon="mdi-auto-fix"
	>
		<template #actions>
			<div class="ai-actions">
				<v-btn
					v-if="loading"
					size="small"
					variant="outlined"
					color="error"
					prepend-icon="mdi-stop-circle-outline"
					@click="$emit('cancel')"
				>
					停止
				</v-btn>
				<v-btn
					v-else
					size="small"
					color="primary"
					variant="flat"
					prepend-icon="mdi-creation-outline"
					@click="$emit('analyze')"
				>
					{{ result ? '重新分析' : 'AI 深度分析' }}
				</v-btn>
			</div>
		</template>

		<div v-if="loading" class="ai-running">
			<div class="ai-running__pulse"><v-icon icon="mdi-brain" size="26" /></div>
			<div>
				<strong>DataAgent 正在分析当前 Dashboard 上下文</strong>
				<p>正在规划、查询、计算并生成有证据的结论，请勿关闭页面。</p>
			</div>
		</div>

		<div v-if="progress.length" class="progress-strip">
			<v-chip
				v-for="(step, index) in progress"
				:key="`${step}-${index}`"
				size="x-small"
				variant="tonal"
				:color="index === progress.length - 1 && loading ? 'primary' : 'grey-darken-1'"
			>
				{{ step }}
			</v-chip>
		</div>

		<v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">
			{{ error }}
		</v-alert>

		<div v-if="result" class="analysis-result">
			<div class="analysis-result__meta">
				<span><v-icon icon="mdi-check-decagram-outline" size="16" /> 分析已完成</span>
				<NuxtLink v-if="agentId" :to="`/chat?agentId=${agentId}`">在数据问答中继续追问</NuxtLink>
			</div>
			<div class="markdown-body" v-html="renderedResult" />
		</div>

		<v-expansion-panels v-if="evidence.length" variant="accordion" class="mt-3">
			<v-expansion-panel>
				<v-expansion-panel-title>
					<span class="evidence-title">
						<v-icon icon="mdi-database-search-outline" size="17" />
						执行证据（{{ evidence.length }}）
					</span>
				</v-expansion-panel-title>
				<v-expansion-panel-text>
					<div v-for="(item, index) in evidence" :key="index" class="evidence-item">
						<strong>{{ item.type }}</strong>
						<pre>{{ item.text }}</pre>
					</div>
				</v-expansion-panel-text>
			</v-expansion-panel>
		</v-expansion-panels>

		<div v-if="!loading && !result && !error" class="analysis-empty">
			<div class="analysis-empty__icon"><v-icon icon="mdi-chart-box-plus-outline" size="32" /></div>
			<div>
				<strong>从固定 BI 监控进入可追溯深度分析</strong>
				<p>当前时间、型号、设备与故障类型会连同聚合证据一起交给已选 DataAgent。</p>
			</div>
		</div>
	</DashboardPanel>
</template>

<script setup lang="ts">
import DOMPurify from 'dompurify';
import { computed } from 'vue';
import { renderMarkdownContent } from '~/utils/markdown';

const props = withDefaults(
	defineProps<{
		loading?: boolean;
		result?: string;
		error?: string;
		progress?: string[];
		evidence?: Array<{ type: string; text: string }>;
		agentId?: number | null;
	}>(),
	{
		loading: false,
		result: '',
		error: '',
		progress: () => [],
		evidence: () => [],
		agentId: null,
	},
);

defineEmits<{ analyze: []; cancel: [] }>();

const renderedResult = computed(() =>
	DOMPurify.sanitize(renderMarkdownContent(props.result), {
		ADD_ATTR: ['class'],
	}),
);
</script>

<style scoped>
.ai-actions {
	display: flex;
	gap: 8px;
}

.ai-running {
	display: flex;
	align-items: center;
	gap: 14px;
	padding: 14px;
	margin-bottom: 12px;
	background: linear-gradient(110deg, #eff6ff, #f5f3ff);
	border: 1px solid #dbeafe;
	border-radius: 12px;
}

.ai-running__pulse {
	width: 46px;
	height: 46px;
	border-radius: 14px;
	display: grid;
	place-items: center;
	color: #fff;
	background: linear-gradient(135deg, #2563eb, #7c3aed);
	box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.3);
	animation: pulse 1.8s infinite;
}

.ai-running strong,
.analysis-empty strong {
	font-size: 13px;
	color: #1e293b;
}

.ai-running p,
.analysis-empty p {
	font-size: 11px;
	color: #64748b;
	margin: 4px 0 0;
}

.progress-strip {
	display: flex;
	flex-wrap: wrap;
	gap: 6px;
	margin-bottom: 12px;
}

.analysis-result {
	border: 1px solid #e5eaf1;
	border-radius: 12px;
	overflow: hidden;
}

.analysis-result__meta {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 12px;
	padding: 10px 14px;
	background: #f8fafc;
	border-bottom: 1px solid #e5eaf1;
	font-size: 11px;
	color: #059669;
}

.analysis-result__meta span {
	display: flex;
	align-items: center;
	gap: 5px;
	font-weight: 700;
}

.analysis-result__meta a {
	color: #2563eb;
	text-decoration: none;
}

.markdown-body {
	max-height: 620px;
	overflow: auto;
	padding: 18px 20px;
	font-size: 13px;
	line-height: 1.72;
	color: #334155;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
	color: #172033;
	margin-top: 1.1em;
}

.markdown-body :deep(table) {
	width: 100%;
	border-collapse: collapse;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
	padding: 7px 9px;
	border: 1px solid #e5eaf1;
}

.evidence-title {
	display: inline-flex;
	align-items: center;
	gap: 7px;
	font-size: 12px;
}

.evidence-item + .evidence-item {
	margin-top: 12px;
}

.evidence-item strong {
	font-size: 11px;
	color: #475569;
}

.evidence-item pre {
	max-height: 190px;
	overflow: auto;
	padding: 10px;
	margin: 5px 0 0;
	border-radius: 8px;
	background: #0f172a;
	color: #dbeafe;
	font-size: 10px;
	white-space: pre-wrap;
	word-break: break-word;
}

.analysis-empty {
	min-height: 112px;
	display: flex;
	align-items: center;
	gap: 14px;
	padding: 16px;
	background: #f8fafc;
	border: 1px dashed #cbd5e1;
	border-radius: 12px;
}

.analysis-empty__icon {
	width: 50px;
	height: 50px;
	border-radius: 14px;
	display: grid;
	place-items: center;
	color: #2563eb;
	background: #eff6ff;
}

@keyframes pulse {
	70% { box-shadow: 0 0 0 12px rgba(37, 99, 235, 0); }
	100% { box-shadow: 0 0 0 0 rgba(37, 99, 235, 0); }
}
</style>
