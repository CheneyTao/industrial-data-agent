<template>
	<DashboardPanel
		title="故障趋势"
		subtitle="按日 / 周 / 月聚合来源故障事件"
		icon="mdi-chart-line"
	>
		<template #actions>
			<v-btn-toggle
				:model-value="granularity"
				mandatory
				density="compact"
				variant="outlined"
				color="primary"
			>
				<v-btn value="day" size="x-small" @click="$emit('granularity', 'day')">日</v-btn>
				<v-btn value="week" size="x-small" @click="$emit('granularity', 'week')">周</v-btn>
				<v-btn value="month" size="x-small" @click="$emit('granularity', 'month')">月</v-btn>
			</v-btn-toggle>
		</template>
		<DashboardEChart
			:option="option"
			:loading="loading"
			:error="error"
			:empty="!items.length"
			:height="310"
		/>
	</DashboardPanel>
</template>

<script setup lang="ts">
import type { EChartsOption } from 'echarts';
import { computed } from 'vue';
import type { FailureTrendItem } from '~/services/dashboard';

const props = withDefaults(
	defineProps<{
		items: FailureTrendItem[];
		granularity: 'day' | 'week' | 'month';
		loading?: boolean;
		error?: string;
	}>(),
	{ loading: false, error: '' },
);

defineEmits<{ granularity: [value: 'day' | 'week' | 'month'] }>();

const option = computed<EChartsOption>(() => ({
	animationDuration: 450,
	color: ['#2563eb'],
	grid: { left: 48, right: 18, top: 25, bottom: 46 },
	tooltip: {
		trigger: 'axis',
		backgroundColor: '#172033',
		borderWidth: 0,
		textStyle: { color: '#fff', fontSize: 12 },
	},
	xAxis: {
		type: 'category',
		data: props.items.map((item) => item.period),
		boundaryGap: false,
		axisLine: { lineStyle: { color: '#dbe2ea' } },
		axisLabel: { color: '#64748b', hideOverlap: true },
	},
	yAxis: {
		type: 'value',
		minInterval: 1,
		name: '故障次数',
		nameTextStyle: { color: '#94a3b8', padding: [0, 0, 0, 8] },
		splitLine: { lineStyle: { color: '#edf1f5', type: 'dashed' } },
		axisLabel: { color: '#64748b' },
	},
	series: [
		{
			name: '故障次数',
			type: 'line',
			smooth: true,
			showSymbol: props.items.length < 40,
			symbolSize: 6,
			lineStyle: { width: 3 },
			areaStyle: {
				color: {
					type: 'linear',
					x: 0,
					y: 0,
					x2: 0,
					y2: 1,
					colorStops: [
						{ offset: 0, color: 'rgba(37, 99, 235, 0.24)' },
						{ offset: 1, color: 'rgba(37, 99, 235, 0.01)' },
					],
				},
			},
			data: props.items.map((item) => item.failureCount),
		},
	],
}));
</script>
