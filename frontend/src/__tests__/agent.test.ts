import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import {
  getDecisionColor,
  parseRetryAfter,
  formatCountdown,
  formatPolicyClause,
} from '../features/agent/utils/agentUtils';
import { ApiError } from '../api/client';

describe('Agent Gateway UI Utilities', () => {
  describe('Decision colours mapping', () => {
    it('maps allowed / authorized decisions to green', () => {
      const allowed = getDecisionColor('allowed');
      expect(allowed.colorName).toBe('green');
      expect(allowed.badgeClass).toContain('emerald');

      const authorized = getDecisionColor('authorized');
      expect(authorized.colorName).toBe('green');
      expect(authorized.badgeClass).toContain('emerald');
    });

    it('maps blocked / refused / unauthorized decisions to red', () => {
      const blocked = getDecisionColor('blocked');
      expect(blocked.colorName).toBe('red');
      expect(blocked.badgeClass).toContain('rose');

      const refused = getDecisionColor('refused');
      expect(refused.colorName).toBe('red');
      expect(refused.badgeClass).toContain('rose');

      const unauthorized = getDecisionColor('unauthorized');
      expect(unauthorized.colorName).toBe('red');
      expect(unauthorized.badgeClass).toContain('rose');
    });

    it('maps manual_review decisions to amber', () => {
      const manualReview = getDecisionColor('manual_review');
      expect(manualReview.colorName).toBe('amber');
      expect(manualReview.badgeClass).toContain('amber');

      const requiresManual = getDecisionColor('requires_manual_review');
      expect(requiresManual.colorName).toBe('amber');
      expect(requiresManual.badgeClass).toContain('amber');

      const spaceManual = getDecisionColor('manual review');
      expect(spaceManual.colorName).toBe('amber');
      expect(spaceManual.badgeClass).toContain('amber');
    });

    it('maps unknown states to neutral gray fallback', () => {
      const unknown = getDecisionColor('something_else');
      expect(unknown.colorName).toBe('gray');
      expect(unknown.badgeClass).toContain('gray');
    });
  });

  describe('Policy clause plain English formatting', () => {
    it('formats ok clearance cleanly', () => {
      const clause = formatPolicyClause(null, 'ok');
      expect(clause.checkTitle).toContain('Policy Invariant Clearance');
      expect(clause.plainEnglish).toContain('authorized');
    });

    it('formats customer opt out invariant', () => {
      const clause = formatPolicyClause('customer_opt_out', 'customer_opted_out');
      expect(clause.checkTitle).toContain('Customer Consent');
      expect(clause.plainEnglish).toContain('opted out');
    });

    it('formats contact cap exceeded invariant', () => {
      const clause = formatPolicyClause('contact_cap', 'contact_cap_exceeded');
      expect(clause.checkTitle).toContain('Frequency Cap');
      expect(clause.plainEnglish).toContain('Maximum contact threshold');
    });

    it('formats 9500 retry refusal', () => {
      const clause = formatPolicyClause('decision_is_actionable', 'retry_not_advised');
      expect(clause.checkTitle).toContain('Actionability Guard');
      expect(clause.plainEnglish).toContain('9500');
    });

    it('formats amount tier approval requirement', () => {
      const clause = formatPolicyClause('amount_tier', 'amount_requires_approval');
      expect(clause.checkTitle).toContain('Value Authorization Ceiling');
      expect(clause.plainEnglish).toContain('manual human authorization');
    });
  });

  describe('429 state and countdown handling', () => {
    it('parses Retry-After from response headers if present', () => {
      const mockHeaders = new Headers();
      mockHeaders.set('retry-after', '45');
      const err = new ApiError('Too many requests', 429, {}, mockHeaders);

      const parsed = parseRetryAfter(err);
      expect(parsed).toBe(45);
    });

    it('parses Retry-After from detail payload string if header not present', () => {
      const err = new ApiError('Rate limit exceeded: try again in 30 seconds', 429, {
        detail: 'Rate limit exceeded: retry in 30s',
      });

      const parsed = parseRetryAfter(err);
      expect(parsed).toBe(30);
    });

    it('defaults to 60 seconds when no retry interval is specified', () => {
      const err = new ApiError('Rate limit exceeded', 429);
      const parsed = parseRetryAfter(err);
      expect(parsed).toBe(60);
    });

    it('formats countdown numbers properly', () => {
      expect(formatCountdown(60)).toBe('60s');
      expect(formatCountdown(5.2)).toBe('6s');
      expect(formatCountdown(0)).toBe('0s');
      expect(formatCountdown(-5)).toBe('0s');
    });
  });

  describe('Codebase token isolation invariants', () => {
    function getAllFiles(dirPath: string, arrayOfFiles: string[] = []): string[] {
      const files: string[] = fs.readdirSync(dirPath);
      files.forEach((file: string) => {
        const fullPath = path.join(dirPath, file);
        if (fs.statSync(fullPath).isDirectory()) {
          getAllFiles(fullPath, arrayOfFiles);
        } else {
          arrayOfFiles.push(fullPath);
        }
      });
      return arrayOfFiles;
    }

    const srcDir = path.resolve(process.cwd(), 'src');
    const allFiles = getAllFiles(srcDir);
    const nonTestFiles = allFiles.filter((f: string) => !f.includes('__tests__'));

    it('ensures nothing under frontend/src contains agent or admin tokens, with admin header only in adminClient.ts', () => {
      const adminHeaderPattern = 'X-Admin-' + 'Token';
      const forbiddenTokens = [
        'ADMIN_' + 'TOKEN',
        'admin_' + 'token',
        'AGENT_' + 'TOKEN',
        'agent_' + 'token',
        'v' + 'ault',
        'p' + 'ayer',
      ];

      const violations: { file: string; match: string; line: number }[] = [];
      const filesWithAdminHeader: string[] = [];

      nonTestFiles.forEach((file: string) => {
        const relPath = path.relative(srcDir, file).replace(/\\/g, '/');
        const content = fs.readFileSync(file, 'utf8');
        const lines: string[] = content.split('\n');

        lines.forEach((line: string, lineIndex: number) => {
          // Check forbidden tokens
          forbiddenTokens.forEach((pattern: string) => {
            if (line.toLowerCase().includes(pattern.toLowerCase())) {
              violations.push({
                file: relPath,
                match: pattern,
                line: lineIndex + 1,
              });
            }
          });

          // Check admin header
          if (line.toLowerCase().includes(adminHeaderPattern.toLowerCase())) {
            if (relPath !== 'features/rules/adminClient.ts') {
              violations.push({
                file: relPath,
                match: adminHeaderPattern,
                line: lineIndex + 1,
              });
            } else if (!filesWithAdminHeader.includes(relPath)) {
              filesWithAdminHeader.push(relPath);
            }
          }
        });
      });

      expect(violations, `Found forbidden token references in frontend/src: ${JSON.stringify(violations)}`).toEqual([]);
      expect(filesWithAdminHeader).toEqual(['features/rules/adminClient.ts']);
    });
  });
});

