export function safeDashboardRoute(value: unknown): string | null {
	if (!value || typeof value !== 'object') return null;
	const action = value as { action?: string; route?: string; dataset_id?: string };
	if (action.action !== 'open_dashboard' || typeof action.route !== 'string') return null;
	if (!['industrial_pdm', 'scania_component_x'].includes(action.dataset_id || '')) return null;
	try {
		const url = new URL(action.route, 'https://local.invalid');
		if (url.origin !== 'https://local.invalid' || url.pathname !== '/dashboard') return null;
		if (url.searchParams.get('dataset') !== action.dataset_id) return null;
		return url.pathname + url.search;
	} catch { return null; }
}

export function finiteDisplay(value: unknown, digits = 1): string {
	if (value === null || value === undefined || value === '') return '—';
	const number = Number(value);
	return Number.isFinite(number) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(number) : '—';
}
