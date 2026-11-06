import { cn } from "@/lib/utils";

interface BadgeProps {
  children: React.ReactNode;
  className?: string;
  variant?: "default" | "secondary" | "destructive" | "outline";
}

export function Badge({ children, className, variant = "default" }: BadgeProps) {
  const variants = {
    default: "bg-blue-600 text-white",
    secondary: "bg-gray-100 text-gray-800",
    destructive: "bg-red-100 text-red-800",
    outline: "border border-gray-300 text-gray-700 bg-transparent",
  };

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
