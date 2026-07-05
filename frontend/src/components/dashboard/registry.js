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
} from './widgets/OrgAdminWidgets';
import {
  CrossTenantWidget,
  SystemHealthWidget,
  ActiveOrganizationsWidget,
  DatasetInventoryWidget,
} from './widgets/PlatformWidgets';

// span: full (whole row) | half | third
const COMMON = [
  { id: 'kpi-summary', component: KpiSummaryWidget, span: 'full' },
  { id: 'emissions-trend', component: EmissionsTrendWidget, span: 'half' },
  { id: 'scope-breakdown', component: ScopeBreakdownWidget, span: 'half' },
];

const BY_ROLE = {
  VIEWER: [
    { id: 'reports', component: ReportsWidget, span: 'third' },
  ],
  ANALYST: [
    { id: 'upload', component: UploadShortcutWidget, span: 'third' },
    { id: 'recent-ingestion', component: RecentIngestionWidget, span: 'third' },
    { id: 'validation-summary', component: ValidationSummaryWidget, span: 'third' },
  ],
  AUDITOR: [
    { id: 'pending-approvals', component: PendingApprovalsWidget, span: 'third' },
    { id: 'audit-queue', component: AuditQueueWidget, span: 'third' },
    { id: 'locked-records', component: LockedRecordsWidget, span: 'third' },
  ],
  ORG_ADMIN: [
    { id: 'org-kpis', component: OrgKpisWidget, span: 'half' },
    { id: 'coverage', component: CoverageWidget, span: 'half' },
    { id: 'user-activity', component: UserActivityWidget, span: 'half' },
    { id: 'factor-dataset', component: FactorDatasetWidget, span: 'half' },
  ],
  PLATFORM_ADMIN: [
    { id: 'cross-tenant', component: CrossTenantWidget, span: 'half' },
    { id: 'system-health', component: SystemHealthWidget, span: 'half' },
    { id: 'active-orgs', component: ActiveOrganizationsWidget, span: 'half' },
    { id: 'dataset-inventory', component: DatasetInventoryWidget, span: 'half' },
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
