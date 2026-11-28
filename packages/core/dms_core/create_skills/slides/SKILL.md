---
name: create-slides
description: Use when the task format is slides. The provider pptx Skill writes the file. Each slide holds up to five labelled comparison charts and a short narrative whose numbers come from SQL.
---

# Slides

Plan the deck, check it, then call return_artifact. The provider pptx Skill writes the file. This skill does not draw the file. Use run_sql for every figure. Do not invent a number.

## One slide

One part is one slide.

- title: what this slide compares.
- body: a narrative in ordinary sentences. Say what the charts compare and what a person should notice.
- sql: the query behind a figure the narrative states.
- charts: up to 5 comparison charts. A sixth chart belongs on the next slide.

## A comparison chart

Each chart has a label a person can read (the things being compared) and the sql that produced the values. Use the chart tool to record that spec. The pptx Skill draws it. A chart without a label, or a chart whose values did not come from run_sql, does not belong in the file.

## Numbers

Every number in a title, a narrative, or a chart label must appear in a run_sql result or in the aggregates already in the data context. If it is not there, leave it out.

## Check before return

Read the deck back. Each slide has at most 5 labelled charts. The narrative is sentences. Every number traces to SQL. Then call return_artifact.
