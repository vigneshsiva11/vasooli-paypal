import React from 'react';

export const StudioSkeleton: React.FC = () => {
  return (
    <div className="space-y-6 animate-pulse" aria-label="Loading Studio dashboard">
      {/* Header skeleton */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="h-8 w-28 rounded-lg bg-gray-200" />
            <div className="h-5 w-24 rounded-full bg-gray-100" />
            <div className="h-5 w-20 rounded-md bg-gray-100" />
          </div>
          <div className="h-4 w-72 rounded bg-gray-100" />
          <div className="h-3 w-48 rounded bg-gray-100" />
        </div>
        <div className="h-9 w-24 rounded-lg bg-gray-200" />
      </div>

      {/* Row 1: Three equal KPI cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs h-28 flex flex-col justify-between">
          <div className="h-3 w-28 rounded bg-gray-200" />
          <div className="h-8 w-36 rounded bg-gray-200" />
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs h-28 flex flex-col justify-between">
          <div className="h-3 w-28 rounded bg-gray-200" />
          <div className="h-8 w-36 rounded bg-gray-200" />
        </div>
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-xs h-28 flex flex-col justify-between">
          <div className="h-3 w-20 rounded bg-gray-200" />
          <div className="h-8 w-24 rounded bg-gray-200" />
        </div>
      </div>

      {/* Row 2: Chart (40%) and Grid (60%) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
        <div className="lg:col-span-5 rounded-xl border border-gray-200 bg-white p-5 shadow-xs h-[460px] flex flex-col justify-between">
          <div className="space-y-2">
            <div className="h-4 w-44 rounded bg-gray-200" />
            <div className="h-3 w-32 rounded bg-gray-100" />
            <div className="space-y-4 pt-6">
              <div className="h-10 rounded bg-gray-100" />
              <div className="h-10 rounded bg-gray-100" />
              <div className="h-10 rounded bg-gray-100" />
            </div>
          </div>
          <div className="h-4 w-full rounded bg-gray-100" />
        </div>

        <div className="lg:col-span-7 rounded-xl border border-gray-200 bg-white p-5 shadow-xs h-[460px] flex flex-col">
          <div className="h-4 w-32 rounded bg-gray-200 mb-4" />
          <div className="space-y-3 flex-1">
            <div className="h-8 rounded bg-gray-100" />
            <div className="h-8 rounded bg-gray-100" />
            <div className="h-8 rounded bg-gray-100" />
            <div className="h-8 rounded bg-gray-100" />
            <div className="h-8 rounded bg-gray-100" />
          </div>
        </div>
      </div>
    </div>
  );
};

