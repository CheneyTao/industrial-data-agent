import { describe, expect, it } from 'vitest';
import { dashboardParams, type DashboardFilters } from './index';

describe('dashboardParams', () => {
	it('maps active filters to the FastAPI snake-case contract', () => {
		const filters: DashboardFilters = {
			startDate: '2024-01-01',
			endDate: '2024-01-31',
			machineId: 7,
			model: 'model3',
			failureType: 'comp2',
		};

		expect(dashboardParams(filters)).toEqual({
			start_date: '2024-01-01',
			end_date: '2024-01-31',
			machine_id: 7,
			model: 'model3',
			failure_type: 'comp2',
		});
	});

	it('omits cleared optional filters instead of serializing null', () => {
		expect(
			dashboardParams({
				startDate: '2024-01-01',
				endDate: '2024-01-31',
				machineId: null,
				model: null,
				failureType: null,
			}),
		).toEqual({
			start_date: '2024-01-01',
			end_date: '2024-01-31',
		});
	});
});
