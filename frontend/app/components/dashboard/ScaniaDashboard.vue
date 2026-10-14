<template>
	<div class="scania-dashboard">
		<header class="hero"><div><small>FLEET DATA INTELLIGENCE</small><h1>SCANIA · Component X</h1><p>车辆观测、匿名规格与事件标签分析</p></div><v-chip color="primary" variant="tonal">相对时间 · 原始 CSV</v-chip></header>
		<v-alert type="info" variant="tonal" density="compact">时间单位为相对 time_step；匿名特征含义未知。标签排序为已知数据展示，不代表预测风险。</v-alert>
		<div class="filters">
			<v-select v-model="split" :items="splitOptions" item-title="label" item-value="value" label="数据分组" hide-details density="compact" @update:model-value="changeSplit" />
			<v-text-field v-model="vehicle" label="车辆 ID（可选）" type="number" min="0" hide-details density="compact" />
			<v-text-field v-model="minTime" label="起始 time_step" type="number" min="0" hide-details density="compact" />
			<v-text-field v-model="maxTime" label="结束 time_step" type="number" min="0" hide-details density="compact" />
			<v-select v-model="dimension" :items="specNames" label="规格维度（匿名）" hide-details density="compact" @update:model-value="refresh" />
			<v-autocomplete v-model="feature" :items="features" label="运行特征（匿名）" hide-details density="compact" @update:model-value="refresh" />
			<v-btn color="primary" variant="flat" :loading="loading" @click="refresh">应用 / 刷新</v-btn>
			<v-btn :disabled="loading" @click="clearFilters">清除筛选</v-btn>
		</div>
		<div v-if="Object.keys(specifications).length || label !== null" class="chips">
			<v-chip v-for="(value, key) in specifications" :key="key" closable @click:close="removeSpec(String(key))">{{ key }} = {{ value }}</v-chip>
			<v-chip v-if="label !== null" closable @click:close="label = null; refresh()">{{ split === 'train' ? '维修标记' : 'class_label' }} = {{ label }}</v-chip>
		</div>
		<v-alert v-if="error" type="error" variant="tonal">{{ error }}</v-alert>
		<div class="kpis"><div v-for="card in cards" :key="card.label" class="kpi"><span>{{ card.label }}</span><strong>{{ card.value }}</strong><small>{{ card.note }}</small></div></div>
		<div class="charts">
			<DashboardPanel title="运行观测覆盖" subtitle="按 20 个相对时间单位分桶，不代表日历趋势" icon="mdi-chart-timeline-variant"><DashboardEChart :option="readoutChart" :loading="loading" :empty="!data?.trend?.length" /></DashboardPanel>
			<DashboardPanel :title="`${dimension} 规格分布`" subtitle="点击分类联动；规格物理含义未知" icon="mdi-chart-bar"><DashboardEChart :option="specChart" :loading="loading" :empty="!data?.specifications?.length" @chart-click="selectSpec" /></DashboardPanel>
			<DashboardPanel :title="split === 'train' ? '观察期维修 / 删失' : '最后观测点标签分布'" subtitle="点击标签联动同一 split 数据" icon="mdi-chart-donut"><DashboardEChart :option="labelChart" :loading="loading" :empty="!data?.labels?.length" @chart-click="selectLabel" /></DashboardPanel>
			<DashboardPanel :title="`${feature} 运行统计`" subtitle="非缺失观测均值；匿名单位，按观测行加权" icon="mdi-waveform"><DashboardEChart :option="featureChart" :loading="loading" :empty="!data?.trend?.length" /></DashboardPanel>
			<DashboardPanel v-if="split === 'train'" title="观察时长与事件" subtitle="事件和右删失分别展示；不是寿命预测" icon="mdi-timer-sand"><DashboardEChart :option="durationChart" :loading="loading" :empty="!data?.duration?.length" /></DashboardPanel>
			<DashboardPanel :title="split === 'train' ? '维修记录车辆' : '已知标签车辆优先级'" subtitle="最多 20 辆，点击车辆查看观测曲线" icon="mdi-truck-outline">
				<v-table density="compact" height="300" fixed-header><thead><tr><th>车辆 ID</th><th>{{ split === 'train' ? '维修标记' : '标签' }}</th><th>{{ dimension }}</th></tr></thead><tbody><tr v-for="row in data?.ranking || []" :key="row.vehicle_id" tabindex="0" @click="selectVehicle(row.vehicle_id)" @keydown.enter="selectVehicle(row.vehicle_id)"><td><button class="entity-link">{{ row.vehicle_id }}</button></td><td>{{ row[data.label_field] }}</td><td>{{ row[dimension] }}</td></tr></tbody></v-table>
				<p v-if="!loading && !data?.ranking?.length" class="muted">当前筛选范围暂无车辆</p>
			</DashboardPanel>
			<DashboardPanel v-if="selectedVehicle !== null" :title="`车辆 ${selectedVehicle} · ${feature}`" subtitle="最多 500 个分桶均值点" icon="mdi-chart-line" class="full"><DashboardEChart :option="entityChart" :loading="entityLoading" :empty="!entityData?.points?.length" :error="entityError" /></DashboardPanel>
		</div>
		<BiWorkbench dataset-id="scania_component_x" :filters="appliedFilters" />
		<v-expansion-panels><v-expansion-panel title="指标口径与数据限制"><v-expansion-panel-text><p v-for="item in data?.limitations || []" :key="item">{{ item }}</p><p v-for="metric in data?.metrics || []" :key="metric.metric_id">{{ metric.name }}：{{ metric.description }}</p><pre>{{ data?.evidence }}</pre></v-expansion-panel-text></v-expansion-panel></v-expansion-panels>
	</div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import type { EChartsOption } from 'echarts';
