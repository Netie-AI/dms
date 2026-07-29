"use client";

import { useState } from "react";

const CATEGORIES = {
  INVENTORY: [
    "Which SKUs are below reorder level in WH-A?",
    "Show me all expired items",
    "What is the total stock value by category?",
    "Which items haven't been restocked in 30 days?",
    "Show CHEMICALS inventory across all warehouses",
  ],
  LOGISTICS: [
    "Which shipments are delayed?",
    "What is the average lead time by supplier?",
    "Show in-transit shipments arriving this week",
    "Which carrier has the most delayed shipments?",
    "Shipment cost breakdown by destination",
  ],
  SUPPLIERS: [
    "Which suppliers have a risk score above 0.7?",
    "Show suppliers whose audit is overdue by 90+ days",
    "What is our total spend by country of origin?",
    "Which supplier has the longest lead time?",
    "List high-risk suppliers with pending shipments",
  ],
  FACILITIES: [
    "Show warehouse capacity utilisation",
    "Which warehouses are above 90% capacity?",
    "Which locations have cold storage?",
    "Show all active alerts by warehouse",
    "Which warehouse has the most delayed incoming shipments?",
  ],
};

export default function PromptCategories({ onSelect }) {
  const [open, setOpen] = useState(null);

  function toggle(cat) {
    setOpen((prev) => (prev === cat ? null : cat));
  }

  function pick(prompt) {
    onSelect(prompt);
    setOpen(null);
  }

  return (
    <div className="cx-prompt-categories">
      <div className="cx-prompt-cat-row">
        {Object.keys(CATEGORIES).map((cat) => (
          <button
            key={cat}
            type="button"
            className={`cx-prompt-cat-btn ${open === cat ? "open" : ""}`}
            onClick={() => toggle(cat)}
          >
            {cat} {open === cat ? "▴" : "▾"}
          </button>
        ))}
      </div>
      {open && (
        <div className="cx-prompt-panel">
          {CATEGORIES[open].map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="cx-prompt-item"
              onClick={() => pick(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
