/* * Copyright 2026 the original author or authors. * * Licensed under the
Apache License, Version 2.0 (the "License"); * you may not use this file except
in compliance with the License. * You may obtain a copy of the License at * *
https://www.apache.org/licenses/LICENSE-2.0 * * Unless required by applicable
law or agreed to in writing, software * distributed under the License is
distributed on an "AS IS" BASIS, * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
either express or implied. * See the License for the specific language governing
permissions and * limitations under the License. */

<template>
	<div class="input-area">
		<!-- Status / Info bar -->
		<div class="status-bar">
			<div class="status-chips">
				<v-text-field
					v-if="store.currentAgentApiKeyEnabled"
					v-model="store.agentApiKey"
					type="password"
					placeholder="输入智能体 API Key"
					prepend-inner-icon="mdi-key-outline"
					variant="outlined"
					density="compact"
					hide-details
					autocomplete="off"
					class="agent-key-input"
					:disabled="store.isStreaming"
				/>

				<!-- Datasource selector -->
				<v-menu
					v-model="showDsMenu"
					location="top start"
					:offset="8"
					:min-width="260"
					:max-width="420"
					:max-height="420"
					:disabled="store.isStreaming"
				>
					<template #activator="{ props }">
						<button
							v-bind="props"
							type="button"
							class="status-chip status-chip--ds"
							:disabled="store.isStreaming"
							:aria-label="`选择数据库，当前：${store.activeDatasource?.name || '未选择'}`"
							:title="store.activeDatasource?.name"
						>
							<v-icon size="13" color="#64748b">mdi-database-outline</v-icon>
							<span class="status-chip__label">{{
								store.activeDatasource?.name || '选择数据库'
							}}</span>
							<v-icon size="13" color="#94a3b8">{{
								showDsMenu ? 'mdi-chevron-up' : 'mdi-chevron-down'
							}}</v-icon>
						</button>
					</template>
					<v-list
						class="chip-dropdown"
						role="menu"
						aria-label="数据库连接"
						density="compact"
					>
						<div class="selector-heading">
							<strong>数据连接（{{ store.allDatasources.length }}）</strong>
							<span>切换会更新当前智能体的数据源</span>
						</div>
						<v-list-item
							v-for="ds in store.allDatasources"
							:key="ds.id"
							class="chip-dropdown-item"
							role="menuitemradio"
							:aria-checked="store.activeDatasource?.id === ds.id"
							:class="{ active: store.activeDatasource?.id === ds.id }"
							@click="selectDs(ds)"
						>
							<div class="item-name">{{ ds.name }}</div>
							<div class="item-detail">{{ datasourceCaption(ds) }}</div>
							<div v-if="ds.description" class="item-detail">
								{{ ds.description }}
							</div>
							<template #append>
								<span class="item-tag">{{ ds.type?.toUpperCase() }}</span>
							</template>
						</v-list-item>
						<div v-if="!store.allDatasources.length" class="selector-empty">
							暂无可用数据连接
						</div>
					</v-list>
				</v-menu>

				<!-- Model selector -->
				<v-menu
					v-model="showModelMenu"
					location="top start"
					:offset="8"
					:min-width="220"
					:max-width="420"
					:max-height="420"
					:disabled="store.isStreaming || store.chatModels.length === 0"
				>
					<template #activator="{ props }">
						<button
							v-bind="props"
							type="button"
							class="status-chip status-chip--model"
							:disabled="store.isStreaming || store.chatModels.length === 0"
							:aria-label="`选择 AI 模型，当前：${store.activeModelConfig?.modelName || '未选择'}`"
							:title="store.activeModelConfig?.modelName"
						>
							<v-icon size="13" color="#3b82f6">mdi-lightning-bolt</v-icon>
							<span class="status-chip__label">{{
								store.activeModelConfig?.modelName || '选择AI模型'
							}}</span>
							<v-icon size="13" color="#94a3b8">{{
								showModelMenu ? 'mdi-chevron-up' : 'mdi-chevron-down'
							}}</v-icon>
						</button>
					</template>
					<v-list
						class="chip-dropdown"
						role="menu"
						aria-label="AI 模型"
						density="compact"
					>
						<v-list-item
							v-for="m in store.chatModels"
							:key="m.id"
							class="chip-dropdown-item"
							role="menuitemradio"
							:aria-checked="store.activeModelConfig?.id === m.id"
							:class="{ active: store.activeModelConfig?.id === m.id }"
							@click="selectModel(m)"
						>
							<div class="item-name">{{ m.modelName }}</div>
							<template #append
								><span class="item-tag">{{ m.provider }}</span></template
							>
						</v-list-item>
					</v-list>
				</v-menu>
			</div>
		</div>

		<ChatAttachments />
		<p v-if="sendError" class="send-error" role="alert">{{ sendError }}</p>
		<!-- Textarea -->
		<div class="textarea-wrap">
			<textarea
				ref="textareaRef"
				v-model="inputText"
				class="chat-textarea"
				:disabled="store.isStreaming || store.showHumanFeedback"
				placeholder="在这里提问，例如：'分析上月各产品的销售增长情况'..."
				rows="3"
				@keydown.enter.exact.prevent="handleSend"
				@input="autoResize"
			/>
		</div>

		<!-- Bottom action bar -->
		<div class="action-bar">
			<div class="action-bar-left">
				<div class="extra-options">
					<label
						class="option-chip"
						:class="{ active: store.requestOptions.humanFeedback }"
					>
						<input
							v-model="store.requestOptions.humanFeedback"
							type="checkbox"
							:disabled="store.requestOptions.nl2sqlOnly || store.isStreaming"
							class="hidden-checkbox"
						/>
						<v-icon size="11">mdi-account-check-outline</v-icon>
						人工反馈
					</label>
					<label
						class="option-chip"
						:class="{ active: store.requestOptions.nl2sqlOnly }"
					>
						<input
							v-model="store.requestOptions.nl2sqlOnly"
							type="checkbox"
							:disabled="store.isStreaming"
							class="hidden-checkbox"
							@change="onNl2sqlChange"
						/>
						<v-icon size="11">mdi-database-search-outline</v-icon>
						仅NL2SQL
					</label>
					<label
						class="option-chip"
						:class="{ active: store.requestOptions.showSqlResults }"
					>
						<input
							v-model="store.requestOptions.showSqlResults"
							type="checkbox"
							:disabled="store.isStreaming"
							class="hidden-checkbox"
						/>
						<v-icon size="11">mdi-table-eye</v-icon>
						显示SQL结果
					</label>
				</div>
			</div>

			<div class="action-bar-right">
				<v-btn
					v-if="!store.isStreaming"
					class="send-btn"
					:disabled="
						(!inputText.trim() && !attachments.ready.length) ||
						attachments.pending || sending || !store.currentSession ||
						store.showHumanFeedback ||
						(store.currentAgentApiKeyEnabled && !store.agentApiKey.trim())
					"
					@click="handleSend"
				>
					发送
					<v-icon size="16" class="ml-1">mdi-arrow-right</v-icon>
				</v-btn>
				<v-btn v-else class="stop-btn" @click="handleStop">
					<v-icon size="16" color="white">mdi-stop</v-icon>
					停止
				</v-btn>
			</div>
		</div>

		<!-- Human Feedback Panel -->
		<Transition name="slide-up">
			<div v-if="store.showHumanFeedback" class="human-feedback-panel">
				<div class="feedback-header">
					<v-icon color="warning" size="16" class="mr-1"
						>mdi-account-question-outline</v-icon
					>
					<span>请确认执行计划</span>
				</div>
				<textarea
					v-model="store.feedbackContent"
					class="feedback-textarea"
					rows="2"
					placeholder="输入您的反馈意见（留空表示接受计划）"
				/>
				<div class="feedback-actions">
					<v-btn
						class="feedback-btn feedback-btn--accept"
						@click="store.submitFeedback(false, store.feedbackContent)"
					>
						<v-icon size="14" class="mr-1">mdi-check</v-icon>接受计划
					</v-btn>
					<v-btn
						class="feedback-btn feedback-btn--reject"
						@click="store.submitFeedback(true, store.feedbackContent)"
					>
						<v-icon size="14" class="mr-1">mdi-close</v-icon>拒绝重规划
					</v-btn>
				</div>
			</div>
		</Transition>
	</div>
