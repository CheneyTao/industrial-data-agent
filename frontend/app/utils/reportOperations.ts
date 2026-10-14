export interface EvidencePoint {
	run_id: string;
	artifact_id: string;
	metric_id: string;
	row_index: number;
}

export interface EvidenceInput {
	runId: string;
	artifactId: string;
	metricId: string;
	rowIndex: string | number;
}

export interface DiagnosisInput {
	runId: string;
	artifactId: string;
	outcome: string;
	exposure: string;
	entity: string;
	keys: string;
	observedAt: string;
	exposureAt: string;
	outcomeAt: string;
	context: string;
	windowStart: string;
	windowEnd: string;
}

const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const artifact = /^[a-f0-9]{32}$/i;
export function splitFields(value: string): string[] {
	return [
		...new Set(
			value
				.split(/[,，]/)
				.map((field) => field.trim())
				.filter(Boolean),
		),
	];
}

function evidenceIds(runId: string, artifactId: string) {
	if (!uuid.test(runId.trim()))
		throw new Error('请填写有效的分析运行 ID（UUID）。');
	if (!artifact.test(artifactId.trim()))
		throw new Error('证据 ID 应为 32 位十六进制字符。');
	return {
		run_id: runId.trim().toLowerCase(),
		artifact_id: artifactId.trim().toLowerCase(),
	};
}

export function evidencePoint(input: EvidenceInput): EvidencePoint {
	const row = Number(input.rowIndex === '' ? 0 : input.rowIndex);
	if (!Number.isInteger(row) || row < 0)
		throw new Error('结果行号须为从 0 开始的非负整数。');
	if (!input.metricId.trim())
		throw new Error('请选择基线证据中的注册指标 ID。');
	return {
		...evidenceIds(input.runId, input.artifactId),
		metric_id: input.metricId.trim(),
		row_index: row,
	};
}

export function diagnosisPayload(
	agentId: number,
	input: DiagnosisInput,
): Record<string, unknown> {
	if (!Number.isInteger(agentId) || agentId < 1)
		throw new Error('请先在导航栏选择智能体。');
	if (!input.outcome.trim() || !input.exposure.trim())
		throw new Error('请明确结局与暴露的二元 0/1 字段。');
	if (input.outcome.trim() === input.exposure.trim())
		throw new Error('结局与暴露不能使用同一个字段。');
	const { run_id, artifact_id } = evidenceIds(input.runId, input.artifactId);
	const payload: Record<string, unknown> = {
		agent_id: agentId,
		run_id,
		result_artifact_id: artifact_id,
		outcome_field: input.outcome.trim(),
		exposure_field: input.exposure.trim(),
		key_fields: splitFields(input.keys),
		context_fields: splitFields(input.context),
	};
	for (const [key, value] of [
		['entity_field', input.entity],
		['observed_at_field', input.observedAt],
		['exposure_at_field', input.exposureAt],
		['outcome_at_field', input.outcomeAt],
	] as const)
		if (value.trim()) payload[key] = value.trim();
	if (Boolean(input.windowStart.trim()) !== Boolean(input.windowEnd.trim()))
		throw new Error('分析窗口的起止时间需同时填写。');
	if (input.windowStart.trim()) {
		const times = [input.windowStart.trim(), input.windowEnd.trim()];
		if (
			times.some(
				(value) =>
					!/(Z|[+-]\d\d:\d\d)$/i.test(value) ||
					!Number.isFinite(Date.parse(value)),
			)
		)
			throw new Error('窗口时间须携带时区，例如 2026-10-01T00:00:00+08:00。');
		if (Date.parse(times[0]!) >= Date.parse(times[1]!))
			throw new Error('窗口结束时间须晚于开始时间。');
		payload.window_start = times[0];
		payload.window_end = times[1];
	}
	return payload;
}

export function schedulePayload(
	name: string,
	question: string,
	frequency: string,
	localTime: string,
	metrics: string[],
) {
	if (!name.trim() || !question.trim())
		throw new Error('请填写报告名称与分析问题。');
	if (!['daily', 'weekly', 'monthly'].includes(frequency))
		throw new Error('请选择日、周或月周期。');
	if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(localTime))
		throw new Error('执行时间须为有效的 HH:mm。');
	const selected = [...new Set(metrics)];
	if (!selected.length || selected.length > 4)
		throw new Error('请选择 1 到 4 个指标。');
	const [hour, minute] = localTime.split(':').map(Number);
	return {
		name: name.trim(),
		question: question.trim(),
		frequency,
		timezone: 'Asia/Shanghai',
		hour,
		minute,
		plan: {
			version: '1',
			dataset_id: 'industrial_pdm',
			intent: 'overview',
			metric_ids: selected,
			filters: {},
			grain: 'day',
			limit: 20,
			chart_type: 'table',
		},
	};
}

