export interface AttachmentColumn {
	name: string;
	originalName: string;
	type: string;
	nullCount: number;
}
export interface AttachmentTable {
	name: string;
	label: string;
	rowCount: number;
	columns: AttachmentColumn[];
	preview: (string | number | null)[][];
	document: boolean;
}
export interface ChatAttachment {
	id: string;
	name: string;
	size: number;
	sha256: string;
	kind: 'table' | 'document';
	format: string;
	status: 'ready';
	agentId: number;
	conversationId: string;
	tables: AttachmentTable[];
	warnings: string[];
}
export interface AttachmentCapabilities {
	extensions: string[];
	maxBytes: number;
	maxFiles: number;
}
export interface AttachmentReference {
	id: string;
	name: string;
	size: number;
	kind: string;
}

export function fileValidation(
	file: { name: string; size: number },
	capabilities: AttachmentCapabilities,
): string | null {
	const dot = file.name.lastIndexOf('.');
	const extension = dot < 0 ? '' : file.name.slice(dot).toLowerCase();
	if (!capabilities.extensions.includes(extension))
		return '此格式尚未接入，请查看支持格式';
	if (file.size === 0) return '文件为空';
	if (file.size > capabilities.maxBytes)
		return `文件超过 ${Math.floor(capabilities.maxBytes / 1024 / 1024)} MB 限制`;
	return null;
}

export function attachmentReferences(metadata?: string): AttachmentReference[] {
	try {
		const parsed = JSON.parse(metadata || '{}');
		if (!Array.isArray(parsed.attachments)) return [];
		return parsed.attachments
			.filter((v: unknown): v is AttachmentReference => {
				if (!v || typeof v !== 'object') return false;
				const a = v as AttachmentReference;
				return (
					typeof a.id === 'string' &&
					/^[a-f0-9-]{36}$/i.test(a.id) &&
					typeof a.name === 'string' &&
					typeof a.size === 'number' &&
					Number.isFinite(a.size)
				);
			})
			.slice(0, 6);
	} catch {
		return [];
	}
}

export async function attachmentRequest<T>(
	url: string,
	options: RequestInit = {},
): Promise<T> {
	const response = await fetch(url, options);
	const payload = await response.json().catch(() => null);
	if (!response.ok)
		throw new Error(
			typeof payload?.message === 'string'
				? payload.message
				: typeof payload?.detail === 'string'
					? payload.detail
					: `附件请求失败 (${response.status})`,
		);
	if (!payload) throw new Error('附件服务返回空响应');
	return payload as T;
}
