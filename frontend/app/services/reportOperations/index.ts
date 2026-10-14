import axios from 'axios';

export interface MetricContract {
	metric_id: string;
	name: string;
	description: string;
	unit: string;
	version: string;
	grain: string;
	numerator: string;
	denominator: string | null;
	exclusions: string[];
	time_policy: string;
	null_policy: string;
	owner: string;
	semantic_hash: string;
}
export interface ReportSchedule {
	schedule_id: string;
	status: string;
	definition_hash: string;
	spec: {
		name: string;
		frequency: string;
		hour: number;
		minute: number;
		timezone: string;
	};
}
export interface ReportRun {
	run_id: string;
	schedule_id: string;
	status: string;
	attempts: number;
	task_id: string | null;
	error: string;
	spec: {
		period: { start_date: string; end_date: string; end_exclusive: string };
	};
	result: {
		answer?: string;
		message?: string;
		result?: { provenance?: { artifactId?: string; runId?: string } };
	};
}
export interface ReportAction {
	action_id: string;
	status: string;
	owner: string;
	due_date: string;
	spec: {
		title: string;
		target: string;
		acceptance: string;
		acceptance_note: string;
	};
	baseline: { value: string; point: { metric_id: string }; scope: unknown };
	review: {
		target_met?: boolean;
		actual?: { value: string };
		difference_from_baseline?: string;
	};
}
export interface SchedulerStatus {
	enabled: boolean;
	running: boolean;
	poll_interval_seconds: number;
	dispatch_policy: string;
	refresh_policy: string;
}
export interface Diagnosis {
	artifactId: string;
	content: {
		evidenceLevel: string;
		qualityGate: {
			status: string;
			rowCount: number;
			duplicateKeys: number;
			issues: Array<{ code: string; severity: string; message: string }>;
		};
		missingInputs: string[];
		association: null | {
			riskDifference: number;
			relativeRisk: number | null;
			riskDifference95Interval: number[] | null;
			groups: Array<{
				exposure: number;
				events: number;
				observations: number;
				risk: number;
			}>;
		};
		causalReadiness: {
			reason: string;
			timingChecked: boolean;
			engineeringConfirmed: boolean;
		};
	};
}
const base = '/api/report-operations';
const options = (agent: number, key: string, signal?: AbortSignal) => ({
	params: { agent_id: agent },
	headers: key ? { 'X-API-Key': key } : {},
	signal,
	timeout: 30000,
});
export function reportErrorMessage(error: unknown): string {
	if (axios.isAxiosError(error)) {
		if (error.response?.status === 401)
			return '此智能体需要有效的 API 密钥。密钥仅保留在当前页面内存。';
		const payload = error.response?.data as
			| { message?: string; detail?: string | Array<{ msg?: string }> }
			| undefined;
		if (payload?.message) return payload.message;
		if (typeof payload?.detail === 'string') return payload.detail;
		if (Array.isArray(payload?.detail))
			return payload.detail.map((item) => item.msg || '输入无效').join('；');
		return '请求未完成，请检查后端连接后重试。';
	}
	return error instanceof Error ? error.message : '请求未完成，请稍后重试。';
}
export default {
	async metrics(
		dataset: string,
		signal?: AbortSignal,
	): Promise<MetricContract[]> {
		return (
			await axios.get(
				`/api/bi/datasets/${encodeURIComponent(dataset)}/metrics`,
				{ signal, timeout: 15000 },
			)
		).data;
	},
	async diagnose(
		payload: Record<string, unknown>,
		key: string,
		signal?: AbortSignal,
	): Promise<Diagnosis> {
		return (
			await axios.post('/api/diagnostics', payload, {
				headers: key ? { 'X-API-Key': key } : {},
				signal,
				timeout: 30000,
			})
		).data;
	},
	async schedules(
		agent: number,
		key: string,
		signal?: AbortSignal,
	): Promise<ReportSchedule[]> {
		return (await axios.get(`${base}/schedules`, options(agent, key, signal)))
			.data;
	},
	async createSchedule(
		payload: unknown,
		agent: number,
		key: string,
		signal?: AbortSignal,
	) {
		return (
			await axios.post(
				`${base}/schedules`,
				payload,
				options(agent, key, signal),
			)
		).data;
	},
	async pause(
		id: string,
		paused: boolean,
		agent: number,
		key: string,
		signal?: AbortSignal,
	) {
		return (
			await axios.post(
				`${base}/schedules/${encodeURIComponent(id)}/pause`,
				{ paused },
				options(agent, key, signal),
			)
		).data;
	},
	async trigger(
		id: string,
		periodEnd: string,
		agent: number,
		key: string,
		signal?: AbortSignal,
	): Promise<ReportRun> {
		return (
			await axios.post(
				`${base}/schedules/${encodeURIComponent(id)}/trigger`,
				periodEnd ? { period_end: periodEnd } : {},
				options(agent, key, signal),
			)
		).data;
	},
	async runs(
		agent: number,
		key: string,
		signal?: AbortSignal,
	): Promise<ReportRun[]> {
		return (await axios.get(`${base}/runs`, options(agent, key, signal))).data;
	},
	async run(
		id: string,
		agent: number,
		key: string,
		signal?: AbortSignal,
	): Promise<ReportRun> {
		return (
			await axios.get(
				`${base}/runs/${encodeURIComponent(id)}`,
				options(agent, key, signal),
			)
		).data;
	},
	async refreshRun(
		id: string,
		agent: number,
		key: string,
		signal?: AbortSignal,
	): Promise<ReportRun> {
		return (
			await axios.post(
				`${base}/runs/${encodeURIComponent(id)}/refresh`,
				null,
				options(agent, key, signal),
			)
		).data;
	},
	async status(
		agent: number,
		key: string,
		signal?: AbortSignal,
	): Promise<SchedulerStatus> {
		return (await axios.get(`${base}/status`, options(agent, key, signal)))
			.data;
	},
	async actions(
		agent: number,
		key: string,
		signal?: AbortSignal,
	): Promise<ReportAction[]> {
		return (await axios.get(`${base}/actions`, options(agent, key, signal)))
			.data;
	},
	async createAction(
		payload: unknown,
		agent: number,
		key: string,
		signal?: AbortSignal,
	) {
		return (
			await axios.post(`${base}/actions`, payload, options(agent, key, signal))
		).data;
	},
	async review(
		id: string,
		payload: unknown,
		agent: number,
		key: string,
		signal?: AbortSignal,
	) {
		return (
			await axios.post(
				`${base}/actions/${encodeURIComponent(id)}/review`,
				payload,
				options(agent, key, signal),
			)
		).data;
	},
};
