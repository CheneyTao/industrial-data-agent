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

import type { ResultData } from '../services/resultSet/index';

export type ResultChartType = 'column' | 'bar' | 'line' | 'pie';

export interface ResultChartSeries {
	name: string;
	values: Array<number | null>;
}

export interface ResultChartModel {
	type: ResultChartType;
	title: string;
	xField: string;
	yFields: string[];
	categories: string[];
	series: ResultChartSeries[];
}

const CHART_TYPES = new Set<ResultChartType>([
	'column',
	'bar',
	'line',
	'pie',
]);

function toFiniteNumber(value: unknown): number | null {
	if (typeof value === 'number') return Number.isFinite(value) ? value : null;
	if (typeof value !== 'string' || value.trim() === '') return null;

	const normalized = value.replace(/,/g, '').trim();
	const parsed = Number(normalized);
	return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Convert the backend displayStyle contract into a validated, renderer-neutral
 * chart model. Invalid chart instructions deliberately fall back to the table.
 */
export function buildResultChartModel(
	data: ResultData | null | undefined,
): ResultChartModel | null {
	const style = data?.displayStyle;
	const rawType = style?.type?.toLowerCase() as ResultChartType | undefined;
	if (!rawType || !CHART_TYPES.has(rawType)) return null;

	const columns = data?.resultSet?.column || [];
	const rows = data?.resultSet?.data || [];
	const xField = style?.x;
	if (!xField || !columns.includes(xField) || rows.length === 0) return null;

	const yFields = (style?.y || []).filter(
		(field): field is string => Boolean(field) && columns.includes(field),
	);
	if (yFields.length === 0) return null;
	if (yFields.includes(xField) || new Set(yFields).size !== yFields.length) return null;

	const series = yFields.map((field) => ({
		name: field,
		values: rows.map((row) => toFiniteNumber(row[field])),
	}));
	if (!series.some((item) => item.values.some((value) => value !== null))) {
		return null;
	}
	const categories = rows.map((row) => String(row[xField] ?? '(空值)'));
	if (new Set(categories).size !== categories.length) return null;
	if (rawType === 'pie') {
		if (series.length !== 1 || rows.length < 2 || rows.length > 8) return null;
		const values = series[0]!.values;
		if (values.some((v) => v === null || v < 0) || values.reduce<number>((a, v) => a + (v ?? 0), 0) <= 0) return null;
	}

	return {
		type: rawType,
		title: style?.title?.trim() || '数据分析图表',
		xField,
		yFields,
		categories,
		series,
	};
}

export function buildResultChartOption(
	model: ResultChartModel,
): Record<string, unknown> {
	const base = {
		animationDuration: 500,
		color: ['#5584ff', '#36b9cc', '#4caf73', '#f0b429', '#f2637b', '#8b5cf6'],
		title: {
			text: model.title,
			left: 12,
			top: 8,
			textStyle: { fontSize: 15, fontWeight: 600, color: '#1f2937' },
		},
		legend: { type: 'scroll', top: 40, right: 12 },
	};

	if (model.type === 'pie') {
		const primarySeries = model.series[0];
		return {
			...base,
			tooltip: { trigger: 'item', renderMode: 'richText' },
			series: [
				{
					name: primarySeries?.name || '',
					type: 'pie',
					radius: ['36%', '67%'],
					center: ['50%', '57%'],
					avoidLabelOverlap: true,
					label: { formatter: '{b}: {c}' },
					data: model.categories.map((name, index) => ({
						name,
						value: primarySeries?.values[index] ?? null,
					})),
				},
			],
		};
	}

	const horizontal = model.type === 'bar';
	const seriesType = model.type === 'line' ? 'line' : 'bar';
	const categoryAxis = {
		type: 'category',
		data: model.categories,
		axisLabel: {
			interval: 0,
			rotate: !horizontal && model.categories.length > 8 ? 30 : 0,
		},
	};
	const valueAxis = { type: 'value' };

	return {
		...base,
		tooltip: {
			trigger: 'axis',
			renderMode: 'richText',
			axisPointer: { type: seriesType === 'bar' ? 'shadow' : 'line' },
		},
		grid: { top: 82, right: 24, bottom: 52, left: 68, containLabel: true },
		xAxis: horizontal ? valueAxis : categoryAxis,
		yAxis: horizontal ? categoryAxis : valueAxis,
		series: model.series.map((item) => ({
			name: item.name,
			type: seriesType,
			data: item.values,
			smooth: seriesType === 'line',
			emphasis: { focus: 'series' },
		})),
	};
}
