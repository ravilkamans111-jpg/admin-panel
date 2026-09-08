import { statusLabel } from "@/lib/format";

const STATUS_STYLES: Record<string, string> = {
  SUCCESS: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  ACCEPTED: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  DECLINED: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  APPEAL: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  PENDING: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
};

const FALLBACK_STYLE = "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";

export function StatusBadge({ value }: { value: unknown }) {
  const text = String(value ?? "—");
  const style = STATUS_STYLES[text.toUpperCase()] || FALLBACK_STYLE;
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${style}`}>
      {statusLabel(text)}
    </span>
  );
}
