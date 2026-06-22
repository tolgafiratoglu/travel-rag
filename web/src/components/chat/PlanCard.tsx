import type { Plan } from "./types";

const STATUS_ICON: Record<string, string> = {
  idle: "○",
  running: "◑",
  done: "●",
  error: "✕",
};

const STATUS_COLOR: Record<string, string> = {
  idle: "text-gray-400",
  running: "text-blue-500 animate-pulse",
  done: "text-green-600",
  error: "text-red-500",
};

type PlanCardProps = {
  plan: Plan;
};

export function PlanCard({ plan }: PlanCardProps) {
  return (
    <div className="my-2 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <p className="mb-3 text-sm font-medium text-gray-700">{plan.summary}</p>
      <ul className="space-y-1.5">
        {plan.steps.map((step) => (
          <li key={step.id} className="flex items-center gap-2 text-sm">
            <span className={STATUS_COLOR[step.status] ?? "text-gray-400"}>
              {STATUS_ICON[step.status] ?? "○"}
            </span>
            <span className={step.status === "done" ? "text-gray-500" : "text-gray-700"}>
              {step.label}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
