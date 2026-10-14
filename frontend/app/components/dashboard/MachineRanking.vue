<template>
	<DashboardPanel
		title="高故障设备 Top 10"
		subtitle="点击设备进入单机分析状态"
		icon="mdi-format-list-numbered"
		flush
	>
		<v-skeleton-loader v-if="loading" type="table-row@8" class="pa-4" />
		<v-alert v-else-if="error" type="error" variant="tonal" density="compact" class="ma-4">
			{{ error }}
		</v-alert>
		<div v-else-if="!items.length" class="ranking-empty">
			<v-icon icon="mdi-database-off-outline" size="34" />
			<span>当前筛选范围暂无设备排行</span>
		</div>
		<div v-else class="ranking-table-wrap">
			<table>
				<thead>
					<tr>
						<th>排名</th>
						<th>machineID</th>
						<th>型号</th>
						<th class="number">Failure</th>
						<th class="number">Error</th>
						<th class="number">Maintenance</th>
						<th />
					</tr>
				</thead>
				<tbody>
					<tr
						v-for="(item, index) in items"
						:key="item.machineId"
						:class="{ selected: item.machineId === selectedMachineId }"
						@click="$emit('selectMachine', item.machineId)"
					>
						<td><span class="rank" :class="`rank--${Math.min(index + 1, 4)}`">{{ index + 1 }}</span></td>
						<td><strong>#{{ item.machineId }}</strong></td>
						<td>
							<div>{{ item.modelLabel || item.model }}</div>
							<small>{{ item.model }} · {{ item.age ?? '—' }}年</small>
						</td>
						<td class="number failure">{{ item.failureCount }}</td>
						<td class="number">{{ item.errorCount }}</td>
						<td class="number">{{ item.maintenanceCount }}</td>
						<td><v-icon icon="mdi-chevron-right" size="18" /></td>
					</tr>
				</tbody>
			</table>
		</div>
	</DashboardPanel>
</template>

<script setup lang="ts">
import type { MachineRankingItem } from '~/services/dashboard';

withDefaults(
	defineProps<{
		items: MachineRankingItem[];
		selectedMachineId?: number | null;
		loading?: boolean;
		error?: string;
	}>(),
	{ selectedMachineId: null, loading: false, error: '' },
);

defineEmits<{ selectMachine: [machineId: number] }>();
</script>

<style scoped>
.ranking-table-wrap {
	overflow-x: auto;
}

table {
	width: 100%;
	border-collapse: collapse;
	font-size: 12px;
}

th {
	padding: 11px 12px;
	background: #f8fafc;
	color: #8491a5;
	font-size: 10px;
	text-transform: uppercase;
	letter-spacing: 0.04em;
	font-weight: 700;
	text-align: left;
	white-space: nowrap;
}

td {
	padding: 10px 12px;
	border-top: 1px solid #eef1f5;
	color: #475569;
	white-space: nowrap;
}

tbody tr {
	cursor: pointer;
	transition: background 0.18s ease;
}

tbody tr:hover,
tbody tr.selected {
	background: #eff6ff;
}

td strong {
	color: #1e293b;
}

td small {
	display: block;
	margin-top: 2px;
	color: #94a3b8;
}

.number {
	text-align: right;
	font-variant-numeric: tabular-nums;
}

.failure {
	font-weight: 700;
	color: #dc2626;
}

.rank {
	display: inline-grid;
	place-items: center;
	width: 23px;
	height: 23px;
	border-radius: 7px;
	background: #f1f5f9;
	color: #64748b;
	font-weight: 800;
}

.rank--1 { background: #fff1f2; color: #e11d48; }
.rank--2 { background: #fff7ed; color: #ea580c; }
.rank--3 { background: #fffbeb; color: #d97706; }

.ranking-empty {
	min-height: 300px;
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	gap: 8px;
	color: #94a3b8;
}
</style>
