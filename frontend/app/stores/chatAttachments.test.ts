import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { nextTick, reactive, watchEffect } from 'vue';
import { useChatAttachments } from './chatAttachments';

const context = vi.hoisted(() => ({ chat: {} as any }));
vi.mock('./chat', () => ({ useChatStore: () => context.chat }));
beforeEach(() => {
	setActivePinia(createPinia());
	context.chat = reactive({
		currentSession: { id: 's1' },
		currentAgentId: 1,
		isStreaming: false,
		showHumanFeedback: false,
		currentAgentApiKeyEnabled: false,
	});
});
describe('conversation-scoped attachment drafts', () => {
	it('does not start queued uploads after leaving the chat page', async () => {
		const fetchMock = vi.fn(
			(_url, options) =>
				new Promise<Response>((_resolve, reject) =>
					options.signal.addEventListener('abort', () =>
						reject(new Error('aborted')),
					),
				),
		);
		vi.stubGlobal('fetch', fetchMock);
		const store = useChatAttachments();
		store.capabilities = { extensions: ['.csv'], maxBytes: 100, maxFiles: 6 };
		const uploading = store.addFiles([
			new File(['x\n1'], 'a.csv'),
			new File(['x\n2'], 'b.csv'),
			new File(['x\n3'], 'c.csv'),
		]);
		await nextTick();
		expect(fetchMock).toHaveBeenCalledTimes(2);
		store.cancelUploads();
		await uploading;
		expect(fetchMock).toHaveBeenCalledTimes(2);
		expect(store.items.every((item) => item.status === 'error')).toBe(true);
		vi.unstubAllGlobals();
	});
	it('updates progress reactively and never moves a late upload into a switched conversation', async () => {
		let finish!: (response: Response) => void;
		vi.stubGlobal(
			'fetch',
			vi.fn(
				() =>
					new Promise<Response>((resolve) => {
						finish = resolve;
					}),
			),
		);
		const store = useChatAttachments();
		store.capabilities = { extensions: ['.csv'], maxBytes: 100, maxFiles: 6 };
		let status = '';
		const stop = watchEffect(() => {
			status = store.items[0]?.status || '';
		});
		const uploaded = store.addFiles([new File(['x\n1'], 'a.csv')]);
		await nextTick();
		expect(status).toBe('uploading');
		context.chat.currentSession = { id: 's2' };
		finish(
			new Response(
				JSON.stringify({
					id: 'a1',
					name: 'a.csv',
					agentId: 1,
					conversationId: 's1',
					status: 'ready',
					tables: [],
				}),
			),
		);
		await uploaded;
		expect(store.items).toHaveLength(0);
		context.chat.currentSession = { id: 's1' };
		await nextTick();
		expect(status).toBe('ready');
		expect(store.pending).toBe(false);
		stop();
		vi.unstubAllGlobals();
	});
	it('makes an unsupported file visible as an error and blocks sending until removed', async () => {
		const store = useChatAttachments();
		store.capabilities = { extensions: ['.csv'], maxBytes: 100, maxFiles: 6 };
		await store.addFiles([new File(['x'], 'photo.png')]);
		expect(store.items[0]?.status).toBe('error');
		expect(store.pending).toBe(true);
		store.remove(store.items[0]!.key);
		expect(store.pending).toBe(false);
	});
});
