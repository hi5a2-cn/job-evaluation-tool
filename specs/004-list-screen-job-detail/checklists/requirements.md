# Specification Quality Checklist: 列表页规则粗筛 + 独立职位页被动读取

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-29
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- 页面事实一节按本项目惯例（003 spec 同样记录了 `encryptJobId`、`bodyType` 等页面字段）保留了探测得到的读取位置表，标明"供 plan 使用"；需求条款（FR/SC）本身用业务语言表述，不依赖具体选择器。草稿删除前已把其中的选择器表完整移入 spec。
- "已判断过的岗位不再显示粗筛提示"是默认取舍，已写入 Assumptions，可在 `/speckit-clarify` 调整。
- FR-018：发给大模型的公司名来源变化已由用户 2026-09-29 确认（CLAUDE.md"先确认后生效"）。