</template>

<script setup lang="ts">
import { useChatStore } from '~/stores/chat';
import { datasourceCaption } from '~/utils/datasourceSelection';
import { useChatAttachments } from '~/stores/chatAttachments';
import ChatAttachments from './ChatAttachments.vue';

const store = useChatStore();
const attachments = useChatAttachments();
const sendError = ref('');
const sending = ref(false);
const inputText = ref('');
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const showDsMenu = ref(false);
const showModelMenu = ref(false);

watch(showDsMenu, (open) => {
	if (open) showModelMenu.value = false;
});
watch(showModelMenu, (open) => {
	if (open) showDsMenu.value = false;
});
watch(
	() => store.isStreaming,
	(streaming) => {
		if (streaming) {
			showDsMenu.value = false;
			showModelMenu.value = false;
		}
	},
);

async function selectDs(ds: (typeof store.allDatasources)[0]) {
	showDsMenu.value = false;
	await store.switchDatasource(ds);
}

async function selectModel(m: (typeof store.chatModels)[0]) {
	showModelMenu.value = false;
	if (m.id !== undefined) await store.switchModel(m.id);
}

function onNl2sqlChange() {
	if (store.requestOptions.nl2sqlOnly) {
		store.requestOptions.humanFeedback = false;
	}
}

