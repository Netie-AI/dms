"use client";

import { highlightSql } from "../lib/sql-highlight";

export default function SqlBlock({ sql }) {
  if (!sql) return null;
  return (
    <div
      className="cx-sql-block"
      dangerouslySetInnerHTML={{ __html: highlightSql(sql) }}
    />
  );
}
