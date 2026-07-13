// Dashboard widget registry — the single place to edit to add/move a widget.
// DashboardPage reads this and never imports a widget directly, so new widgets
// are pluggable without touching the page. Composition = common baseline +
// exact per-role set (per the approved design).
import {
  KpiSummaryWidget,
  EmissionsTrendWidget,
  ScopeBreakdownWidget,
  ReportsWidget,
} from './widgets/CommonWidgets';
import {
  UploadShortcutWidget,
  RecentIngestionWidget,
  ValidationSummaryWidget,
} from './widgets/AnalystWidgets';
import {
  PendingApprovalsWidget,
  AuditQueueWidget,
  LockedRecordsWidget,
} from './widgets/AuditorWidgets';
import {
  OrgKpisWidget,
  CoverageWidget,
  UserActivityWidget,
  FactorDatasetWidget,
  AIBudgetWidget,
} from './widgets/OrgAdminWidgets';
import {
  CrossTenantWidget,
  SystemHealthWidget,
  ActiveOrganizationsWidget,
  DatasetInventoryWidget,
} from './widgets/PlatformWidgets';
import {
  AIUsageWidget,
  AIProviderMixWidget,
  AIEvaluationWidget,
  AILatencyTrendWidget,
} from './widgets/AIObservabilityWidgets';

// span: full (whole row) | half | third
const COMMON = [
  { id: 'kpi-summary', component: KpiSummaryWidget, span: 'full' },
  { id: 'emissions-trend', component: EmissionsTrendWidget, span: 'half' },
  { id: 'scope-breakdown', component: ScopeBreakdownWidget, span: 'half' },
];

const BY_ROLE = {
  // Phase 8b: 'full' rather than 'third' -- Viewer's grid is COMMON (6
  // cols) + this one widget alone, so at 'third' (2 of 6 cols) it left a
  // 4-column dead zone on its own row. 'full' also gives the AI narrative
  // sub-section (when a date range is picked) the same width every other
  // "reports" placement below gets.
  VIEWER: [
    { id: 'reports', component: ReportsWidget, span: 'full' },
  ],
  ANALYST: [
    { id: 'upload', component: UploadShortcutWidget, span: 'third' },
    { id: 'recent-ingestion', component: RecentIngestionWidget, span: 'third' },
    { id: 'validation-summary', component: ValidationSummaryWidget, span: 'third' },
  ],
  // Phase 7f: 'reports' also added here -- Auditor is one of the two
  // roles (with Org Admin) that can actually see AI report narration
  // (CanViewActivity, matching the compliance report's own RBAC), so the
  // widget that surfaces it needs to reach them, not just Viewer.
  // Phase 7g: AI Budget also added here -- CanViewAICosts' role set
  // (apps.accounts.permissions) is Org Admin/Auditor/Platform Admin,
  // mirroring CanViewActivity exactly, same reason 'reports' reaches
  // both roles.
  // Phase 8b: both 'half' rather than 'third' -- as the trailing pair on
  // their own row (2 x 'third' = 4 of 6 cols), they left a 2-column dead
  // zone; 'half' + 'half' fills the row exactly and gives both more room.
  AUDITOR: [
    { id: 'pending-approvals', component: PendingApprovalsWidget, span: 'third' },
    { id: 'audit-queue', component: AuditQueueWidget, span: 'third' },
    { id: 'locked-records', component: LockedRecordsWidget, span: 'third' },
    { id: 'reports', component: ReportsWidget, span: 'half' },
    { id: 'ai-budget', component: AIBudgetWidget, span: 'half' },
  ],
  ORG_ADMIN: [
    { id: 'org-kpis', component: OrgKpisWidget, span: 'half' },
    { id: 'coverage', component: CoverageWidget, span: 'half' },
    { id: 'user-activity', component: UserActivityWidget, span: 'half' },
    { id: 'factor-dataset', component: FactorDatasetWidget, span: 'half' },
    { id: 'reports', component: ReportsWidget, span: 'half' },
    { id: 'ai-budget', component: AIBudgetWidget, span: 'half' },
  ],
  // Phase 7g: cross-tenant AI observability (IsPlatformAdmin, apps.ai.
  // ops_views.AIObservabilityView) -- Platform Admin only, same boundary
  // as the pre-existing cross-tenant carbon widgets above.
  PLATFORM_ADMIN: [
    { id: 'cross-tenant', component: CrossTenantWidget, span: 'half' },
    { id: 'system-health', component: SystemHealthWidget, span: 'half' },
    { id: 'active-orgs', component: ActiveOrganizationsWidget, span: 'half' },
    { id: 'dataset-inventory', component: DatasetInventoryWidget, span: 'half' },
    { id: 'ai-usage', component: AIUsageWidget, span: 'half' },
    { id: 'ai-provider-mix', component: AIProviderMixWidget, span: 'half' },
    { id: 'ai-evaluation', component: AIEvaluationWidget, span: 'half' },
    { id: 'ai-latency-trend', component: AILatencyTrendWidget, span: 'half' },
  ],
};

export const SPAN_CLASS = {
  full: 'col-span-1 md:col-span-2 lg:col-span-6',
  half: 'col-span-1 md:col-span-1 lg:col-span-3',
  third: 'col-span-1 md:col-span-1 lg:col-span-2',
};

export function widgetsForRole(role, isPlatformAdmin) {
  const roleWidgets = isPlatformAdmin ? BY_ROLE.PLATFORM_ADMIN : BY_ROLE[role] || [];
  return [...COMMON, ...roleWidgets];
}