import biService, { type Filters } from '~/services/bi';
import { dashboardErrorMessage } from '~/services/dashboard';
import { finiteDisplay } from '~/utils/bi';
const route = useRoute();
const split = ref('train');
const vehicle = ref(''); const minTime = ref(''); const maxTime = ref('');
const dimension = ref('Spec_0'); const feature = ref(''); const features = ref<string[]>([]);
const specifications = ref<Record<string, string>>({}); const label = ref<number | null>(null);
const data = ref<any>(null); const error = ref(''); const loading = ref(false);
const entityData = ref<any>(null); const entityLoading = ref(false); const entityError = ref('');
const selectedVehicle = ref<number | null>(null);
const appliedFilters = ref<Filters>({ split: 'train' });
let generation = 0; let detailGeneration = 0; let controller: AbortController | undefined;
const splitOptions = [{ value:'train',label:'训练集 · 维修/删失' }, { value:'validation',label:'验证集 · 已知标签' }, { value:'test',label:'测试集 · 已知标签' }];
const specNames = Array.from({ length: 8 }, (_, index) => `Spec_${index}`);
const cards = computed(() => {
	const overview = data.value?.overview || {};
	return [
		{ label:'车辆总数',value:finiteDisplay(overview.vehicle_count,0),note:'当前 split 和筛选范围' },
		{ label:'运行观测数',value:finiteDisplay(overview.readout_count,0),note:'多时点 readout 行数' },
		{ label:split.value === 'train' ? '观察期维修车辆' : '临近事件标签车辆', value:finiteDisplay(overview.repair_vehicle_count ?? overview.label_window_vehicle_count,0),note:split.value === 'train' ? '首次维修标记 = 1' : '最后 readout 标签 1–4' },
		{ label:split.value === 'train' ? '观察期维修车辆占比' : '最大观测 time_step',value:finiteDisplay(overview.repair_vehicle_share_pct ?? overview.max_time) + (split.value === 'train' && overview.repair_vehicle_share_pct != null ? '%' : ''),note:split.value === 'train' ? '当前车辆群体内比例' : '相对时间，非日历日期' },
		{ label:'相对时间覆盖',value:`${finiteDisplay(overview.min_time)} – ${finiteDisplay(overview.max_time)}`,note:'筛选区间内观测范围' },
		{ label:'所选特征缺失率',value:finiteDisplay(overview.feature_missing_pct,2) + (overview.feature_missing_pct != null ? '%' : ''),note:feature.value || '未选择特征' },
	];
});
function axisChart(rows: any[], x: string, y: string, type: 'line' | 'bar', name: string): EChartsOption {
	return { color:['#2563eb'], tooltip:{ trigger:'axis',renderMode:'richText' }, grid:{ left:58,right:20,bottom:55,top:30 }, xAxis:{ type:'category',data:rows.map(row => String(row[x])) }, yAxis:{ type:'value',name }, series:[{ type,data:rows.map(row => row[y]),name,showSymbol:false }], dataZoom:rows.length > 20 ? [{ type:'inside' },{ type:'slider',height:14,bottom:5 }] : [] };
}
const readoutChart = computed(() => axisChart(data.value?.trend || [],'time_bucket','readout_count','bar','观测数'));
const featureChart = computed(() => axisChart(data.value?.trend || [],'time_bucket','feature_mean','line','均值'));
const specChart = computed(() => axisChart(data.value?.specifications || [],'category','vehicle_count','bar','车辆数'));
const entityChart = computed(() => axisChart(entityData.value?.points || [],'time_step','value','line',feature.value));
const labelChart = computed<EChartsOption>(() => ({ tooltip:{ trigger:'item',renderMode:'richText' },legend:{ bottom:0 },series:[{ type:'pie',radius:['40%','65%'],center:['50%','42%'],data:(data.value?.labels || []).map((row:any) => ({ name:String(row.label),value:row.vehicle_count })) }] }));
const durationChart = computed<EChartsOption>(() => {
	const rows = data.value?.duration || []; const buckets = [...new Set(rows.map((row:any) => row.time_bucket))];
	return { tooltip:{ trigger:'axis',renderMode:'richText' },legend:{ bottom:0 },grid:{ left:55,right:20,bottom:50,top:20 },xAxis:{ type:'category',data:buckets.map(String) },yAxis:{ type:'value' },series:[0,1].map(flag => ({ name:flag ? '维修事件' : '右删失',type:'bar',stack:'count',data:buckets.map(bucket => rows.find((row:any) => row.time_bucket === bucket && row.in_study_repair === flag)?.vehicle_count || 0) })) };
});
function numeric(value: string) { if (value === '' || value == null) return null; const parsed = Number(value); if (!Number.isFinite(parsed) || parsed < 0) throw new Error('车辆 ID 与 time_step 必须是非负有限数值'); return parsed; }
async function refresh() {
	const current = ++generation; controller?.abort(); controller = new AbortController(); error.value = ''; loading.value = true; data.value = null;
	++detailGeneration; selectedVehicle.value = null; entityData.value = null; entityLoading.value = false; entityError.value = '';
	try {
		const filters: Filters = { split:split.value,vehicle_id:numeric(vehicle.value),min_time:numeric(minTime.value),max_time:numeric(maxTime.value),specifications:specifications.value };
		if (label.value !== null) filters[split.value === 'train' ? 'in_study_repair' : 'class_label'] = label.value;
		appliedFilters.value = JSON.parse(JSON.stringify(filters));
		const response = await biService.dashboard('scania_component_x',filters,feature.value,dimension.value,controller.signal);
		if (current !== generation) return; data.value = response;
		if (vehicle.value !== '') void loadEntity(Number(vehicle.value));
	} catch (e) { if (current === generation) error.value = dashboardErrorMessage(e); }
	finally { if (current === generation) loading.value = false; }
}
async function loadEntity(id: number) {
	const current = ++detailGeneration; selectedVehicle.value = id; entityData.value = null; entityLoading.value = true; entityError.value = '';
	try { const response = await biService.entity('scania_component_x',id,appliedFilters.value,feature.value); if (current === detailGeneration) entityData.value = response; }
	catch (e) { if (current === detailGeneration) entityError.value = dashboardErrorMessage(e); }
	finally { if (current === detailGeneration) entityLoading.value = false; }
}
function selectSpec(event: any) { specifications.value = { ...specifications.value,[dimension.value]:event.name }; void refresh(); }
function selectLabel(event: any) { label.value = Number(event.name); void refresh(); }
function selectVehicle(id: number) { vehicle.value = String(id); void refresh(); }
function removeSpec(key: string) { delete specifications.value[key]; void refresh(); }
function clearFilters() { vehicle.value = ''; minTime.value = ''; maxTime.value = ''; specifications.value = {}; label.value = null; selectedVehicle.value = null; entityData.value = null; ++detailGeneration; void refresh(); }
function changeSplit() { clearFilters(); }
onMounted(async () => {
	try {
		const definition = await biService.definition('scania_component_x');
		features.value = definition.fields.filter((field:any) => field.role === 'measure' && field.anonymized).map((field:any) => field.name);
		feature.value = features.value[0] || '';
		if (typeof route.query.filters === 'string') { const incoming = JSON.parse(route.query.filters); split.value = incoming.split || 'train'; vehicle.value = incoming.vehicle_id == null ? '' : String(incoming.vehicle_id); minTime.value = incoming.min_time == null ? '' : String(incoming.min_time); maxTime.value = incoming.max_time == null ? '' : String(incoming.max_time); specifications.value = incoming.specifications || {}; label.value = incoming.class_label ?? incoming.in_study_repair ?? null; }
		await refresh();
	} catch (e) { error.value = dashboardErrorMessage(e); }
});
onBeforeUnmount(() => { ++generation; ++detailGeneration; controller?.abort(); });
</script>
<style scoped>
.scania-dashboard { padding:24px; display:grid; gap:18px; background:#f4f7fb; }
.hero { display:flex; justify-content:space-between; align-items:center; gap:18px; }
.hero small { color:#2563eb; font-weight:700; letter-spacing:2px; font-size:10px; }
.hero h1 { font-size:27px; color:#172033; margin:5px 0; }
.hero p,.muted { font-size:12px; color:#64748b; }
.filters { display:grid; grid-template-columns:repeat(4,minmax(130px,1fr)); gap:14px; background:white; padding:18px; border-radius:12px; }
.kpis { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:12px; }
.kpi { display:flex; flex-direction:column; gap:8px; background:white; padding:18px; border:1px solid #e2e8f0; border-radius:12px; }
.kpi span { font-size:12px; color:#64748b; }.kpi strong { font-size:23px; color:#172033; }.kpi small { font-size:10px; color:#94a3b8; }
.charts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }.full { grid-column:1/-1; }
.chips { display:flex; gap:8px; flex-wrap:wrap; }.entity-link { color:#2563eb; text-decoration:underline; cursor:pointer; }
tbody tr:hover { background:#eff6ff; cursor:pointer; }pre { font-size:11px; white-space:pre-wrap; }
@media(max-width:1100px) { .kpis { grid-template-columns:repeat(3,1fr); } }
@media(max-width:750px) { .scania-dashboard { padding:14px; }.kpis,.filters { grid-template-columns:repeat(2,minmax(0,1fr)); }.charts { grid-template-columns:1fr; }.hero { align-items:flex-start; flex-direction:column; } }
</style>
