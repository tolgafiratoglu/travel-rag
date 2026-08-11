"use client";

import type { Plan, PlanStep } from "./types";

type PlanCardProps = {
  plan: Plan;
  /** Live status line under the summary (e.g. Thinking…). */
  status?: string;
};

function StepIcon({ status }: { status: PlanStep["status"] }) {
  const common = "h-4 w-4 shrink-0";
  if (status === "done") {
    return (
      <svg
        className={`${common} text-emerald-600`}
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden
      >
        <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
        <path
          d="M4.5 8.2 6.8 10.5 11.5 5.5"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  if (status === "running") {
    return (
      <svg
        className={`${common} animate-spin text-sky-600`}
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden
      >
        <circle
          cx="8"
          cy="8"
          r="6"
          stroke="currentColor"
          strokeOpacity="0.25"
          strokeWidth="2"
        />
        <path
          d="M14 8a6 6 0 0 0-6-6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (status === "error") {
    return (
      <svg
        className={`${common} text-red-500`}
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden
      >
        <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
        <path
          d="M5.5 5.5 10.5 10.5M10.5 5.5 5.5 10.5"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  return (
    <svg
      className={`${common} text-gray-300`}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
    >
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

export function PlanCard({ plan, status }: PlanCardProps) {
  if (!plan.summary && plan.steps.length === 0) return null;

  return (
    <div className="mb-3 space-y-2">
      {plan.summary ? (
        <p className="text-sm font-medium text-gray-800">{plan.summary}</p>
      ) : null}
      {status ? (
        <p className="text-sm text-gray-400 animate-pulse">{status}</p>
      ) : null}
      {plan.steps.length > 0 ? (
        <ul className="space-y-1.5">
          {plan.steps.map((step) => (
            <li key={step.id} className="flex items-start gap-2 text-sm">
              <span className="mt-0.5">
                <StepIcon status={step.status} />
              </span>
              <span
                className={
                  step.status === "done"
                    ? "text-gray-500"
                    : step.status === "running"
                      ? "text-gray-800"
                      : "text-gray-400"
                }
              >
                {step.label}
                {step.detail && step.status === "done" ? (
                  <span className="text-gray-400"> — {step.detail}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