function autoResize() {
	const el = textareaRef.value;
	if (!el) return;
	el.style.height = 'auto';
	el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

async function handleSend() {
	const query = inputText.value.trim() || (attachments.ready.length ? '请分析所选附件，先说明数据范围与质量，再给出有数据证据的结论。' : '');
	if (!query) return;
	if (!store.currentSession) return;
	if (store.isStreaming || sending.value || store.showHumanFeedback || attachments.pending) return;
	if (store.currentAgentApiKeyEnabled && !store.agentApiKey.trim()) return;
	const sessionId = store.currentSession.id;
	sendError.value = '';
	sending.value = true;

	try {
		await store.sendMessage(query, attachments.ready);
		if (store.currentSession?.id === sessionId) {
			inputText.value = '';
			nextTick(() => { if (textareaRef.value) textareaRef.value.style.height = 'auto'; });
		}
	} catch (e) {
		sendError.value = e instanceof Error ? e.message : '发送失败，请重试';
	} finally { sending.value = false; }
}

async function handleStop() {
	try {
		await store.stopStreaming();
	} catch (e) {
		console.error('停止失败', e);
	}
}
</script>

<style scoped>
.send-error { color:#b91c1c; font-size:12px; margin:6px 0; }
.input-area {
	flex-shrink: 0;
	background: white;
	border-top: 1px solid #e8edf2;
	padding: 12px 32px 16px;
}

/* ── Status bar ──────────────────────────────────────────────────────────────── */
.status-bar {
	margin-bottom: 10px;
}
.status-chips {
	display: flex;
	align-items: center;
	gap: 8px;
	flex-wrap: wrap;
}

.agent-key-input {
	max-width: 245px;
	min-width: 200px;
}

.status-chip {
	display: inline-flex;
	align-items: center;
	gap: 5px;
	padding: 4px 10px;
	background: #f1f5f9;
	border: 1px solid #e2e8f0;
	border-radius: 20px;
	font-size: 12.5px;
	color: #475569;
	cursor: pointer;
	user-select: none;
	max-width: 100%;
	min-width: 0;
	transition:
		border-color 0.1s,
		background 0.1s;
}
.status-chip__label {
	min-width: 0;
	white-space: normal;
	overflow-wrap: anywhere;
	text-align: left;
}
.status-chip:hover:not(:disabled) {
	border-color: #94a3b8;
}
.status-chip:disabled {
	opacity: 0.5;
	cursor: not-allowed;
}
.status-chip--model {
	background: #eff6ff;
	border-color: #bfdbfe;
	color: #1d4ed8;
}
.status-chip--model:hover:not(:disabled) {
	border-color: #93c5fd;
}

.chip-dropdown {
	background: white;
	border: 1px solid #e2e8f0;
	border-radius: 10px;
	box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
	max-height: inherit;
	overflow-y: auto;
	overscroll-behavior: contain;
	padding: 4px 0;
}

.chip-dropdown-item {
	padding: 10px 14px;
	font-size: 13px;
	color: #334155;
	cursor: pointer;
	transition: background 0.1s;
}
.chip-dropdown-item:hover {
	background: #f1f5f9;
}
.chip-dropdown-item.active {
	background: #eff6ff;
	color: #2563eb;
	font-weight: 500;
}
.item-name {
	white-space: normal;
	overflow-wrap: anywhere;
	line-height: 1.5;
}
.item-detail {
	margin-top: 3px;
	font-size: 11px;
	color: #64748b;
	white-space: normal;
	overflow-wrap: anywhere;
}
.selector-heading {
	display: grid;
	gap: 3px;
	padding: 10px 14px;
	border-bottom: 1px solid #e2e8f0;
	font-size: 12px;
}
.selector-heading span,
.selector-empty {
	font-size: 11px;
	color: #64748b;
}
.selector-empty {
	padding: 12px 14px;
}
.item-tag {
	margin-left: 10px;
	flex-shrink: 0;
	font-size: 11px;
	color: #94a3b8;
	background: #f1f5f9;
	border-radius: 4px;
	padding: 1px 5px;
}

/* ── Textarea ────────────────────────────────────────────────────────────────── */
.textarea-wrap {
	background: #f8fafc;
	border: 1.5px solid #e2e8f0;
	border-radius: 14px;
	overflow: hidden;
	transition: border-color 0.15s;
}
.textarea-wrap:focus-within {
	border-color: #3b82f6;
	background: #fff;
}
.chat-textarea {
	display: block;
	width: 100%;
	padding: 14px 16px 8px;
	background: none;
	border: none;
	outline: none;
	resize: vertical;
	font-size: 14.5px;
	line-height: 1.6;
	color: #1e293b;
	font-family: inherit;
	min-height: 80px;
	max-height: 300px;
}
.chat-textarea::placeholder {
	color: #94a3b8;
}
.chat-textarea:disabled {
	opacity: 0.6;
	cursor: not-allowed;
}

/* ── Action bar ──────────────────────────────────────────────────────────────── */
.action-bar {
	display: flex;
	align-items: center;
	justify-content: space-between;
	padding: 6px 4px 0;
}
.action-bar-left {
	display: flex;
	align-items: center;
	gap: 4px;
	flex-wrap: wrap;
}
/* ── Extra options ───────────────────────────────────────────────────────────── */
.extra-options {
	display: flex;
	align-items: center;
	gap: 6px;
	flex-wrap: wrap;
}
.option-chip {
	display: inline-flex;
	align-items: center;
	gap: 4px;
	padding: 3px 10px;
	background: #f8fafc;
	border: 1px solid #e2e8f0;
	border-radius: 16px;
	font-size: 12px;
	color: #64748b;
	cursor: pointer;
	transition:
		border-color 0.1s,
		background 0.1s;
	user-select: none;
}
.option-chip:hover {
	border-color: #3b82f6;
	color: #3b82f6;
}
.option-chip.active {
	background: #eff6ff;
	border-color: #3b82f6;
	color: #2563eb;
}
.hidden-checkbox {
	position: absolute;
	opacity: 0;
	width: 0;
	height: 0;
}

/* ── Send button ─────────────────────────────────────────────────────────────── */
.send-btn {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	padding: 10px 24px;
	background: #2563eb;
	color: white;
	border: none;
	border-radius: 24px;
	font-size: 14px;
	font-weight: 600;
	cursor: pointer;
	transition:
		background 0.15s,
		opacity 0.15s;
	white-space: nowrap;
}
.send-btn:hover:not(:disabled) {
	background: #1d4ed8;
}
.send-btn:disabled {
	opacity: 0.4;
	cursor: not-allowed;
}
.send-icon {
	flex-shrink: 0;
}

/* ── Stop button ─────────────────────────────────────────────────────────────── */
.stop-btn {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	padding: 10px 20px;
	background: #ef4444;
	color: white;
	border: none;
	border-radius: 24px;
	font-size: 14px;
	font-weight: 600;
	cursor: pointer;
	transition: background 0.15s;
}
.stop-btn:hover {
	background: #dc2626;
}

/* ── Human feedback ──────────────────────────────────────────────────────────── */
.human-feedback-panel {
	margin-top: 10px;
	background: #fffbeb;
	border: 1px solid #fde68a;
	border-radius: 10px;
	padding: 12px 14px;
}
.feedback-header {
	display: flex;
	align-items: center;
	font-size: 13px;
	font-weight: 600;
	color: #92400e;
	margin-bottom: 8px;
}
.feedback-textarea {
	width: 100%;
	background: white;
	border: 1px solid #fde68a;
	border-radius: 6px;
	padding: 8px 10px;
	font-size: 13px;
	resize: none;
	outline: none;
	color: #1e293b;
	font-family: inherit;
	margin-bottom: 8px;
}
.feedback-actions {
	display: flex;
	gap: 8px;
}
.feedback-btn {
	display: inline-flex;
	align-items: center;
	padding: 6px 16px;
	border-radius: 6px;
	font-size: 12.5px;
	font-weight: 600;
	border: none;
	cursor: pointer;
	transition: opacity 0.1s;
}
.feedback-btn--accept {
	background: #22c55e;
	color: white;
}
.feedback-btn--reject {
	background: white;
	color: #ef4444;
	border: 1px solid #ef4444;
}
.feedback-btn:hover {
	opacity: 0.85;
}

/* ── Transitions ─────────────────────────────────────────────────────────────── */
.fade-enter-active,
.fade-leave-active {
	transition: opacity 0.15s;
}
.fade-enter-from,
.fade-leave-to {
	opacity: 0;
}
.slide-up-enter-active,
.slide-up-leave-active {
	transition: all 0.2s ease;
}
.slide-up-enter-from,
.slide-up-leave-to {
	transform: translateY(10px);
	opacity: 0;
}

@media (max-width: 768px) {
	.input-area {
		padding: 10px 12px 12px;
	}
}
</style>
