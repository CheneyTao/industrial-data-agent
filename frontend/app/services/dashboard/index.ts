import axios from 'axios';

const API_BASE_URL = '/api/dashboard';

export interface DashboardFilters {
	startDate: string;
	endDate: string;
	machineId: number | null;
	model: string | null;
	failureType: string | null;
}

export interface DashboardModelOption {
	value: string;
	label: string;
	machineCount: number;
}

export interface DashboardMachineOption {
	id: number;
	model: string;
	modelLabel: string;
	age: number | null;
}

export interface DashboardFailureOption {
	value: string;
	eventCount: number;
}

export interface DashboardOptions {
	dataRange: { startDate: string; endDate: string };
	defaultRange: { startDate: string; endDate: string };
	presets: Array<{ label: string; days: number }>;
	models: DashboardModelOption[];
	machines: DashboardMachineOption[];
	failureTypes: DashboardFailureOption[];
	telemetryMetrics: string[];
}

export interface DashboardOverview {
	totalDevices: number;
	failedDevices: number;
	failureCount: number;
	failureRatePct: number;
	errorCount: number;
	maintenanceCount: number;
	averageDeviceAge: number | null;
	mtbfHours: number | null;
	mttrHours: number | null;
	mttrAvailability: string;
	recent7FailureCount: number;
	recent30FailureRatePct: number;
	telemetryPoints: number;
	definitions: Record<string, string>;
}

export interface FailureTrendItem {
	period: string;
	failureCount: number;
	failedMachineCount: number;
}

export interface FailureModelItem {
	model: string;
	modelLabel: string;
	deviceCount: number;
	failedDeviceCount: number;
	failureCount: number;
	failureRatePct: number;
	failureEventsPerDevice: number;
}

export interface FailureTypeItem {
	failureType: string;
	failureCount: number;
	failedMachineCount: number;
	sharePct: number;
}

export interface ErrorRankingItem {
	errorId: string;
	errorCount: number;
	affectedMachineCount: number;
}

export interface MachineRankingItem {
	machineId: number;
	model: string;
	modelLabel: string;
	age: number | null;
	failureCount: number;
	errorCount: number;
	maintenanceCount: number;
}

export interface TelemetryPoint {
	datetime: string;
	volt: number | null;
	rotate: number | null;
	pressure: number | null;
	vibration: number | null;
	samples: number;
}

export interface TelemetryResponse {
	machineId: number;
	sourcePointCount: number;
	returnedPointCount: number;
	sampled: boolean;
	bucketSize?: number;
	points: TelemetryPoint[];
}

export interface MaintenanceItem {
	maintenanceType: string;
	maintenanceCount: number;
	sameComponentFailuresBefore7d: number;
	sameComponentFailuresAfter7d: number;
	maintenanceWithFollowupFailure: number;
	followupFailureRatePct: number;
}

export interface MaintenanceResponse {
	windowDays: number;
	items: MaintenanceItem[];
	summary: {
		maintenanceCount: number;
		sameComponentFailuresBefore7d: number;
		sameComponentFailuresAfter7d: number;
		maintenanceWithFollowupFailure: number;
		followupFailureRatePct: number;
	};
	definition: string;
}

export interface AnalysisContextResponse {
	question: string;
	evidence: Record<string, unknown>;
	scope: Record<string, unknown>;
}

export function dashboardParams(filters: DashboardFilters): Record<string, string | number> {
	const params: Record<string, string | number> = {
		start_date: filters.startDate,
		end_date: filters.endDate,
	};
	if (filters.machineId !== null) params.machine_id = filters.machineId;
	if (filters.model) params.model = filters.model;
	if (filters.failureType) params.failure_type = filters.failureType;
	return params;
}

export function dashboardErrorMessage(error: unknown): string {
	if (axios.isAxiosError(error)) {
		const payload = error.response?.data as
			| { message?: string; detail?: string }
			| undefined;
		return payload?.message || payload?.detail || error.message || 'Dashboard 请求失败';
	}
	return error instanceof Error ? error.message : 'Dashboard 请求失败';
}

class DashboardService {
	async options(): Promise<DashboardOptions> {
		return (await axios.get<DashboardOptions>(`${API_BASE_URL}/filters`)).data;
	}

	async overview(filters: DashboardFilters): Promise<DashboardOverview> {
		return (
			await axios.get<DashboardOverview>(`${API_BASE_URL}/overview`, {
				params: dashboardParams(filters),
			})
		).data;
	}

	async failureTrend(
		filters: DashboardFilters,
		granularity: 'day' | 'week' | 'month',
	): Promise<FailureTrendItem[]> {
		const response = await axios.get<{ items: FailureTrendItem[] }>(
			`${API_BASE_URL}/failure-trend`,
			{ params: { ...dashboardParams(filters), granularity } },
		);
		return response.data.items;
	}

	async failureByModel(filters: DashboardFilters): Promise<FailureModelItem[]> {
		const response = await axios.get<{ items: FailureModelItem[] }>(
			`${API_BASE_URL}/failure-by-model`,
			{ params: dashboardParams(filters) },
		);
		return response.data.items;
	}

	async failureTypes(filters: DashboardFilters): Promise<FailureTypeItem[]> {
		const response = await axios.get<{ items: FailureTypeItem[] }>(
			`${API_BASE_URL}/failure-types`,
			{ params: dashboardParams(filters) },
		);
		return response.data.items;
	}

	async errorRanking(filters: DashboardFilters): Promise<ErrorRankingItem[]> {
		const response = await axios.get<{ items: ErrorRankingItem[] }>(
			`${API_BASE_URL}/error-ranking`,
			{ params: { ...dashboardParams(filters), limit: 10 } },
		);
		return response.data.items;
	}

	async machineRanking(filters: DashboardFilters): Promise<MachineRankingItem[]> {
		const response = await axios.get<{ items: MachineRankingItem[] }>(
			`${API_BASE_URL}/machine-ranking`,
			{ params: { ...dashboardParams(filters), limit: 10 } },
		);
		return response.data.items;
	}

	async telemetry(
		filters: DashboardFilters,
		machineId: number,
	): Promise<TelemetryResponse> {
		const params = dashboardParams({ ...filters, machineId });
		delete params.machine_id;
		return (
			await axios.get<TelemetryResponse>(
				`${API_BASE_URL}/machine/${machineId}/telemetry`,
				{ params: { ...params, max_points: 500 } },
			)
		).data;
	}

	async maintenance(filters: DashboardFilters): Promise<MaintenanceResponse> {
		return (
			await axios.get<MaintenanceResponse>(`${API_BASE_URL}/maintenance-analysis`, {
				params: dashboardParams(filters),
			})
		).data;
	}

	async analysisContext(filters: DashboardFilters): Promise<AnalysisContextResponse> {
		return (
			await axios.post<AnalysisContextResponse>(
				`${API_BASE_URL}/analysis-context`,
				null,
				{ params: dashboardParams(filters) },
			)
		).data;
	}
}

export default new DashboardService();
