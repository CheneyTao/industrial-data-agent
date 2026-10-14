/* * Copyright 2026 the original author or authors. * * Licensed under the
Apache License, Version 2.0 (the "License"); * you may not use this file except
in compliance with the License. * You may obtain a copy of the License at * *
https://www.apache.org/licenses/LICENSE-2.0 * * Unless required by applicable
law or agreed to in writing, software * distributed under the License is
distributed on an "AS IS" BASIS, * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
either express or implied. * See the License for the specific language governing
permissions and * limitations under the License. */

<template>
	<div class="chat-page">
		<ChatSidebar />
		<div
			class="chat-body"
			@dragenter="dragEnter"
			@dragover="dragOver"
			@dragleave="dragLeave"
			@drop="dropFiles"
		>
			<div v-if="dragDepth > 0" class="file-drop-overlay" role="status">
				<v-icon size="42">mdi-cloud-upload-outline</v-icon
				><strong>松开即可添加文件</strong
				><span>先识别与预览，不会直接写入数据库</span>
			</div>
			<ChatMessageList />
			<ChatInputArea />
		</div>
	</div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, watch, computed, ref } from 'vue';
import { useChatAttachments } from '~/stores/chatAttachments';
import { useChatStore } from '~/stores/chat';
import agentService from '~/services/agent/index';
import ChatSidebar from '~/components/chat/ChatSidebar.vue';
import ChatMessageList from '~/components/chat/ChatMessageList.vue';
import ChatInputArea from '~/components/chat/ChatInputArea.vue';

const route = useRoute();
const store = useChatStore();
const attachments = useChatAttachments();
const dragDepth = ref(0);
const hasFiles = (event: DragEvent) =>
	Array.from(event.dataTransfer?.types || []).includes('Files');
function dragEnter(event: DragEvent) {
	if (hasFiles(event)) {
		event.preventDefault();
		dragDepth.value++;
	}
}
function dragOver(event: DragEvent) {
	if (hasFiles(event)) {
		event.preventDefault();
		if (event.dataTransfer)
			event.dataTransfer.dropEffect = attachments.disabled ? 'none' : 'copy';
	}
}
function dragLeave(event: DragEvent) {
	if (hasFiles(event)) {
		event.preventDefault();
		dragDepth.value = Math.max(0, dragDepth.value - 1);
	}
}
function dropFiles(event: DragEvent) {
	if (!hasFiles(event)) return;
	event.preventDefault();
	dragDepth.value = 0;
	void attachments.addFiles(Array.from(event.dataTransfer?.files || []));
}
function resetDrag() {
	dragDepth.value = 0;
}
watch(
	() => store.currentSession?.id,
	() => {
		resetDrag();
		attachments.preview = null;
	},
);

const currentAgentId = computed(() => {
	const q = route.query.agentId;
	return q ? Number(q) : undefined;
});

async function init(agentId: number) {
	store.currentAgentId = agentId;
	store.currentAgentApiKeyEnabled = false;
	store.agentApiKey = '';

	// Load agent info
	try {
		const agent = await agentService.get(agentId);
		if (agent) {
			store.currentAgentName = agent.name || '';
			store.currentAgentAvatar = agent.avatar || '';
			store.currentAgentDescription = agent.description || '';
			store.currentAgentApiKeyEnabled = Boolean(agent.apiKeyEnabled);
		}
	} catch {
		/* ignore */
	}

	// Load active model — handled by store.loadSessions

	store.connectSessionStream(agentId);
	await store.loadSessions(agentId);
}

onMounted(async () => {
	window.addEventListener('dragend', resetDrag);
	window.addEventListener('blur', resetDrag);
	if (currentAgentId.value) await init(currentAgentId.value);
});

watch(currentAgentId, async (newId, oldId) => {
	if (newId && newId !== oldId) {
		store.sessions = [];
		store.currentSession = null;
		store.currentMessages = [];
		store.isStreaming = false;
		store.nodeBlocks = [];
		await init(newId);
	}
});

onUnmounted(() => {
	window.removeEventListener('dragend', resetDrag);
	window.removeEventListener('blur', resetDrag);
	attachments.cancelUploads();
	store.disconnectSessionStream();
});
</script>

<style scoped>
.file-drop-overlay {
	position: absolute;
	inset: 8px;
	z-index: 20;
	background: rgba(239, 246, 255, 0.97);
	border: 2px dashed #3b82f6;
	border-radius: 12px;
	display: flex;
	align-items: center;
	justify-content: center;
	flex-direction: column;
	gap: 12px;
	color: #2563eb;
	pointer-events: none;
}
.file-drop-overlay span {
	color: #64748b;
	font-size: 13px;
	text-align: center;
}
.chat-page {
	display: flex;
	height: calc(100vh - 64px);
	overflow: hidden;
	background: #f1f5f9;
}

.chat-body {
	position: relative;
	flex: 1;
	display: flex;
	flex-direction: column;
	overflow: hidden;
	min-width: 0;
	background: #fff;
}
</style>
