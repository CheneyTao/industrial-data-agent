import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { useChatStore } from './chat';
import {
	attachmentRequest,
	fileValidation,
	type AttachmentCapabilities,
	type ChatAttachment,
} from '../services/attachments';

interface DraftAttachment {
	key: string;
	name: string;
	file?: File;
	status: 'uploading' | 'ready' | 'error';
	error?: string;
	asset?: ChatAttachment;
}

export const useChatAttachments = defineStore('chat-attachments', () => {
	const chat = useChatStore();
	const drafts = ref<Record<string, DraftAttachment[]>>({});
	const controllers = new Map<string, AbortController>();
	const capabilities = ref<AttachmentCapabilities | null>(null);
	const error = ref('');
	const preview = ref<ChatAttachment | null>(null);
	const items = computed(
		() => drafts.value[chat.currentSession?.id || ''] || [],
	);
	const ready = computed(() =>
		items.value.flatMap((item) => (item.asset ? [item.asset] : [])),
	);
	const pending = computed(() =>
		items.value.some((item) => item.status !== 'ready'),
	);
	const disabled = computed(
		() =>
			!chat.currentSession ||
			chat.isStreaming ||
			chat.showHumanFeedback ||
			(chat.currentAgentApiKeyEnabled && !chat.agentApiKey.trim()),
	);
	const headers = (): Record<string, string> =>
		chat.currentAgentApiKeyEnabled
			? { 'X-API-Key': chat.agentApiKey.trim() }
			: {};

	async function loadCapabilities() {
		try {
			capabilities.value = await attachmentRequest<AttachmentCapabilities>(
				'/api/chat-attachments/capabilities',
				{ signal: AbortSignal.timeout(15000) },
			);
			error.value = '';
		} catch (e) {
			error.value = e instanceof Error ? e.message : '无法获取附件支持格式';
		}
	}

	async function upload(
		item: DraftAttachment,
		agentId: number,
		sessionId: string,
		authHeaders: Record<string, string>,
	) {
		const controller = new AbortController();
		controllers.set(item.key, controller);
		const timer = setTimeout(() => controller.abort(), 100000);
		item.status = 'uploading';
		item.error = undefined;
		try {
			const form = new FormData();
			form.append('file', item.file!);
			item.asset = await attachmentRequest<ChatAttachment>(
				`/api/chat-attachments?${new URLSearchParams({ agentId: String(agentId), conversationId: sessionId })}`,
				{
					method: 'POST',
					body: form,
					headers: authHeaders,
					signal: controller.signal,
				},
			);
			item.status = 'ready';
			item.file = undefined;
		} catch (e) {
			item.status = 'error';
			item.error = controller.signal.aborted
				? '上传已取消或超时，可重新选择文件'
				: e instanceof Error
					? e.message
					: '上传失败';
		} finally {
			clearTimeout(timer);
			controllers.delete(item.key);
		}
	}

	async function addFiles(files: File[]) {
		error.value = '';
		if (disabled.value) {
			error.value = '请先选择会话、填写所需 API Key，并等待本次分析结束';
			return;
		}
		const sessionId = chat.currentSession!.id;
		const agentId = chat.currentAgentId!;
		const authHeaders = headers();
		if (!capabilities.value) await loadCapabilities();
		if (!capabilities.value) return;
		if (
			chat.currentSession?.id !== sessionId ||
			chat.currentAgentId !== agentId ||
			disabled.value
		) {
			error.value = '会话状态已改变，请重新选择附件';
			return;
		}
		drafts.value[sessionId] ||= [];
		const list = drafts.value[sessionId]!;
		const jobs: (() => Promise<void>)[] = [];
		for (const file of files) {
			if (list.length >= capabilities.value.maxFiles) {
				error.value = `每轮最多选择 ${capabilities.value.maxFiles} 个文件，请先移除不需要的附件`;
				break;
			}
			const invalid = fileValidation(file, capabilities.value);
			list.push({
				key: crypto.randomUUID(),
				name: file.name,
				file,
				status: invalid ? 'error' : 'uploading',
				error: invalid || undefined,
			});
			const item = list[list.length - 1]!;
			if (!invalid)
				jobs.push(() => {
					if (
						item.status !== 'uploading' ||
						!drafts.value[sessionId]?.some((entry) => entry.key === item.key)
					)
						return Promise.resolve();
					return upload(item, agentId, sessionId, authHeaders);
				});
		}
		for (let i = 0; i < jobs.length; i += 2)
			await Promise.all(jobs.slice(i, i + 2).map((job) => job()));
	}

	function remove(key: string) {
		controllers.get(key)?.abort();
		if (chat.currentSession)
			drafts.value[chat.currentSession.id] = items.value.filter(
				(item) => item.key !== key,
			);
		error.value = '';
	}

	async function reuse(id: string) {
		if (disabled.value) return;
		const sessionId = chat.currentSession!.id;
		const agentId = chat.currentAgentId!;
		const authHeaders = headers();
		if (!capabilities.value) await loadCapabilities();
		if (!capabilities.value || !chat.currentSession) return;
		if (chat.currentSession.id !== sessionId || chat.currentAgentId !== agentId)
			return;
		if (ready.value.some((a) => a.id === id)) return;
		if (items.value.length >= capabilities.value.maxFiles) {
			error.value = '请先移除不需要的附件';
			return;
		}
		try {
			const asset = await attachmentRequest<ChatAttachment>(
				`/api/chat-attachments/${encodeURIComponent(id)}?${new URLSearchParams({ agentId: String(agentId), conversationId: sessionId })}`,
				{ headers: authHeaders, signal: AbortSignal.timeout(15000) },
			);
			drafts.value[sessionId] ||= [];
			const list = drafts.value[sessionId]!;
			if (
				list.length < capabilities.value.maxFiles &&
				!list.some((item) => item.asset?.id === id)
			)
				list.push({
					key: crypto.randomUUID(),
					name: asset.name,
					status: 'ready',
					asset,
				});
		} catch (e) {
			error.value = e instanceof Error ? e.message : '附件读取失败';
		}
	}

	function cancelUploads() {
		for (const list of Object.values(drafts.value))
			for (const item of list) {
				if (item.status === 'uploading') {
					item.status = 'error';
					item.error = '上传已取消，可移除后重新选择';
				}
			}
		for (const controller of controllers.values()) controller.abort();
		preview.value = null;
	}

	return {
		capabilities,
		items,
		ready,
		pending,
		disabled,
		error,
		preview,
		loadCapabilities,
		addFiles,
		remove,
		reuse,
		cancelUploads,
	};
});
