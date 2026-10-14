<template>
	<div class="attachments">
		<div class="attachment-toolbar">
			<button
				type="button"
				class="attach-button"
				:disabled="attachments.disabled"
				@click="picker?.click()"
			>
				<v-icon size="17">mdi-paperclip</v-icon> 添加文件
			</button>
			<span>也可以拖到聊天区域 · 先识别预览，点击发送才分析</span>
			<details class="format-help">
				<summary>支持格式</summary>
				<p v-if="attachments.capabilities">
					{{ attachments.capabilities.extensions.join('、') }}<br />每个文件
					{{
						Math.floor(attachments.capabilities.maxBytes / 1024 / 1024)
					}}
					MB，每轮最多 {{ attachments.capabilities.maxFiles }} 个。<br />扫描件
					OCR、图片、旧版 .xls 和压缩包尚未接入。
				</p>
				<button v-else type="button" @click="attachments.loadCapabilities()">
					重新获取支持格式
				</button>
			</details>
		</div>
		<input
			ref="picker"
			type="file"
			multiple
			:accept="attachments.capabilities?.extensions.join(',')"
			aria-label="选择聊天附件"
			class="file-picker"
			@change="choose"
		/>
		<div
			v-if="attachments.items.length"
			class="attachment-list"
			aria-live="polite"
		>
			<div
				v-for="item in attachments.items"
				:key="item.key"
				class="attachment-card"
				:class="{ failed: item.status === 'error' }"
			>
				<v-progress-circular
					v-if="item.status === 'uploading'"
					indeterminate
					size="18"
					width="2"
				/>
				<v-icon v-else size="20">{{
					item.asset?.kind === 'document'
						? 'mdi-file-document-outline'
						: 'mdi-table'
				}}</v-icon>
				<div class="attachment-info">
					<strong>{{ item.name }}</strong
					><span v-if="item.status === 'uploading'">上传与解析中…</span
					><span v-else-if="item.error">{{ item.error }}</span
					><span v-else-if="item.asset"
						>{{ item.asset.kind === 'document' ? '文档文字' : '结构化数据' }} ·
						{{ item.asset.tables.length }}
						{{ item.asset.kind === 'document' ? '组片段' : '张表' }} ·
						已就绪</span
					>
				</div>
				<button
					v-if="item.asset"
					type="button"
					class="text-action"
					@click="attachments.preview = item.asset"
				>
					预览
				</button>
				<button
					type="button"
					class="remove-attachment"
					:aria-label="`移除附件 ${item.name}`"
					:disabled="chat.isStreaming"
					@click="attachments.remove(item.key)"
				>
					<v-icon size="17">mdi-close</v-icon>
				</button>
			</div>
		</div>
		<p v-if="attachments.items.length" class="attachment-scope">
			本轮使用所选附件，后续提问继续使用；移除全部附件后恢复数据库分析。不会覆盖数据库或加入共享知识库。
		</p>
		<p v-if="attachments.error" class="attachment-error" role="alert">
			{{ attachments.error }}
		</p>
		<v-dialog
			:model-value="!!attachments.preview"
			max-width="900"
			scrollable
			@update:model-value="
				(value) => {
					if (!value) attachments.preview = null;
				}
			"
		>
			<v-card v-if="attachments.preview" class="preview-card">
				<v-card-title class="preview-title"
					>{{ attachments.preview.name
					}}<v-btn
						icon="mdi-close"
						variant="text"
						aria-label="关闭附件预览"
						@click="attachments.preview = null"
				/></v-card-title>
				<v-card-text>
					<p>识别结果 · 原始文件保留 · 预览仅显示前 5 行，不代表全量统计</p>
					<p
						v-for="warning in attachments.preview.warnings"
						:key="warning"
						class="preview-warning"
					>
						{{ warning }}
					</p>
					<section
						v-for="table in attachments.preview.tables"
						:key="table.name"
						class="preview-table"
					>
						<h3>
							{{ table.label }} · {{ table.rowCount.toLocaleString() }}
							{{ table.document ? '个文字片段' : '行' }} ·
							{{ table.columns.length }} 列
						</h3>
						<p class="table-id">分析表：{{ table.name }}</p>
						<div class="preview-scroll">
							<table>
								<thead>
									<tr>
										<th v-for="column in table.columns" :key="column.name">
											{{ column.originalName || '(空表头)'
											}}<small
												>{{ column.name }} · {{ column.type }} · 缺失
												{{ column.nullCount }}</small
											>
										</th>
									</tr>
								</thead>
								<tbody>
									<tr v-for="(row, index) in table.preview" :key="index">
										<td v-for="(cell, col) in row" :key="col">
											{{ cell === null ? 'NULL' : cell }}
										</td>
									</tr>
								</tbody>
							</table>
						</div>
					</section>
				</v-card-text>
			</v-card>
		</v-dialog>
	</div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useChatStore } from '~/stores/chat';