export function actionPayload(input: {
	title: string;
	owner: string;
	dueDate: string;
	target: string;
	acceptance: string;
	note: string;
	baseline: EvidenceInput;
}) {
	if (!input.title.trim() || !input.owner.trim() || !input.note.trim())
		throw new Error('请填写行动标题、负责人及验收说明。');
	const due = new Date(input.dueDate);
	if (
		!/^\d{4}-\d{2}-\d{2}$/.test(input.dueDate) ||
		!Number.isFinite(due.getTime()) ||
		due.toISOString().slice(0, 10) !== input.dueDate
	)
		throw new Error('请填写有效的验收日期。');
	if (
		!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(
			input.target.trim(),
		) ||
		!Number.isFinite(Number(input.target))
	)
		throw new Error('目标值须为有限数字。');
	if (!['at_most', 'at_least', 'equal'].includes(input.acceptance))
		throw new Error('请选择验收比较方式。');
	return {
		title: input.title.trim(),
		owner: input.owner.trim(),
		due_date: input.dueDate,
		target: input.target.trim(),
		acceptance: input.acceptance,
		acceptance_note: input.note.trim(),
		baseline: evidencePoint(input.baseline),
	};
}

export const reportRunTerminal = (status: string) =>
	[
		'COMPLETED',
		'PARTIAL',
		'FAILED',
		'TIMED_OUT',
		'CANCELLED',
		'DISPATCH_UNCERTAIN',
	].includes(status);
const statusNames: Record<string, string> = {
	ACTIVE: '已启用',
	PAUSED: '已暂停',
	PENDING: '待执行',
	WORKING: '执行中',
	RUNNING: '执行中',
	RETRYING: '等待重试',
	WAITING: '等待审核',
	COMPLETED: '已完成',
	PARTIAL: '部分完成',
	FAILED: '失败',
	TIMED_OUT: '超时',
	CANCELLED: '已取消',
	DISPATCH_UNCERTAIN: '派发结果待核查',
	RETRY: '等待重试',
	DISPATCHING: '正在派发',
	CANCELLING: '正在停止',
	OPEN: '待落实',
	REVIEWED: '已复核',
	CLOSED: '已验收',
};
export function reportStatus(status: string): string {
	return statusNames[status] || status;
}
export function percent(value: number | null | undefined): string {
	return typeof value === 'number' && Number.isFinite(value)
		? `${(value * 100).toFixed(2)}%`
		: '不可计算';
}

/** Agent switches invalidate all earlier promises even when transport cancellation is too late. */
export class ReportRequestScope {
	private controller = new AbortController();
	private generation = 0;
	reset() {
		this.controller.abort();
		this.controller = new AbortController();
		this.generation += 1;
	}
	capture() {
		return { signal: this.controller.signal, generation: this.generation };
	}
	isCurrent(generation: number) {
		return generation === this.generation && !this.controller.signal.aborted;
	}
	close() {
		this.controller.abort();
		this.generation += 1;
	}
}

/** A refresh requested during a fetch always gets a later fetch, coalescing bursts.
 * Reset drops queued work from the previous agent without waiting for its transport.
 */
export class CoalescedRefreshQueue {
	private generation = 0;
	private pending = false;
	private running: Promise<void> | null = null;
	constructor(private readonly work: () => Promise<void>) {}
	request(): Promise<void> {
		this.pending = true;
		if (this.running) return this.running;
		const generation = this.generation;
		const running = this.drain(generation);
		this.running = running;
		const clear = () => {
			if (this.running === running) this.running = null;
		};
		void running.then(clear, clear);
		return running;
	}
	private async drain(generation: number): Promise<void> {
		do {
			this.pending = false;
			await this.work();
		} while (generation === this.generation && this.pending);
	}
	reset(): void {
		this.generation += 1;
		this.pending = false;
		this.running = null;
	}
}

export function reportAnswer(result: unknown): string {
	if (!result || typeof result !== 'object') return '';
	const value = result as Record<string, unknown>;
	if (typeof value.answer === 'string') return value.answer;
	const task = value.task;
	if (!task || typeof task !== 'object') return '';
	const taskResult = (task as Record<string, unknown>).result;
	if (!taskResult || typeof taskResult !== 'object') return '';
	const answer = (taskResult as Record<string, unknown>).answer;
	return typeof answer === 'string' ? answer : '';
}
