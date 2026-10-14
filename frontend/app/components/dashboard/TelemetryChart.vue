<template>
	<DashboardPanel
		:title="machineId ? `设备 #${machineId} 状态参数趋势` : '设备状态参数趋势'"
		:subtitle="subtitle"
		icon="mdi-pulse"
	>
		<template #actions>
			<v-chip v-if="data?.sampled" size="x-small" color="primary" variant="tonal">
				{{ data.sourcePointCount.toLocaleString('zh-CN') }} 点聚合为 {{ data.returnedPointCount }} 点
			</v-chip>
		</template>
		<DashboardEChart
			:option="option"
			:loading="loading"
			:error="error"
			:empty="!data?.points.length"
			:height="350"
		/>
	</DashboardPanel>
</template>

<script setup lang="ts">
import type { EChartsOption } from 'echarts';
import { computed } from 'vue';
import type { TelemetryResponse } from '~/services/dashboard';

const props = withDefaults(
	defineProps<{
		data: TelemetryResponse | null;
		machineId?: number | null;
		loading?: boolean;
		error?: string;
	}>(),
	{ machineId: null, loading: false, error: '' },
);

const subtitle = computed(() =>
	props.machineId
		? '小时级遥测经数据库端自适应采样；可缩放查看局部波动'
		: '请从设备排行或筛选器选择一台设备',
);

const metrics = [
	{ key: 'volt', name: '电压', color: '#2563eb' },
	{ key: 'rotate', name: '转速', color: '#7c3aed' },
	{ key: 'pressure', name: '压力', color: '#0891b2' },
	{ key: 'vibration', name: '振动', color: '#e11d48' },
] as const;

const option = computed<EChartsOption>(() => ({
	animationDuration: 300,
	color: metrics.map((metric) => metric.color),
	grid: { left: 54, right: 22, top: 50, bottom: 62 },
	legend: {
		top: 4,
		icon: 'roundRect',
		itemWidth: 14,
		textStyle: { color: '#64748b', fontSize: 11 },
	},
	tooltip: { trigger: 'axis' },
	dataZoom: [
		{ type: 'inside', start: 0, end: 100 },
		{ type: 'slider', height: 18, bottom: 12, borderColor: 'transparent' },
	],
	xAxis: {
		type: 'category',
		boundaryGap: false,
		data: props.data?.points.map((point) => point.datetime) || [],
		axisLabel: { color: '#64748b', hideOverlap: true },
		axisLine: { lineStyle: { color: '#dbe2ea' } },
	},
	yAxis: {
		type: 'value',
		scale: true,
		axisLabel: { color: '#64748b' },
		splitLine: { lineStyle: { color: '#edf1f5', type: 'dashed' } },
	},
	series: metrics.map((metric) => ({
		name: metric.name,
		type: 'line',
		smooth: true,
		showSymbol: false,
		lineStyle: { width: 1.8 },
		data: props.data?.points.map((point) => point[metric.key]) || [],
	})),
}));
</script>
