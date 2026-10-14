<template>
	<div class="kpi-grid" aria-label="关键指标">
		<article v-for="item in cards" :key="item.key" class="kpi-card">
			<div class="kpi-card__top">
				<span class="kpi-card__icon" :style="{ color: item.color, background: item.tint }">
					<v-icon :icon="item.icon" size="19" />
				</span>
				<v-tooltip v-if="item.tooltip" :text="item.tooltip" location="top">
					<template #activator="{ props: tooltipProps }">
						<v-icon v-bind="tooltipProps" icon="mdi-information-outline" size="15" color="grey" />
					</template>
				</v-tooltip>
			</div>
			<v-skeleton-loader v-if="loading" type="heading" width="76" />
			<strong v-else>{{ item.value }}</strong>
			<span>{{ item.label }}</span>
		</article>
	</div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { DashboardOverview } from '~/services/dashboard';

const props = withDefaults(
	defineProps<{
		data: DashboardOverview | null;
		loading?: boolean;
	}>(),
	{ loading: false },
);

function integer(value: number | null | undefined): string {
	return Number.isFinite(value) ? Math.round(Number(value)).toLocaleString('zh-CN') : '—';
}

function decimal(value: number | null | undefined, suffix = ''): string {
	return Number.isFinite(value) ? `${Number(value).toFixed(2)}${suffix}` : '—';
}

const cards = computed(() => {
	const data = props.data;
	return [
		{
			key: 'devices',
			label: '设备总数',
			value: integer(data?.totalDevices),
			icon: 'mdi-engine-outline',
			color: '#2563eb',
			tint: '#eff6ff',
		},
		{
			key: 'failed-devices',
			label: '故障设备数',
			value: integer(data?.failedDevices),
			icon: 'mdi-alert-octagon-outline',
			color: '#dc2626',
			tint: '#fef2f2',
		},
		{
			key: 'failures',
			label: '故障总次数',
			value: integer(data?.failureCount),
			icon: 'mdi-alert-circle-outline',
			color: '#ea580c',
			tint: '#fff7ed',
		},
		{
			key: 'rate',
			label: '当前故障率',
			value: decimal(data?.failureRatePct, '%'),
			icon: 'mdi-percent-outline',
			color: '#d97706',
			tint: '#fffbeb',
			tooltip: data?.definitions?.failureRatePct,
		},
		{
			key: 'errors',
			label: 'Error 总次数',
			value: integer(data?.errorCount),
			icon: 'mdi-bug-outline',
			color: '#7c3aed',
			tint: '#f5f3ff',
		},
		{
			key: 'maintenance',
			label: 'Maintenance 总次数',
			value: integer(data?.maintenanceCount),
			icon: 'mdi-tools',
			color: '#0891b2',
			tint: '#ecfeff',
		},
		{
			key: 'age',
			label: '平均设备年龄',
			value: decimal(data?.averageDeviceAge, ' 年'),
			icon: 'mdi-calendar-clock-outline',
			color: '#475569',
			tint: '#f8fafc',
		},
		{
			key: 'mtbf',
			label: 'MTBF',
			value: decimal(data?.mtbfHours, ' h'),
			icon: 'mdi-timer-sand',
			color: '#059669',
			tint: '#ecfdf5',
			tooltip: data?.definitions?.mtbfHours,
		},
		{
			key: 'recent7',
			label: '近7天故障数',
			value: integer(data?.recent7FailureCount),
			icon: 'mdi-calendar-alert-outline',
			color: '#e11d48',
			tint: '#fff1f2',
		},
		{
			key: 'recent30',
			label: '近30天故障率',
			value: decimal(data?.recent30FailureRatePct, '%'),
			icon: 'mdi-chart-timeline-variant',
			color: '#0f766e',
			tint: '#f0fdfa',
			tooltip: data?.definitions?.recent30FailureRatePct,
		},
		{
			key: 'mttr',
			label: 'MTTR',
			value: decimal(data?.mttrHours, ' h'),
			icon: 'mdi-timer-wrench-outline',
			color: '#64748b',
			tint: '#f8fafc',
			tooltip: data?.mttrAvailability,
		},
	];
});
</script>

<style scoped>
.kpi-grid {
	display: grid;
	grid-template-columns: repeat(6, minmax(0, 1fr));
	gap: 12px;
}

.kpi-card {
	min-width: 0;
	min-height: 126px;
	padding: 14px 15px;
	background: rgba(255, 255, 255, 0.97);
	border: 1px solid #e5eaf1;
	border-radius: 14px;
	box-shadow: 0 7px 22px rgba(15, 23, 42, 0.045);
	display: flex;
	flex-direction: column;
}

.kpi-card__top {
	display: flex;
	align-items: center;
	justify-content: space-between;
	margin-bottom: 10px;
}

.kpi-card__icon {
	width: 32px;
	height: 32px;
	border-radius: 9px;
	display: grid;
	place-items: center;
}

.kpi-card strong {
	font-size: clamp(19px, 1.6vw, 25px);
	line-height: 1.15;
	font-weight: 760;
	letter-spacing: -0.02em;
	color: #172033;
	white-space: nowrap;
}

.kpi-card > span {
	margin-top: 5px;
	font-size: 11px;
	color: #7b879b;
}

@media (max-width: 1500px) {
	.kpi-grid {
		grid-template-columns: repeat(4, minmax(0, 1fr));
	}
}

@media (max-width: 900px) {
	.kpi-grid {
		grid-template-columns: repeat(2, minmax(0, 1fr));
	}
}
</style>
