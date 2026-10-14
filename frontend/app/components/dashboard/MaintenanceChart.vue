<template>
	<DashboardPanel
		title="维修与故障关系"
		:subtitle="data?.definition || '维修前后同部件故障的时序关系'"
		icon="mdi-tools"
	>
		<template #actions>
			<v-chip v-if="data?.summary" size="x-small" color="warning" variant="tonal">
				后续故障关联率 {{ data.summary.followupFailureRatePct.toFixed(2) }}%
			</v-chip>
		</template>
		<DashboardEChart
			:option="option"
			:loading="loading"
			:error="error"
			:empty="!data?.items.length"
			:height="350"
		/>
	</DashboardPanel>
</template>

<script setup lang="ts">
import type { EChartsOption } from 'echarts';
import { computed } from 'vue';
import type { MaintenanceResponse } from '~/services/dashboard';

const props = withDefaults(
	defineProps<{
		data: MaintenanceResponse | null;
		loading?: boolean;
		error?: string;
	}>(),
	{ loading: false, error: '' },
);

const option = computed<EChartsOption>(() => ({
	animationDuration: 450,
	color: ['#94a3b8', '#2563eb', '#e11d48'],
	grid: { left: 48, right: 52, top: 52, bottom: 42 },
	legend: {
		top: 4,
		textStyle: { color: '#64748b', fontSize: 11 },
	},
	tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
	xAxis: {
		type: 'category',
		data: props.data?.items.map((item) => item.maintenanceType) || [],
		axisLabel: { color: '#64748b' },
		axisLine: { lineStyle: { color: '#dbe2ea' } },
	},
	yAxis: [
		{
			type: 'value',
			name: '事件数',
			minInterval: 1,
			axisLabel: { color: '#64748b' },
			splitLine: { lineStyle: { color: '#edf1f5', type: 'dashed' } },
		},
		{
			type: 'value',
			name: '关联率',
			max: 100,
			axisLabel: { color: '#64748b', formatter: '{value}%' },
			splitLine: { show: false },
		},
	],
	series: [
		{
			name: '维修前7天故障',
			type: 'bar',
			barMaxWidth: 28,
			itemStyle: { borderRadius: [5, 5, 0, 0] },
			data: props.data?.items.map((item) => item.sameComponentFailuresBefore7d) || [],
		},
		{
			name: '维修后7天故障',
			type: 'bar',
			barMaxWidth: 28,
			itemStyle: { borderRadius: [5, 5, 0, 0] },
			data: props.data?.items.map((item) => item.sameComponentFailuresAfter7d) || [],
		},
		{
			name: '后续故障关联率',
			type: 'line',
			yAxisIndex: 1,
			smooth: true,
			symbolSize: 7,
			lineStyle: { width: 2.4 },
			data: props.data?.items.map((item) => item.followupFailureRatePct) || [],
		},
	],
}));
</script>
