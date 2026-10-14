<template>
	<div>
		<div class="dataset-switch">
			<v-icon icon="mdi-view-dashboard-outline" color="primary" />
			<span>工业分析中心</span>
			<v-select :model-value="datasetId" :items="datasets" item-title="name" item-value="dataset_id"
				label="数据集" density="compact" hide-details variant="outlined" @update:model-value="switchDataset" />
		</div>
		<v-alert v-if="error" type="error" class="ma-4">{{ error }}</v-alert>
		<DashboardView v-if="datasetId === 'industrial_pdm'" :key="route.fullPath" />
		<ScaniaDashboard v-else-if="datasetId === 'scania_component_x'" :key="route.fullPath" />
		<v-alert v-else type="warning" class="ma-4">未知数据集，请重新选择。</v-alert>
	</div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import biService from '~/services/bi';
const route = useRoute();
const router = useRouter();
const datasetId = computed(() => String(route.query.dataset || 'industrial_pdm'));
const datasets = ref<Array<{ dataset_id: string; name: string }>>([]);
const error = ref('');
function switchDataset(id: string) { void router.push({ path: '/dashboard', query: { dataset: id } }); }
onMounted(async () => {
	try { datasets.value = await biService.datasets(); }
	catch { error.value = '无法读取数据集列表，请检查后端服务。'; }
});
</script>
<style scoped>
.dataset-switch { display:flex; align-items:center; gap:12px; padding:16px 24px 0; color:#334155; }
.dataset-switch > span { font-size:13px; font-weight:700; }
.dataset-switch :deep(.v-input) { max-width:300px; margin-left:auto; }
</style>
