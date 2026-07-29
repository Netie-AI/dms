"use client";

export default function ProgressBar({ active }) {
  return (
    <div className={`cx-progress${active ? " active" : ""}`}>
      <div className="cx-progress-bar" />
    </div>
  );
}
