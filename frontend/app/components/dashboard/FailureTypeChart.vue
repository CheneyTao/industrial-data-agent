<template>
	<DashboardPanel
		title="故障类型分布"
		subtitle="点击扇区筛选对应 failure"
		icon="mdi-chart-donut"
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
import type { FailureTypeItem } from '~/services/dashboard';

const props = withDefaults(
	defineProps<{ items: FailureTypeItem[]; loading?: boolean; error?: string }>(),
	{ loading: false, error: '' },
);
const emit = defineEmits<{ selectFailure: [value: string] }>();

const option = computed<EChartsOption>(() => ({
	animationDuration: 450,
	color: ['#2563eb', '#7c3aed', '#0891b2', '#d97706', '#e11d48', '#64748b'],
	tooltip: {
		trigger: 'item',
		formatter: '{b}<br/>故障次数：{c}<br/>占比：{d}%',
	},
	legend: {
		bottom: 0,
		icon: 'circle',
		textStyle: { color: '#64748b', fontSize: 11 },
	},
	series: [
		{
			type: 'pie',
			radius: ['48%', '70%'],
			center: ['50%', '44%'],
			avoidLabelOverlap: true,
			itemStyle: { borderRadius: 7, borderColor: '#fff', borderWidth: 3 },
			label: { color: '#475569', formatter: '{b}\n{d}%' },
			data: props.items.map((item) => ({
				name: item.failureType,
				value: item.failureCount,
				...item,
			})),
		},
	],
}));

function handleClick(payload: unknown) {
	const item = (payload as { data?: FailureTypeItem }).data;
	if (item?.failureType) emit('selectFailure', item.failureType);
}
</script>
