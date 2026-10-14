/*
 * Copyright 2026 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import graphService, { parseSseFrame } from './index';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('parseSseFrame', () => {
	it('supports comments, named events and multi-line data', () => {
		expect(
			parseSseFrame(': keep-alive\r\nevent: update\r\ndata: first\r\ndata: second'),
		).toEqual({ event: 'update', data: 'first\nsecond' });
	});
});

describe('GraphService.streamSearch', () => {
	it('sends attachment IDs in POST body, retaining credentials only in headers', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('event: complete\ndata: {}\n\n'));
		vi.stubGlobal('fetch', fetchMock);
		await graphService.streamSearch({ agentId: '8', conversationId: 's1', query: 'analyse file', humanFeedback: false, rejectedPlan: false, nl2sqlOnly: false, apiKey: 'private-key', attachmentIds: ['a1'] }, async () => {});
		const [url, options] = fetchMock.mock.calls[0]!;
		expect(url).toBe('/api/stream/search');
		expect(options.method).toBe('POST');
		expect(JSON.parse(options.body).attachmentIds).toEqual(['a1']);
		expect(options.body).not.toContain('private-key');
		expect(options.headers['X-API-Key']).toBe('private-key');
	});
	it('keeps retryable node failures in the stream and normalizes Python names', async () => {
		const node = {agentId:'8', threadId:'r1', nodeName:'SQL_EXECUTE_NODE', textType:'TEXT', text:'retry', error:true};
		const next = {...node, nodeName:'REPORT_GENERATOR_NODE', error:false, text:'done'};
		const stream = new ReadableStream<Uint8Array>({
			start(controller) {
				controller.enqueue(new TextEncoder().encode(
					'data: ' + JSON.stringify(node) + '\n\n' +
					'data: ' + JSON.stringify(next) + '\n\nevent: complete\ndata: {}\n\n',
				));
				controller.close();
			},
		});
		vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)));
		const received: string[] = [];
		const errors = vi.fn();
		let completed = false;
		await graphService.streamSearch(
			{agentId:'8', conversationId:'s1', query:'compare', humanFeedback:false, rejectedPlan:false, nl2sqlOnly:false},
			async event => {received.push(event.nodeName);},
			errors,
			async () => {completed = true;},
		);
		await vi.waitFor(() => expect(completed).toBe(true));
		expect(received).toEqual(['SqlExecuteNode', 'ReportGeneratorNode']);
		expect(errors).not.toHaveBeenCalled();
	});
	it('streams with an API key header without leaking the key into the URL', async () => {
		const node = {
			agentId: '8',
			threadId: 'run-1',
			nodeName: 'PlannerNode',
			textType: 'TEXT',
			text: 'working',
			error: false,
			complete: false,
		};
		const bytes = new TextEncoder().encode(
			`data: ${JSON.stringify(node)}\n\nevent: complete\ndata: {}\n\n`,
		);
		const stream = new ReadableStream<Uint8Array>({
			start(controller) {
				controller.enqueue(bytes.slice(0, 17));
				controller.enqueue(bytes.slice(17));
				controller.close();
			},
		});
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(stream, {
				status: 200,
				headers: { 'Content-Type': 'text/event-stream' },
			}),
		);
		vi.stubGlobal('fetch', fetchMock);

		const received: unknown[] = [];
		let completed = false;
		await graphService.streamSearch(
			{
				agentId: '8',
				conversationId: 'session-1',
				query: 'analyse',
				humanFeedback: false,
				rejectedPlan: false,
				nl2sqlOnly: false,
				apiKey: 'top-secret',
			},
			async (value) => {
				received.push(value);
			},
			undefined,
			async () => {
				completed = true;
			},
		);

		await vi.waitFor(() => expect(completed).toBe(true));
		expect(received).toHaveLength(1);
		const [url, options] = fetchMock.mock.calls[0] as [
			string,
			{ headers: Record<string, string> },
		];
		expect(url).not.toContain('top-secret');
		expect(options.headers['X-API-Key']).toBe('top-secret');
	});
});
