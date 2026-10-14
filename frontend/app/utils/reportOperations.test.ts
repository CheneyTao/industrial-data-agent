import { describe, expect, it } from 'vitest';
import {
	actionPayload,
	CoalescedRefreshQueue,
	diagnosisPayload,
	evidencePoint,
	percent,
	ReportRequestScope,
	reportAnswer,
	reportRunTerminal,
	schedulePayload,
	splitFields,
	type DiagnosisInput,
} from './reportOperations';

const runId = '019d0000-0000-7000-8000-000000000001';
const artifactId = 'a'.repeat(32);
const evidence = {
	runId,
	artifactId,
	metricId: 'failure_rate_pct',
	rowIndex: 0,
};
const diagnostic: DiagnosisInput = {
	runId,
	artifactId,
	outcome: 'failed',
	exposure: 'exposed',
	entity: 'device',
	keys: 'device, day',
	context: '',
	observedAt: '',
	exposureAt: '',
	outcomeAt: '',
	windowStart: '',
	windowEnd: '',
};

describe('controlled diagnosis inputs', () => {
	it('maps fields without fabricating missing data or sending SQL', () => {
		expect(diagnosisPayload(7, diagnostic)).toEqual({
			agent_id: 7,
			run_id: runId,
			result_artifact_id: artifactId,
			outcome_field: 'failed',
			exposure_field: 'exposed',
			entity_field: 'device',
			key_fields: ['device', 'day'],
			context_fields: [],
		});
	});
	it('requires outcome/exposure definitions and valid artifact ownership locators', () => {
		expect(() => diagnosisPayload(0, diagnostic)).toThrow('选择智能体');
		expect(() => diagnosisPayload(7, { ...diagnostic, outcome: '' })).toThrow(
			'0/1',
		);
		expect(() =>
			diagnosisPayload(7, { ...diagnostic, exposure: 'failed' }),
		).toThrow('同一个字段');
		expect(() =>
			diagnosisPayload(7, { ...diagnostic, artifactId: '../manifest' }),
		).toThrow('32 位');
	});
	it('rejects ambiguous timezone, partial and reversed windows', () => {
		expect(() =>
			diagnosisPayload(7, {
				...diagnostic,
				windowStart: '2026-10-01T00:00:00+08:00',
			}),
		).toThrow('同时');
		expect(() =>
			diagnosisPayload(7, {
				...diagnostic,
				windowStart: '2026-10-01T00:00:00',
				windowEnd: '2026-10-02T00:00:00',
			}),
		).toThrow('时区');
		expect(() =>
			diagnosisPayload(7, {
				...diagnostic,
				windowStart: '2026-10-02T00:00:00Z',
				windowEnd: '2026-10-01T00:00:00Z',
			}),
		).toThrow('晚于');
	});
	it('preserves explicit windows and strips duplicate field selections', () => {
		expect(splitFields('device, device， time, ')).toEqual(['device', 'time']);
		expect(
			diagnosisPayload(7, {
				...diagnostic,
				windowStart: '2026-10-01T00:00:00+08:00',
				windowEnd: '2026-10-02T00:00:00+08:00',
			}).window_end,
		).toBe('2026-10-02T00:00:00+08:00');
	});
});

