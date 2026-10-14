import axios from 'axios';

export type Filters = Record<string, unknown>;
export interface BITask {
	task_id: string;
	conversation_id: string;
	status: string;
	current_node: string;
	progress: number;
	error: string;
	result: {
		action?: string;
		dataset_id?: string;
		message?: string;
		route?: string;
		answer?: string;
		plan?: unknown;
		result?: { column: string[]; data: Record<string, unknown>[]; provenance: unknown; sql?: string; truncated?: boolean };
		charts?: Array<{ artifact_id: string; option: any }>;
		limitations?: string[];
	};
}
export const terminalTask = (status: string) => ['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'WAITING'].includes(status);
const headers = (key = '') => key ? { 'X-API-Key': key } : {};
const base = '/api/bi';
export default {
	async datasets() { return (await axios.get(`${base}/datasets`, { timeout: 15000 })).data; },
	async definition(id: string) { return (await axios.get(`${base}/datasets/${id}`, { timeout: 15000 })).data; },
	async dashboard(id: string, filters: Filters, feature: string, dimension: string, signal?: AbortSignal) {
		return (await axios.post(`${base}/datasets/${id}/dashboard`, { filters, feature, dimension }, { signal, timeout: 70000 })).data;
	},
	async entity(id: string, entity: number, filters: Filters, feature: string, signal?: AbortSignal) {
		return (await axios.post(`${base}/datasets/${id}/entities/${entity}`, { filters, feature }, { signal, timeout: 70000 })).data;
	},
	async analyze(payload: Record<string, unknown>, key = '') {
		return (await axios.post(`${base}/analyze`, payload, { headers: headers(key), timeout: 15000 })).data;
	},
	async task(id: string, agent: number, key = ''): Promise<BITask> {
		return (await axios.get(`${base}/tasks/${id}`, { params: { agent_id: agent }, headers: headers(key), timeout: 15000 })).data;
	},
	async cancel(id: string, agent: number, key = '') {
		return (await axios.post(`${base}/tasks/${id}/cancel`, null, { params: { agent_id: agent }, headers: headers(key), timeout: 15000 })).data;
	},
	async resume(id: string, agent: number, approved: boolean, feedback = '', key = '') {
		return (await axios.post(`${base}/tasks/${id}/resume`, { approved, feedback }, { params: { agent_id: agent }, headers: headers(key), timeout: 15000 })).data;
	},
};
