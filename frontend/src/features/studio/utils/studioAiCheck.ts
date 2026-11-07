export const isStudioAiEnabled = (): boolean => {
  return import.meta.env.VITE_STUDIO_AI === 'true';
};

