import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusBadge } from './StatusBadge';

// Phase 6h / H2 — every EmissionRecord.RecordStatus value introduced through
// Phase 6c/6d must render a recognizable (non-"unknown") label. This is the
// exact gap the architecture review found: SUBMITTED/REJECTED/VALIDATED fell
// through to the generic gray "unknown" style.
describe('StatusBadge', () => {
  it.each([
    ['DRAFT', /draft/i],
    ['SUSPICIOUS', /suspicious/i],
    ['VALIDATED', /validated/i],
    ['SUBMITTED', /submitted/i],
    ['APPROVED', /approved/i],
    ['REJECTED', /rejected/i],
    ['FAILED', /failed/i],
  ])('renders a recognizable label for %s', (status, expectedText) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(expectedText)).toBeInTheDocument();
  });

  it('falls back to the raw status string for an unrecognized value instead of throwing', () => {
    render(<StatusBadge status="SOME_FUTURE_STATUS" />);
    expect(screen.getByText('SOME_FUTURE_STATUS')).toBeInTheDocument();
  });
});
