import React, { useState } from 'react';
import { Copy, Check, ExternalLink } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { LedgerRow } from '@/features/ledger/api/getLedger';
import { formatCurrency } from '@/utils/formatters';
import { formatShortEventId, getAssertionBadgeInfo } from '../utils/studioFormatters';

interface StudioGridProps {
  rows: LedgerRow[];
  currency: string;
}

export const StudioAssertionBadge: React.FC<{ assertion?: string | null }> = ({ assertion }) => {
  const badge = getAssertionBadgeInfo(assertion);
  return (
    <span
      data-testid="assertion-badge"
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${badge.className}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${badge.dotClassName}`} />
      <span>{badge.label}</span>
    </span>
  );
};

export const StudioGrid: React.FC<StudioGridProps> = ({ rows, currency }) => {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const handleCopy = (id: string) => {
    navigator.clipboard?.writeText(id).catch(() => {});
    setCopiedId(id);
    setTimeout(() => {
      setCopiedId((current) => (current === id ? null : current));
    }, 1500);
  };

  const formatDate = (isoStr?: string | null) => {
    if (!isoStr) return '—';
    try {
      const d = new Date(isoStr);
      return d.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
    } catch {
      return isoStr.slice(0, 10);
    }
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs flex flex-col h-full min-h-[460px]">
      <div className="flex items-center justify-between pb-3 border-b border-gray-100 mb-3">
        <div>
          <h2 className="text-base font-semibold text-[var(--color-primary)]">
            Ledger Events
          </h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Sanitized transaction stream
          </p>
        </div>
        <span className="text-xs font-medium text-[var(--color-text-muted)]">
          {rows.length} {rows.length === 1 ? 'record' : 'records'}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overflow-x-auto">
        <table className="w-full text-left text-xs whitespace-nowrap">
          <thead className="bg-[var(--color-bg-surface)] text-[var(--color-text-muted)] sticky top-0 uppercase tracking-wider text-[10px]">
            <tr>
              <th className="px-2.5 py-2 font-medium">Event ID</th>
              <th className="px-2.5 py-2 font-medium">Customer</th>
              <th className="px-2.5 py-2 font-medium">Created Date</th>
              <th className="px-2.5 py-2 font-medium text-right">Amount at Risk</th>
              <th className="px-2.5 py-2 font-medium text-right">Verified Recovered</th>
              <th className="px-2.5 py-2 font-medium text-center">Status</th>
              <th className="px-2.5 py-2 font-medium text-center">Assertion</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => {
              const isCopied = copiedId === row.event_id;
              const shortId = formatShortEventId(row.event_id, 12);
              const isRecovered = row.status?.toLowerCase() === 'recovered';

              return (
                <tr
                  key={row.event_id}
                  className="hover:bg-gray-50/75 transition-colors group"
                >
                  {/* Event ID with copy button */}
                  <td className="px-2.5 py-2.5">
                    <div className="flex items-center gap-1.5 font-mono text-[11px]">
                      <Link
                        to={`/audit-trail?event_id=${encodeURIComponent(row.event_id)}`}
                        className="font-semibold text-[var(--color-primary)] hover:text-[var(--color-accent)] hover:underline inline-flex items-center gap-1"
                        title={row.event_id}
                      >
                        {shortId}
                        <ExternalLink className="h-3 w-3 opacity-0 group-hover:opacity-60 transition" />
                      </Link>
                      <button
                        type="button"
                        onClick={() => handleCopy(row.event_id)}
                        className="p-1 rounded text-gray-400 hover:text-[var(--color-primary)] hover:bg-gray-100 transition"
                        title={isCopied ? 'Copied full ID' : 'Copy full event ID'}
                        aria-label={`Copy event ID ${row.event_id}`}
                      >
                        {isCopied ? (
                          <Check className="h-3 w-3 text-[var(--color-status-recovered)]" />
                        ) : (
                          <Copy className="h-3 w-3" />
                        )}
                      </button>
                    </div>
                  </td>

                  {/* Customer */}
                  <td className="px-2.5 py-2.5 text-[var(--color-text-muted)] font-mono text-[11px]">
                    {row.customer_ref || '—'}
                  </td>

                  {/* Created Date */}
                  <td className="px-2.5 py-2.5 text-[var(--color-text-muted)]">
                    {formatDate(row.created_at)}
                  </td>

                  {/* Amount at Risk */}
                  <td className="px-2.5 py-2.5 text-right font-medium text-[var(--color-primary)] font-mono text-[11px]">
                    {formatCurrency(row.amount_at_risk || 0, currency)}
                  </td>

                  {/* Verified Recovered */}
                  <td className="px-2.5 py-2.5 text-right font-medium text-[var(--color-status-recovered)] font-mono text-[11px]">
                    {formatCurrency(row.verified_amount || 0, currency)}
                  </td>

                  {/* Status */}
                  <td className="px-2.5 py-2.5 text-center">
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                        isRecovered
                          ? 'bg-[var(--color-status-recovered)]/15 text-[var(--color-status-recovered)]'
                          : 'bg-gray-100 text-[var(--color-text-muted)]'
                      }`}
                    >
                      {row.status || 'UNKNOWN'}
                    </span>
                  </td>

                  {/* Verified/Asserted badge */}
                  <td className="px-2.5 py-2.5 text-center">
                    <StudioAssertionBadge assertion={row.assertion} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

