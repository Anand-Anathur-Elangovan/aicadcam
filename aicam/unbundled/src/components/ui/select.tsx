"use client";

import { cn } from "@/lib/utils";

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
}

export function Select({ label, className, children, ...props }: SelectProps) {
  return (
    <div className="flex flex-col gap-1">
      {label && <label className="text-sm font-medium text-gray-700">{label}</label>}
      <select
        className={cn(
          "h-9 w-full rounded-md border border-gray-300 bg-white px-3 py-1 text-sm",
          "focus:outline-none focus:ring-2 focus:ring-blue-500",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className
        )}
        {...props}
      >
        {children}
      </select>
    </div>
  );
}

export function SelectOption({
  value,
  children,
}: {
  value: string;
  children: React.ReactNode;
}) {
  return <option value={value}>{children}</option>;
}
