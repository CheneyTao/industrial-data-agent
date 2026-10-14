<template>
	<div class="chart-frame" :style="{ minHeight: `${height}px` }">
		<v-skeleton-loader v-if="loading" type="image" class="chart-skeleton" />
		<v-alert
			v-else-if="error"
			type="error"
			variant="tonal"
			density="compact"
			class="ma-3"
		>
			{{ error }}
		</v-alert>
		<div v-else-if="empty" class="chart-empty">
			<v-icon icon="mdi-chart-box-outline" size="36" />
			<span>当前筛选范围暂无数据</span>
		</div>
		<div v-show="!loading && !error && !empty" ref="chartEl" class="chart-canvas" />
	</div>
</template>

<script setup lang="ts">
import type { ECharts, EChartsOption } from 'echarts';
import { markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = withDefaults(
	defineProps<{
		option: EChartsOption;
		loading?: boolean;
		empty?: boolean;
		error?: string;
		height?: number;
	}>(),
	{
		loading: false,
		empty: false,
		error: '',
		height: 300,
	},
);

const emit = defineEmits<{ chartClick: [payload: unknown] }>();
const chartEl = ref<HTMLElement | null>(null);
let chart: ECharts | null = null;
let resizeObserver: ResizeObserver | null = null;

async function renderChart() {
	if (props.loading || props.empty || props.error) return;
	await nextTick();
	if (!chartEl.value) return;
	if (!chart) {
		const echarts = await import('echarts');
		chart = markRaw(echarts.init(chartEl.value));
		chart.on('click', (payload) => emit('chartClick', payload));
		resizeObserver = new ResizeObserver(() => chart?.resize());
		resizeObserver.observe(chartEl.value);
	}
	chart.setOption(props.option, { notMerge: true, lazyUpdate: true });
}

watch(
	() => [props.option, props.loading, props.empty, props.error],
	() => void renderChart(),
	{ deep: true },
);

onMounted(() => void renderChart());

onBeforeUnmount(() => {
	resizeObserver?.disconnect();
	chart?.dispose();
	chart = null;
});
</script>

<style scoped>
.chart-frame {
	position: relative;
	width: 100%;
}

.chart-canvas {
	position: absolute;
	inset: 0;
}

.chart-skeleton {
	height: 100%;
	min-height: inherit;
}

.chart-empty {
	position: absolute;
	inset: 0;
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	gap: 8px;
	color: #94a3b8;
	font-size: 13px;
}
</style>
