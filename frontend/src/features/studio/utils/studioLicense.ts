import { AgStudioLicenseManager } from 'ag-studio';

let licenseApplied = false;

export const applyStudioLicense = (): void => {
  if (licenseApplied) return;
  const key = import.meta.env.VITE_AG_STUDIO_LICENSE_KEY;
  if (key && typeof key === 'string' && key.trim().length > 0) {
    AgStudioLicenseManager.setLicenseKey(key.trim());
    licenseApplied = true;
  }
};

