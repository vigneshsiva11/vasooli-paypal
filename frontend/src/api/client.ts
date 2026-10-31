export class ApiError extends Error {
  status: number;
  payload: any;
  headers?: Headers;

  constructor(message: string, status: number, payload: any = null, headers?: Headers) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
    this.headers = headers;
  }
}

export function isValidAbsoluteHttpUrl(urlStr: string): boolean {
  try {
    const parsed = new URL(urlStr);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

export function getApiConfig(): { baseUrl: string; error: string | null } {
  const isProd = import.meta.env.PROD;
  const raw = (import.meta.env.VITE_API_BASE_URL || '').trim();

  if (!raw) {
    if (isProd) {
      return {
        baseUrl: '',
        error: 'VITE_API_BASE_URL is missing in production build. Set an absolute HTTP(S) URL.',
      };
    }
    return { baseUrl: 'http://127.0.0.1:8124', error: null };
  }

  if (!isValidAbsoluteHttpUrl(raw)) {
    return {
      baseUrl: '',
      error: `VITE_API_BASE_URL "${raw}" is not a valid absolute http(s) URL.`,
    };
  }

  const trimmed = raw.replace(/\/+$/, '');

  if (isProd) {
    try {
      const parsed = new URL(trimmed);
      if (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1') {
        return {
          baseUrl: '',
          error: 'VITE_API_BASE_URL must not point to localhost in production build.',
        };
      }
    } catch {
      return {
        baseUrl: '',
        error: `VITE_API_BASE_URL "${raw}" is invalid.`,
      };
    }
  }

  return { baseUrl: trimmed, error: null };
}

export const apiClient = async <T>(endpoint: string, options: RequestInit = {}): Promise<T> => {
  if (endpoint.startsWith('/replay/')) {
    const res = await fetch(endpoint, options);
    if (!res.ok) {
      throw new ApiError(`Failed to load replay file ${endpoint}`, res.status);
    }
    return res.json() as Promise<T>;
  }

  const { baseUrl, error } = getApiConfig();
  if (error || !baseUrl) {
    throw new ApiError(error || 'VITE_API_BASE_URL configuration error', 500);
  }

  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  const url = `${baseUrl}${cleanEndpoint}`;

  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });

  if (!res.ok) {
    let payload = null;
    try {
      payload = await res.json();
    } catch (_) {
      // Ignore
    }

    throw new ApiError(
      payload?.detail || payload?.message || 'Request failed',
      res.status,
      payload,
      res.headers
    );
  }

  if (res.status === 204) return null as unknown as T;

  const text = await res.text();
  return text ? JSON.parse(text) : (null as unknown as T);
};
