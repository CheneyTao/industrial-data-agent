import { describe, expect, it } from 'vitest';
import { attachmentReferences, fileValidation } from './attachments';
const capabilities = {
	extensions: ['.csv', '.pdf'],
	maxBytes: 100,
	maxFiles: 6,
};
describe('chat attachments', () => {
	it('validates content-independent limits without guessing unsupported formats', () => {
		expect(
			fileValidation({ name: 'data.CSV', size: 10 }, capabilities),
		).toBeNull();
		expect(
			fileValidation({ name: 'foo.csv.exe', size: 10 }, capabilities),
		).toBeTruthy();
		expect(
			fileValidation({ name: 'foo.pdf', size: 0 }, capabilities),
		).toBeTruthy();
		expect(
			fileValidation({ name: 'foo.csv', size: 101 }, capabilities),
		).toBeTruthy();
	});
	it('reads only valid attachment history metadata', () => {
		expect(attachmentReferences('{bad')).toEqual([]);
		expect(
			attachmentReferences('{"attachments":[null,{"id":"../../x"}]}'),
		).toEqual([]);
		const attachment = {
			id: '39c63d6f-9327-447e-a6f2-65472dd0dc25',
			name: '<img>.csv',
			size: 10,
			kind: 'table',
		};
		expect(
			attachmentReferences(JSON.stringify({ attachments: [attachment] })),
		).toEqual([attachment]);
	});
});
