<template>
	<section class="filters-card">
		<div class="filters-card__topline">
			<div>
				<div class="filters-card__eyebrow">GLOBAL FILTERS</div>
				<h2>分析范围</h2>
			</div>
			<v-btn-toggle
				:model-value="activePreset"
				variant="outlined"
				color="primary"
				density="compact"
				class="preset-toggle"
			>
				<v-btn
					v-for="preset in options?.presets || []"
					:key="preset.days"
					:value="preset.days"
					size="small"
					@click="applyPreset(preset.days)"
				>
					{{ preset.label }}
				</v-btn>
			</v-btn-toggle>
		</div>

		<div class="filters-grid">
			<v-text-field
				v-model="draft.startDate"
				type="date"
				label="开始日期"
				:min="options?.dataRange.startDate"
				:max="draft.endDate || options?.dataRange.endDate"
				variant="outlined"
				density="compact"
				hide-details
			/>
			<v-text-field
				v-model="draft.endDate"
				type="date"
				label="结束日期"
				:min="draft.startDate || options?.dataRange.startDate"
				:max="options?.dataRange.endDate"
				variant="outlined"
				density="compact"
				hide-details
			/>
			<v-select
				v-model="draft.model"
				:items="modelItems"
				label="设备型号"
				variant="outlined"
				density="compact"
				hide-details
				clearable
			/>
			<v-autocomplete
				v-model="draft.machineId"
				:items="machineItems"
				label="machineID"
				variant="outlined"
				density="compact"
				hide-details
				clearable
			/>
			<v-select
				v-model="draft.failureType"
				:items="failureItems"
				label="故障类型"
				variant="outlined"
				density="compact"
				hide-details
				clearable
			/>
			<div class="filter-actions">
				<v-btn
					variant="text"
					color="grey-darken-1"
					prepend-icon="mdi-filter-remove-outline"
					@click="clearFilters"
				>
					清除
				</v-btn>
				<v-btn
					color="primary"
					variant="flat"
					prepend-icon="mdi-filter-check-outline"
					:loading="loading"
					@click="applyFilters"
				>
					应用筛选
				</v-btn>
			</div>
		</div>
		<p v-if="validationError" class="filters-error">{{ validationError }}</p>
	</section>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import type {
	DashboardFilters,
	DashboardOptions,
} from '~/services/dashboard';

const props = withDefaults(
	defineProps<{
		modelValue: DashboardFilters;
		options: DashboardOptions | null;
		loading?: boolean;
	}>(),
	{ loading: false },
);

const emit = defineEmits<{
	'update:modelValue': [value: DashboardFilters];
	apply: [value: DashboardFilters];
}>();

const draft = reactive<DashboardFilters>({ ...props.modelValue });
const validationError = ref('');

watch(
	() => props.modelValue,
	(value) => Object.assign(draft, value),
	{ deep: true },
);

const modelItems = computed(() => [
	{ title: '全部型号', value: null },
	...(props.options?.models || []).map((item) => ({
		title: `${item.label} · ${item.value} (${item.machineCount}台)`,
		value: item.value,
	})),
]);

const machineItems = computed(() => [
	{ title: '全部设备', value: null },
	...(props.options?.machines || [])
		.filter((item) => !draft.model || item.model === draft.model)
		.map((item) => ({
			title: `#${item.id} · ${item.modelLabel || item.model}`,
			value: item.id,
		})),
]);

const failureItems = computed(() => [
	{ title: '全部故障类型', value: null },
	...(props.options?.failureTypes || []).map((item) => ({
		title: `${item.value} (${item.eventCount}次)`,
		value: item.value,
	})),
]);

function subtractDays(value: string, days: number): string {
	const parsed = new Date(`${value}T00:00:00Z`);
	parsed.setUTCDate(parsed.getUTCDate() - days);
	return parsed.toISOString().slice(0, 10);
}

const activePreset = computed(() => {
	if (!props.options || !draft.startDate || !draft.endDate) return null;
	for (const preset of props.options.presets) {
		const expected = subtractDays(draft.endDate, preset.days - 1);
		const clipped = expected < props.options.dataRange.startDate
			? props.options.dataRange.startDate
			: expected;
		if (draft.startDate === clipped) return preset.days;
	}
	return null;
});

function applyPreset(days: number) {
	if (!props.options) return;
	draft.endDate = props.options.dataRange.endDate;
	const calculated = subtractDays(draft.endDate, days - 1);
	draft.startDate =
		calculated < props.options.dataRange.startDate
			? props.options.dataRange.startDate
			: calculated;
	applyFilters();
}

function applyFilters() {
	validationError.value = '';
	if (!draft.startDate || !draft.endDate) {
		validationError.value = '请选择完整的开始和结束日期。';
		return;
	}
	if (draft.startDate > draft.endDate) {
		validationError.value = '开始日期不能晚于结束日期。';
		return;
	}
	const value = { ...draft };
	emit('update:modelValue', value);
	emit('apply', value);
}

function clearFilters() {
	if (!props.options) return;
	Object.assign(draft, {
		...props.options.defaultRange,
		machineId: null,
		model: null,
		failureType: null,
	});
	applyFilters();
}
</script>

<style scoped>
.filters-card {
	padding: 18px;
	background: rgba(255, 255, 255, 0.96);
	border: 1px solid #e5eaf1;
	border-radius: 16px;
	box-shadow: 0 8px 28px rgba(15, 23, 42, 0.05);
}

.filters-card__topline {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 16px;
	margin-bottom: 14px;
}

.filters-card__eyebrow {
	font-size: 9px;
	font-weight: 800;
	letter-spacing: 0.12em;
	color: #2563eb;
}

h2 {
	font-size: 17px;
	color: #172033;
	margin: 2px 0 0;
}

.filters-grid {
	display: grid;
	grid-template-columns: repeat(5, minmax(140px, 1fr)) auto;
	gap: 12px;
	align-items: center;
}

.filter-actions {
	display: flex;
	align-items: center;
	justify-content: flex-end;
	gap: 4px;
}

.filters-error {
	margin: 10px 0 0;
	color: #dc2626;
	font-size: 12px;
}

@media (max-width: 1280px) {
	.filters-grid {
		grid-template-columns: repeat(3, minmax(160px, 1fr));
	}
}

@media (max-width: 720px) {
	.filters-card__topline {
		align-items: flex-start;
		flex-direction: column;
	}

	.preset-toggle {
		width: 100%;
	}

	.filters-grid {
		grid-template-columns: 1fr;
	}

	.filter-actions {
		justify-content: stretch;
	}
}
</style>
