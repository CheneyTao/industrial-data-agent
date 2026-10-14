/*
 * Copyright 2026 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

<template>
	<v-container fluid class="pa-8 agents-container">
		<!-- Header Section -->
		<header class="d-flex align-center justify-space-between mb-8">
			<div>
				<h1 class="text-h4 font-weight-bold mb-1 text-slate-900">智能体管理</h1>
				<p class="text-body-2 text-medium-emphasis">
					创建和管理您的AI智能体,让数据分析更智能
				</p>
			</div>
			<div class="d-flex ga-3">
				<v-btn
					variant="outlined"
					prepend-icon="mdi-refresh"
					:loading="loading"
					class="text-none"
					style="border-color: #e2e8f0"
					@click="loadAgents"
				>
					刷新
				</v-btn>
				<v-btn
					color="black"
					prepend-icon="mdi-plus"
					class="text-none px-6"
					elevation="0"
					@click="goToCreateAgent"
				>
					新建智能体
				</v-btn>
			</div>
		</header>

		<!-- Filter and Search Section -->
		<v-card variant="flat" border class="rounded-lg mb-4 pa-4">
			<div class="d-flex flex-wrap ga-3 align-center">
				<v-text-field
					v-model="searchKeyword"
					placeholder="搜索智能体名称、ID或描述..."
					prepend-inner-icon="mdi-magnify"
					variant="outlined"
					density="compact"
					clearable
					hide-details
					class="search-field"
					style="max-width: 350px"
				/>

				<v-spacer />

				<v-btn-toggle
					v-model="activeFilter"
					mandatory
					rounded="pill"
					color="primary"
					class="filter-toggle"
					density="comfortable"
					variant="flat"
				>
					<v-btn value="all" variant="flat" class="px-6 text-none font-weight-medium">
						全部智能体
						<v-chip size="x-small" color="grey-lighten-3" class="ml-2">{{ agents.length }}</v-chip>
					</v-btn>
					<v-btn value="published" variant="flat" class="px-6 text-none font-weight-medium">
						已发布
						<v-chip size="x-small" color="success-lighten-3" class="ml-2">{{ publishedCount }}</v-chip>
					</v-btn>
					<v-btn value="draft" variant="flat" class="px-6 text-none font-weight-medium">
						草稿
						<v-chip size="x-small" color="warning-lighten-3" class="ml-2">{{ draftCount }}</v-chip>
					</v-btn>
					<v-btn value="offline" variant="flat" class="px-6 text-none font-weight-medium">
						已下线
						<v-chip size="x-small" color="grey-lighten-3" class="ml-2">{{ offlineCount }}</v-chip>
					</v-btn>
				</v-btn-toggle>
			</div>
		</v-card>

		<!-- Data Table -->
		<v-card variant="flat" border class="rounded-lg">
			<v-data-table
				:headers="headers"
				:items="filteredAgents"
				:loading="loading"
				item-value="id"
				hover
				hide-default-footer
			>
				<!-- ID Column -->
				<!-- eslint-disable-next-line vue/valid-v-slot -->
				<template #item.id="{ item }">
					<span class="text-body-2 font-weight-medium text-grey-darken-2">{{ item.id }}</span>
				</template>

				<!-- Avatar + Name Column -->
				<!-- eslint-disable-next-line vue/valid-v-slot -->
				<template #item.name="{ item }">
					<div class="d-flex align-center ga-3">
						<v-avatar size="40" rounded="lg">
							<v-img v-if="item.avatar" :src="item.avatar" />
							<span v-else class="text-caption">{{ getInitials(item.name) }}</span>
						</v-avatar>
						<div>
							<div class="text-subtitle-2 font-weight-bold">{{ item.name }}</div>
							<div class="text-caption text-medium-emphasis">{{ item.category || '未分类' }}</div>
							<v-btn
								prepend-icon="mdi-chat-processing-outline"
								variant="text"
								size="x-small"
								color="primary"
								class="text-none font-weight-bold px-0 mt-1"
								@click="handleChat(item)"
							>
								进入对话
							</v-btn>
						</div>
					</div>
				</template>

				<!-- Description Column -->
				<!-- eslint-disable-next-line vue/valid-v-slot -->
				<template #item.description="{ item }">
					<div class="text-body-2 text-grey-darken-1" style="max-width: 300px;">
						{{ item.description || '暂无描述' }}
					</div>
				</template>

				<!-- Tags Column -->
				<!-- eslint-disable-next-line vue/valid-v-slot -->
				<template #item.tags="{ item }">
					<div class="d-flex align-center ga-1">
						<template v-if="parseTags(item.tags).length > 0">
							<v-chip
								v-for="(tag, index) in parseTags(item.tags).slice(0, 4)"
								:key="index"
								size="small"
								color="blue"
								variant="tonal"
							>
								{{ tag }}
							</v-chip>
							<v-btn
								v-if="parseTags(item.tags).length > 4"
								variant="text"
								size="small"
								icon
								@click="showAllTags(item)"
							>
								<v-icon size="16">mdi-dots-horizontal</v-icon>
								<v-tooltip activator="parent" location="top">查看全部标签</v-tooltip>
							</v-btn>
						</template>
						<span v-else class="text-caption text-grey">暂无标签</span>
					</div>
				</template>

				<!-- Status Column -->
				<!-- eslint-disable-next-line vue/valid-v-slot -->
				<template #item.status="{ item }">
					<v-chip size="small" :color="getStatusColor(item.status)" variant="tonal">
						{{ getStatusText(item.status) }}
					</v-chip>
				</template>

				<!-- Create Time Column -->
				<!-- eslint-disable-next-line vue/valid-v-slot -->
				<template #item.createTime="{ item }">
					<span class="text-body-2 text-grey-darken-1">{{ formatTime(item.createTime) }}</span>
				</template>

				<!-- Actions Column -->
				<!-- eslint-disable-next-line vue/valid-v-slot -->
				<template #item.actions="{ item }">
					<div class="d-flex align-center ga-1">
						<v-btn
							prepend-icon="mdi-chat-processing-outline"
							variant="text"
							size="small"
							color="primary"
							class="text-none font-weight-bold"
							@click="handleChat(item)"
						>
							进入对话
						</v-btn>
						<v-btn
							icon="mdi-key-outline"
							variant="text"
							size="small"
							color="amber-darken-3"
							@click="openApiKeyDialog(item)"
						>
							<v-icon size="20" />
							<v-tooltip activator="parent" location="top">API 密钥</v-tooltip>
						</v-btn>
						<v-btn
							icon="mdi-pencil-outline"
							variant="text"
							size="small"
							color="blue-darken-1"
							@click="handleEdit(item)"
						>
							<v-icon size="20" />
							<v-tooltip activator="parent" location="top">编辑</v-tooltip>
						</v-btn>
						<v-btn
							icon="mdi-delete-outline"
							variant="text"
							size="small"
							color="error"
							@click="handleDelete(item)"
						>
							<v-icon size="20" />
							<v-tooltip activator="parent" location="top">删除</v-tooltip>
						</v-btn>
					</div>
				</template>

				<!-- No Data Slot -->
				<template #no-data>
					<div class="text-center py-16">
						<v-icon icon="mdi-robot-confused-outline" size="64" color="grey-lighten-2" class="mb-4" />
						<h3 class="text-h6 font-weight-medium text-grey-darken-1">
							{{ debouncedSearch.trim() ? '未找到匹配的智能体' : '暂无智能体' }}
						</h3>
						<p class="text-body-2 text-grey mb-6">
							{{
								debouncedSearch.trim()
									? `没有与“${debouncedSearch.trim()}”匹配的结果，请调整搜索条件`
									: activeFilter === 'all'
										? '您还没有创建任何智能体'
										: '该分类下暂无智能体'
							}}
						</p>
						<v-btn
							v-if="activeFilter === 'all' && !debouncedSearch.trim()"
							color="black"
							variant="flat"
							prepend-icon="mdi-plus"
							@click="goToCreateAgent"
						>
							新建智能体
						</v-btn>
					</div>
				</template>

				<!-- Loading Slot -->
				<template #loading>
					<v-skeleton-loader type="table-row@5" />
				</template>
			</v-data-table>
		</v-card>

		<!-- Edit Dialog -->
		<v-dialog v-model="editDialog" max-width="600" persistent>
			<v-card rounded="lg">
				<v-card-title class="d-flex align-center justify-space-between px-6 pt-6 pb-4">
					<div class="d-flex align-center">
						<v-icon icon="mdi-pencil-circle" color="blue-darken-1" class="mr-3" size="28" />
						<span class="text-h6 font-weight-bold">编辑智能体</span>
					</div>
					<v-btn icon="mdi-close" variant="text" size="small" @click="closeEditDialog" />
				</v-card-title>
				<v-divider />

				<v-card-text class="pa-6">
					<v-form ref="editFormRef">
						<div class="mb-4">
							<p class="text-body-2 font-weight-medium text-grey-darken-2 mb-2">
								智能体名称 <span class="text-error">*</span>
							</p>
							<v-text-field
								v-model="editForm.name"
								placeholder="请输入智能体名称"
								variant="outlined"
								density="compact"
								:rules="[v => !!v?.trim() || '名称不能为空']"
								hide-details="auto"
							/>
						</div>

						<div class="mb-4">
							<p class="text-body-2 font-weight-medium text-grey-darken-2 mb-2">描述</p>
							<v-textarea
								v-model="editForm.description"
								placeholder="请输入智能体描述"
								variant="outlined"
								density="compact"
								rows="3"
								hide-details
							/>
						</div>

						<div class="mb-4">
							<p class="text-body-2 font-weight-medium text-grey-darken-2 mb-2">智能体 Prompt</p>
							<v-textarea
								v-model="editForm.prompt"
								placeholder="请输入该智能体的专属指令"
								variant="outlined"
								density="compact"
								rows="3"
								hide-details
							/>
						</div>

						<div class="mb-4">
							<p class="text-body-2 font-weight-medium text-grey-darken-2 mb-2">分类</p>
							<v-text-field
								v-model="editForm.category"
								placeholder="请输入分类"
								variant="outlined"
								density="compact"
								hide-details
							/>
						</div>

						<div class="mb-4">
							<p class="text-body-2 font-weight-medium text-grey-darken-2 mb-2">标签 (逗号分隔)</p>
							<v-text-field
								v-model="editForm.tags"
								placeholder="例如: 数据分析,智能助手,推荐系统"
								variant="outlined"
								density="compact"
								hide-details
							/>
						</div>

						<div class="mb-2">
							<p class="text-body-2 font-weight-medium text-grey-darken-2 mb-2">状态</p>
							<v-select
								v-model="editForm.status"
								:items="statusOptions"
								item-title="label"
								item-value="value"
								variant="outlined"
								density="compact"
								hide-details
							/>
						</div>

						<v-switch
							v-model="editForm.humanReviewEnabled"
							label="默认启用人工计划审核"
							color="primary"
							hide-details
						/>
					</v-form>
				</v-card-text>

				<v-divider />
				<v-card-actions class="pa-4 d-flex justify-end ga-2">
					<v-btn variant="outlined" class="text-none px-6" @click="closeEditDialog">取消</v-btn>
					<v-btn
						color="blue-darken-3"
						class="text-none px-6"
						elevation="0"
						:loading="saveLoading"
						@click="saveEdit"
					>
						保存修改
					</v-btn>
				</v-card-actions>
			</v-card>
		</v-dialog>

		<!-- API Key Dialog -->
		<v-dialog v-model="apiKeyDialog" max-width="620" persistent>
			<v-card rounded="lg">
				<v-card-title class="d-flex align-center justify-space-between px-6 pt-6 pb-4">
					<div class="d-flex align-center">
						<v-icon icon="mdi-key-variant" color="amber-darken-3" class="mr-3" size="28" />
						<span class="text-h6 font-weight-bold">API 密钥 · {{ apiKeyAgent?.name }}</span>
					</div>
					<v-btn icon="mdi-close" variant="text" size="small" @click="closeApiKeyDialog" />
				</v-card-title>
				<v-divider />
				<v-card-text class="pa-6">
					<v-alert type="info" variant="tonal" class="mb-4">
						密钥只在生成或重置时完整显示一次。聊天页会把它放在请求头中，不会写入 URL。
					</v-alert>
					<div class="d-flex align-center ga-3 mb-4">
						<span class="text-body-2 font-weight-medium">鉴权状态</span>
						<v-chip :color="apiKeyEnabled ? 'success' : 'grey'" size="small" variant="tonal">
							{{ apiKeyEnabled ? '已启用' : '未启用' }}
						</v-chip>
						<span class="text-body-2 text-medium-emphasis">
							{{ apiKeyInfo?.apiKey || '尚未生成密钥' }}
						</span>
					</div>
					<v-text-field
						v-if="issuedApiKey"
						:model-value="issuedApiKey"
						label="本次生成的密钥（请立即保存）"
						variant="outlined"
						readonly
						append-inner-icon="mdi-content-copy"
						@click:append-inner="copyIssuedApiKey"
					/>
					<div class="d-flex flex-wrap ga-2">
						<v-btn
							v-if="!apiKeyInfo?.apiKey"
							color="primary"
							prepend-icon="mdi-key-plus"
							:loading="apiKeyLoading"
							@click="generateAgentApiKey"
						>
							生成并启用
						</v-btn>
						<v-btn
							v-else
							variant="outlined"
							prepend-icon="mdi-refresh"
							:loading="apiKeyLoading"
							@click="confirmResetAgentApiKey"
						>
							重置密钥
						</v-btn>
						<v-btn
							v-if="apiKeyInfo?.apiKey"
							variant="outlined"
							:color="apiKeyEnabled ? 'warning' : 'success'"
							:prepend-icon="apiKeyEnabled ? 'mdi-lock-off-outline' : 'mdi-lock-outline'"
							:loading="apiKeyLoading"
							@click="toggleAgentApiKey"
						>
							{{ apiKeyEnabled ? '停用鉴权' : '启用鉴权' }}
						</v-btn>
						<v-btn
							v-if="apiKeyInfo?.apiKey"
							variant="text"
							color="error"
							prepend-icon="mdi-delete-outline"
							:loading="apiKeyLoading"
							@click="confirmDeleteAgentApiKey"
						>
							删除密钥
						</v-btn>
					</div>
				</v-card-text>
				<v-divider />
				<v-card-actions class="pa-4 d-flex justify-end">
					<v-btn variant="text" @click="closeApiKeyDialog">关闭</v-btn>
				</v-card-actions>
			</v-card>
		</v-dialog>

		<!-- Tags Dialog -->
		<v-dialog v-model="tagsDialog" max-width="500">
			<v-card rounded="lg">
				<v-card-title class="d-flex align-center justify-space-between px-6 pt-6 pb-4">
					<div class="d-flex align-center">
						<v-icon icon="mdi-tag-multiple" color="blue" class="mr-3" size="24" />
						<span class="text-h6 font-weight-bold">全部标签</span>
					</div>
					<v-btn icon="mdi-close" variant="text" size="small" @click="tagsDialog = false" />
				</v-card-title>
				<v-divider />

				<v-card-text class="pa-6">
					<div class="d-flex flex-wrap ga-2">
						<v-chip
							v-for="(tag, index) in currentTags"
							:key="index"
							size="default"
							color="blue"
							variant="tonal"
						>
							{{ tag }}
						</v-chip>
						<div v-if="currentTags.length === 0" class="text-body-2 text-grey text-center w-100 py-4">
							暂无标签
						</div>
					</div>
				</v-card-text>

				<v-divider />
				<v-card-actions class="pa-4 d-flex justify-end">
					<v-btn variant="text" class="text-none" @click="tagsDialog = false">关闭</v-btn>
				</v-card-actions>
			</v-card>
		</v-dialog>
	</v-container>
</template>

<script setup lang="ts">
import type { Agent, AgentApiKeyResponse } from '~/services/agent/index';
import agentService from '~/services/agent/index';
import { useCrudPage } from '~/composables/useCrudPage/index';

const { $tip } = useNuxtApp();
const { showConfirm } = useConfirm();
const router = useRouter();

// ——— 额外状态 ———
const activeFilter = ref<'all' | 'published' | 'draft' | 'offline'>('all');
const searchKeyword = ref('');
const tagsDialog = ref(false);
const currentTags = ref<string[]>([]);
const editingId = ref<number | undefined>(undefined);
const apiKeyDialog = ref(false);
const apiKeyAgent = ref<Agent | null>(null);
const apiKeyInfo = ref<AgentApiKeyResponse | null>(null);
const issuedApiKey = ref('');
const apiKeyLoading = ref(false);
const apiKeyEnabled = computed(() => Boolean(apiKeyInfo.value?.apiKeyEnabled));

// ——— useCrudPage ———
const {
	loading,
	saveLoading,
	items: agents,
	dialogVisible: editDialog,
	formRef: editFormRef,
	formData: editForm,
	loadItems: loadAgents,
	openEditDialog,
	closeDialog: _closeDialog,
} = useCrudPage<Agent>({
	loadFn: () => agentService.list(),
	updateFn: async (id, data) => { const r = await agentService.update(id, data); return r != null; },
	deleteFn: (id) => agentService.delete(id),
	defaultFormFactory: () => ({
		id: undefined,
		name: '',
		description: '',
		category: '',
		tags: '',
		status: 'draft',
		prompt: '',
		humanReviewEnabled: false,
	}),
});

// ——— Debounced search ———
const debouncedSearch = ref('');
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchKeyword, (newVal) => {
	if (searchTimer) clearTimeout(searchTimer);
	searchTimer = setTimeout(() => { debouncedSearch.value = newVal; }, 300);
});

// Table Headers
const headers = [
	{ title: 'ID', key: 'id', width: '80px', sortable: false },
	{ title: '智能体', key: 'name', minWidth: '200px', sortable: false },
	{ title: '描述', key: 'description', minWidth: '250px', sortable: false },
	{ title: '标签', key: 'tags', width: '220px', sortable: false },
	{ title: '状态', key: 'status', width: '100px', sortable: false },
	{ title: '创建时间', key: 'createTime', width: '170px', sortable: false },
	{ title: '操作', key: 'actions', width: '260px', sortable: false, align: 'center' as const },
];

const statusOptions = [
	{ label: '草稿', value: 'draft' },
	{ label: '已发布', value: 'published' },
	{ label: '已下线', value: 'offline' },
];

const publishedCount = computed(() => agents.value.filter(a => a.status === 'published').length);
const draftCount = computed(() => agents.value.filter(a => a.status === 'draft').length);
const offlineCount = computed(() => agents.value.filter(a => a.status === 'offline').length);

const filteredAgents = computed(() => {
	let filtered = agents.value;
	if (activeFilter.value !== 'all') {
		filtered = filtered.filter(agent => agent.status === activeFilter.value);
	}
	if (debouncedSearch.value.trim()) {
		const keyword = debouncedSearch.value.toLowerCase().trim();
		filtered = filtered.filter(agent => {
			const nameMatch = agent.name?.toLowerCase().includes(keyword);
			const descMatch = agent.description?.toLowerCase().includes(keyword);
			const idMatch = agent.id?.toString().includes(keyword);
			return nameMatch || descMatch || idMatch;
		});
	}
	return filtered;
});

function goToCreateAgent() { router.push('/agent/new'); }

function handleChat(agent: Agent) {
	if (!agent.id) {
		$tip('智能体ID不存在，无法进入对话', { icon: 'mdi-alert-circle', color: 'error' });
		return;
	}
	router.push({ path: '/chat', query: { agentId: String(agent.id) } });
}

function handleEdit(agent: Agent) {
	editingId.value = agent.id;
	openEditDialog(agent);
}

function closeEditDialog() {
	_closeDialog();
	editingId.value = undefined;
}

async function saveEdit() {
	if (!editFormRef.value) return;
	const { valid } = await editFormRef.value.validate();
	if (!valid) return;
	if (!editingId.value) {
		$tip('智能体ID不存在', { icon: 'mdi-alert-circle', color: 'error' });
		return;
	}
	const agentId = editingId.value;
	saveLoading.value = true;
	try {
		const result = await agentService.update(agentId, {
			name: editForm.value.name?.trim(),
			description: editForm.value.description?.trim(),
			category: editForm.value.category?.trim(),
			tags: editForm.value.tags?.trim(),
			status: editForm.value.status,
			prompt: editForm.value.prompt,
			humanReviewEnabled: editForm.value.humanReviewEnabled,
		});
		if (result) {
			$tip('智能体更新成功');
			const index = agents.value.findIndex((agent) => agent.id === agentId);
			if (index !== -1) agents.value[index] = result;
			closeEditDialog();
		} else {
			$tip('智能体更新失败', { icon: 'mdi-alert-circle', color: 'error' });
		}
	} catch {
		$tip('更新请求失败,请检查网络', { icon: 'mdi-alert-circle', color: 'error' });
	} finally {
		saveLoading.value = false;
	}
}

function updateAgentApiKeyState(enabled: boolean) {
	const id = apiKeyAgent.value?.id;
	if (!id) return;
	const target = agents.value.find((agent) => agent.id === id);
	if (target) target.apiKeyEnabled = enabled ? 1 : 0;
}

async function openApiKeyDialog(agent: Agent) {
	if (!agent.id) return;
	apiKeyAgent.value = agent;
	apiKeyInfo.value = null;
	issuedApiKey.value = '';
	apiKeyDialog.value = true;
	apiKeyLoading.value = true;
	try {
		apiKeyInfo.value = await agentService.getApiKey(agent.id);
	} catch {
		$tip('加载 API 密钥状态失败', { icon: 'mdi-alert-circle', color: 'error' });
	} finally {
		apiKeyLoading.value = false;
	}
}

function closeApiKeyDialog() {
	issuedApiKey.value = '';
	apiKeyInfo.value = null;
	apiKeyAgent.value = null;
	apiKeyDialog.value = false;
}

async function generateAgentApiKey() {
	const id = apiKeyAgent.value?.id;
	if (!id) return;
	apiKeyLoading.value = true;
	try {
		const result = await agentService.generateApiKey(id);
		apiKeyInfo.value = result;
		issuedApiKey.value = result.apiKey || '';
		updateAgentApiKeyState(true);
		$tip('密钥已生成并启用，请立即保存');
	} catch {
		$tip('生成 API 密钥失败', { icon: 'mdi-alert-circle', color: 'error' });
	} finally {
		apiKeyLoading.value = false;
	}
}

function confirmResetAgentApiKey() {
	showConfirm({
		title: '确认重置密钥',
		message: '旧密钥会立即失效，所有调用方都需要更新。确定继续吗？',
		icon: 'mdi-key-alert-outline',
		confirmText: '确认重置',
		onConfirm: resetAgentApiKey,
	});
}

async function resetAgentApiKey() {
	const id = apiKeyAgent.value?.id;
	if (!id) return;
	apiKeyLoading.value = true;
	try {
		const result = await agentService.resetApiKey(id);
		apiKeyInfo.value = result;
		issuedApiKey.value = result.apiKey || '';
		updateAgentApiKeyState(true);
		$tip('密钥已重置，请立即保存新密钥');
	} catch {
		$tip('重置 API 密钥失败', { icon: 'mdi-alert-circle', color: 'error' });
	} finally {
		apiKeyLoading.value = false;
	}
}

async function toggleAgentApiKey() {
	const id = apiKeyAgent.value?.id;
	if (!id) return;
	apiKeyLoading.value = true;
	try {
		const result = await agentService.toggleApiKey(id, !apiKeyEnabled.value);
		apiKeyInfo.value = result;
		updateAgentApiKeyState(Boolean(result.apiKeyEnabled));
		$tip(result.apiKeyEnabled ? 'API 密钥鉴权已启用' : 'API 密钥鉴权已停用');
	} catch {
		$tip('更新 API 密钥状态失败', { icon: 'mdi-alert-circle', color: 'error' });
	} finally {
		apiKeyLoading.value = false;
	}
}

function confirmDeleteAgentApiKey() {
	showConfirm({
		title: '确认删除密钥',
		message: '密钥删除后无法恢复，API 鉴权也会一并停用。',
		icon: 'mdi-delete-alert-outline',
		confirmText: '确认删除',
		onConfirm: deleteAgentApiKey,
	});
}

async function deleteAgentApiKey() {
	const id = apiKeyAgent.value?.id;
	if (!id) return;
	apiKeyLoading.value = true;
	try {
		apiKeyInfo.value = await agentService.deleteApiKey(id);
		issuedApiKey.value = '';
		updateAgentApiKeyState(false);
		$tip('API 密钥已删除');
	} catch {
		$tip('删除 API 密钥失败', { icon: 'mdi-alert-circle', color: 'error' });
	} finally {
		apiKeyLoading.value = false;
	}
}

async function copyIssuedApiKey() {
	if (!issuedApiKey.value) return;
	try {
		await navigator.clipboard.writeText(issuedApiKey.value);
		$tip('密钥已复制');
	} catch {
		$tip('复制失败，请手动复制', { icon: 'mdi-alert-circle', color: 'error' });
	}
}

function showAllTags(agent: Agent) {
	currentTags.value = parseTags(agent.tags);
	tagsDialog.value = true;
}

function handleDelete(agent: Agent) {
	if (!agent.id) {
		$tip('智能体ID不存在', { icon: 'mdi-alert-circle', color: 'error' });
		return;
	}
	showConfirm({
		title: '删除确认',
		message: `确定要删除智能体 "${agent.name}" 吗?此操作不可恢复。`,
		icon: 'mdi-help-circle',
		confirmText: '确认删除',
		onConfirm: async () => {
			try {
				const success = await agentService.delete(agent.id!);
				if (success) {
					$tip('智能体删除成功');
					agents.value = agents.value.filter(a => a.id !== agent.id);
				} else {
					$tip('智能体删除失败', { icon: 'mdi-alert-circle', color: 'error' });
				}
			} catch {
				$tip('删除请求失败,请检查网络', { icon: 'mdi-alert-circle', color: 'error' });
			}
		},
	});
}

const getInitials = (name?: string) => {
	if (!name) return 'AI';
	return name.substring(0, 2).toUpperCase();
};

const parseTags = (tags?: string) => {
	if (!tags || tags.trim() === '') return [];
	return tags.split(',').map(tag => tag.trim()).filter(tag => tag);
};

const getStatusText = (status?: string) => {
	const statusMap: Record<string, string> = { published: '已发布', draft: '草稿', offline: '已下线' };
	return statusMap[status || ''] || status || '未知';
};

const getStatusColor = (status?: string) => {
	const colorMap: Record<string, string> = { published: 'success', draft: 'warning', offline: 'grey' };
	return colorMap[status || ''] || 'grey';
};

const formatTime = (time?: Date | string) => {
	if (!time) return '';
	const date = typeof time === 'string' ? new Date(time) : time;
	return date.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};

onMounted(() => { loadAgents(); });
</script>

<style scoped>
.agents-container {
	background-color: #f8fafc;
	min-height: 100%;
}

.text-slate-900 {
	color: #0f172a;
}

.filter-toggle {
	background-color: #f1f5f9 !important;
	padding: 4px !important;
}

.filter-toggle .v-btn {
	text-transform: none !important;
}

.search-field {
	border-color: #e2e8f0;
}
</style>
