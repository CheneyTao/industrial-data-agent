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
import type { ResultData } from '../services/resultSet/index';
import { buildResultChartModel, buildResultChartOption } from './resultChart';

function result(
	type: string,
	x: string | null,
	y: string[],
): ResultData {
	return {
		displayStyle: { type, title: '商品销量', x, y },
		resultSet: {
			column: ['product_name', 'sales_volume', 'sales_amount'],
			data: [
				{ product_name: '机械键盘', sales_volume: '12', sales_amount: 3588 },
				{ product_name: '显示器', sales_volume: 5, sales_amount: '6495.00' },
			],
		},
	};
}

describe('buildResultChartModel', () => {
	it('builds a numeric column chart model from backend displayStyle', () => {
		const model = buildResultChartModel(
			result('column', 'product_name', ['sales_volume', 'sales_amount']),
		);

		expect(model?.categories).toEqual(['机械键盘', '显示器']);
		expect(model?.series).toEqual([
			{ name: 'sales_volume', values: [12, 5] },
			{ name: 'sales_amount', values: [3588, 6495] },
		]);
	});

	it('falls back to table for table style or invalid chart fields', () => {
		expect(
			buildResultChartModel(result('table', null, [])),
		).toBeNull();
		expect(
			buildResultChartModel(result('line', 'missing_column', ['sales_volume'])),
		).toBeNull();
	});

	it('creates horizontal axes for a bar chart', () => {
		const model = buildResultChartModel(
			result('bar', 'product_name', ['sales_volume']),
		);
		const option = buildResultChartOption(model!);

		expect(option.xAxis).toEqual({ type: 'value' });
		expect(option.yAxis).toMatchObject({
			type: 'category',
			data: ['机械键盘', '显示器'],
		});
	});

	it('requires exactly one non-negative metric for a pie chart', () => {
		expect(buildResultChartModel(result('pie', 'product_name', ['sales_volume', 'sales_amount']))).toBeNull();
		const model = buildResultChartModel(
			result('pie', 'product_name', ['sales_volume']),
		);
		const option = buildResultChartOption(model!);
		const series = option.series as Array<Record<string, unknown>>;

		expect(series).toHaveLength(1);
		expect(series[0]?.data).toEqual([
			{ name: '机械键盘', value: 12 },
			{ name: '显示器', value: 5 },
		]);
	});

	it('keeps nulls and rejects repeated categories', () => {
		const payload = result('line', 'product_name', ['sales_volume']);
		payload.resultSet.data[0]!.sales_volume = null;
		expect(buildResultChartModel(payload)?.series[0]?.values).toEqual([null, 5]);
		payload.resultSet.data[1]!.product_name = '机械键盘';
		expect(buildResultChartModel(payload)).toBeNull();
	});
});