import { useChatAttachments } from '~/stores/chatAttachments';
const chat = useChatStore();
const attachments = useChatAttachments();
const picker = ref<HTMLInputElement | null>(null);
onMounted(() => attachments.loadCapabilities());
function choose(event: Event) {
	const input = event.target as HTMLInputElement;
	const files = Array.from(input.files || []);
	input.value = '';
	void attachments.addFiles(files);
}
</script>

<style scoped>
.attachment-toolbar {
	display: flex;
	align-items: center;
	gap: 10px;
	flex-wrap: wrap;
	color: #64748b;
	font-size: 12px;
}
.attach-button {
	display: flex;
	align-items: center;
	gap: 4px;
	border: 1px solid #cbd5e1;
	border-radius: 7px;
	padding: 5px 9px;
	color: #334155;
}
button:disabled {
	opacity: 0.5;
	cursor: not-allowed;
}
.file-picker {
	position: absolute;
	width: 1px;
	height: 1px;
	opacity: 0;
	overflow: hidden;
	pointer-events: none;
}
.format-help {
	font-size: 12px;
}
.format-help p {
	max-width: 600px;
	overflow-wrap: anywhere;
}
.attachment-list {
	display: flex;
	gap: 8px;
	flex-wrap: wrap;
	max-height: 150px;
	overflow: auto;
	margin-top: 8px;
}
.attachment-card {
	display: flex;
	align-items: center;
	gap: 8px;
	background: #f0f7ff;
	border: 1px solid #dbeafe;
	padding: 8px;
	border-radius: 8px;
	min-width: 0;
	flex: 1 1 260px;
	max-width: 100%;
}
.attachment-info {
	display: flex;
	flex-direction: column;
	min-width: 0;
	flex: 1;
	font-size: 12px;
}
.attachment-info strong {
	overflow-wrap: anywhere;
	color: #334155;
}
.attachment-info span {
	color: #64748b;
}
.text-action {
	color: #2563eb;
	font-size: 12px;
	flex-shrink: 0;
}
.failed {
	border-color: #fecaca;
	background: #fff5f5;
}
.attachment-scope {
	font-size: 11px;
	color: #476487;
	margin: 6px 0 0;
}
.attachment-error {
	color: #b91c1c;
	font-size: 12px;
	margin: 6px 0 0;
}
.preview-title {
	display: flex;
	justify-content: space-between;
	align-items: center;
	white-space: normal;
	overflow-wrap: anywhere;
}
.preview-warning {
	padding: 7px 10px;
	background: #f8fafc;
	font-size: 12px;
	margin-top: 6px;
}
.preview-table {
	margin-top: 20px;
}
.preview-table h3 {
	font-size: 15px;
}
.table-id {
	font-size: 11px;
	color: #64748b;
	overflow-wrap: anywhere;
}
.preview-scroll {
	overflow: auto;
	max-height: 360px;
	margin-top: 8px;
}
table {
	border-collapse: collapse;
	font-size: 12px;
	width: 100%;
}
th,
td {
	border: 1px solid #e2e8f0;
	padding: 8px;
	text-align: left;
	vertical-align: top;
	min-width: 140px;
	max-width: 380px;
	white-space: pre-wrap;
	overflow-wrap: anywhere;
}
th {
	background: #f1f5f9;
}
th small {
	display: block;
	font-weight: normal;
	color: #64748b;
	margin-top: 4px;
}
</style>
