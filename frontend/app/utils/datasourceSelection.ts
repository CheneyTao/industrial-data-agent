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

export interface SelectableDatasource {
	id?: number;
	isActive?: boolean;
}

/** Display safe database metadata, never credentials or connection URLs. */
export function datasourceCaption(datasource: {
	type?: string;
	databaseName?: string;
	schemaName?: string;
}): string {
	const database = datasource.databaseName?.trim();
	if (!database) return '未配置数据库名称';
	const name =
		datasource.type?.toLowerCase() === 'sqlite'
			? database.split(/[\\/]/).filter(Boolean).at(-1) || database
			: database;
	const schema = datasource.schemaName?.trim();
	return `数据库：${name}${schema ? ` · Schema：${schema}` : ''}`;
}

export function resolveActiveDatasource<T extends SelectableDatasource>(
	datasources: T[],
	activeDatasourceId?: number,
): { datasources: T[]; activeDatasource: T | null } {
	const resolvedDatasources = datasources.map((datasource) => ({
		...datasource,
		isActive: datasource.id === activeDatasourceId,
	}));

	return {
		datasources: resolvedDatasources,
		activeDatasource:
			resolvedDatasources.find((datasource) => datasource.isActive) || null,
	};
}
