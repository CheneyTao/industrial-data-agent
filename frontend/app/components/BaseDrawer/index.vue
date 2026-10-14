/* * Copyright 2026 the original author or authors. * * Licensed under the
Apache License, Version 2.0 (the "License"); * you may not use this file except
in compliance with the License. * You may obtain a copy of the License at * *
https://www.apache.org/licenses/LICENSE-2.0 * * Unless required by applicable
law or agreed to in writing, software * distributed under the License is
distributed on an "AS IS" BASIS, * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
either express or implied. * See the License for the specific language governing
permissions and * limitations under the License. */

<template>
	<div
		ref="drawerRoot"
		class="base-drawer"
		:class="{ 'base-drawer--overlay': isOverlay }"
		:style="cssVars"
		@keydown.esc="handleEscape"
	>
		<button
			v-if="isOverlay && modelValue"
			class="base-drawer__backdrop"
			type="button"
			aria-label="点击遮罩收起导航栏"
			tabindex="-1"
			@click="close"
		/>
		<aside
			:id="drawerId"
			class="base-drawer__left"
			:class="{ 'base-drawer__left--closed': !modelValue }"
			aria-label="主导航"
			:aria-hidden="!modelValue"
			:inert="!modelValue"
		>
			<div v-if="isOverlay" class="base-drawer__toolbar">
				<span>导航菜单</span>
				<button
					ref="closeButton"
					type="button"
					class="base-drawer__close"
					aria-label="收起导航栏"
					:aria-controls="drawerId"
					@click="close"
				>
					<v-icon icon="mdi-close" size="20" />
				</button>
			</div>
			<div class="base-drawer__body">
				<slot name="drawer" />
			</div>
		</aside>

		<section class="base-drawer__right" :inert="isOverlay && modelValue">
			<header v-if="$slots.header" class="base-drawer__header">
				<slot
					name="header"
					:toggle="toggle"
					:is-open="modelValue"
					:drawer-id="drawerId"
				/>
			</header>
			<div class="base-drawer__content">
				<slot />
			</div>
		</section>
	</div>
</template>

<script setup lang="ts">
import { useDisplay } from 'vuetify';

/**
 * @description 基础侧边栏布局组件，提供左侧抽屉和右侧内容区的响应式布局
 */

interface Props {
	/** 抽屉宽度 (数字或带单位的字符串) */
	drawerWidth?: number | string;
	/** 抽屉是否展开 (支持 v-model) */
	modelValue?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
	drawerWidth: 260,
	modelValue: true,
});

const emit = defineEmits<{
	/** 更新展开状态 */
	'update:modelValue': [value: boolean];
}>();

// One breakpoint controls both interaction and layout; an overlay must have
// its own close control because it covers the page header's toggle.
const { mobile: isOverlay } = useDisplay({ mobileBreakpoint: 769 });
const drawerId = useId();
const drawerRoot = ref<HTMLElement>();
const closeButton = ref<HTMLButtonElement>();

const close = () => emit('update:modelValue', false);

watch(
	isOverlay,
	(overlay) => {
		if (overlay) close();
	},
	{ immediate: true },
);

watch(
	() => props.modelValue,
	async (open, wasOpen) => {
		await nextTick();
		if (open && isOverlay.value) {
			closeButton.value?.focus();
		} else if (wasOpen && !open) {
			// Return keyboard focus to the now-reachable header toggle.
			drawerRoot.value
				?.querySelector<HTMLButtonElement>(
					'.base-drawer__header [aria-controls]',
				)
				?.focus();
		}
	},
);

function handleEscape(event: KeyboardEvent) {
	if (!event.defaultPrevented && isOverlay.value && props.modelValue) {
		event.preventDefault();
		close();
	}
}

/**
 * @description 切换抽屉展开/折叠状态
 */
const toggle = () => {
	emit('update:modelValue', !props.modelValue);
};

const normalizedDrawerWidth = computed(() => {
	const width = props.drawerWidth;
	return typeof width === 'number' ? `${width}px` : width;
});

const cssVars = computed(() => ({
	'--drawer-width': normalizedDrawerWidth.value,
}));
</script>

<style scoped>
.base-drawer {
	position: relative;
	display: flex;
	width: 100%;
	height: 100vh;
	overflow: hidden;
	background-color: #f8fafc;
}

.base-drawer__left {
	width: var(--drawer-width);
	height: 100%;
	background-color: #1e293b;
	color: #e2e8f0;
	transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
	overflow: hidden;
	display: flex;
	flex-direction: column;
	border-right: 1px solid rgba(255, 255, 255, 0.05);
	flex-shrink: 0;
	white-space: nowrap;
}

.base-drawer__left--closed {
	width: 0;
	border-right-width: 0;
}

.base-drawer__body {
	flex: 1;
	min-height: 0;
	overflow: auto;
}

.base-drawer__toolbar {
	display: flex;
	align-items: center;
	justify-content: space-between;
	flex-shrink: 0;
	padding: 6px 12px 6px 16px;
	font-size: 12px;
	border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.base-drawer__close {
	display: grid;
	place-items: center;
	width: 36px;
	height: 36px;
	border-radius: 8px;
	color: inherit;
	cursor: pointer;
}

.base-drawer__close:hover,
.base-drawer__close:focus-visible {
	background: rgba(255, 255, 255, 0.12);
}

.base-drawer__close:focus-visible {
	outline: 2px solid #93c5fd;
	outline-offset: -2px;
}

.base-drawer__backdrop {
	position: absolute;
	inset: 0;
	z-index: 99;
	background: rgba(15, 23, 42, 0.45);
	cursor: pointer;
}

.base-drawer__right {
	flex: 1;
	display: flex;
	flex-direction: column;
	min-width: 0; /* Prevent flex child overflow */
	height: 100%;
}

.base-drawer__header {
	height: 56px;
	border-bottom: 1px solid #e2e8f0;
	background-color: #ffffff;
	display: flex;
	align-items: center;
	padding: 0 16px;
	flex-shrink: 0;
}

.base-drawer__content {
	flex: 1;
	overflow: auto;
	position: relative;
}

.base-drawer--overlay {
	.base-drawer__left {
		position: absolute;
		inset: 0 auto 0 0;
		max-width: calc(100% - 48px);
		z-index: 100;
		box-shadow: 8px 0 24px rgba(15, 23, 42, 0.2);
	}

	.base-drawer__left--closed {
		box-shadow: none;
	}

	.base-drawer__right {
		width: 100%;
	}
}
</style>