describe('report operations contracts', () => {
	it('reads only the final stored report, not arbitrary tool output', () => {
		expect(reportAnswer({ task: { result: { answer: '# 已核查报告' } } })).toBe(
			'# 已核查报告',
		);
		expect(reportAnswer({ task: { result: { sql: 'SELECT 1' } } })).toBe('');
		expect(reportAnswer({ answer: { unsafe: 'not text' } })).toBe('');
	});
	it('creates an approved fixed metric plan without arbitrary calendar dates or SQL', () => {
		const value = schedulePayload('日常报告', '概览故障', 'weekly', '08:30', [
			'failure_count',
		]);
		expect(value.timezone).toBe('Asia/Shanghai');
		expect(value.hour).toBe(8);
		expect(value.minute).toBe(30);
		expect(value.plan.dataset_id).toBe('industrial_pdm');
		expect(value.plan.filters).toEqual({});
		expect(value.plan.metric_ids).toEqual(['failure_count']);
	});
	it('rejects missing metrics and invalid clock times', () => {
		expect(() =>
			schedulePayload('报告', '问题', 'daily', '25:00', ['failure_count']),
		).toThrow('HH:mm');
		expect(() => schedulePayload('报告', '问题', 'daily', '08:00', [])).toThrow(
			'1 到 4',
		);
	});
	it('binds numeric targets to persisted evidence preserving decimal precision', () => {
		const value = actionPayload({
			title: '降低故障占比',
			owner: '张工',
			dueDate: '2026-11-01',
			target: '0.123456789012345678',
			acceptance: 'at_most',
			note: '同等负荷窗口验收',
			baseline: evidence,
		});
		expect(value.target).toBe('0.123456789012345678');
		expect(value.baseline.artifact_id).toBe(artifactId);
	});
	it('rejects invalid dates, infinity, and fractional row numbers', () => {
		const input = {
			title: '行动',
			owner: '张工',
			dueDate: '2026-11-01',
			target: '10',
			acceptance: 'at_most',
			note: '验收',
			baseline: evidence,
		};
		expect(() => actionPayload({ ...input, dueDate: '2026-02-31' })).toThrow(
			'日期',
		);
		expect(() => actionPayload({ ...input, dueDate: 'invalid' })).toThrow(
			'日期',
		);
		expect(() => actionPayload({ ...input, target: 'Infinity' })).toThrow(
			'有限数字',
		);
		expect(() => evidencePoint({ ...evidence, rowIndex: '1.5' })).toThrow(
			'整数',
		);
	});
	it('treats uncertain dispatch as terminal without automatically attempting duplicate dispatch', () => {
		expect(reportRunTerminal('DISPATCH_UNCERTAIN')).toBe(true);
		expect(reportRunTerminal('WAITING')).toBe(false);
	});
	it('keeps uncomputable ratios distinct from zeros', () => {
		expect(percent(null)).toBe('不可计算');
		expect(percent(0)).toBe('0.00%');
	});
});

describe('agent-isolated request lifecycle', () => {
	it('refetches after a mutation even if an older list snapshot is in flight', async () => {
		let releaseFirst = () => {};
		const firstResponse = new Promise<void>((resolve) => {
			releaseFirst = resolve;
		});
		const serverRows = ['existing'],
			displayed: string[] = [];
		let calls = 0;
		const refresh = new CoalescedRefreshQueue(async () => {
			const snapshot = [...serverRows];
			calls += 1;
			if (calls === 1) await firstResponse;
			displayed.splice(0, displayed.length, ...snapshot);
		});
		const earlier = refresh.request();
		serverRows.push('newly-created');
		const later = refresh.request();
		refresh.request(); // Multiple mutation completions share one later fetch.
		expect(calls).toBe(1);
		releaseFirst();
		await Promise.all([earlier, later]);
		expect(calls).toBe(2);
		expect(displayed).toEqual(['existing', 'newly-created']);
	});
	it('drops the old agent queued refresh and cannot clear the new agent request', async () => {
		let releaseFirst = () => {};
		const firstResponse = new Promise<void>((resolve) => {
			releaseFirst = resolve;
		});
		let calls = 0;
		const refresh = new CoalescedRefreshQueue(async () => {
			calls += 1;
			if (calls === 1) await firstResponse;
		});
		const earlier = refresh.request();
		refresh.request();
		refresh.reset();
		await refresh.request();
		releaseFirst();
		await earlier;
		expect(calls).toBe(2);
		await refresh.request();
		expect(calls).toBe(3);
	});
	it('cancels transport and discards late responses after a switch', () => {
		const scope = new ReportRequestScope(),
			earlier = scope.capture();
		scope.reset();
		expect(earlier.signal.aborted).toBe(true);
		expect(scope.isCurrent(earlier.generation)).toBe(false);
		const current = scope.capture();
		expect(scope.isCurrent(current.generation)).toBe(true);
		scope.close();
		expect(current.signal.aborted).toBe(true);
		expect(scope.isCurrent(current.generation)).toBe(false);
	});
});
