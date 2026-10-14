<template>
	<DashboardPanel
		title="Error 类型排行"
		subtitle="筛选设备范围内出现次数 Top 10"
		icon="mdi-bug-outline"
	>
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
import type { ErrorRankingItem } from '~/services/dashboard';

const props = withDefaults(
	defineProps<{ items: ErrorRankingItem[]; loading?: boolean; error?: string }>(),
	{ loading: false, error: '' },
);

const ordered = computed(() => [...props.items].reverse());
const option = computed<EChartsOption>(() => ({
	animationDuration: 450,
	grid: { left: 70, right: 28, top: 20, bottom: 32 },
	tooltip: {
		trigger: 'axis',
		axisPointer: { type: 'shadow' },
	},
	xAxis: {
		type: 'value',
		minInterval: 1,
		axisLabel: { color: '#64748b' },
		splitLine: { lineStyle: { color: '#edf1f5', type: 'dashed' } },
	},
	yAxis: {
		type: 'category',
		data: ordered.value.map((item) => item.errorId),
		axisLabel: { color: '#475569', fontWeight: 600 },
		axisTick: { show: false },
		axisLine: { show: false },
	},
	series: [
		{
			name: 'Error 次数',
			type: 'bar',
			barMaxWidth: 22,
			itemStyle: { color: '#7c3aed', borderRadius: [0, 6, 6, 0] },
			label: { show: true, position: 'right', color: '#64748b' },
			data: ordered.value.map((item) => item.errorCount),
		},
	],
}));
</script>
