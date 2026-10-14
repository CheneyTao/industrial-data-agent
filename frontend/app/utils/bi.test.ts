import { describe, expect, it } from 'vitest';
import { finiteDisplay, safeDashboardRoute } from './bi';
import { terminalTask } from '../services/bi';
describe('BI UI contracts', () => {
	it('accepts only local registered dashboard actions', () => {
		expect(safeDashboardRoute({ action:'open_dashboard',dataset_id:'scania_component_x',route:'/dashboard?dataset=scania_component_x' })).toBe('/dashboard?dataset=scania_component_x');
		for (const route of ['https://evil.example/dashboard','javascript:alert(1)','//evil.example/dashboard','/system/model-config']) {
			expect(safeDashboardRoute({ action:'open_dashboard',dataset_id:'industrial_pdm',route })).toBeNull();
		}
	});
	it('does not render unavailable measurements as NaN or zero', () => {
		for (const value of [undefined,null,NaN,Infinity,'bad']) expect(finiteDisplay(value)).toBe('—');
		expect(finiteDisplay(0)).toBe('0');
	});
	it('distinguishes cancel acknowledgement from terminal cancellation', () => {
		expect(terminalTask('CANCELLING')).toBe(false);
		for (const state of ['COMPLETED','CANCELLED','FAILED','TIMED_OUT']) expect(terminalTask(state)).toBe(true);
	});
});
