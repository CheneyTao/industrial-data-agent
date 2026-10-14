# 组件: BaseDrawer

## 模块描述
基础侧边栏布局组件，提供左侧抽屉和右侧内容区的响应式布局

## Props
| 属性 | 类型 | 默认值 | 描述 |
| --- | --- | --- | --- |
| drawerWidth | `union` | 260 | 抽屉宽度 (数字或带单位的字符串) |
| modelValue | `boolean` | true | 抽屉是否展开 (支持 v-model) |

## Slots
| 名称 | 描述 |
| --- | --- |
| drawer | - |
| header | `{ toggle, isOpen, drawerId }`，开关按钮应使用 `aria-controls=drawerId` 与动态 `aria-expanded` |
| default | - |

## Events
| 名称 | 描述 |
| --- | --- |
| update:modelValue | 更新展开状态 |

## 响应式与关闭行为

- 视口宽度小于 769px 时使用遮罩抽屉；布局和行为共用同一个断点。
- 进入窄屏默认收起，展开后可使用抽屉内关闭按钮、点击遮罩或按 Esc 收起。
- 遮罩展开时背景不可聚焦；收起后隐藏导航不可聚焦，并将焦点还给页头开关。
- 桌面保持并列布局，通过页头开关展开/收起；菜单分组状态不受影响。

---
> 🤖 AI 提示: 修改此组件前请阅读上述定义。代码位于 `BaseDrawer/index.vue`。
