import React, { useState } from 'react';
import { Copy, Check, ExternalLink } from 'lucide-react';

export const FORBIDDEN_GATEWAY_HOSTS = [
  'api.sandbox.paypal.com',
  'api-m.sandbox.paypal.com',
  'api.paypal.com',
] as const;

export const GATEWAY_VERIFIED_CAPTION =
  'Confirmed by the signed webhook in the Verification step';

export function isForbiddenGatewayUrl(url?: string | null): boolean {
  if (!url || typeof url !== 'string') return false;
  if (url.includes('[MASKED]')) return true;

  try {
    const parsed = new URL(url, 'https://example.com');
    const hostname = parsed.hostname.toLowerCase();
    if (
      hostname === 'api.sandbox.paypal.com' ||
      hostname === 'api-m.sandbox.paypal.com' ||
      hostname === 'api.paypal.com'
    ) {
      return true;
    }
  } catch {
    // If URL parsing fails, check via substring
    const lower = url.toLowerCase();
    if (
      lower.includes('api.sandbox.paypal.com') ||
      lower.includes('api-m.sandbox.paypal.com') ||
      lower.includes('api.paypal.com')
    ) {
      return true;
    }
  }

  return false;
}

export function extractOrderId(url?: string | null, gatewayRef?: string | null): string {
  if (gatewayRef && gatewayRef.trim() && gatewayRef !== 'None') {
    return gatewayRef.trim();
  }
  if (!url || typeof url !== 'string') return '';
  const match = url.match(/\/orders\/([A-Za-z0-9_-]+)/i);
  if (match && match[1]) {
    return match[1];
  }
  return '';
}

export interface GatewayLinkProps {
  url?: string | null;
  gatewayRef?: string | null;
  className?: string;
  linkText?: string;
}

export const GatewayLink: React.FC<GatewayLinkProps> = ({
  url,
  gatewayRef,
  className,
  linkText = 'Gateway link',
}) => {
  const [copied, setCopied] = useState(false);

  const isForbidden = isForbiddenGatewayUrl(url);

  if (isForbidden) {
    const orderId = extractOrderId(url, gatewayRef) || 'None';

    const handleCopy = async () => {
      if (!orderId || orderId === 'None') return;
      try {
        if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(orderId);
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        }
      } catch {
        // clipboard access failure fallback
      }
    };

    return (
      <div
        className="inline-flex flex-wrap items-center gap-2 text-xs"
        data-testid="gateway-forbidden-display"
      >
        <span className="inline-flex items-center gap-1.5">
          <code
            className="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 font-mono text-xs font-semibold text-[var(--color-primary)]"
            data-testid="gateway-order-id"
          >
            {orderId}
          </code>
          <button
            type="button"
            onClick={handleCopy}
            className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-2 py-0.5 text-[11px] font-medium text-gray-700 hover:bg-gray-50 active:bg-gray-100 transition-colors shadow-2xs"
            title="Copy order ID"
            aria-label="Copy order ID"
            data-testid="gateway-copy-button"
          >
            {copied ? (
              <Check className="h-3 w-3 text-emerald-600" />
            ) : (
              <Copy className="h-3 w-3 text-gray-500" />
            )}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </span>
        <span
          className="text-[11px] text-[var(--color-text-muted)]"
          data-testid="gateway-caption"
        >
          {GATEWAY_VERIFIED_CAPTION}
        </span>
      </div>
    );
  }

  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className={
          className ||
          'inline-flex items-center gap-1 text-[var(--color-accent)] underline hover:opacity-80 text-xs'
        }
        data-testid="gateway-ordinary-link"
      >
        <span>{linkText}</span>
        <ExternalLink className="h-3 w-3" />
      </a>
    );
  }

  if (gatewayRef && gatewayRef !== 'None') {
    return (
      <span className="font-mono text-xs text-[var(--color-primary)]" data-testid="gateway-ref-only">
        {gatewayRef}
      </span>
    );
  }

  return null;
};

export default GatewayLink;

