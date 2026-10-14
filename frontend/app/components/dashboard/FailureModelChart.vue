<template>
	<DashboardPanel
		title="设备型号故障率"
		subtitle="点击型号联动全局筛选"
		icon="mdi-chart-bar"
	>
		<DashboardEChart
			:option="option"
			:loading="loading"
			:error="error"
			:empty="!items.length"
			:height="310"
			@chart-click="handleClick"
		/>
	</DashboardPanel>
</template>

<script setup lang="ts">
import type { EChartsOption } from 'echarts';
import { computed } from 'vue';
import type { FailureModelItem } from '~/services/dashboard';

const props = withDefaults(
	defineProps<{ items: FailureModelItem[]; loading?: boolean; error?: string }>(),
	{ loading: false, error: '' },
);
const emit = defineEmits<{ selectModel: [value: string] }>();

const option = computed<EChartsOption>(() => ({
	animationDuration: 450,
	grid: { left: 48, right: 18, top: 25, bottom: 52 },
	tooltip: {
		trigger: 'item',
		formatter: (params: unknown) => {
			const payload = params as { data?: FailureModelItem };
			const item = payload.data;
			return item
				? `${item.modelLabel}<br/>故障设备率：<b>${item.failureRatePct.toFixed(2)}%</b><br/>故障事件：${item.failureCount}<br/>设备数：${item.deviceCount}`
				: '';
		},
	},
	xAxis: {
		type: 'category',
		data: props.items.map((item) => item.modelLabel || item.model),
		axisLabel: { color: '#64748b', interval: 0 },
		axisLine: { lineStyle: { color: '#dbe2ea' } },
	},
	yAxis: {
		type: 'value',
		name: '故障率 %',
		max: 100,
		axisLabel: { color: '#64748b', formatter: '{value}%' },
		splitLine: { lineStyle: { color: '#edf1f5', type: 'dashed' } },
	},
	series: [
		{
			type: 'bar',
			barMaxWidth: 42,
			itemStyle: {
				borderRadius: [7, 7, 0, 0],
				color: '#3b82f6',
			},
			label: {
				show: true,
				position: 'top',
				color: '#475569',
				formatter: (params: unknown) => `${Number((params as { value: number }).value).toFixed(1)}%`,
			},
			data: props.items.map((item) => ({ value: item.failureRatePct, ...item })),
		},
	],
}));

function handleClick(payload: unknown) {
	const item = (payload as { data?: FailureModelItem }).data;
	if (item?.model) emit('selectModel', item.model);
}
</script>
