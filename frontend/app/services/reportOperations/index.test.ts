import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import service from './index';

vi.mock('axios', () => ({
	default: { get: vi.fn(), post: vi.fn(), isAxiosError: vi.fn() },
}));
beforeEach(() => {
	vi.clearAllMocks();
	vi.mocked(axios.get).mockResolvedValue({ data: [] });
	vi.mocked(axios.post).mockResolvedValue({ data: {} });
});

describe('report API credential and intent boundaries', () => {
	it('places credentials only in headers and propagates cancellation', async () => {
		const signal = new AbortController().signal;
		await service.schedules(7, 'private-key', signal);
		const [url, options] = vi.mocked(axios.get).mock.calls[0]!;
		expect(url).toBe('/api/report-operations/schedules');
		expect(options?.params).toEqual({ agent_id: 7 });
		expect(options?.headers).toEqual({ 'X-API-Key': 'private-key' });
		expect(options?.signal).toBe(signal);
		expect(url).not.toContain('private-key');
	});
	it('does not send reconciliation while simply reading run state', async () => {
		await service.run('run-id', 7, '');
		expect(axios.post).not.toHaveBeenCalled();
		expect(axios.get).toHaveBeenCalledWith(
			'/api/report-operations/runs/run-id',
			expect.anything(),
		);
		await service.refreshRun('run-id', 7, '');
		expect(axios.post).toHaveBeenCalledWith(
			'/api/report-operations/runs/run-id/refresh',
			null,
			expect.anything(),
		);
	});
	it('does not include a secret in diagnosis payload or URL', async () => {
		const payload = {
			agent_id: 7,
			run_id: 'r1',
			result_artifact_id: 'a'.repeat(32),
		};
		await service.diagnose(payload, 'private-key');
		const [url, body, options] = vi.mocked(axios.post).mock.calls[0]!;
		expect(url).toBe('/api/diagnostics');
		expect(body).toEqual(payload);
		expect(JSON.stringify(body)).not.toContain('private-key');
		expect(options?.headers).toEqual({ 'X-API-Key': 'private-key' });
	});
	it('exposes scheduler configuration through authenticated status reads', async () => {
		await service.status(7, '');
		expect(axios.get).toHaveBeenCalledWith(
			'/api/report-operations/status',
			expect.objectContaining({ params: { agent_id: 7 } }),
		);
	});
});
