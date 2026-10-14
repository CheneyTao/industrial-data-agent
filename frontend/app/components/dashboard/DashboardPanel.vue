<template>
	<section class="dashboard-panel">
		<header class="dashboard-panel__header">
			<div class="dashboard-panel__heading">
				<span class="dashboard-panel__icon">
					<v-icon :icon="icon" size="18" />
				</span>
				<div>
					<h2>{{ title }}</h2>
					<p v-if="subtitle">{{ subtitle }}</p>
				</div>
			</div>
			<div class="dashboard-panel__actions"><slot name="actions" /></div>
		</header>
		<div class="dashboard-panel__body" :class="{ 'dashboard-panel__body--flush': flush }">
			<slot />
		</div>
	</section>
</template>

<script setup lang="ts">
withDefaults(
	defineProps<{
		title: string;
		subtitle?: string;
		icon?: string;
		flush?: boolean;
	}>(),
	{
		subtitle: '',
		icon: 'mdi-chart-box-outline',
		flush: false,
	},
);
</script>

<style scoped>
.dashboard-panel {
	min-width: 0;
	height: 100%;
	background: rgba(255, 255, 255, 0.96);
	border: 1px solid #e5eaf1;
	border-radius: 16px;
	box-shadow: 0 8px 28px rgba(15, 23, 42, 0.055);
	overflow: hidden;
}

.dashboard-panel__header {
	min-height: 68px;
	padding: 14px 18px;
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 16px;
	border-bottom: 1px solid #edf0f5;
}

.dashboard-panel__heading {
	min-width: 0;
	display: flex;
	align-items: center;
	gap: 10px;
}

.dashboard-panel__icon {
	width: 34px;
	height: 34px;
	border-radius: 10px;
	display: grid;
	place-items: center;
	color: #2563eb;
	background: #eff6ff;
}

h2 {
	font-size: 15px;
	line-height: 1.3;
	font-weight: 700;
	color: #172033;
	margin: 0;
}

p {
	font-size: 11px;
	line-height: 1.4;
	color: #7b879b;
	margin: 3px 0 0;
}

.dashboard-panel__actions {
	flex: 0 0 auto;
}

.dashboard-panel__body {
	padding: 12px 16px 16px;
}

.dashboard-panel__body--flush {
	padding: 0;
}
</style>
