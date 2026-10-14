/*
 * Copyright 2026 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

<template>
	<div class="result-set-wrap">
		<!-- Error state -->
		<div v-if="errorMsg" class="result-error">
			<v-icon size="14" color="error" class="mr-1">mdi-alert-circle-outline</v-icon>
			{{ errorMsg }}
		</div>

		<!-- Empty state -->
		<div v-else-if="!columns.length" class="result-empty">
			暂无数据
		</div>

		<!-- Chart + raw data table -->
		<template v-else>
			<div v-if="data?.resultSet?.truncated" class="result-warning" role="status">
				查询结果已截断：显示前 {{ totalRows }} 行。图表和分析仅覆盖这些返回行，不代表全量数据。
			</div>
			<div v-if="chartOption" class="chart-container">
				<div
					ref="chartElement"
					class="result-chart"
					:style="{ height: `${chartHeight}px` }"
					role="img"
					:aria-label="data?.displayStyle?.title || '查询结果图表'"
				/>
			</div>
			<div class="result-header">
				<span class="result-count">
					{{ chartOption ? '明细数据 · ' : '' }}本次返回 {{ totalRows }} 条记录
				</span>
				<div v-if="totalPages > 1" class="pagination">
					<span class="pagination-info">第 {{ currentPage }} / {{ totalPages }} 页</span>
					<button class="page-btn" :disabled="currentPage <= 1" @click="currentPage--">
						<v-icon size="13">mdi-chevron-left</v-icon>
					</button>
					<button class="page-btn" :disabled="currentPage >= totalPages" @click="currentPage++">
						<v-icon size="13">mdi-chevron-right</v-icon>
					</button>
				</div>
			</div>
			<div class="table-container custom-scrollbar">
				<table class="result-table">
					<thead>
						<tr>
							<th v-for="col in columns" :key="col">{{ col }}</th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="(row, i) in pageData" :key="i">
							<td v-for="col in columns" :key="col" :class="{ 'null-cell': row[col] == null }">
								{{ row[col] == null ? 'NULL' : row[col] === '' ? '（空字符串）' : row[col] }}
							</td>
						</tr>
					</tbody>
				</table>
			</div>
			<details v-if="provenance" class="result-provenance">
				<summary>查看数据来源与查询依据</summary>
				<dl>
					<dt>数据源</dt><dd>{{ provenance.sourceName }}（{{ provenance.sourceId }}）</dd>
					<dt>执行时间</dt><dd>{{ provenance.executedAt }} · {{ provenance.elapsedMs }} ms</dd>
					<dt>运行编号</dt><dd>{{ provenance.runId }}</dd>
					<dt>结果证据</dt><dd>{{ provenance.artifactId }}</dd>
					<template v-if="data?.chartArtifactId"><dt>图表证据</dt><dd>{{ data.chartArtifactId }}</dd></template>
					<dt>内容校验值</dt><dd>{{ provenance.sha256 }}</dd>
				</dl>
				<pre>{{ provenance.sql }}</pre>
				<p>校验值用于核验保存的结果内容，不代表数据库当前状态或分析结论必然正确。</p>
				<button class="evidence-download" @click="downloadEvidence">下载本次结果与来源</button>
			</details>
		</template>
	</div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, PieChart, ScatterChart, HeatmapChart } from 'echarts/charts';
import {
	GridComponent,
	LegendComponent,
	TitleComponent,
	TooltipComponent,
	VisualMapComponent,
	DataZoomComponent,
} from 'echarts/components';
import { LabelLayout } from 'echarts/features';
import { CanvasRenderer } from 'echarts/renderers';
import type { ResultData } from '~/services/resultSet/index';
import {
	buildResultChartModel,
	buildResultChartOption,
} from '~/utils/resultChart';

echarts.use([
	BarChart,
	LineChart,
	PieChart,
	ScatterChart,
	HeatmapChart,
	VisualMapComponent,
	DataZoomComponent,
	GridComponent,
	LegendComponent,
	TitleComponent,
	TooltipComponent,
	LabelLayout,
	CanvasRenderer,
]);

const props = defineProps<{
	data: ResultData | null;
	pageSize?: number;
}>();

const currentPage = ref(1);
const chartElement = ref<HTMLElement | null>(null);
let chartInstance: ReturnType<typeof echarts.init> | null = null;
let resizeObserver: ResizeObserver | null = null;
let observedElement: HTMLElement | null = null;

const pageSz = computed(() => props.pageSize || 20);
const columns = computed(() => props.data?.resultSet?.column || []);
const allRows = computed(() => props.data?.resultSet?.data || []);
const totalRows = computed(() => allRows.value.length);
const totalPages = computed(() => Math.max(1, Math.ceil(totalRows.value / pageSz.value)));
const pageData = computed(() => {
	const start = (currentPage.value - 1) * pageSz.value;
	return allRows.value.slice(start, start + pageSz.value);
});
const errorMsg = computed(() => props.data?.resultSet?.errorMsg || '');
const provenance = computed(() => props.data?.resultSet?.provenance);
const chartModel = computed(() => buildResultChartModel(props.data));
const chartOption = computed(() =>
	props.data?.chartOption ?? (chartModel.value ? buildResultChartOption(chartModel.value) : null),
);
const chartHeight = computed(() => {
	if (chartModel.value?.type !== 'bar') return 360;
	return Math.min(640, Math.max(340, chartModel.value.categories.length * 38 + 110));
});

function downloadEvidence() {
	if (!props.data) return;
	const url = URL.createObjectURL(new Blob([JSON.stringify(props.data, null, 2)], { type: 'application/json' }));
	const link = document.createElement('a');
	link.href = url;
	link.download = `query-${provenance.value?.artifactId || 'result'}.json`;
	link.click();
	URL.revokeObjectURL(url);
}

function disposeChart() {
	resizeObserver?.disconnect();
	resizeObserver = null;
	observedElement = null;
	chartInstance?.dispose();
	chartInstance = null;
}

function renderChart() {
	const element = chartElement.value;
	const option = chartOption.value;
	if (!element || !option) {
		if (!option) disposeChart();
		return;
	}

	if (!chartInstance || chartInstance.isDisposed()) {
		chartInstance = echarts.init(element);
	}
	chartInstance.setOption(option, true);

	if (observedElement !== element && typeof ResizeObserver !== 'undefined') {
		resizeObserver?.disconnect();
		resizeObserver = new ResizeObserver(() => chartInstance?.resize());
		resizeObserver.observe(element);
		observedElement = element;
	}
}

watch(
	chartOption,
	async (option) => {
		if (!option) {
			disposeChart();
			return;
		}
		await nextTick();
		renderChart();
	},
	{ deep: true },
);

watch(
	() => props.data,
	() => {
		currentPage.value = 1;
	},
);

onMounted(() => renderChart());
onBeforeUnmount(() => disposeChart());
</script>

<style scoped>
.result-set-wrap {
	font-size: 13px;
}
.result-warning { padding: 12px; color: #92400e; background: #fffbeb; }
.result-provenance { padding: 12px; border-top: 1px solid #e2e8f0; color: #475569; }
.result-provenance summary { cursor: pointer; }
.result-provenance dl { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 8px; margin: 12px 0; }
.result-provenance dd { margin: 0; overflow-wrap: anywhere; }
.result-provenance pre { overflow: auto; padding: 12px; background: #f1f5f9; }
.evidence-download { margin-top: 8px; color: #2563eb; text-decoration: underline; }
.result-table td.null-cell { color: #94a3b8; font-style: italic; }
.result-error {
	display: flex;
	align-items: center;
	background: #fef2f2;
	color: #dc2626;
	padding: 10px 14px;
	font-size: 13px;
}
.result-empty {
	text-align: center;
	color: #94a3b8;
	padding: 16px;
	font-size: 13px;
}
.chart-container {
	padding: 8px 10px 0;
	background: #fff;
	border-bottom: 1px solid #e8edf2;
}
.result-chart {
	width: 100%;
	min-width: 280px;
}
.result-header {
	display: flex;
	justify-content: space-between;
	align-items: center;
	padding: 8px 12px;
	background: #f8fafc;
	border-bottom: 1px solid #e8edf2;
}
.result-count {
	font-size: 12px;
	color: #64748b;
}
.pagination {
	display: flex;
	align-items: center;
	gap: 4px;
}
.pagination-info {
	font-size: 11.5px;
	color: #64748b;
	padding: 0 4px;
}
.page-btn {
	display: flex;
	align-items: center;
	justify-content: center;
	width: 24px;
	height: 24px;
	background: white;
	border: 1px solid #e2e8f0;
	border-radius: 4px;
	cursor: pointer;
	transition: background 0.1s;
}
.page-btn:hover:not(:disabled) {
	background: #f1f5f9;
}
.page-btn:disabled {
	opacity: 0.4;
	cursor: not-allowed;
}
.table-container {
	overflow-x: auto;
}
.result-table {
	width: 100%;
	border-collapse: collapse;
}
.result-table th {
	background: #f8fafc;
	padding: 8px 12px;
	border-bottom: 1px solid #e2e8f0;
	font-weight: 600;
	color: #475569;
	font-size: 12.5px;
	text-align: left;
	white-space: nowrap;
}
.result-table td {
	padding: 7px 12px;
	border-bottom: 1px solid #f1f5f9;
	color: #374151;
	font-size: 12.5px;
	word-break: break-word;
	max-width: 200px;
}
.result-table tr:hover td {
	background: #f8fafc;
}
.custom-scrollbar::-webkit-scrollbar { height: 4px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
</style>
