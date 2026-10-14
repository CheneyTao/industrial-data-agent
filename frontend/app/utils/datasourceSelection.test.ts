/*
 * Copyright 2026 the original author or authors.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { describe, expect, it } from 'vitest';
import {
	datasourceCaption,
	resolveActiveDatasource,
} from './datasourceSelection';

describe('datasourceCaption', () => {
	it('shows the real database and optional schema without guessing its purpose', () => {
		expect(
			datasourceCaption({ type: 'mysql', databaseName: 'product_db' }),
		).toBe('数据库：product_db');
		expect(
			datasourceCaption({
				type: 'postgresql',
				databaseName: 'analytics',
				schemaName: 'public',
			}),
		).toBe('数据库：analytics · Schema：public');
	});
	it('shows only the SQLite filename for both Windows and Linux paths', () => {
		expect(
			datasourceCaption({
				type: 'SQLITE',
				databaseName: 'D:\\work\\mysql-smoke-analysis.db',
			}),
		).toBe('数据库：mysql-smoke-analysis.db');
		expect(
			datasourceCaption({ type: 'sqlite', databaseName: '/app/data/demo.db' }),
		).toBe('数据库：demo.db');
	});
	it('handles missing metadata without undefined or invented labels', () => {
		expect(datasourceCaption({})).toBe('未配置数据库名称');
		expect(datasourceCaption({ databaseName: '  ' })).toBe('未配置数据库名称');
	});
	it('does not disclose credentials even when passed a complete source record', () => {
		const source = {
			type: 'mysql',
			databaseName: 'industrial_pdm',
			username: 'private',
			password: 'secret',
			connectionUrl: 'mysql://private:secret@example/industrial_pdm',
		};
		expect(datasourceCaption(source)).toBe('数据库：industrial_pdm');
	});
});

describe('resolveActiveDatasource', () => {
	it('uses the datasource bound to the current agent instead of the first global datasource', () => {
		const result = resolveActiveDatasource(
			[
				{ id: 5, name: 'newest global datasource' },
				{ id: 3, name: 'agent datasource' },
			],
			3,
		);

		expect(result.activeDatasource?.name).toBe('agent datasource');
		expect(result.datasources).toEqual([
			{ id: 5, name: 'newest global datasource', isActive: false },
			{ id: 3, name: 'agent datasource', isActive: true },
		]);
	});

	it('does not mislabel a global datasource when the agent has no active binding', () => {
		const result = resolveActiveDatasource([
			{ id: 5, name: 'global datasource' },
		]);

		expect(result.activeDatasource).toBeNull();
		expect(result.datasources[0]?.isActive).toBe(false);
	});
});
